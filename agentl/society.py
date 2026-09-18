"""Société d'agents (AGENT-L v0.6).

`DELEGATE` (v0.3) traitait le sous-agent comme un oracle : appel synchrone,
réponse immédiate, contrat `EXPECT`. C'est la bonne primitive quand on sait
*qui* interroger et qu'on attend la réponse.

La v0.6 ajoute le cas où ce n'est pas vrai : plusieurs agents qui tournent en
parallèle, s'envoient des messages asynchrones et partagent un état. Deux
mécanismes, tous deux volontairement modestes :

  * **`MESSAGE`** — remise asynchrone, adressée ou diffusée. Le message est
    déposé dans la boîte du destinataire et traité à son tick suivant, en
    phase `RECEIVE`, avant toute perception. Un agent ne peut donc jamais
    agir sur un message et l'observer dans le même souffle.

  * **`MEMORY { SHARED { … } }`** — un unique dictionnaire, versionné
    **par clé** (v1.2). Chaque écriture incrémente la version de *sa* clé ;
    un agent qui écrit alors que cette clé a bougé depuis sa dernière
    écriture voit la sienne acceptée — dernier écrivain gagnant — mais **le
    conflit est tracé et compté**. Deux agents qui écrivent des faits sans
    rapport ne se déclarent plus mutuellement en conflit, ce qui était le
    défaut de la v0.6 : à granularité trop grossière, le signal se noie.

    Le compartiment se **lit** depuis la v1.6 : `SHARED.<clé>` en position
    d'expression rend la suite d'enregistrements, et `.count`, `.version`,
    `.last[.champ]` en donnent les vues utiles à une garde. Jusque-là il
    s'écrivait sans pouvoir se relire — les écritures étaient tracées et
    versionnées, et n'influençaient aucune décision. Un état « partagé » que
    personne ne peut observer ne partage rien.

Ce que la société ne fait pas, délibérément : pas de transaction, pas de
consensus, pas d'ordre global sur les messages autre que le tour de rôle.
Ces garanties se paient, et rien dans le langage ne les promet.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .core import fmt
from .host import Host
from .llm import LLM, MockLLM
from .nodes import Agent
from .runtime import Runtime, Trace


@dataclass
class Envelope:
    sender: str
    recipient: str
    name: str
    payload: Dict[str, Any] = field(default_factory=dict)
    tick: int = 0


class Society:
    """Ordonnance plusieurs runtimes et achemine leurs messages."""

    def __init__(self, agents: Sequence[Agent],
                 hosts: Optional[Dict[str, Host]] = None,
                 llms: Optional[Dict[str, LLM]] = None,
                 echo: bool = False):
        hosts = hosts or {}
        llms = llms or {}
        self.shared: Dict[str, Any] = {"__versions": {}}
        self.log: List[Envelope] = []
        self.trace = Trace()
        self.trace.echo = echo
        self.runtimes: Dict[str, Runtime] = {}
        self.order: List[str] = []

        for agent in agents:
            runtime = Runtime(agent,
                              hosts.get(agent.name, Host()),
                              llms.get(agent.name, MockLLM()),
                              echo=echo)
            runtime.society = self
            # Un seul dictionnaire pour tous : c'est ce qui rend le partage réel.
            runtime.state.memory["SHARED"] = self.shared
            # …et il faut y re-semer les clés déclarées. `_bootstrap()` les a
            # posées dans le dictionnaire *privé* du runtime, que la ligne
            # ci-dessus vient de remplacer : sans ce second semis, une clé
            # déclarée mais pas encore écrite se lirait `UNDEFINED` au lieu de
            # la suite vide — et une garde `SHARED.k.count == 0` serait fausse
            # au premier tick, quand elle est précisément vraie.
            for name in agent.memory.shared:
                self.shared.setdefault(name, [])
            self.runtimes[agent.name] = runtime
            self.order.append(agent.name)

    # ------------------------------------------------------------------ bus
    def send(self, sender: str, name: str, payload: Dict[str, Any],
             to: Optional[str] = None) -> int:
        """Dépose un message. Retourne le nombre de destinataires."""
        if to is None:
            recipients = [n for n in self.order if n != sender]
        else:
            recipients = [n for n in self.order if _matches(n, to)]
            if not recipients:
                self.trace.log(0, "ERROR",
                               f"{sender} → destinataire inconnu : {to}")
                return 0
        for recipient in recipients:
            envelope = Envelope(sender, recipient, name, dict(payload))
            self.log.append(envelope)
            self.runtimes[recipient].inbox.append(
                {"name": name, "from": sender, "payload": dict(payload)})
        return len(recipients)

    # ----------------------------------------------------------- ordonnancement
    def tick(self) -> None:
        for name in self.order:
            self.runtimes[name].tick()

    def run(self, max_ticks: int = 6, until: Optional[str] = None) -> "Society":
        """Tourne au plus `max_ticks` tours de rôle.

        `until` nomme un agent : dès que son objectif global est satisfait,
        la société s'arrête. Sans lui, on s'arrête quand *tous* les agents
        ont un score de 1.
        """
        for _ in self.iter_ticks(max_ticks, until):
            pass
        return self

    def iter_ticks(self, max_ticks: int = 6, until: Optional[str] = None):
        """Même boucle que `run`, rendue tour après tour (exécution durable)."""
        for round_no in range(1, max_ticks + 1):
            self.tick()
            yield round_no
            if until is not None:
                if self.runtimes[until].state.get("goal.satisfied") is True:
                    break
            elif all(rt.state.get("goal.satisfied") is True
                     for rt in self.runtimes.values() if rt.agent.goals):
                break

    # ------------------------------------------------------------- rapports
    @property
    def metrics(self) -> Dict[str, int]:
        total: Dict[str, int] = {}
        for runtime in self.runtimes.values():
            for key, value in runtime.metrics.items():
                total[key] = total.get(key, 0) + value
        total["messages_in_flight"] = sum(len(r.inbox)
                                          for r in self.runtimes.values())
        total["shared_keys"] = len(self.shared.get("__versions", {}))
        return total

    @property
    def shared_versions(self) -> Dict[str, int]:
        return dict(self.shared.get("__versions", {}))

    def render_traces(self) -> str:
        chunks = []
        for name in self.order:
            chunks.append(f"\n╔═ {name} " + "═" * max(0, 48 - len(name)))
            chunks.append(self.runtimes[name].trace.render())
        return "\n".join(chunks)

    def render_messages(self) -> str:
        if not self.log:
            return "aucun message échangé"
        return "\n".join(
            f"  {e.sender} → {e.recipient} : {e.name}(" +
            ", ".join(f"{k}={fmt(v)}" for k, v in e.payload.items()) + ")"
            for e in self.log)

    def render_shared(self) -> str:
        versions = self.shared.get("__versions", {})
        if not versions:
            return "  mémoire partagée vide"
        lines = ["  mémoire partagée :"]
        for key, version in sorted(versions.items()):
            records = self.shared.get(key, [])
            lines.append(f"    {key}  v{version} — {len(records)} entrée(s)")
            for record in records:
                lines.append("      · " + ", ".join(f"{k}={fmt(v)}"
                                                    for k, v in record.items()))
        return "\n".join(lines)


def _matches(agent_name: str, target: str) -> bool:
    """Adressage insensible à la casse : `TO soc_analyst` vise SOC_ANALYST."""
    return agent_name.lower() == target.lower()

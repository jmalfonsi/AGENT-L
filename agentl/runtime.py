"""Runtime AGENT-L.

Boucle canonique (§18 de l'étude) :

    Observe → Believe → Evaluate → Plan → Act → Verify → Learn

Invariant d'architecture : **le LLM ne contrôle pas le runtime**. Il propose ;
le runtime décide. Et depuis la v1.9, le runtime lui-même ne détient plus le
droit d'appeler l'hôte : il demande un permis au noyau (`agentl.kernel`), qui
applique le contrat INPUT, la politique et l'approbation, puis exécute. Un
chemin du runtime qui oublierait le noyau n'a pas de permis à présenter, et
l'hôte le refuse.
"""
from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field
import math
from typing import Any, Dict, List, Optional

from .core import UNDEFINED, Symbol, fmt, truthy
from .host import Host
from .kernel import ActionInDoubt, Kernel, KernelAbort
from .kernel import gate as _gate
from .kernel import provenance as P
from .kernel.provenance import Prov, UNKNOWN_LABEL, unwrap
from .kernel.action import (_NUMERIC_TYPES, _TYPE_CHECKS,  # noqa: F401
                            coerce_inputs as _coerce_inputs,
                            typecheck as _typecheck)
from .llm import LLM, MockLLM, judge_via_reason
from .nodes import (
    Agent, AskStmt, CallStmt, ControlStmt, DelegateStmt, ForEachStmt, IfStmt,
    JudgeStmt, LoopStmt,
    MessageStmt, Plan, ReasonStmt, SetStmt, Stmt, ThenPlan, VerifyStmt,
)
from .planner import Planner, PlanningResult
from .policy import ActionRequest, _ActionScope
from .state import Evaluator, State, label_key

DEFAULT_CYCLE = ["RECEIVE", "OBSERVE", "UPDATE_BELIEFS", "UPDATE_HYPOTHESES",
                 "EVALUATE_GOALS", "SELECT_PLAN", "EXECUTE", "VERIFY",
                 "UPDATE_MEMORY"]

#: Étiquettes de provenance des sources fixes (v1.9).
_RUNTIME = Prov({P.RUNTIME})
_DECLARED = Prov({P.DECLARED})
_OBSERVED = Prov({P.OBSERVED})
_HUMAN = Prov({P.HUMAN})
_LLM = Prov({P.LLM})
_TOOL = Prov({P.TOOL})
_DELEGATE = Prov({P.DELEGATE})
_INFERRED = Prov({P.INFERRED})
_EFFECT = Prov({P.EFFECT})
_PAYLOAD = {"message": Prov({P.MESSAGE}), "event": Prov({P.EVENT})}


@dataclass
class TraceEvent:
    tick: int
    kind: str
    text: str
    detail: str = ""


class Trace:
    GLYPH = {
        "TICK": "──", "OBSERVE": "👁", "BELIEF": "◆", "GOAL": "◎",
        "PLAN": "▶", "STEP": "  ·", "TOOL": "🔧", "BLOCKED": "⛔",
        "APPROVAL": "🙋", "VERIFY_OK": "✅", "VERIFY_FAIL": "❌",
        "LLM": "🧠", "MEMORY": "💾", "EVENT": "⚡", "ASK": "❓",
        "DELEGATE": "→", "ERROR": "‼", "INFO": "•", "RETRY": "↻",
        "BAYES": "∿", "PLANNER": "⌘", "MESSAGE": "✉", "SHARED": "⇄",
        "POLICY": "§",
    }

    def __init__(self, sink=None) -> None:
        self.events: List[TraceEvent] = []
        self.sink = sink
        self.echo = False

    def log(self, tick: int, kind: str, text: str, detail: str = "") -> None:
        ev = TraceEvent(tick, kind, text, detail)
        self.events.append(ev)
        if self.sink is not None:
            try:
                self.sink(ev)
            except Exception:  # le journal externe ne pilote jamais le runtime
                pass
        if self.echo:
            print(self.line(ev))

    def line(self, ev: TraceEvent) -> str:
        if ev.kind == "TICK":
            return f"\n┌─ tick {ev.tick} {'─' * max(0, 46 - len(str(ev.tick)))}"
        glyph = self.GLYPH.get(ev.kind, "•")
        suffix = f"   [{ev.detail}]" if ev.detail else ""
        return f"│ {glyph} {ev.text}{suffix}"

    def render(self) -> str:
        return "\n".join(self.line(e) for e in self.events) + "\n└" + "─" * 52

    def of_kind(self, *kinds: str) -> List[TraceEvent]:
        return [e for e in self.events if e.kind in kinds]


class _VerifyFailure(Exception):
    def __init__(self, stmt: VerifyStmt):
        self.stmt = stmt


class Runtime:
    #: Échecs consécutifs au-delà desquels un outil cesse d'être appelé.
    #: Un service mort ne devient pas vivant parce qu'on insiste : sans
    #: disjoncteur, un programme à curseur martelait une API tombée à chaque
    #: tick — 122 tentatives observées sur une épreuve de panne. Zéro désarme.
    TOOL_BREAKER_THRESHOLD = 3
    #: Ticks avant de laisser repasser **un seul** appel de sondage
    #: (demi-ouverture). C'est ce qui permet à l'agent de repartir tout seul
    #: quand le service revient, sans avoir insisté entre-temps.
    TOOL_BREAKER_COOLDOWN = 5

    def __init__(self, agent: Agent, host: Optional[Host] = None,
                 llm: Optional[LLM] = None, echo: bool = False,
                 tool_breaker: Optional[int] = None,
                 tool_cooldown: Optional[int] = None,
                 run_id: str = "run"):
        self.agent = agent
        self.host = host or Host()
        self.llm = llm or MockLLM()
        self.tool_breaker = (self.TOOL_BREAKER_THRESHOLD if tool_breaker is None
                             else int(tool_breaker))
        self.tool_cooldown = (self.TOOL_BREAKER_COOLDOWN if tool_cooldown is None
                              else int(tool_cooldown))
        #: `nom d'outil → (échecs consécutifs, tick du dernier échec)`.
        self._tool_failures: Dict[str, tuple] = {}
        self.state = State()
        #: Seule autorité capable d'appeler l'hôte. Le moteur de politiques
        #: est le sien : le runtime le partage (planificateur, affichage) mais
        #: ne décide plus avec.
        self.kernel = Kernel(agent, run_id=run_id)
        self.policy = self.kernel.policy
        self.trace = Trace(getattr(self.host, "trace_sink", None))
        self.trace.echo = echo
        self.plan_queue: deque = deque()
        self.verify_failure: Optional[VerifyStmt] = None
        self.metrics = {"ticks": 0, "tool_calls": 0, "blocked": 0,
                        "approvals": 0, "verify_pass": 0, "verify_fail": 0,
                        "llm_calls": 0, "memory_writes": 0,
                        "inferences": 0, "plans_synthesized": 0,
                        "messages_sent": 0, "messages_received": 0,
                        "shared_writes": 0, "shared_conflicts": 0,
                        "domain_clamps": 0, "effect_drift": 0,
                        "redactions": 0, "sensor_unknown": 0,
                        "sensor_degraded": 0, "tool_failures": 0,
                        "tool_contract_failures": 0,
                        "tool_output_dropped": 0,
                        "circuit_open": 0, "reason_degraded": 0,
                        "judge_abstained": 0}
        self.society = None                 # branché par Society (v0.6)
        self.inbox: deque = deque()
        self._seen_shared: Dict[str, int] = {}
        #: Capteurs dont la panne a déjà réveillé un humain (v1.8).
        self._sensor_escalated: set = set()
        #: Registre de dérive : `"outil→chemin" → {confirmé, démenti}`.
        #: Rechargé depuis `LONG_TERM.effect_drift` si la clé est déclarée,
        #: pour qu'une exécution ne reparte pas crédule (voir `DRIFT_KEY`).
        self.drift: Dict[str, Dict[str, int]] = {}
        self.inferences: Dict[str, Any] = {}
        self.planner = (Planner(agent, agent.planner, self.policy)
                        if agent.planner and agent.planner.enabled else None)
        self._synth: Dict[str, Plan] = {}
        #: Contexte de contrôle (v1.9) : étiquettes des décisions en cours —
        #: plan choisi, branche prise, gestionnaire déclenché. Tout ce qui
        #: s'écrit ou s'exécute dessous en hérite (flux implicite).
        self._control: List[Prov] = []
        #: Étiquette de la décision qui a mis chaque plan en file.
        self._plan_labels: Dict[str, Prov] = {}
        self._bootstrap()

    # -------------------------------------------------------- résolution
    def plan_named(self, name: str) -> Optional[Plan]:
        """Plans déclarés *et* plans synthétisés par le planificateur."""
        return self.agent.plan(name) or self._synth.get(name)

    # ---------------------------------------------------- provenance (v1.9)
    def _pc(self) -> Prov:
        """Étiquette du contexte de contrôle courant."""
        return P.join(*self._control) if self._control else P.NONE

    @contextmanager
    def _under(self, label: Prov):
        """Exécute un bloc sous une décision d'étiquette `label`."""
        self._control.append(label)
        try:
            yield
        finally:
            self._control.pop()

    def _label(self, node: Any, evaluator: Optional[Evaluator] = None) -> Prov:
        """Étiquette d'une expression ; inévaluable = inconnue, jamais sûre."""
        try:
            return (evaluator or Evaluator(self.state)).label(node)
        except Exception:                             # noqa: BLE001
            return UNKNOWN_LABEL

    # ------------------------------------------------------ évaluation sûre
    def _safe_test(self, cond, *, on_error: bool, context: str,
                   evaluator: Optional[Evaluator] = None) -> bool:
        """Évalue une condition sans jamais laisser une exception remonter.

        Une expression malformée (arithmétique sur symbole, appel non pur,
        etc.) ne doit pas faire tomber la boucle d'un agent régulé. En cas
        d'erreur, on retient `on_error` — choisi *fermé* (refus) partout où la
        sûreté est en jeu — et on trace.

        Jusqu'en v1.8 seuls trois sites y passaient : les gardes de plan, de
        `DECIDE`, d'`IF`, d'`EVENT`, d'`ON MESSAGE`, d'`OBSERVE`, d'écriture
        mémoire et de score de but appelaient `Evaluator.test` en direct. Un
        capteur rendant une chaîne là où la garde attend un nombre —
        `sensor.value + 1 > 2` — faisait alors remonter une `EvalError` hors
        du tick : le processus tombait. C'est un défaut de disponibilité, pas
        de sûreté, mais un agent régulé qui s'arrête sur une donnée
        inattendue ne surveille plus rien.

        `evaluator` sert aux appelants qui ont besoin des `notes` accumulées
        (`VERIFY` les consigne) ou d'un régime particulier.
        """
        try:
            return (evaluator or Evaluator(self.state)).test(cond)
        except Exception as exc:                      # noqa: BLE001
            self.trace.log(self.state.tick, "ERROR",
                           f"condition inévaluable ({context})",
                           f"{type(exc).__name__}: {exc} — "
                           f"repli {'vrai' if on_error else 'fermé (faux)'}")
            return on_error

    # ---------------------------------------------------- frontière de l'hôte
    def _read(self, path: str) -> Any:
        """Lecture de capteur, tolérante aux pannes : un capteur qui lève ne
        fait pas tomber la boucle — il ne produit simplement pas de perception."""
        try:
            return self.host.read(path)
        except KernelAbort:
            # Une divergence de rejeu n'est pas une panne de capteur. La
            # convertir en perception absente permettrait à un journal
            # tronqué ou réordonné de sembler rejouable. Même chose pour une
            # annulation ou une violation de permis (v1.9).
            raise
        except Exception as exc:                      # noqa: BLE001
            self.trace.log(self.state.tick, "ERROR",
                           f"capteur {path} en erreur",
                           f"{type(exc).__name__}: {exc} — perception ignorée")
            return None

    def _ask(self, question: str, reason: str = "") -> Any:
        """Interrogation de l'opérateur, tolérante aux pannes : une console
        injoignable vaut absence de réponse (le DEFAULT s'appliquera)."""
        try:
            return self.host.ask(question, reason)
        except KernelAbort:
            raise
        except Exception as exc:                      # noqa: BLE001
            self.trace.log(self.state.tick, "ERROR",
                           "opérateur injoignable",
                           f"{type(exc).__name__}: {exc} — sans réponse")
            return Symbol("no_answer")

    def _drain_events(self) -> List[Dict[str, Any]]:
        """Une file d'événements indisponible ne tue pas la surveillance.

        ``read`` et ``invoke`` étaient déjà des frontières tolérantes aux
        pannes, mais ``Host.drain`` restait appelé directement. Un broker
        déconnecté faisait donc remonter son exception hors du tick. Le repli
        est l'absence d'événement pour ce tour, avec une trace explicite ; il
        n'invente jamais un message ni une action.
        """
        try:
            events = self.host.drain()
        except KernelAbort:
            raise
        except Exception as exc:                      # noqa: BLE001
            self.trace.log(
                self.state.tick, "ERROR", "file d'événements indisponible",
                f"{type(exc).__name__}: {exc} — aucun événement consommé")
            return []
        if not isinstance(events, list):
            self.trace.log(
                self.state.tick, "ERROR", "file d'événements non conforme",
                f"attendu list, reçu {type(events).__name__} — contenu ignoré")
            return []
        return events

    # -------------------------------------------------- action gouvernée
    def _authorize(self, request: ActionRequest, kind: str = "invoke"):
        """Demande un permis au noyau, et trace chacune de ses étapes.

        Outil et sous-agent sont deux Adapters d'invocation différents, mais
        leur autorisation a une seule sémantique — elle vit dans
        `Kernel.authorize` depuis la v1.9. Ce qui reste ici est ce qui n'est
        pas du noyau : les lignes de trace, les métriques et l'état d'audit
        `last_action.blocked`, dans le même ordre qu'avant l'extraction (les
        journaux publiés se rejouent à l'octet, `tests/golden`).

        Rend le permis, ou `None` si l'action ne passe pas.
        """
        subject = (f"DELEGATE {request.tool}"
                   if kind == "delegate" else request.render())
        auth = self.kernel.authorize(
            request, self.state, kind=kind,
            # L'attribut est résolu *dans* le noyau : un hôte sans `approve`
            # échoue à l'approbation — refus — au lieu de faire tomber la
            # boucle avant même la politique.
            approve=lambda proposal: self.host.approve(proposal),
            observer=_AuthorizationTrace(self, subject))
        if auth.granted:
            if auth.approved:
                self.trace.log(self.state.tick, "APPROVAL", "approuvé")
            # La capacité a franchi la gouvernance. Une panne d'invocation
            # reste une panne du monde, pas un refus de politique.
            self.state.set_local("last_action.blocked", False, _RUNTIME)
            return auth.permit

        self.metrics["blocked"] += 1
        if auth.stage == _gate.STAGE_ISOLATION:
            self.trace.log(self.state.tick, "BLOCKED", f"{subject} refusé",
                           f"proposition non isolable : {auth.detail} — "
                           "refus (fail-closed)")
        elif auth.stage == _gate.STAGE_POLICY_ERROR:
            self.trace.log(
                self.state.tick, "BLOCKED", f"{subject} refusé",
                f"politique inévaluable : {auth.detail} — "
                "repli fermé (DENY)")
        elif auth.stage == _gate.STAGE_POLICY:
            self.trace.log(self.state.tick, "BLOCKED",
                           f"{subject} refusé", auth.detail)
        elif auth.stage == _gate.STAGE_APPROVAL_ISOLATION:
            self.trace.log(
                self.state.tick, "BLOCKED", f"{subject} non approuvé",
                f"proposition non isolable : {auth.detail}"
                " — refus (fail-closed)")
        else:
            self.trace.log(self.state.tick, "BLOCKED",
                           f"{subject} non approuvé")
        self.state.set_local("last_action.blocked", True, _RUNTIME)
        return None

    # ------------------------------------------------------- oracle dégradé
    def _flag_reason(self, stmt: Any, missing: Optional[List[str]],
                     schema: Dict[str, str]) -> None:
        """Publie l'état de la dernière réponse d'oracle, lisible par une garde.

        Trois cas, et le programme doit pouvoir les distinguer :
        complet (le modèle a rendu tous les champs), partiel (il en manque),
        muet (il n'en a rendu aucun, ou l'appel a levé). Seul le troisième
        justifie de suspendre une décision — mais un `PRODUCE` numérique
        partiellement absent mérite au moins d'apparaître dans la trace.

            NEVER escalate WHEN reason.degraded == true
        """
        total = len(schema)
        absent = len(missing) if missing is not None else 0
        # `missing is None` : adaptateur qui ne renseigne pas le verdict. On
        # ne présume pas d'une panne — silence n'est pas dégradation.
        degraded = bool(total) and absent >= total
        # Le modèle décide de répondre ou de se taire : le fait est calculé
        # par le runtime, mais c'est l'oracle qui le provoque.
        self.state.set_local("reason.degraded", degraded, _RUNTIME | _LLM)
        self.state.set_local("reason.missing", absent, _RUNTIME | _LLM)
        if degraded:
            self.metrics["reason_degraded"] += 1
            # Volontairement PAS `ERROR` : un oracle qui se tait n'est pas une
            # faute du runtime, et `agentl autoloop` juge l'invariant « aucune
            # erreur d'exécution » sur ce type. Confondre les deux ferait
            # tomber la porte sur tout monde dérivé qu'aucun script ne couvre.
            # La visibilité passe par `reason.degraded`, la métrique
            # `reason_degraded` et cette ligne — pas par le type d'événement.
            self.trace.log(
                self.state.tick, "LLM",
                f"{stmt.keyword} « {stmt.task} » : oracle muet",
                f"aucun des {total} champs rendu — schéma par défaut appliqué, "
                f"`reason.degraded` posé")
        elif absent:
            self.trace.log(
                self.state.tick, "LLM",
                f"{stmt.keyword} « {stmt.task} » : réponse partielle",
                f"{absent}/{total} champ(s) absent(s) : "
                f"{', '.join(sorted(missing or []))} — défauts appliqués")

    # ----------------------------------------------------------- disjoncteur
    # L'hôte fournit des FAITS : « cet outil est indisponible » en est un, au
    # même titre qu'un capteur muet. Le disjoncteur ne décide donc rien à la
    # place du programme — il cesse de marteler un service mort et **publie
    # l'état**, que la politique peut lire :
    #
    #     NEVER close_batch WHEN tools.update_row.available == false
    #
    # Même forme que `sensors.<chemin>.available`, pour qu'il n'y ait qu'une
    # convention à retenir.

    def _record_tool_failure(self, name: str) -> None:
        failures = self._tool_failures.get(name, (0, 0))[0] + 1
        self._tool_failures[name] = (failures, self.state.tick)
        self.state.set_world(f"tools.{name}.failures", failures, _RUNTIME)
        if self.tool_breaker > 0 and failures >= self.tool_breaker:
            self.state.set_world(f"tools.{name}.available", False, _RUNTIME)

    def _record_tool_success(self, name: str) -> None:
        """Un succès referme le disjoncteur : la panne était passagère."""
        if name in self._tool_failures:
            self._tool_failures.pop(name)
        self.state.set_world(f"tools.{name}.failures", 0, _RUNTIME)
        self.state.set_world(f"tools.{name}.available", True, _RUNTIME)

    def _cooldown_left(self, name: str) -> int:
        last = self._tool_failures.get(name, (0, 0))[1]
        return max(0, self.tool_cooldown - (self.state.tick - last))

    def _circuit_open(self, name: str) -> bool:
        """Vrai quand l'outil est coupé — sauf pour le sondage de reprise.

        Demi-ouverture : passé le délai de garde, **un** appel repasse. S'il
        réussit le compteur est remis à zéro, s'il échoue le délai repart.
        Sans ce sondage, un service revenu à la vie resterait coupé jusqu'à
        la fin du run.
        """
        if self.tool_breaker <= 0:
            return False
        failures = self._tool_failures.get(name)
        if failures is None or failures[0] < self.tool_breaker:
            return False
        return self._cooldown_left(name) > 0

    # ------------------------------------------------------------ amorçage
    def _bootstrap(self) -> None:
        ev = Evaluator(self.state)
        for decl in self.agent.beliefs:
            self.state.set_belief(decl.path, ev.eval(decl.value),
                                  decl.confidence, decl.source, decl.updated,
                                  _DECLARED | self._label(decl.value, ev))
        for bucket, names in (("SHORT_TERM", self.agent.memory.short_term),
                              ("LONG_TERM", self.agent.memory.long_term),
                              ("KNOWLEDGE", self.agent.memory.knowledge),
                              ("SHARED", self.agent.memory.shared)):
            self.state.memory.setdefault(bucket, {})
            for name in names:
                self.state.memory[bucket].setdefault(name, [])
                self.state.label_memory(bucket, name, _DECLARED)
        # « Aucune action de cet outil n'est indéterminée » est un fait vrai
        # au démarrage, et il doit être posé : indéfini, il rendrait
        # indéterminée toute garde `NEVER x WHEN tools.x.in_doubt == true` —
        # donc, fermée, elle interdirait l'outil pour toujours (v1.9).
        for decl in self.agent.tools:
            self.state.set_world(f"tools.{decl.name}.in_doubt", False,
                                 _RUNTIME)
        self._load_drift()

    def _apply_initial_values(self, values: Dict[str, Any], source: str) -> None:
        """Route un état initial vers le magasin effectivement lu.

        Une croyance déclarée masque le monde dans ``State.get``. Scenario et
        Autoloop doivent donc remplacer cette croyance, tandis que les autres
        valeurs appartiennent au monde. Cette règle vit ici pour que tous les
        Adapters d'exécution observent le même tick zéro.
        """
        # Un monde de départ simulé tient lieu d'observation.
        for path, value in values.items():
            if path in self.state.beliefs:
                self.state.set_belief(path, value, 1.0, source,
                                      prov=_OBSERVED)
            else:
                self.state.set_world(path, value, _OBSERVED)

    # ------------------------------------------------------------- boucle
    def run(self, max_ticks: Optional[int] = None) -> "Runtime":
        for _ in self.iter_ticks(max_ticks):
            pass
        return self

    def iter_ticks(self, max_ticks: Optional[int] = None, *, horizon: Optional[int] = None):
        """Boucle commune ; horizon borne une exécution sans remplacer LOOP MAX."""
        loop = self.agent.loop
        declared = loop.max_iter if loop else None
        limit = max_ticks if max_ticks is not None else (declared or 10)
        if horizon is not None:
            limit = min(limit, horizon)
        for _ in range(max(0, limit)):
            self.tick()
            yield self
            if loop and loop.until is not None and self._safe_test(
                    loop.until, on_error=False, context="LOOP UNTIL globale"):
                self.trace.log(self.state.tick, "INFO",
                               "condition d'arrêt UNTIL satisfaite")
                break

    def tick(self) -> None:
        self.state.tick += 1
        self.metrics["ticks"] += 1
        self.trace.log(self.state.tick, "TICK", "")
        for phase in self._cycle():
            self._run_phase(phase)

    def _cycle(self) -> List[Any]:
        if not self.agent.loop:
            return list(DEFAULT_CYCLE)
        out: List[Any] = []
        for stmt in self.agent.loop.body:
            if isinstance(stmt, ControlStmt) and stmt.kind == "PHASE":
                out.append(stmt.arg)
            else:
                out.append(stmt)
        return out or list(DEFAULT_CYCLE)

    def _run_phase(self, phase: Any) -> None:
        if not isinstance(phase, str):
            self.exec_stmts([phase])
            return
        handler = {
            "OBSERVE": self.phase_observe,
            "RECEIVE": self.phase_receive,
            "UPDATE_BELIEFS": self.phase_update_beliefs,
            "UPDATE_HYPOTHESES": self.phase_update_hypotheses,
            "SYNTHESIZE": self.phase_synthesize,
            "EVALUATE_GOALS": self.phase_evaluate_goals,
            "SELECT_PLAN": self.phase_select_plan,
            "DECIDE": self.phase_select_plan,
            "EXECUTE": self.phase_execute,
            "ACT": self.phase_execute,
            "VERIFY": self.phase_verify,
            "UPDATE_MEMORY": self.phase_update_memory,
        }.get(phase)
        if handler:
            handler()

    # -------------------------------------------------------------- phases
    def phase_receive(self) -> None:
        """v0.6 — remise des messages, avant toute perception du monde."""
        while self.inbox:
            message = self.inbox.popleft()
            self.metrics["messages_received"] += 1
            handlers = [h for h in self.agent.messages
                        if h.name == message["name"]]
            if not handlers:
                self.trace.log(self.state.tick, "MESSAGE",
                               f"{message['name']} de {message['from']} ignoré",
                               "aucun ON MESSAGE correspondant")
                continue
            payload = message.get("payload", {})
            meta = {"name": message["name"], "from": message["from"]}
            for handler in handlers:
                snapshot = self._scope_snapshot()
                self._bind_payload(payload, meta, "message")
                if not self._safe_test(
                        handler.when, on_error=False,
                        context=f"ON MESSAGE {message['name']}"):
                    # Un message refusé ne doit rien laisser derrière lui.
                    self._scope_restore(snapshot)
                    self.trace.log(self.state.tick, "MESSAGE",
                                   f"{message['name']} filtré par WHEN")
                    continue
                self.trace.log(
                    self.state.tick, "MESSAGE",
                    f"{message['name']} ← {message['from']}",
                    ", ".join(f"{k}={fmt(v)}" for k, v in payload.items()))
                # Le gestionnaire s'exécute *parce qu'un message est arrivé* :
                # tout ce qu'il décide en porte la provenance.
                with self._under(_PAYLOAD["message"]
                                 | self._label(handler.when)):
                    self.exec_stmts(handler.body)

    def _scope_snapshot(self):
        return (dict(self.state.locals), dict(self.state.untrusted),
                dict(self.state.labels))

    def _scope_restore(self, snapshot) -> None:
        self.state.locals, self.state.untrusted, self.state.labels = snapshot

    def _bind_payload(self, payload: Dict[str, Any], meta: Dict[str, Any],
                      kind: str) -> None:
        """Lie une charge utile pour le **reste du tick**.

        Un `THEN <plan>` met le plan en file ; celui-ci ne s'exécute qu'en
        phase EXECUTE. Restaurer la portée à la sortie du gestionnaire
        rendrait la charge utile invisible au plan qu'elle a déclenché.

        **Deux espaces, et c'est la correction de sûreté de la v1.6.** Les
        formes préfixées (`payload.x`, `event.source`, `message.from`) vont
        dans les locales : leur provenance est lisible dans le programme, il
        n'y a pas d'ambiguïté. Les noms **nus** vont dans `state.untrusted`,
        consulté en dernier — après le monde, les croyances et la mémoire.

        Auparavant ils étaient liés parmi les locales, prioritaires sur tout
        le reste : un événement portant une clé `asset` masquait
        l'observation `asset.criticality`, et une charge utile fournie par
        l'hôte pouvait ainsi **désactiver un `NEVER`** — une donnée non fiable
        décidait d'une question de sécurité. Reléguée en dernier, elle ne
        comble plus que ce que rien d'autre ne renseigne.
        """
        label = _PAYLOAD[kind]
        self.state.set_local("payload", dict(payload), label)
        # L'enveloppe — émetteur, source — est posée par le runtime ou la
        # société, pas par l'émetteur : elle ne vaut pas le contenu.
        self.state.set_local(kind, dict(meta), _RUNTIME)
        shadowed = []
        for key, value in payload.items():
            if self.state.get(key) is not UNDEFINED:
                # On le dit : une clé de charge utile qui porte le nom d'un
                # fait connu est soit une méprise, soit une tentative. Muet,
                # le comportement sûr serait indistinguable d'un bug.
                shadowed.append(key)
            self.state.set_untrusted(key, value, label)
        if shadowed:
            self.trace.log(
                self.state.tick, "BLOCKED",
                f"charge utile {kind} ignorée pour "
                f"{', '.join(sorted(shadowed))}",
                "un fait observé ou cru porte déjà ce nom : la donnée reçue "
                "ne le masque pas (lisible en payload.<clé>)")

    def phase_observe(self) -> None:
        ev = Evaluator(self.state)
        for obs in self.agent.observers:
            if obs.when is not None and not self._safe_test(
                    obs.when, on_error=False,
                    context=f"OBSERVE {obs.path}", evaluator=ev):
                continue
            value, label = unwrap(self._read(obs.path), _OBSERVED)
            if value is None:
                self._sensor_unknown(obs)
                continue
            self.state.set_world(f"sensors.{obs.path}.available", True,
                                 _RUNTIME)
            self._sensor_escalated.discard(obs.path)
            self.state.set_world(obs.path, value, label)
            self.trace.log(self.state.tick, "OBSERVE",
                           f"{obs.path} = {fmt(value)}")

    def _sensor_unknown(self, obs) -> None:
        """Capteur muet — conduite déclarée par `ON UNKNOWN` (v1.8, SPEC §31).

        Le silence d'un capteur n'était pas un événement : le chemin restait
        indéfini, les gardes échouaient fermé — la bonne direction — et
        l'agent se bloquait sans qu'aucune ligne de trace ne dise pourquoi.
        Une panne de capteur et un programme mal écrit produisaient le même
        silence. C'est la tension sûreté/continuité du §31 : elle ne se
        résout pas, elle se déclare.

        Trois conduites, toutes fail-closed par défaut :

        * rien de déclaré — comportement historique, plus une ligne de trace
          et `sensors.<chemin>.available = false`, lisible par une garde ;
        * `ESCALATE` — la panne devient une escalade nommée ;
        * `DEGRADE <valeur>` — le repli **déclaré** est substitué, tracé, et
          posé avec une confiance basse et la source `fallback`, pour qu'une
          garde puisse distinguer une mesure d'une hypothèse.
        """
        self.metrics["sensor_unknown"] += 1
        self.state.set_world(f"sensors.{obs.path}.available", False, _RUNTIME)

        if obs.on_unknown == "DEGRADE":
            value = Evaluator(self.state).eval(obs.fallback)
            label = Prov({P.FALLBACK}) | self._label(obs.fallback)
            self.metrics["sensor_degraded"] += 1
            self.state.set_world(obs.path, value, label)
            self.state.set_belief(obs.path, value, 0.3, "fallback",
                                  self.state.tick, label)
            self.trace.log(self.state.tick, "OBSERVE",
                           f"{obs.path} : capteur muet → repli {fmt(value)}",
                           "valeur déclarée, non mesurée — confiance 0.3, "
                           "source `fallback`")
            return

        if obs.on_unknown == "ESCALATE":
            self.trace.log(self.state.tick, "ASK",
                           f"capteur muet : {obs.path}",
                           "ON UNKNOWN ESCALATE — aucune valeur de repli "
                           "déclarée, l'humain tranche")
            # Une seule fois par panne : un capteur mort réveillerait sinon
            # l'astreinte à chaque tick, et une alerte répétée n'est plus lue.
            if obs.path not in self._sensor_escalated:
                self._sensor_escalated.add(obs.path)
                self._ask(f"capteur muet : {obs.path}",
                          "ON UNKNOWN ESCALATE")
            return

        self.trace.log(self.state.tick, "OBSERVE",
                       f"{obs.path} : capteur muet, chemin indéfini",
                       "aucun ON UNKNOWN déclaré — toute garde portant sur "
                       "ce chemin échoue fermé (W131)")

    def phase_update_beliefs(self) -> None:
        """b_{t+1} = Update(b_t, o_t) — les observations écrasent les croyances
        de même chemin, avec une confiance issue du capteur."""
        for obs in self.agent.observers:
            value = self.state.world.get(obs.path, UNDEFINED)
            if value is UNDEFINED:
                continue
            if obs.path in self.state.beliefs:
                old = self.state.beliefs[obs.path]
                if old.value != value:
                    self.trace.log(self.state.tick, "BELIEF",
                                   f"{obs.path}: {fmt(old.value)} → {fmt(value)}")
                self._audit_effect(obs.path, old, value)
            self.state.set_belief(obs.path, value, 0.95, "observation",
                                  self.state.tick,
                                  self.state.label_at(label_key("W", obs.path)))

    def _audit_effect(self, path: str, previous, observed: Any) -> None:
        """Confronte le modèle d'effets au monde (v1.2, conséquences en v1.7).

        Le planificateur raisonne sur des `EFFECT` déclarés que rien ne
        vérifiait. Un `EFFECT` faux corrompt silencieusement tous les plans.
        Ici, chaque fois qu'une perception succède à une croyance posée par
        un effet, on compare — et l'on tient le compte.

        Depuis la v1.7 le compte **a des conséquences** : il fixe la confiance
        des postconditions suivantes (`_effect_confidence`) et se dépose en
        mémoire longue s'il y est déclaré (`_publish_drift`). Un registre qui
        ne servait qu'à journaliser laissait le modèle démenti agir avec
        exactement le même poids que le modèle confirmé.
        """
        source = getattr(previous, "source", "")
        if not source.startswith("effect:"):
            return
        tool = source.split(":", 1)[1]
        ledger = self.drift.setdefault(f"{tool}→{path}",
                                       {"confirmé": 0, "démenti": 0})
        if previous.value == observed:
            ledger["confirmé"] += 1
            self._publish_drift()
            return
        ledger["démenti"] += 1
        self._publish_drift()
        self.metrics["effect_drift"] += 1
        total = ledger["confirmé"] + ledger["démenti"]
        self.trace.log(
            self.state.tick, "ERROR",
            f"modèle d'effet démenti : {tool}() prédisait "
            f"{path} = {fmt(previous.value)}",
            f"observé {fmt(observed)} — {ledger['démenti']}/{total} démentis")

    #: Clé de mémoire longue où se dépose le registre de dérive. Le registre
    #: n'a de valeur que s'il survit à l'exécution : une dérive se manifeste
    #: sur des dizaines de cycles, et un processus qui repart de zéro repart
    #: **crédule**. Passer par `MEMORY { LONG_TERM { effect_drift } }` plutôt
    #: que par un fichier annexe : c'est de la mémoire opérationnelle comme
    #: les incidents, elle se lit dans le programme et suit la persistance
    #: déjà en place. Non déclarée, la clé n'est pas écrite — on n'invente pas
    #: de la mémoire dans le dos de l'auteur.
    DRIFT_KEY = "effect_drift"

    #: Confiance d'une postcondition qu'aucune perception n'a encore jugée.
    #: Inférieure à celle d'une observation (0.95) : le modèle utilisé pour
    #: planifier et celui utilisé pour exécuter ne divergent pas, mais une
    #: perception prime toujours sur une promesse.
    EFFECT_CONFIDENCE = 0.80

    def _effect_confidence(self, tool: str, path: str) -> float:
        """Confiance d'un `EFFECT`, **empirique** dès qu'il a été jugé (v1.7).

        Jusqu'ici : 0.80, fixe et à jamais. Un effet démenti trois fois sur
        trois reposait sa croyance avec exactement le poids d'un effet
        toujours confirmé — le registre de dérive comptait dans le vide, et le
        modèle démenti continuait d'alimenter les gardes, les `VERIFY` et le
        planificateur.

        Lissage de Jeffreys $(k + 1/2)/(n + 1)$ sur le registre — le geste
        déjà fait par `PRIOR FROM` pour l'a priori. Un historique unanime ne
        produit ni 0 ni 1 : deux confirmations ne valent pas une certitude, et
        deux démentis n'interdisent pas à l'effet de se produire. Sans
        historique, on retombe sur la valeur déclarée plutôt que sur le 0.5 de
        Jeffreys : l'absence de jugement n'est pas un jugement neutre.

        Ce n'est pas une correction automatique du modèle — l'`EFFECT` reste
        celui que l'auteur a écrit, et le planificateur continue de raisonner
        dessus. C'est sa **crédibilité** qui devient mesurée, et visible par
        `CONFIDENCE(chemin)`.
        """
        ledger = self.drift.get(f"{tool}→{path}")
        if not ledger:
            return self.EFFECT_CONFIDENCE
        confirmed = ledger.get("confirmé", 0)
        total = confirmed + ledger.get("démenti", 0)
        if total == 0:
            return self.EFFECT_CONFIDENCE
        return (confirmed + 0.5) / (total + 1)

    def seed_memory(self, bucket: str, key: str, records: List[Any]) -> None:
        """Injecte une mémoire venue d'une exécution précédente.

        Rien dans le langage ne persiste la mémoire : c'est l'hôte qui décide
        où elle vit. Il faut donc une couture, sinon `LONG_TERM.effect_drift`
        est une clé que seul le runtime sait écrire et que personne ne sait
        lui rendre. Recharger le registre ici plutôt que d'attendre le
        prochain démarrage : la mémoire arrive après le `_bootstrap`.
        """
        self.state.memory.setdefault(bucket, {})[key] = list(records)
        # Rendue par l'hôte depuis un stockage : ce qu'elle contient a pu être
        # modifié hors de toute exécution.
        self.state.label_memory(bucket, key, Prov({P.MEMORY}))
        if bucket == "LONG_TERM" and key == self.DRIFT_KEY:
            self.drift = {}
            self._load_drift()

    def _load_drift(self) -> None:
        """Recharge le registre depuis la mémoire longue, si elle en porte un.

        Les enregistrements viennent d'une exécution précédente, donc d'un
        état que l'hôte a conservé : on lit défensivement, un registre illisible
        vaut registre vide. Repartir crédule est un défaut ; refuser de
        démarrer parce qu'une mémoire est abîmée en serait un pire.
        """
        records = self.state.memory.get("LONG_TERM", {}).get(self.DRIFT_KEY)
        if not isinstance(records, list):
            return
        for record in records:
            if not isinstance(record, dict):
                continue
            tool, path = record.get("tool"), record.get("path")
            if not tool or not path:
                continue
            self.drift[f"{tool}→{path}"] = {
                "confirmé": int(record.get("confirmé", 0) or 0),
                "démenti": int(record.get("démenti", 0) or 0),
            }
        if self.drift:
            self.trace.log(0, "MEMORY",
                           f"registre de dérive rechargé : {len(self.drift)} "
                           f"couple(s) outil→chemin")

    def _publish_drift(self) -> None:
        """Dépose le registre en mémoire longue — un état, pas un journal.

        On réécrit la liste au lieu d'y ajouter : le registre est un **compte
        courant**, pas une suite d'événements. Empiler produirait une mémoire
        qui grossit à chaque perception et dont seule la dernière ligne
        compte.
        """
        if self.DRIFT_KEY not in self.agent.memory.long_term:
            return
        bucket = self.state.memory.setdefault("LONG_TERM", {})
        self.state.label_memory("LONG_TERM", self.DRIFT_KEY, _RUNTIME)
        bucket[self.DRIFT_KEY] = [
            {"tool": couple.split("→", 1)[0], "path": couple.split("→", 1)[1],
             "confirmé": ledger["confirmé"], "démenti": ledger["démenti"]}
            for couple, ledger in sorted(self.drift.items())
        ]

    def phase_update_hypotheses(self) -> None:
        """v0.4 — la confiance cesse d'être déclarée et devient calculée."""
        from .bayes import infer, publish

        best: Optional[Any] = None
        for hypothesis in self.agent.hypotheses:
            inference = infer(hypothesis, self.state)
            evidence = P.join(_INFERRED, *[self._label(item.test)
                                           for item in hypothesis.evidence])
            publish(inference, self.state, evidence)
            self.inferences[hypothesis.name] = inference
            self.metrics["inferences"] += 1
            self.trace.log(self.state.tick, "BAYES", inference.render(),
                           inference.explain())
            if best is None or inference.posterior > best.posterior:
                best = inference
            if inference.supported and hypothesis.explains_path:
                value = (Evaluator(self.state).eval(hypothesis.explains_value)
                         if hypothesis.explains_value is not None
                         else Symbol(hypothesis.name))
                self.state.set_belief(hypothesis.explains_path, value,
                                      inference.posterior,
                                      f"hypothesis:{hypothesis.name}",
                                      self.state.tick, evidence)
                self.trace.log(self.state.tick, "BELIEF",
                               f"{hypothesis.explains_path} = {fmt(value)}",
                               f"c={inference.posterior:.3f} (dérivée)")
        if best is not None:
            self.state.set_world("hypotheses.best", Symbol(best.name),
                                 _INFERRED)
            self.state.set_world("hypotheses.best.posterior", best.posterior,
                                 _INFERRED)

    def phase_synthesize(self) -> None:
        """v0.5 — synthétise un plan quand aucun plan déclaré ne s'applique."""
        if self.planner is None or self.plan_queue:
            return
        self.state.set_world("planner.exhausted", False, _RUNTIME)
        result = self.planner.synthesize(self.state)
        for note in result.pruned_by_policy:
            self.trace.log(self.state.tick, "PLANNER",
                           "action écartée à la planification", note)
        if not result.found:
            self.state.set_world("planner.exhausted",
                                 "atteints" not in result.reason, _RUNTIME)
            self.trace.log(self.state.tick, "PLANNER", result.render())
            return
        self.state.set_world("planner.exhausted", False, _RUNTIME)
        name = f"__synth_t{self.state.tick}"
        self._synth[name] = self.planner.as_plan(result, name)
        self.metrics["plans_synthesized"] += 1
        self.trace.log(self.state.tick, "PLANNER", result.render(), "synthétisé")
        self._enqueue(name, "synthèse")

    def phase_evaluate_goals(self) -> None:
        ev = Evaluator(self.state)
        scores = []
        for goal in self.agent.goals:
            parts = []
            if goal.condition is not None:
                parts.append(1.0 if self._safe_test(
                    goal.condition, on_error=False,
                    context=f"GOAL {goal.name}", evaluator=ev) else 0.0)
            for target in goal.targets:
                parts.append(1.0 if self._safe_test(
                    target, on_error=False,
                    context=f"TARGET de {goal.name}", evaluator=ev) else 0.0)
            score = sum(parts) / len(parts) if parts else 1.0
            label = P.join(_RUNTIME, self._label(goal.condition, ev),
                           *[self._label(t, ev) for t in goal.targets])
            self.state.set_world(f"goals.{goal.name}.score", score, label)
            scores.append((goal, score, label))
            self.trace.log(self.state.tick, "GOAL",
                           f"{goal.name} score={score:.2f}",
                           goal.mode.lower())
        total_w = sum(g.weight for g, _, _ in scores) or 1.0
        overall = (sum(s * g.weight for g, s, _ in scores) / total_w
                   if scores else 1.0)
        label = P.join(_RUNTIME, *[lab for _, _, lab in scores])
        self.state.set_world("goal.score", overall, label)
        self.state.set_world("goal.satisfied", overall >= 1.0, label)

    def phase_select_plan(self) -> None:
        # 1. Événements
        for event in self._drain_events():
            self._handle_event(event)
        # 2. Gardes de plan
        ev = Evaluator(self.state)
        for plan in self.agent.plans:
            if plan.when is not None and self._safe_test(
                    plan.when, on_error=False,
                    context=f"garde WHEN de {plan.name}", evaluator=ev):
                self._enqueue(plan.name, "garde WHEN",
                              self._label(plan.when, ev))
        # 3. Règles DECIDE
        decide = self.agent.decide
        if decide:
            for rule in decide.rules:
                if self._safe_test(rule.cond, on_error=False,
                                   context="règle DECIDE"):
                    with self._under(self._label(rule.cond)):
                        self.exec_stmts(rule.then)
        # 4. Synthèse (v0.5) — le déterministe avant le probabiliste
        self.phase_synthesize()
        # 5. Raisonnement LLM, en dernier recours seulement
        if decide:
            if not self.plan_queue and decide.reason is not None:
                self._run_reason(decide.reason)
                candidates = [p.name for p in self.agent.plans]
                self.metrics["llm_calls"] += 1
                try:
                    choice = self.llm.select_plan(self.llm_context(), candidates)
                except KernelAbort:
                    raise
                except Exception as exc:              # noqa: BLE001
                    choice = None
                    self.trace.log(self.state.tick, "ERROR",
                                   "sélection de plan par le LLM en échec",
                                   f"{type(exc).__name__}: {exc} — aucun plan")
                if choice in candidates:
                    self.trace.log(self.state.tick, "LLM",
                                   f"plan proposé : {choice}", "validé")
                    # Le modèle a choisi : tout ce que le plan fera en dépend.
                    self._enqueue(choice, "proposition LLM", _LLM)
                elif choice is not None:
                    self.trace.log(self.state.tick, "BLOCKED",
                                   f"plan inconnu proposé par le LLM : {choice}")

    def phase_execute(self) -> None:
        while self.plan_queue:
            name = self.plan_queue.popleft()
            plan = self.plan_named(name)
            label = self._plan_labels.pop(name, _RUNTIME)
            if plan is None:
                self.trace.log(self.state.tick, "ERROR", f"plan inconnu : {name}")
                continue
            with self._under(label):
                self.run_plan(plan)

    def phase_verify(self) -> None:
        """Vérification globale : réévalue les objectifs après action."""
        self.phase_evaluate_goals()

    def phase_update_memory(self) -> None:
        ev = Evaluator(self.state)
        for write in self.agent.memory.writes:
            if not self._safe_test(write.when, on_error=False,
                                   context="garde d'écriture MEMORY",
                                   evaluator=ev):
                continue
            record, labels = {}, [self._label(write.when, ev)]
            for path in write.store:
                record[path], label = self.state.get_labeled(path)
                labels.append(label)
            bucket = self.state.memory.setdefault(write.into, {})
            records = bucket.setdefault(write.key, [])
            if not isinstance(records, list):
                records = bucket[write.key] = []
            if records and records[-1] == record:
                continue      # mémoire opérationnelle : pas de doublon successif
            records.append(record)
            if write.into != "SHARED":
                # La suite d'enregistrements porte l'union de tout ce qui y
                # est entré : une mémoire ne blanchit pas ce qu'elle retient.
                previous = self.state.label_at(
                    label_key(f"M:{write.into}", write.key))
                self.state.label_memory(
                    write.into, write.key,
                    P.join(*labels) if len(records) == 1
                    else P.join(previous, *labels))
            self.metrics["memory_writes"] += 1
            rendered = ", ".join(f"{k}={fmt(v)}" for k, v in record.items())
            if write.into == "SHARED":
                self._write_shared(bucket, write.key, rendered)
            else:
                self.trace.log(self.state.tick, "MEMORY",
                               f"{write.target} ← {rendered}")

    def _write_shared(self, bucket: Dict[str, Any], key: str,
                      rendered: str) -> None:
        """Écriture partagée, versionnée **par clé** (v1.2).

        En v0.6 la version était globale au compartiment : deux agents
        écrivant des faits sans rapport se déclaraient mutuellement en
        conflit. La granularité de détection doit être celle de la donnée,
        sinon le signal se noie dans le bruit dès trois agents.
        """
        versions = bucket.setdefault("__versions", {})
        version = versions.get(key, 0) + 1
        seen = self._seen_shared.get(key, 0)
        if seen not in (0, version - 1):
            self.metrics["shared_conflicts"] += 1
            self.trace.log(self.state.tick, "SHARED",
                           f"écriture concurrente sur {key}",
                           f"vue v{seen}, réelle v{version - 1} — "
                           f"dernier écrivain l'emporte")
        versions[key] = version
        self._seen_shared[key] = version
        self.metrics["shared_writes"] += 1
        self.state.set_world(f"shared.{key}.version", version, _RUNTIME)
        self.trace.log(self.state.tick, "SHARED",
                       f"{key} v{version} ← {rendered}")

    # -------------------------------------------------------------- événements
    def _handle_event(self, event: Dict[str, Any]) -> None:
        source = event["source"]
        payload = event.get("payload", {})
        for handler in self.agent.events:
            if handler.source != source:
                continue
            snapshot = self._scope_snapshot()
            self._bind_payload(payload, {"source": source}, "event")
            if self._safe_test(handler.when, on_error=False,
                               context=f"ON {source}"):
                self.trace.log(self.state.tick, "EVENT",
                               f"{source} déclenché",
                               ", ".join(f"{k}={fmt(v)}"
                                         for k, v in payload.items()))
                with self._under(_PAYLOAD["event"]
                                 | self._label(handler.when)):
                    self.exec_stmts(handler.body)
            else:
                self._scope_restore(snapshot)
                self.trace.log(self.state.tick, "EVENT",
                               f"{source} filtré par WHEN")

    def _enqueue(self, name: str, why: str,
                 label: Optional[Prov] = None) -> None:
        # La décision qui met un plan en file est son contexte de contrôle.
        # Mis en file deux fois, il dépend des deux : les étiquettes s'unissent.
        decided = (label if label is not None else _RUNTIME) | self._pc()
        if name in self.plan_queue:
            self._plan_labels[name] = self._plan_labels.get(name, P.NONE) \
                | decided
            return
        if self.plan_named(name) is None:
            self.trace.log(self.state.tick, "ERROR",
                           f"plan non déclaré : {name}")
            return
        self.plan_queue.append(name)
        self._plan_labels[name] = decided
        self.trace.log(self.state.tick, "PLAN", f"{name} mis en file", why)

    # ------------------------------------------------------------ exécution
    def run_plan(self, plan: Plan) -> bool:
        attempt, retries_allowed = 0, 0
        while True:
            self.verify_failure = None
            self.trace.log(self.state.tick, "PLAN",
                           f"exécution de {plan.name}"
                           + (f" (tentative {attempt + 1})" if attempt else ""))
            for step in plan.steps:
                self.trace.log(self.state.tick, "STEP", f"STEP {step.name}")
                self.exec_stmts(step.body)
                if self.verify_failure is not None:
                    break
            if self.verify_failure is None:
                return True
            handler = self.verify_failure.on_fail or self.agent.on_verify_fail
            retries_allowed = max(retries_allowed, _retry_count(handler))
            if attempt < retries_allowed:
                attempt += 1
                self.trace.log(self.state.tick, "RETRY",
                               f"{plan.name} : nouvelle tentative "
                               f"{attempt}/{retries_allowed}")
                continue
            self.state.set_local("still_failed", True, _RUNTIME)
            failed = self.verify_failure
            self.verify_failure = None
            self.exec_stmts([s for s in handler
                             if not (isinstance(s, ControlStmt) and s.kind == "RETRY")])
            self.verify_failure = failed
            return False

    def exec_stmts(self, stmts: List[Stmt]) -> None:
        for stmt in stmts:
            if self.verify_failure is not None:
                return
            self.exec_stmt(stmt)

    def _project(self, var: str, item: Any,
                 label: Optional[Prov] = None) -> List[str]:
        """Projette un élément de collection en locales scalaires.

        Une valeur composite (dict imbriqué, liste) n'entre jamais dans
        l'état : seuls les scalaires sont liés, une liste ne donne que sa
        taille sous `<var>.<champ>.count`. Ce que le programme ne peut pas
        comparer, il ne peut pas non plus le lire par accident.
        """
        bound: List[str] = []

        def put(path: str, value: Any) -> None:
            # Chaque champ projeté hérite de la collection dont il sort.
            self.state.set_local(path, value, label)
            bound.append(path)

        if isinstance(item, dict):
            for key, value in item.items():
                path = f"{var}.{key}"
                if isinstance(value, str):
                    put(path, Symbol(value))
                elif isinstance(value, (int, float, bool)) or value is None:
                    put(path, value)
                elif isinstance(value, list):
                    put(f"{path}.count", len(value))
                elif isinstance(value, dict):
                    for sub, subvalue in value.items():
                        if isinstance(subvalue, (str, int, float, bool)):
                            put(f"{path}.{sub}",
                                Symbol(subvalue) if isinstance(subvalue, str) else subvalue)
                        elif not isinstance(subvalue, (list, tuple, dict)):
                            put(f"{path}.{sub}", subvalue)
                else:
                    # Tout scalaire non reconnu — au premier chef un `Symbol`
                    # produit par l'hôte — doit être lié tel quel. L'ignorer
                    # rendait le champ indéfini, donc toute comparaison fausse,
                    # et l'élément filtré sans le moindre diagnostic : le pire
                    # des comportements pour un langage qui prétend qu'un
                    # défaut doit se voir.
                    put(path, value)
        elif isinstance(item, str):
            put(var, Symbol(item))
        else:
            put(var, item)
        return bound

    def _exec_foreach(self, stmt: ForEachStmt) -> None:
        try:
            source = Evaluator(self.state).eval(stmt.source)
            source_label = self._label(stmt.source) | self._pc()
        except Exception as exc:                      # noqa: BLE001
            self.trace.log(self.state.tick, "ERROR",
                           "FOREACH : source inévaluable",
                           f"{type(exc).__name__}: {exc}")
            return None
        if isinstance(source, dict):
            source = list(source.values())
        if not isinstance(source, (list, tuple)):
            # Indéterminé n'est pas vide : on le dit, on ne l'ignore pas. Et
            # un diagnostic qui nomme le défaut sans nommer les issues oblige
            # à deviner : on énumère donc les collections réellement liées.
            # Les deux espaces : une collection rapportée par un outil ou par
            # une charge utile est liée sous son nom nu dans l'espace non
            # fiable (§7.3). L'omettre ici ferait mentir le diagnostic.
            visible = {**self.state.locals,
                       **(getattr(self.state, "untrusted", None) or {})}
            available = sorted(k for k, v in visible.items()
                               if isinstance(v, (list, tuple)))
            detail = (f"collections disponibles : {', '.join(available)}"
                      if available else "aucune collection liée à cet instant")
            self.trace.log(self.state.tick, "ERROR",
                           f"FOREACH : {_render_expr(stmt.source)} "
                           f"n'est pas une collection", detail)
            return None
        bound_max = stmt.max_iter if isinstance(stmt.max_iter, int) \
            and stmt.max_iter > 0 else 200
        total = len(source)
        if total > bound_max:
            self.trace.log(self.state.tick, "INFO",
                           f"FOREACH tronqué : {total} éléments > MAX {bound_max}")
        self.trace.log(self.state.tick, "INFO",
                       f"FOREACH {stmt.var} sur {min(total, bound_max)} élément(s)")
        for index, item in enumerate(source[:bound_max]):
            bound = self._project(stmt.var, item, source_label)
            self.state.set_local(f"{stmt.var}.index", index, source_label)
            bound.append(f"{stmt.var}.index")
            try:
                # Le nombre de tours et chaque élément viennent de la source.
                with self._under(source_label):
                    self.exec_stmts(stmt.body)
            finally:
                # Décision 7 : une liaison qui n'a plus cours ne laisse rien
                # derrière elle. Sans cela l'élément n+1 hériterait des
                # champs absents de l'élément n.
                for path in bound:
                    self.state.locals.pop(path, None)
            if self.verify_failure is not None:
                break
        return None

    def exec_stmt(self, stmt: Stmt) -> Any:
        if isinstance(stmt, CallStmt):
            try:
                args = self._eval_args(stmt.call)
                labels = self._label_args(stmt.call)
            except Exception as exc:                  # noqa: BLE001
                # Un argument inévaluable ne doit pas crasher la boucle : on
                # bloque l'appel (fail-closed), on ne l'exécute pas à moitié.
                self.metrics["blocked"] += 1
                self.trace.log(self.state.tick, "BLOCKED",
                               f"{stmt.call.name}() : arguments inévaluables",
                               f"{type(exc).__name__}: {exc}")
                return None
            return self.call_tool(stmt.call.name, args, origin="plan",
                                  provenance=labels)
        if isinstance(stmt, SetStmt):
            try:
                ev = Evaluator(self.state)
                value = ev.eval(stmt.value)
                label = ev.label(stmt.value) | self._pc()
            except Exception as exc:                  # noqa: BLE001
                # Une expression inévaluable n'écrit rien et n'interrompt pas
                # la boucle : l'affectation échoue, la trace le dit, et la
                # suite du plan s'exécutera sur un état inchangé — jamais sur
                # une valeur à moitié calculée.
                self.trace.log(self.state.tick, "ERROR",
                               f"SET {stmt.target} : expression inévaluable",
                               f"{type(exc).__name__}: {exc}")
                return None
            self.state.assign(stmt.target, value, label)
            self.trace.log(self.state.tick, "INFO",
                           f"SET {stmt.target} = {fmt(value)}")
            return value
        if isinstance(stmt, IfStmt):
            taken = self._safe_test(stmt.cond, on_error=False, context="IF")
            # Les deux branches dépendent de la condition : celle qu'on ne
            # prend pas aussi, par ce qu'elle n'a pas écrit.
            with self._under(self._label(stmt.cond)):
                self.exec_stmts(stmt.then if taken else stmt.otherwise)
            return None
        if isinstance(stmt, VerifyStmt):
            return self._exec_verify(stmt)
        if isinstance(stmt, JudgeStmt):
            return self._run_judge(stmt)
        if isinstance(stmt, ReasonStmt):
            return self._run_reason(stmt)
        if isinstance(stmt, AskStmt):
            return self._exec_ask(stmt)
        if isinstance(stmt, DelegateStmt):
            return self._exec_delegate(stmt)
        if isinstance(stmt, MessageStmt):
            return self._exec_message(stmt)
        if isinstance(stmt, ThenPlan):
            self._enqueue(stmt.plan, "invocation directe")
            return None
        if isinstance(stmt, ForEachStmt):
            return self._exec_foreach(stmt)
        if isinstance(stmt, LoopStmt):
            # MAX doit rester fini : un AST malformé (None, négatif) retombe sur
            # une borne par défaut plutôt que de crasher (range(None)) ou de
            # boucler sans fin.
            bound = stmt.max_iter if isinstance(stmt.max_iter, int) \
                and stmt.max_iter > 0 else 100
            with self._under(self._label(stmt.until)
                             if stmt.until is not None else P.NONE):
                for _ in range(bound):
                    self.exec_stmts(stmt.body)
                    if stmt.until is not None and self._safe_test(
                            stmt.until, on_error=False, context="LOOP UNTIL"):
                        break
                    if self.verify_failure is not None:
                        break
            return None
        if isinstance(stmt, ControlStmt):
            if stmt.kind == "ESCALATE":
                self.trace.log(self.state.tick, "ASK", "escalade opérateur")
                self._ask("Escalade requise", "ESCALATE")
            elif stmt.kind == "ROLLBACK":
                self.trace.log(self.state.tick, "INFO", "rollback demandé")
            elif stmt.kind == "PHASE":
                self._run_phase(stmt.arg)
            return None
        self.trace.log(self.state.tick, "ERROR",
                       f"instruction non supportée : {type(stmt).__name__}")
        return None

    # ------------------------------------------------------------ vérification
    def _exec_verify(self, stmt: VerifyStmt) -> bool:
        self._refresh_for(stmt.cond)
        # Régime strict : une vérification ne réussit pas par ignorance. Un
        # `VERIFY { incident.resolved != open }` passait tant que le chemin
        # n'existait pas — l'absence de preuve valait preuve.
        ev = Evaluator(self.state, strict_undefined=True)
        ok = self._safe_test(stmt.cond, on_error=False,
                             context="VERIFY", evaluator=ev)
        label = stmt.label or _render_expr(stmt.cond)
        if ok:
            self.metrics["verify_pass"] += 1
            self.trace.log(self.state.tick, "VERIFY_OK", label)
            return True
        self.metrics["verify_fail"] += 1
        detail = "; ".join(ev.notes) if ev.notes else ""
        self.trace.log(self.state.tick, "VERIFY_FAIL", label, detail)
        self.verify_failure = stmt
        return False

    # ------------------------------------------------------------ raisonnement
    def _refresh_for(self, cond) -> None:
        """Re-perçoit les chemins observés que la condition met en jeu.

        `VERIFY` doit statuer sur s_{t+1}, c'est-à-dire sur le monde *après*
        l'action, et non sur les croyances héritées du début du tick.

        Un capteur muet **pendant cette relecture** est le cas dangereux : on
        vient d'agir, on cherche à savoir si l'action a mordu, et la seule
        valeur disponible est celle d'*avant* l'action. La conserver ferait
        passer un `VERIFY` sur la croyance que l'action prétendait changer —
        un échec d'outil déguisé en succès. Le chemin est donc invalidé : il
        redevient indéfini, et le régime strict de `VERIFY` échoue fermé.
        """
        from .analyzer import _collect_paths

        wanted: set = set()
        _collect_paths(cond, wanted)
        for obs in self.agent.observers:
            if obs.path not in wanted:
                continue
            value, label = unwrap(self._read(obs.path), _OBSERVED)
            if value is None:
                self.metrics["sensor_unknown"] += 1
                self.state.set_world(f"sensors.{obs.path}.available", False,
                                     _RUNTIME)
                self.state.invalidate(obs.path)
                detail = ("la croyance d'avant l'action est écartée — la "
                          "vérification ne statuera pas sur une mesure "
                          "périmée")
                if obs.on_unknown == "DEGRADE":
                    # Le repli déclaré vaut pour *percevoir* faute de mieux,
                    # pas pour *constater* qu'une action a mordu : vérifier
                    # une action contre une valeur qu'on a soi-même posée
                    # revient à se donner raison.
                    detail += (" ; le repli ON UNKNOWN DEGRADE ne s'applique "
                               "pas à une revérification")
                self.trace.log(
                    self.state.tick, "OBSERVE",
                    f"{obs.path} : capteur muet à la revérification", detail)
                continue
            self.state.set_world(f"sensors.{obs.path}.available", True,
                                 _RUNTIME)
            self.state.set_world(obs.path, value, label)
            previous = self.state.beliefs.get(obs.path)
            if previous is None or previous.value != value:
                self.trace.log(self.state.tick, "BELIEF",
                               f"{obs.path} → {fmt(value)}", "revérification")
            # C'est ici que la dérive est la plus visible : on vient d'agir,
            # et l'on reperçoit exactement les chemins que l'action prétendait
            # modifier.
            if previous is not None:
                self._audit_effect(obs.path, previous, value)
            self.state.set_belief(obs.path, value, 0.95, "observation",
                                  self.state.tick, label)

    def _reason_schema(self, stmt: ReasonStmt) -> Dict[str, str]:
        """Le schéma transmis au modèle, domaines et DEFAULT compris.

        Un domaine clos que le LLM ne connaît pas ne borne rien — il garantit
        seulement un écrêtage vers la valeur de repli, donc une réponse
        perdue. La coercition et l'écrêtage, eux, restent côté runtime.
        """
        schema = {}
        for key, typ in stmt.produce.items():
            rendered = (f"{typ} IN {stmt.domains[key].render()}"
                        if key in stmt.domains else typ)
            if key in stmt.defaults:
                default = Evaluator(self.state).eval(stmt.defaults[key])
                import json
                literal = json.dumps(default) if isinstance(default, str) and not isinstance(default, Symbol) else str(default).lower() if isinstance(default, bool) else str(default)
                rendered += f" DEFAULT {literal}"
            schema[key] = rendered
        return schema

    def _run_reason(self, stmt: ReasonStmt) -> Dict[str, Any]:
        self.metrics["llm_calls"] += 1
        context = self.llm_context(stmt.using)
        schema = self._reason_schema(stmt)
        # Le LLM est un oracle faillible (timeout, 429, JSON illisible). S'il
        # lève, le runtime impose malgré tout le schéma : valeurs par défaut
        # typées, neutres. Aucune sortie non contrainte ne fuite, aucun crash.
        # L'oracle est prié de dire s'il a répondu. `_coerce` comble les
        # champs absents par leur défaut — comportement voulu — mais cela
        # rendait une réponse **tronquée** indiscernable d'une réponse
        # complète et neutre : c'est le piège du schéma trop long, où dix
        # champs dépassent le budget de sortie et retombent tous à zéro sans
        # que rien ne le signale. On efface d'abord pour ne pas relire le
        # verdict de l'appel précédent.
        try:
            self.llm.last_reason_missing = None
        except Exception:                             # noqa: BLE001
            pass                                      # adaptateur tiers
        missing: Optional[List[str]] = None
        try:
            produced = self.llm.reason(stmt.task, context, schema)
        except KernelAbort:
            raise
        except Exception as exc:                      # noqa: BLE001
            produced = {}
            missing = list(schema)
            self.trace.log(self.state.tick, "ERROR",
                           f"REASON « {stmt.task} » a échoué",
                           f"{type(exc).__name__}: {exc} — "
                           "DEFAULT explicites appliqués")
        else:
            missing = getattr(self.llm, "last_reason_missing", None)
        if not isinstance(produced, dict):
            produced = {}
            missing = list(schema)
        if missing is None:
            # Adaptateur tiers sans protocole `last_reason_missing` : la
            # forme de la réponse suffit au moins à identifier les absences.
            missing = [key for key in schema if key not in produced]
        # La frontière est imposée ici, pas seulement dans les adaptateurs
        # livrés. Un adaptateur tiers peut rendre des types bruts, NaN, ou des
        # clés qui n'existent pas dans PRODUCE : aucun de ces éléments ne doit
        # entrer dans l'état vivant.
        from .llm import _coerce
        produced = _coerce(
            {key: produced[key] for key in schema if key in produced},
            schema,
        )
        for key, node in stmt.defaults.items():
            if key not in produced or key in missing:
                produced[key] = Evaluator(self.state).eval(node)
        # Un champ absent sans DEFAULT explicite reste indéterminé. Inventer
        # 0, faux ou une chaîne neutre ici contredit la logique trivalente :
        # une panne d'oracle pourrait rendre définitivement fausse la garde
        # d'un NEVER et ouvrir l'action qu'elle devait fermer.
        for key in missing:
            if key not in stmt.defaults:
                produced[key] = UNDEFINED
        self._flag_reason(stmt, missing, schema)
        produced = self._enforce_domains(stmt, produced)
        # Une sortie du modèle reste une sortie du modèle, même bornée, même
        # remplacée par son DEFAULT : c'est l'oracle qui a répondu — ou qui
        # s'est tu.
        label = _LLM | self._pc()
        for key, value in produced.items():
            self.state.set_local(key, value, label)
            self.state.set_local(f"reason.{key}", value, label)
        conf = produced.get("confidence")
        if isinstance(conf, (int, float)):
            self.state.set_local("confidence", float(conf), label)
        self.trace.log(self.state.tick, "LLM", f"REASON « {stmt.task} »",
                       ", ".join(f"{k}={fmt(v)}" for k, v in produced.items()))
        return produced

    # ------------------------------------------------------------- JUDGE
    @staticmethod
    def _probability(raw: Any) -> Optional[float]:
        """Une probabilité, ou rien. Hors [0, 1] ou non finie : rien.

        Un seuil d'abstention est une borne de confiance : un oracle qui
        rend 1.7 ou NaN ne doit pas pouvoir la franchir par accident de
        typage — exactement la raison d'être de `_enforce_domains` pour les
        valeurs.
        """
        if isinstance(raw, bool) or raw is None:
            return None
        try:
            value = float(raw)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(value) or not 0.0 <= value <= 1.0:
            return None
        return value

    def _run_judge(self, stmt: JudgeStmt) -> Dict[str, Any]:
        """`JUDGE` : des questions fermées, et la probabilité de la réponse.

        Deux choses le distinguent d'un `REASON`, et ce sont les deux qui
        manquaient pour décider sur une sortie de modèle :

        1. **La question part avec le champ.** `PRODUCE { holds: Bool }` fait
           deviner le sens au modèle ; `NOUL "le message demande-t-il de
           suspendre les réponses ?"` ne le fait pas.
        2. **La probabilité entre dans l'état**, sous `judge.<champ>.p` — donc
           dans les gardes de politique, au même titre qu'un fait observé.
           Elle n'y entre que si l'oracle sait la calibrer : un modèle
           génératif laisse `p` indéterminé plutôt que d'écrire un nombre.

        `ABSTAIN BELOW s` referme la boucle côté programme : sous le seuil —
        ou faute de probabilité — la réponse n'est **pas** retenue, le champ
        est déclaré absent et son `DEFAULT` s'applique. Les bancs d'injection
        disent pourquoi ce choix plutôt qu'un renvoi vers un modèle
        génératif : une consigne piégée fait chuter la probabilité de
        l'oracle de jugement, et le modèle génératif cède à cette même
        consigne bien plus souvent (29 fois sur 60 contre 8).
        """
        self.metrics["llm_calls"] += 1
        context = self.llm_context(stmt.using)
        schema = self._reason_schema(stmt)
        questions: Dict[str, Any] = {}
        for name, question in stmt.questions.items():
            payload = question.payload()
            payload["schema"] = schema.get(name, "Any")
            if question.abstain_below is not None:
                payload["abstain_below"] = question.abstain_below
            questions[name] = payload
        answers: Dict[str, Any] = {}
        try:
            ask = getattr(self.llm, "judge", None)
            answers = (ask(stmt.task, context, questions) if callable(ask)
                       else judge_via_reason(self.llm, stmt.task, context,
                                             questions))
        except KernelAbort:
            raise
        except Exception as exc:                      # noqa: BLE001
            answers = {}
            self.trace.log(self.state.tick, "ERROR",
                           f"JUDGE « {stmt.task} » a échoué",
                           f"{type(exc).__name__}: {exc} — "
                           "DEFAULT explicites appliqués")
        if not isinstance(answers, dict):
            answers = {}
        from .llm import _coerce
        values: Dict[str, Any] = {}
        prob: Dict[str, float] = {}
        conf: Dict[str, float] = {}
        for name in stmt.questions:
            answer = answers.get(name)
            if answer is None:
                continue
            if not isinstance(answer, dict):
                answer = {"value": answer}
            if "value" not in answer or answer["value"] is None:
                continue
            values[name] = answer["value"]
            probability = self._probability(answer.get("p"))
            if probability is not None:
                prob[name] = probability
            certainty = self._probability(answer.get("confidence"))
            if certainty is not None:
                conf[name] = certainty
        # Abstention : déclarée par le programme, appliquée par le runtime.
        abstained: List[str] = []
        for name, question in stmt.questions.items():
            if question.abstain_below is None or name not in values:
                continue
            probability = prob.get(name)
            if probability is None or probability < question.abstain_below:
                abstained.append(name)
                del values[name]
                prob.pop(name, None)
                conf.pop(name, None)
        if abstained:
            self.metrics["judge_abstained"] += len(abstained)
            self.trace.log(
                self.state.tick, "LLM",
                f"JUDGE « {stmt.task} » : abstention",
                ", ".join(
                    f"{name} sous le seuil "
                    f"{stmt.questions[name].abstain_below:g}" for name in abstained)
                + " — champ déclaré absent, DEFAULT appliqué")
        missing = [key for key in schema if key not in values]
        produced = _coerce({key: values[key] for key in schema if key in values},
                           schema)
        for key, node in stmt.defaults.items():
            if key in missing:
                produced[key] = Evaluator(self.state).eval(node)
        for key in missing:
            if key not in stmt.defaults:
                produced[key] = UNDEFINED
        self._flag_reason(stmt, missing, schema)
        # Seules les réponses **rendues** passent par l'écrêtage : un champ
        # absent ou abstenu vaut déjà son DEFAULT, et le faire apparaître
        # comme « sortie hors domaine » confondrait un oracle qui s'est tu
        # avec un oracle qui a répondu à côté.
        answered = {key: value for key, value in produced.items()
                    if key not in missing}
        produced.update(self._enforce_domains(stmt, answered))
        label = _LLM | self._pc()
        for key, value in produced.items():
            self.state.set_local(key, value, label)
            self.state.set_local(f"reason.{key}", value, label)
            self.state.set_local(f"judge.{key}", value, label)
            self.state.set_local(f"judge.{key}.value", value, label)
            # Une probabilité ne survit pas à l'abstention ni à l'absence :
            # rendre celle d'une valeur qui n'a pas été retenue autoriserait
            # une décision sur un chiffre qui ne porte plus sur rien.
            self.state.set_local(f"judge.{key}.p",
                                 prob.get(key, UNDEFINED), label)
            self.state.set_local(f"judge.{key}.confidence",
                                 conf.get(key, prob.get(key, UNDEFINED)), label)
        self.trace.log(
            self.state.tick, "LLM", f"JUDGE « {stmt.task} »",
            ", ".join(
                f"{k}={fmt(v)}" + (f" (p={prob[k]:.2f})" if k in prob else "")
                for k, v in produced.items()))
        return produced

    def _enforce_domains(self, stmt: ReasonStmt,
                         produced: Dict[str, Any]) -> Dict[str, Any]:
        """Borne les sorties du modèle (v1.1).

        Typer `confidence: Number` n'empêche pas de renvoyer 5000. Or ces
        valeurs finissent dans des gardes de politique : c'est le dernier
        canal par lequel un LLM peut influencer une autorisation avec une
        valeur non contrainte. Le programme borne, le modèle s'y plie.
        """
        for key, domain in stmt.domains.items():
            if key not in produced:
                continue
            value = produced[key]
            if domain.kind == "RANGE":
                try:
                    number = float(value)
                    if not math.isfinite(number):
                        raise ValueError("non-finite number")
                except (TypeError, ValueError, OverflowError):
                    replacement: Any = UNDEFINED
                    if key in stmt.defaults:
                        candidate = Evaluator(self.state).eval(
                            stmt.defaults[key])
                        try:
                            finite = float(candidate)
                            if (math.isfinite(finite)
                                    and domain.low <= finite <= domain.high):
                                replacement = candidate
                        except (TypeError, ValueError, OverflowError):
                            pass
                    produced[key] = replacement
                    self.metrics["domain_clamps"] += 1
                    self.trace.log(
                        self.state.tick, "BLOCKED",
                        f"sortie LLM numérique invalide : {key}",
                        f"{fmt(value)} rejeté hors {domain.render()} — "
                        + ("DEFAULT explicite appliqué"
                           if replacement is not UNDEFINED
                           else "valeur rendue indéterminée"))
                    continue
                clamped = min(max(number, domain.low), domain.high)
                if clamped != number:
                    if key in stmt.defaults:
                        candidate = Evaluator(self.state).eval(
                            stmt.defaults[key])
                        try:
                            finite = float(candidate)
                            clamped = (candidate
                                       if (math.isfinite(finite)
                                           and domain.low <= finite <= domain.high)
                                       else UNDEFINED)
                        except (TypeError, ValueError, OverflowError):
                            clamped = UNDEFINED
                    self.metrics["domain_clamps"] += 1
                    self.trace.log(self.state.tick, "BLOCKED",
                                   f"sortie LLM hors domaine : {key}",
                                   f"{fmt(number)} ramené dans "
                                   f"{domain.render()}")
                produced[key] = clamped
            else:
                allowed = [str(v) for v in domain.values]
                if str(value) not in allowed:
                    self.metrics["domain_clamps"] += 1
                    self.trace.log(self.state.tick, "BLOCKED",
                                   f"sortie LLM hors domaine : {key}",
                                   f"{fmt(value)} ∉ {domain.render()} — "
                                   f"remplacé par {allowed[-1]}")
                    if key in stmt.defaults:
                        produced[key] = Evaluator(self.state).eval(stmt.defaults[key])
                    else:
                        produced[key] = (Symbol(allowed[-1])
                                         if isinstance(domain.values[-1], Symbol)
                                         else domain.values[-1])
        return produced

    def llm_context(self, using: Optional[List[str]] = None) -> Dict[str, Any]:
        """Contexte transmis au LLM. Lecture seule, sans capacité d'action.

        `USING [a, b]` **restreint** le contexte à ces chemins. Jusqu'en v1.6
        il ne faisait qu'ajouter un `focus` : toutes les croyances, tous les
        outils avec leur risque, tous les plans et tous les buts partaient
        quand même. La documentation présentait `USING` comme la surface
        d'exposition minimale, et T4 (« surface LLM ») raisonnait sur cette
        liste — le certificat bornait donc une surface que le runtime
        n'appliquait pas. Une croyance sensible restait exposée à un modèle
        distant alors que le programme avait explicitement listé ce qu'il
        acceptait de montrer.

        Sans `USING`, rien ne change : le contexte complet est la sémantique
        de la sélection de plan, où le modèle doit voir les plans pour en
        choisir un. Un `REASON` sans `USING` reçoit donc tout l'état — c'est
        le défaut historique, et `W129` le signale plutôt que de le changer
        en silence : restreindre à *rien* un programme qui ne déclarait rien
        casserait son raisonnement sans qu'il ait demandé quoi que ce soit.
        """
        if using:
            # Uniquement ce qui a été listé, plus l'horloge — un raisonnement
            # sur des faits datés a besoin de savoir quand. Ni outils ni
            # plans : `REASON` produit des valeurs, il ne choisit pas d'action.
            return {"tick": self.state.tick,
                    "focus": {name: str(self._sendable(name,
                                                       self.state.get(name)))
                              for name in using}}
        ctx: Dict[str, Any] = {
            "tick": self.state.tick,
            "goals": {g.name: {
                "mode": g.mode,
                "score": self.state.world.get(f"goals.{g.name}.score"),
            } for g in self.agent.goals},
            "beliefs": {k: {"value": str(self._sendable(k, v.value)),
                            "confidence": v.confidence, "source": v.source}
                        for k, v in self.state.beliefs.items()},
            "tools": {t.name: {"risk": t.risk, "inputs": t.inputs}
                      for t in self.agent.tools},
            "plans": [p.name for p in self.agent.plans],
        }
        for name, goal in ctx["goals"].items():
            goal["score"] = self._sendable(f"goals.{name}.score",
                                           goal["score"])
        return ctx

    #: Ce qui remplace une valeur retenue. Une clé qui disparaît sans trace
    #: se lit comme une absence de donnée ; le modèle doit savoir qu'il
    #: raisonne sur un état amputé, sans apprendre ce qui lui manque.
    REDACTED = "⟦retenu⟧"

    def _redacted(self, path: str) -> bool:
        """Le chemin tombe-t-il sous un `NEVER SEND` ?

        Par préfixe de segment : `NEVER SEND credentials` couvre
        `credentials.token` mais pas `credentials_publics`. Une interdiction
        de sortie qui s'arrêterait au chemin exact ne retiendrait rien —
        c'est sous les feuilles que sont les secrets.
        """
        for red in self.agent.redactions:
            if path == red.path or path.startswith(red.path + "."):
                return True
        return False

    def _covers_below(self, path: str) -> bool:
        """Un `NEVER SEND` porte-t-il sur un descendant de ce chemin ?

        L'autre sens de la lecture, et le trou de la v1.8 : interdire
        `credentials.token` ne disait rien de `credentials`. Envoyer le parent
        composite livrait donc le secret, sans qu'aucun contrôle ne se
        déclenche — la protection se contournait en désignant le nœud du
        dessus, ce qui est exactement ce que fait un `USING { credentials }`.
        """
        return any(red.path.startswith(path + ".")
                   for red in self.agent.redactions)

    def _sendable(self, path: str, value: Any) -> Any:
        """Rend la valeur telle qu'elle peut partir vers le fournisseur.

        Trois cas. Le chemin est lui-même interdit : rien ne part. Un
        descendant est interdit et la valeur est un composite parcourable :
        on retient **la feuille**, pas le composite — retenir tout
        `credentials` parce que son `token` est secret amputerait le
        raisonnement de données que le programme n'a jamais protégées. La
        valeur n'est pas parcourable : on retient tout, faute de savoir
        où est le secret à l'intérieur. C'est le sens fermé, et le seul
        défendable : une interdiction qu'on ne sait pas appliquer finement
        s'applique en grand.
        """
        if self._redacted(path):
            self._log_redaction(path)
            return self.REDACTED
        if self._covers_below(path):
            return self._redact_within(path, value)
        return value

    def _redact_within(self, path: str, value: Any) -> Any:
        """Descend dans un composite pour n'en retenir que les feuilles
        interdites. Les clés restent visibles, comme au niveau du dessus."""
        if isinstance(value, dict):
            out = {}
            for key, sub in value.items():
                below = f"{path}.{key}"
                if self._redacted(below):
                    self._log_redaction(below)
                    out[key] = self.REDACTED
                elif self._covers_below(below):
                    out[key] = self._redact_within(below, sub)
                else:
                    out[key] = sub
            return out
        if isinstance(value, (list, tuple)):
            # Les éléments d'une liste partagent le chemin de leur parent : le
            # langage ne sait pas désigner un indice, donc l'interdiction porte
            # sur chacun d'eux.
            return [self._redact_within(path, item) for item in value]
        # Composite opaque : on ne sait pas où regarder, donc on retient tout.
        self._log_redaction(path, whole=True)
        return self.REDACTED

    def _log_redaction(self, path: str, whole: bool = False) -> None:
        self.metrics["redactions"] += 1
        self.trace.log(self.state.tick, "BLOCKED",
                       f"{path} retenu — NEVER SEND",
                       "la valeur n'atteint pas le fournisseur de modèle"
                       if not whole else
                       "valeur non parcourable dont un descendant est "
                       "interdit : retenue entière, faute de pouvoir isoler "
                       "la feuille")

    # ------------------------------------------------------ humain / délégation
    def _exec_ask(self, stmt: AskStmt) -> Any:
        self.trace.log(self.state.tick, "ASK",
                       f"{stmt.addressee} : {stmt.question}", stmt.reason)
        answer = self._ask(stmt.question, stmt.reason)
        if answer is None or (isinstance(answer, Symbol)
                              and answer.name == "no_answer"):
            answer = Evaluator(self.state).eval(stmt.default) \
                if stmt.default is not None else Symbol("no_answer")
        self.state.set_local("answer", answer, _HUMAN)
        self.state.set_local(f"answer.{stmt.addressee}", answer, _HUMAN)
        self.trace.log(self.state.tick, "ASK", f"réponse = {fmt(answer)}")
        return answer

    #: Risque prêté à un sous-agent qu'aucun `TOOL` ne décrit. Un sous-agent
    #: est une fonction Python opaque : rien dans l'interface n'empêche d'y
    #: écrire en base, d'appeler le réseau ou de lancer une commande. Sans
    #: contrat, on ne *devine* pas son innocuité — on échoue fermé.
    UNDECLARED_DELEGATE_RISK = Kernel.UNDECLARED_DELEGATE_RISK

    def _exec_delegate(self, stmt: DelegateStmt) -> Any:
        """Délègue à un sous-agent, **après** passage par les politiques.

        Jusqu'en v1.6, `DELEGATE` appelait directement la fonction enregistrée
        dans `host.subagents` : ni contrôle de politique, ni risque déclaré, ni
        approbation. Un programme sous `DEFAULT DENY` voyait donc son
        sous-agent s'exécuter quand même — le moteur de politiques n'était plus
        « le point de passage obligé » qu'annonce la spécification, et T1
        (« aucun appel interdit n'aboutit ») ne disait rien de ce chemin.

        La cible de la règle est le **nom du sous-agent** : `NEVER forensic`,
        `ALLOW forensic IF …` s'écrivent comme pour un outil, et `DEFAULT DENY`
        le couvre sans rien écrire. Un sous-agent décrit par un `TOOL` homonyme
        emprunte son risque et ses effets de bord ; sinon `W127` le signale et
        le risque supposé est `CRITICAL`.
        """
        self.trace.log(self.state.tick, "DELEGATE",
                       f"{stmt.agent} ← « {stmt.task} »")
        inputs, labels = {}, {"$control": self._pc()}
        for name in stmt.inputs:
            inputs[name], labels[name] = self.state.get_labeled(name)
        request = self.kernel.propose_delegate(
            stmt.agent, inputs,
            self.state.locals["confidence"]
            if "confidence" in self.state.locals else 1.0)
        request.provenance = labels
        permit = self._authorize(request, kind="delegate")
        if permit is None:
            return None

        try:
            registered, result = self.kernel.delegate(permit, self.host)
        except KernelAbort:
            raise
        except Exception as exc:                      # noqa: BLE001
            self.trace.log(self.state.tick, "ERROR",
                           f"sous-agent {stmt.agent} en erreur",
                           f"{type(exc).__name__}: {exc}")
            if isinstance(exc, ActionInDoubt):
                self._flag_in_doubt(stmt.agent, request.side_effects)
            return None
        if not registered:
            self.trace.log(self.state.tick, "ERROR",
                           f"sous-agent non enregistré : {stmt.agent}")
            return None
        result = result or {}
        if not isinstance(result, dict):
            self.trace.log(self.state.tick, "ERROR",
                           f"sous-agent {stmt.agent} : retour non conforme",
                           f"attendu dict, reçu {type(result).__name__}")
            return None
        missing = [k for k in stmt.expect if k not in result]
        if missing:
            self.trace.log(self.state.tick, "ERROR",
                           f"contrat EXPECT non respecté par {stmt.agent}",
                           ", ".join(missing))
        for key, value in result.items():
            # Préfixé dans les locales (provenance lisible), nu dans l'espace
            # non fiable : le retour d'un sous-agent est de la donnée externe
            # au même titre qu'une charge utile, il ne masque rien.
            value, label = unwrap(value, _DELEGATE)
            self.state.set_local(f"{stmt.agent}.{key}", value, label)
            self.state.set_untrusted(key, value, label)
        self.trace.log(self.state.tick, "DELEGATE",
                       f"{stmt.agent} → " +
                       ", ".join(f"{k}={fmt(v)}" for k, v in result.items()))
        return result

    def _exec_message(self, stmt: MessageStmt) -> None:
        ev = Evaluator(self.state)
        payload = {k: ev.eval(v) for k, v in stmt.payload.items()}
        target = "tous" if stmt.broadcast else (stmt.to or "?")
        if self.society is None:
            self.trace.log(self.state.tick, "MESSAGE",
                           f"{stmt.name} → {target} non remis",
                           "aucune société d'agents : exécution isolée")
            return
        delivered = self.society.send(self.agent.name, stmt.name, payload,
                                      to=None if stmt.broadcast else stmt.to)
        self.metrics["messages_sent"] += 1
        self.trace.log(self.state.tick, "MESSAGE",
                       f"{stmt.name} → {target}",
                       ", ".join(f"{k}={fmt(v)}" for k, v in payload.items())
                       + (f" | {delivered} destinataire(s)" if delivered != 1
                          else ""))

    # ------------------------------------------------------------- outils
    def _label_args(self, call) -> Dict[str, Prov]:
        """Provenance de chaque argument, plus le contexte de la décision."""
        decl = self.agent.tool(call.name)
        names = list(decl.inputs) if decl else []
        ev = Evaluator(self.state)
        pc = self._pc()
        labels: Dict[str, Prov] = {"$control": pc}
        for idx, node in enumerate(call.args):
            key = names[idx] if idx < len(names) else f"arg{idx}"
            labels[key] = ev.label(node) | pc
        for key, node in call.kwargs.items():
            labels[key] = ev.label(node) | pc
        return labels

    def _eval_args(self, call) -> Dict[str, Any]:
        decl = self.agent.tool(call.name)
        names = list(decl.inputs) if decl else []
        ev = Evaluator(self.state)
        args: Dict[str, Any] = {}
        for idx, node in enumerate(call.args):
            key = names[idx] if idx < len(names) else f"arg{idx}"
            args[key] = ev.eval(node)
        for key, node in call.kwargs.items():
            args[key] = ev.eval(node)
        return args

    def call_tool(self, name: str, args: Dict[str, Any],
                  origin: str = "plan",
                  provenance: Optional[Dict[str, Prov]] = None) -> Any:
        decl = self.agent.tool(name)
        if decl is None:
            self.metrics["blocked"] += 1
            self.trace.log(self.state.tick, "BLOCKED",
                           f"outil non déclaré : {name}()",
                           "un outil non déclaré n'existe pas pour le runtime")
            return None

        # Un plan conforme (v0.7) est une séquence unique jouée dans un monde
        # qui, lui, a tranché. Rejouer une action dont la précondition est
        # retombée serait au mieux inutile, au pire nuisible : c'est ainsi
        # qu'on couperait le réseau d'un poste déjà assaini. `REQUIRES` fait
        # partie du contrat de l'outil, il est donc réévalué à l'exécution.
        if decl.requires is not None:
            self._refresh_for(decl.requires)
            # Fail-closed : une précondition inévaluable interdit l'action
            # (jamais l'inverse) — on ne joue pas un outil dont on ne peut pas
            # établir la précondition.
            if not self._safe_test(decl.requires, on_error=False,
                                    context=f"REQUIRES de {name}()"):
                self.trace.log(self.state.tick, "INFO",
                               f"{name}() ignoré", "précondition REQUIRES "
                               "retombée depuis la planification")
                return None

        confidence = (self.state.locals["confidence"]
                      if "confidence" in self.state.locals else 1.0)
        request, type_error = self.kernel.propose_tool(
            name, args, origin, confidence)
        if request is not None:
            # Sans étiquettes — appel direct, hors d'un plan — rien n'est
            # présumé : `UNKNOWN`, donc non fiable.
            request.provenance = dict(provenance or {})
        if request is None:
            self.metrics["blocked"] += 1
            self.trace.log(self.state.tick, "BLOCKED",
                           f"{name}() : contrat INPUT violé", type_error)
            return None
        permit = self._authorize(request)
        if permit is None:
            return None

        if self._circuit_open(name):
            self.kernel.void(permit)
            self.metrics["circuit_open"] += 1
            failures = self._tool_failures[name][0]
            self.trace.log(
                self.state.tick, "BLOCKED", f"{request.render()} non tenté",
                f"disjoncteur ouvert : {failures} échecs consécutifs — "
                f"nouvel essai dans {self._cooldown_left(name)} tick(s)")
            self.state.set_local("last_action.blocked", True, _RUNTIME)
            return None

        try:
            result = self.kernel.execute(permit, self.host)
        except KernelAbort:
            raise
        except Exception as exc:                      # noqa: BLE001
            self._record_tool_failure(name)
            self.metrics["tool_failures"] += 1
            self.trace.log(self.state.tick, "ERROR", f"{name}() a échoué",
                           f"{type(exc).__name__}: {exc} — "
                           f"{self._tool_failures[name][0]} échec(s) consécutif(s)")
            if isinstance(exc, ActionInDoubt):
                self._flag_in_doubt(name, decl.side_effects)
            return None

        # L'appel a franchi la frontière et peut avoir produit un effet dans
        # le monde, même si sa réponse est ensuite jugée non conforme. On le
        # marque donc avant de valider le payload. En revanche, aucune
        # postcondition déclarée ne sera crue tant que le contrat OUTPUT
        # n'est pas satisfait.
        for path in decl.side_effects:
            self.state.set_world(f"{path}.dirty", True, _RUNTIME)

        # Une valeur qu'un hôte déclare lui-même non fiable (`untrusted()`)
        # garde cette étiquette en plus de celle de l'outil.
        out_labels: Dict[str, Prov] = {}
        if isinstance(result, dict):
            result = dict(result)
            for key in list(result):
                result[key], out_labels[key] = unwrap(result[key], _TOOL)
        safe_result, contract_error = self._validate_tool_result(
            name, decl.outputs, result)
        if contract_error:
            self._record_tool_failure(name)
            self.metrics["tool_failures"] += 1
            self.metrics["tool_contract_failures"] += 1
            self.trace.log(
                self.state.tick, "ERROR", f"{name}() : contrat OUTPUT violé",
                contract_error + " — sortie ignorée, effets non présumés")
            return None

        self._record_tool_success(name)
        self.metrics["tool_calls"] += 1
        self.state.set_local("last_action.blocked", False, _RUNTIME)
        self.state.set_local(f"result.{name}", safe_result, _TOOL)
        self.state.set_local("result", safe_result, _TOOL)
        # L'outil a accepté ces valeurs : c'est un fait sur elles, que
        # `ATTESTED(x, outil)` peut lire. Pas une confiance — un validateur
        # qui accepte une cible injectée ne la rend pas fiable.
        for value in request.args.values():
            self.state.attest(name, value)
        for key, value in safe_result.items():
            # Provenance, et non confiance. Un outil est une frontière externe
            # (SPEC §28) : ce qu'il rapporte peut avoir été écrit par un tiers
            # — page web, ticket, corps de courriel. Les formes **préfixées**
            # disent d'où vient la donnée et restent dans l'état vivant
            # (`result.<outil>.<clé>`, `<outil>.<clé>` dans le monde) ; le nom
            # **nu**, lui, va dans l'espace non fiable, consulté en dernier.
            # Il comble donc ce que rien d'autre ne renseigne, mais ne masque
            # plus une observation ni une croyance homonyme — c'est ce qui
            # pouvait désactiver un `NEVER` dont la garde porte ce nom.
            # Même traitement que le retour d'un sous-agent (`_exec_delegate`)
            # et qu'une charge utile d'événement (`_bind_payload`).
            label = out_labels.get(key, _TOOL)
            self.state.set_untrusted(key, value, label)
            self.state.set_world(f"{name}.{key}", value, label)
        # Les EFFECT déclarés sont enregistrés comme *attendus*, avec une
        # confiance inférieure à celle d'une observation : le modèle utilisé
        # pour planifier et celui utilisé pour exécuter ne divergent pas, mais
        # une perception ultérieure prime toujours sur une postcondition.
        if decl.effects:
            ev = Evaluator(_ActionScope(self.state, request))
            control = request.provenance.get("$control", UNKNOWN_LABEL)
            for effect in decl.effects:
                self.state.set_belief(
                    effect.path, ev.eval(effect.value),
                    self._effect_confidence(name, effect.path),
                    f"effect:{name}", self.state.tick,
                    _EFFECT | ev.label(effect.value) | control)
        self.trace.log(self.state.tick, "TOOL", request.render(),
                       f"risk={decl.risk} → {fmt(safe_result)}")
        return safe_result

    def _flag_in_doubt(self, name: str, side_effects: List[str]) -> None:
        """Une action a pu avoir lieu sans qu'on sache si elle a eu lieu (v1.9).

        Cas de l'exécution durable : l'intention est au journal, pas le
        résultat, et l'hôte ne sait ni honorer une clé d'idempotence ni
        réconcilier. Le noyau n'a pas relancé l'action — au plus une fois,
        jamais deux. Ce qui reste à faire ici, c'est ne rien présumer et le
        **dire** : les effets de bord déclarés sont marqués sales, aucun
        `EFFECT` n'est cru, et `tools.<outil>.in_doubt` devient lisible par
        une politique :

            NEVER transfer WHEN tools.transfer.in_doubt == true
        """
        self.state.set_world(f"tools.{name}.in_doubt", True, _RUNTIME)
        self.state.set_local("last_action.in_doubt", True, _RUNTIME)
        for path in side_effects:
            self.state.set_world(f"{path}.dirty", True, _RUNTIME)
        self.trace.log(
            self.state.tick, "ERROR", f"{name}() : effet indéterminé",
            "intention journalisée sans résultat — action non relancée (au "
            f"plus une fois), EFFECT non présumés ; `tools.{name}.in_doubt` "
            "posé")

    def _validate_tool_result(self, name: str, outputs: Dict[str, str],
                              result: Any) -> tuple[Dict[str, Any], str]:
        """Réduit une réponse d'outil à son contrat déclaré.

        Un outil est une frontière externe au même titre qu'un adaptateur
        LLM. Accepter toutes les clés de son dictionnaire permettait à une
        réponse ``{"operator.confirmed": yes}`` d'écraser une observation,
        puis d'ouvrir une action protégée par politique. Seules les clés
        OUTPUT sont donc admises dans l'état vivant ; le reste est tracé et
        jeté. Un contrat typé incomplet ou invalide rend l'appel indéterminé
        et interdit de croire ses EFFECT.
        """
        if not outputs:
            if isinstance(result, dict) and result:
                self.metrics["tool_output_dropped"] += len(result)
                self.trace.log(
                    self.state.tick, "INFO",
                    f"{name}() : sortie hors contrat ignorée",
                    ", ".join(sorted(str(key) for key in result)))
            return {}, ""
        if not isinstance(result, dict):
            return {}, (f"attendu dict avec {', '.join(outputs)}, reçu "
                        f"{type(result).__name__}")

        unknown = [key for key in result if key not in outputs]
        if unknown:
            self.metrics["tool_output_dropped"] += len(unknown)
            self.trace.log(
                self.state.tick, "INFO",
                f"{name}() : champ(s) OUTPUT non déclaré(s) ignoré(s)",
                ", ".join(sorted(str(key) for key in unknown)))
        safe = {key: result[key] for key in outputs if key in result}
        _coerce_inputs(outputs, safe)
        error = _typecheck(outputs, safe)
        return ({} if error else safe), error


# ------------------------------------------------------------------ helpers
def _retry_count(handler: List[Stmt]) -> int:
    for stmt in handler:
        if isinstance(stmt, ControlStmt) and stmt.kind == "RETRY":
            return int(stmt.arg or 1)
    return 0


class _AuthorizationTrace:
    """Observateur du noyau : les étapes d'une autorisation, en trace.

    Il voit passer la décision et l'attente d'approbation au moment où elles
    ont lieu — une interface en direct montre « en attente » pendant que
    l'humain est interrogé — mais ne peut rien y changer : le permis n'est
    émis qu'après lui.
    """

    def __init__(self, runtime: "Runtime", subject: str) -> None:
        self.runtime, self.subject = runtime, subject

    def decided(self, request: ActionRequest, decision: Any) -> None:
        if decision.shadowed_args:
            rt = self.runtime
            rt.trace.log(
                rt.state.tick, "POLICY",
                f"{request.render()} : garde évaluée sur l'argument",
                ", ".join(f"`{key}` masque la locale {fmt(value)}"
                          for key, value
                          in sorted(decision.shadowed_args.items())))

    def pending(self, request: ActionRequest, decision: Any) -> None:
        rt = self.runtime
        rt.metrics["approvals"] += 1
        rt.trace.log(rt.state.tick, "APPROVAL",
                     f"{self.subject} en attente", decision.reason)

    def approver_failed(self, request: ActionRequest, exc: BaseException) -> None:
        rt = self.runtime
        rt.trace.log(rt.state.tick, "ERROR", "approbateur en erreur",
                     f"{type(exc).__name__}: {exc} — refus (fail-closed)")


def _render_expr(node) -> str:
    from .nodes import BinOp, Literal, PathExpr, UnOp
    if isinstance(node, Literal):
        return fmt(node.value)
    if isinstance(node, PathExpr):
        return node.dotted
    if isinstance(node, UnOp):
        return f"{node.op} {_render_expr(node.operand)}"
    if isinstance(node, BinOp):
        return f"{_render_expr(node.left)} {node.op} {_render_expr(node.right)}"
    return type(node).__name__

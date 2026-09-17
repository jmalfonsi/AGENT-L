"""Client HTTP d'ENTERPRISE-SIM — transport, extraction, mise en forme.

Ce module est délibérément **sans décision**. Il ouvre des sockets, lit
l'API du simulateur, en extrait des scalaires et concatène des libellés.
On n'y trouvera aucun seuil, aucun tri par importance, aucun choix de cible
ni aucune boucle « traiter ce qui mérite de l'être » : tout cela appartient
aux fichiers `.agent`, seuls audités par `agentl boundary` et `agentl verify`.

Le test à appliquer à chaque ligne ajoutée ici : *si la politique
d'exploitation de l'usine changeait demain, faudrait-il modifier cette
ligne ?* Si oui, elle est au mauvais endroit.

Variables d'environnement :

    VALMONT_SIM_URL     défaut http://localhost:4500
    VALMONT_TOKEN       défaut demo-operator-key
    VALMONT_DRY_RUN     défaut "1" — les commandes ne sont PAS envoyées.
                        Opt-in explicite « 0 » pour agir réellement.
"""
from __future__ import annotations

import atexit
import itertools
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Dict, List, Optional

DEFAULT_URL = os.environ.get("VALMONT_SIM_URL", "http://localhost:4500")
DEFAULT_TOKEN = os.environ.get("VALMONT_TOKEN", "demo-operator-key")
CACHE_TTL_S = 2.0
RUN_ID = os.environ.get("VALMONT_RUN_ID") or \
    f"valmont-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:8]}"
_AGENT_SEQUENCE = itertools.count(1)


class SimUnavailable(RuntimeError):
    """Le simulateur n'a pas répondu. Une panne est un fait, pas un zéro."""


def external_llm_consent() -> bool:
    """Opt-in explicite avant toute sortie de données vers un LLM externe.

    Les libellés d'alarmes, la synthèse des utilités et les réponses du
    responsable de site sortent du réseau de l'usine dès qu'un oracle
    hébergé est branché. C'est un choix d'exploitant, pas un défaut : sans
    `VALMONT_LLM_EXTERNE=1`, les agents tournent sur un oracle déterministe
    local et rien ne quitte le site.
    """
    return os.environ.get("VALMONT_LLM_EXTERNE", "").strip().lower() in (
        "1", "oui", "o", "yes", "y", "true")


def gemini_key() -> str:
    """Clé de l'oracle : environnement d'abord, puis `~/HAL/.env`.

    Sous cron ou systemd il n'y a pas d'environnement de shell ; sans ce
    repli, les `REASON` retomberaient silencieusement sur leurs défauts.
    """
    key = os.environ.get("GEMINI_API_KEY", "").strip()
    if key:
        return key
    hal = os.path.expanduser("~/HAL/.env")
    if not os.path.exists(hal):
        return ""
    with open(hal, encoding="utf-8") as handle:
        for raw in handle:
            if raw.strip().startswith("GEMINI_API_KEY="):
                return raw.strip().partition("=")[2].strip()
    return ""


class SimClient:
    """Accès en lecture et en commande à ENTERPRISE-SIM."""

    def __init__(self, base_url: str = DEFAULT_URL, token: str = DEFAULT_TOKEN,
                 dry_run: Optional[bool] = None, timeout: float = 8.0,
                 chat_timeout: float = 35.0):
        self.base = base_url.rstrip("/") + "/api/v1"
        self.token = token
        self.timeout = timeout
        self.chat_timeout = max(timeout, chat_timeout)
        # Défaut fermé : sans opt-in explicite, aucune commande ne part.
        env = os.environ.get("VALMONT_DRY_RUN", "1").strip().lower()
        self.dry_run = (env not in ("0", "false", "non", "no")) if dry_run is None else dry_run
        self._cache: Dict[str, Any] = {}
        self.run_id = RUN_ID
        self._agent_events: List[Dict[str, Any]] = []
        self._journal_warning = False
        atexit.register(self.flush_agent_events)
        #: Recettes de retour arrière journalisées AVANT chaque effet.
        self.rollback_log: List[Dict[str, Any]] = []
        self.command_log: List[Dict[str, Any]] = []

    # -------------------------------------------- journal d activite agent
    def _post_agent_events(self, events: List[Dict[str, Any]]) -> None:
        data = json.dumps({"events": events}, ensure_ascii=False,
                          default=str).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base}/agent-events", data=data,
            headers={"Authorization": f"Bearer {self.token}",
                     "Accept": "application/json",
                     "Content-Type": "application/json"},
            method="POST")
        with urllib.request.urlopen(req, timeout=min(self.timeout, 3.0)) as resp:
            if resp.status >= 300:
                raise SimUnavailable(f"HTTP {resp.status} sur /agent-events")

    def agent_event(self, kind: str, summary: str, *, agent: str = "VALMONT",
                    direction: str = "INTERNAL", tick=None, detail: str = "",
                    payload=None, urgent: bool = False) -> Dict[str, Any]:
        """Met un échange détaillé en file, sans jamais piloter la ronde."""
        sequence = next(_AGENT_SEQUENCE)
        text = str(summary)
        event = {
            "id": f"{self.run_id}:{sequence}",
            "runId": self.run_id,
            "sequence": sequence,
            "agent": str(agent),
            "tick": tick,
            "kind": str(kind).upper(),
            "direction": str(direction).upper(),
            "summary": text[:4000],
            "detail": str(detail or "")[:16000],
            "payload": payload,
        }
        self._agent_events.append(event)
        if urgent or len(self._agent_events) >= 25:
            self.flush_agent_events()
        return event

    def flush_agent_events(self) -> None:
        if not self._agent_events:
            return
        events, self._agent_events = self._agent_events, []
        try:
            self._post_agent_events(events)
            self._journal_warning = False
        except Exception as exc:                              # noqa: BLE001
            # La traçabilité ne doit jamais devenir une autorité sur le run.
            # On garde les entrées pour un prochain lot, dans une borne mémoire.
            self._agent_events = (events + self._agent_events)[-500:]
            if not self._journal_warning:
                print(f"    ⚠  journal agent indisponible : {exc}")
                self._journal_warning = True

    def trace_sink(self, agent: str):
        """Construit le relais des événements natifs de Trace vers le simulateur."""
        self.agent_event("RUN", f"Démarrage du composant {agent}",
                         agent=agent, urgent=True)
        def sink(event):
            direction = ("SIM_TO_AGENT" if event.kind == "OBSERVE"
                         else "AGENT_TO_SIM" if event.kind in ("TOOL", "DELEGATE")
                         else "INTERNAL")
            payload = ({"fullText": event.text[:64000]}
                       if len(event.text) > 4000 else None)
            self.agent_event(event.kind, event.text or f"tick {event.tick}",
                             agent=agent, direction=direction, tick=event.tick,
                             detail=event.detail, payload=payload)
        return sink

    # ------------------------------------------------------------- transport
    def _request(self, path: str, payload: Optional[dict] = None,
                 headers: Optional[dict] = None,
                 timeout: Optional[float] = None) -> dict:
        request_timeout = self.timeout if timeout is None else timeout
        url = f"{self.base}{path}"
        head = {"Authorization": f"Bearer {self.token}",
                "Accept": "application/json"}
        head.update(headers or {})
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            head["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=head,
                                     method="POST" if data else "GET")
        try:
            with urllib.request.urlopen(req, timeout=request_timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:            # 4xx/5xx
            body = exc.read().decode("utf-8", "replace")[:300]
            raise SimUnavailable(f"HTTP {exc.code} sur {path} : {body}") from exc
        except Exception as exc:                          # réseau, timeout, JSON
            raise SimUnavailable(f"{type(exc).__name__} sur {path} : {exc}") from exc

    def get(self, path: str) -> dict:
        """Lecture avec cache court : un tick n'interroge pas dix fois l'API."""
        now = time.monotonic()
        hit = self._cache.get(path)
        if hit and now - hit[0] < CACHE_TTL_S:
            return hit[1]
        value = self._request(path)
        self._cache[path] = (now, value)
        return value

    # ------------------------------------------------------------- lectures
    def summary(self) -> dict:
        return self.get("/summary")

    #: Plafond que l'API s'impose elle-même (`Math.min(500, limit)`).
    LIMITE_API = 500

    def _page_alarmes(self, etat: str) -> List[dict]:
        """Une page d'alarmes, et le refus explicite d'en lire une tronquée.

        `count` est le total APRÈS filtrage et AVANT découpe : quand il dépasse
        ce qui est rendu, la fenêtre cache des lignes. Rendre la page quand même
        ferait lire « il y a moins d'alarmes » là où il faut lire « je n'ai pas
        pu compter » — indéterminé n'est pas zéro, et une lecture partielle est
        pire qu'une lecture ratée puisqu'elle a l'air d'avoir réussi.
        """
        page = self.get(f"/alarms?state={etat}&limit={self.LIMITE_API}")
        rows = page.get("alarms", [])
        total = int(page.get("count", len(rows)))
        if total > len(rows):
            raise SimUnavailable(
                f"journal d'alarmes tronque sur state={etat} : {total} lignes "
                f"filtrees, {len(rows)} rendues")
        return rows

    def alarms(self) -> dict:
        """Alarmes NON CLÔTURÉES, demandées au serveur par ses propres filtres.

        Cette méthode demandait `state=all&limit=40`, et c'était un défaut de
        perception : le serveur découpe la liste AVANT qu'on la trie, et son
        ordre est chronologique inverse. Une alarme majeure toujours active mais
        ancienne — carburant du groupe électrogène, rang 79 sur 101 — sortait
        de la fenêtre, chassée par des dizaines de clôturées plus récentes.
        L'agent inventoriait alors zéro alarme pendant que le résumé en comptait
        deux, et ne demandait donc jamais quoi faire de celle-là.

        Trier après coup, comme le fait `open_alarms`, ne corrige rien : le tri
        s'applique à ce qui a survécu à la découpe. Il faut que le FILTRE soit
        posé côté serveur, où il précède la découpe. D'où deux appels dont
        l'union est exactement l'ensemble des non-clôturées :

          · `state=active`         → `ACTIVE` et `ACKED` ;
          · `state=unacknowledged` → tout ce qui n'est pas acquitté ni clôturé,
            ce qui ajoute les `CLEARED_UNACK` — un état qui n'existe que non
            acquitté (`alarms.js`, l'acquittement le promeut en `CLEARED`).
        """
        vues: Dict[str, dict] = {}
        for etat in ("active", "unacknowledged"):
            for row in self._page_alarmes(etat):
                identifiant = str(row.get("id"))
                if identifiant not in vues:
                    vues[identifiant] = row
        return {"alarms": list(vues.values())}

    def faults(self) -> dict:
        return self.get("/faults")

    def production(self) -> dict:
        return self.get("/production")

    def water(self) -> dict:
        return self.get("/water")

    def air(self) -> dict:
        return self.get("/air")

    def gas(self) -> dict:
        return self.get("/gas")

    def electricity(self) -> dict:
        return self.get("/electricity")

    def energy(self) -> dict:
        return self.get("/energy")

    def personnel(self) -> dict:
        return self.get("/personnel")

    def telemetry(self) -> dict:
        return self.get("/telemetry")

    def faults_awaiting_repair(self) -> int:
        """Défauts au statut `ACTIVE`, c'est-à-dire EN ATTENTE d'intervention.

        Ce n'est pas `summary.faults`, qui compte aussi les défauts déjà pris
        en charge (`REPAIRING`) par les équipes postées du simulateur. La
        commande `maintenance.repair *` ne cible, elle, que les `ACTIVE` :
        compter les autres faisait lancer une intervention sur un défaut déjà
        en cours, et l'API répondait « aucun defaut actif ». Le vocabulaire du
        fait doit être celui de la commande qu'il autorise.
        """
        return sum(1 for f in self.faults().get("faults", [])
                   if f.get("status") == "ACTIVE")

    def doubtful_points(self) -> int:
        """Nombre de points de mesure dont l'indice qualité n'est pas GOOD."""
        tags = self.telemetry().get("tags", {})
        return sum(1 for p in tags.values() if p.get("q") != "GOOD")

    #: Rang de gravité **publié par le simulateur** — on ne l'invente pas.
    _SEVERITY_RANK = {"critical": 0, "major": 1, "minor": 2}

    def open_alarms(self) -> List[dict]:
        """Alarmes non clôturées, dédupliquées par clé, les plus graves d'abord.

        Trois corrections d'un même bug, mesuré en production : l'ancienne
        version rendait les 8 alarmes les plus RÉCENTES, et une alarme
        critique vieille de huit heures sortait de la fenêtre, chassée par
        quatre répétitions du même déclenchement de départ. L'oracle
        qualifiait alors la situation sans jamais voir la critique.

        - Le regroupement se fait sur la CONDITION (equipement + message),
          pas sur `key` : un defaut injecte recoit une cle unique par
          occurrence, si bien que le meme declenchement de Q24 apparaissait
          quatre fois. Rien n'est masque pour autant — l'occurrence la plus
          recente est conservee et `repetitions` dit combien il y en a eu.
        - L'ordre suit la gravité **déclarée par le simulateur**, puis la
          date. Présenter le monde dans l'ordre où il se déclare grave est
          de la mise en forme ; ce que l'agent en fait reste dans le `.agent`.
        """
        seen: Dict[tuple, dict] = {}
        for a in self.alarms().get("alarms", []):
            if a.get("state") == "CLEARED":
                continue
            cond = (a.get("source"), a.get("message"))
            kept = seen.get(cond)
            if kept is None or (a.get("lastAt") or a.get("at") or 0) > (kept.get("lastAt") or kept.get("at") or 0):
                merged = dict(a)
                merged["repetitions"] = (kept or {}).get("repetitions", 0) + 1
                seen[cond] = merged
            else:
                kept["repetitions"] = kept.get("repetitions", 1) + 1
        return sorted(
            seen.values(),
            key=lambda a: (self._SEVERITY_RANK.get(a.get("severity"), 9),
                           -(a.get("lastAt") or a.get("at") or 0)))

    def critical_alarms(self) -> List[dict]:
        """Alarmes critiques actuellement actives — fait jamais tronqué.

        ``open_alarms`` contient aussi les ``CLEARED_UNACK`` afin que la ronde
        puisse les acquitter. Elles ne sont toutefois plus actives et ne
        doivent donc pas contredire le compteur ``summary.alarms.critical`` du
        simulateur, qui ne porte que sur les états ``ACTIVE`` et ``ACKED``.
        """
        return [a for a in self.open_alarms()
                if a.get("severity") == "critical"
                and a.get("state") in ("ACTIVE", "ACKED")]

    def critical_without_fault(self) -> List[dict]:
        """Critiques SANS défaut équipement derrière elles.

        `faultId` vide signifie qu'aucune équipe n'est dépêchée
        automatiquement : c'est une dérive de procédé, pas une panne. Aucune
        commande de maintenance ne s'y applique — d'où l'escalade humaine.
        """
        return [a for a in self.critical_alarms() if not a.get("faultId")]

    #: Ligne dont la charge pilote l'effluent : le traitement de surface
    #: rejette ses bains de rinçage vers la STEP. C'est un fait d'installation,
    #: pas une hypothèse de l'agent.
    LIGNE_TRAITEMENT_SURFACE = "L3"

    def discharge_compliant(self) -> bool:
        """Le rejet de la STEP tient-il ses limites ?

        C'est le simulateur qui tranche (`wwtp.compliant`), en confrontant
        tous ses paramètres à leurs seuils. On ne recalcule rien ici : lire
        soi-même « la température dépasse » serait rejuger le monde.
        """
        return bool(self.water().get("wwtp", {}).get("compliant"))

    def surface_line_rate(self) -> float:
        """Cadence de la ligne de traitement de surface, en pourcent."""
        line = self.production().get("lines", {}).get(self.LIGNE_TRAITEMENT_SURFACE, {})
        return float(line.get("actual_pct") or 0.0)

    def active_alarm_text(self, limit: int = 10) -> str:
        """Libellés bruts des alarmes ouvertes, pour la qualification.

        Texte du monde, donc hostile par construction : il n'alimente qu'un
        `REASON` à domaine clos, jamais une garde de politique. La troncature
        ne peut plus faire disparaître une critique : elles sont en tête de
        l'ordre, et le compte total est rappelé en clair.
        """
        rows = self.open_alarms()
        if not rows:
            return "Aucune alarme active."
        head = rows[:limit]

        def line(a: dict) -> str:
            reps = a.get("repetitions", 1)
            return (f"[{a.get('severity')}] {a.get('domain')}/{a.get('source')} "
                    f"({a.get('id')}) — {a.get('message')} ({a.get('detail')})"
                    + ("" if a.get("faultId") else " [aucun defaut equipement associe]")
                    + (f" [x{reps} occurrences]" if reps > 1 else ""))

        text = "\n".join(line(a) for a in head)
        if len(rows) > len(head):
            text += f"\n… et {len(rows) - len(head)} autre(s) alarme(s) ouverte(s), moins graves."
        return text

    def utilities_text(self) -> str:
        """Synthèse textuelle des utilités, pour le diagnostic de goulot."""
        prod, air, gas = self.production(), self.air(), self.gas()
        net = air.get("network", {})
        summ = self.summary()
        lines = "; ".join(
            f"{k}: etat={v.get('state')} cadence={round(v.get('rate_pct', 0), 1)}% "
            f"contrainte={v.get('constraint') or 'aucune'}"
            for k, v in prod.get("lines", {}).items())
        return (
            f"Cadence moyenne {round(prod.get('avgRate_pct', 0), 1)} %, "
            f"TRS {round(prod.get('oee', 0) * 100, 1)} %, "
            f"{prod.get('linesRunning', 0)} lignes en marche.\n"
            f"Lignes — {lines}\n"
            f"Air comprime : {round(net.get('pressure_bar', 0), 2)} bar "
            f"(consigne {net.get('effective_setpoint_bar')}, mini {net.get('min_bar')}), "
            f"fuites {round(net.get('leak_pct', 0) * 100, 1)} %, "
            f"puissance {round(net.get('power_kW', 0))} kW.\n"
            f"Vapeur : {round(gas.get('steam', {}).get('pressure_bar', 0), 2)} bar "
            f"(consigne {gas.get('steam', {}).get('setpoint_bar')}), "
            f"rendement chaudieres {round(summ.get('gas', {}).get('boiler_efficiency', 0) * 100, 1)} %.\n"
            f"Electricite : {round(summ.get('electricity', {}).get('subscribed_pct', 0), 1)} % "
            f"du souscrit, tarif {summ.get('context', {}).get('tariff')}, "
            f"presence {round(summ.get('personnel', {}).get('present', 0))} personnes."
        )

    # -------------------------------------------------------------- commande
    def command(self, name: str, args: dict, justification: str,
                idem_key: str, rollback: str = "") -> dict:
        """Envoie une commande justifiée et idempotente, avec reçu détaillé."""
        self.rollback_log.append({"at": time.time(), "command": name,
                                  "args": dict(args), "rollback": rollback})
        entry = {"command": name, "args": dict(args), "justification": justification,
                 "idempotencyKey": idem_key, "dryRun": self.dry_run}
        self.agent_event(
            "REQUEST", f"Commande demandée : {name}",
            agent="VALMONT_SUPERVISION", direction="AGENT_TO_SIM",
            payload={"command": name, "args": args, "justification": justification,
                     "idempotencyKey": idem_key, "rollback": rollback,
                     "dryRun": self.dry_run}, urgent=True)
        if self.dry_run:
            entry["result"] = {"ok": True, "simulated": True}
            self.command_log.append(entry)
            self.agent_event(
                "RESPONSE", f"Commande simulée : {name}",
                agent="VALMONT_SUPERVISION", direction="INTERNAL",
                payload=entry, urgent=True)
            print(f"    [DRY-RUN] {name} {args} — {justification}")
            return entry["result"]
        try:
            result = self._request(
                "/commands",
                payload={"command": name, "args": args,
                         "idempotencyKey": idem_key, "justification": justification},
                headers={"Idempotency-Key": idem_key,
                         "X-Command-Justification": justification[:200]})
        except Exception as exc:
            self.agent_event(
                "ERROR", f"Échec de la commande {name}",
                agent="VALMONT_SUPERVISION", direction="SIM_TO_AGENT",
                detail=f"{type(exc).__name__}: {exc}",
                payload={"command": name, "args": args}, urgent=True)
            raise
        entry["result"] = result
        self.command_log.append(entry)
        self.agent_event(
            "RESPONSE", f"Résultat de la commande : {name}",
            agent="VALMONT_SUPERVISION", direction="SIM_TO_AGENT",
            payload=entry, urgent=True)
        self._cache.clear()          # le monde a bougé : re-percevoir
        print(f"    [COMMANDE] {name} {args} → {result.get('message', result)}")
        return result

    # ------------------------------------------------- catalogue du simulateur
    def catalog(self) -> dict:
        """Catalogue des commandes **publié par le simulateur**.

        Noms, rôles et schémas JSON viennent du serveur ; rien n'est recopié
        ici. Une commande ajoutée demain au simulateur apparaît d'elle-même,
        une commande retirée disparaît, et l'agent ne peut pas croire à une
        capacité qui n'existe plus.
        """
        return self.get("/commands")

    def command_spec(self, name: str) -> Optional[dict]:
        """Fiche publiée d'une commande, ou `None` si le nom est inconnu."""
        for entry in self.catalog().get("commands", []):
            if entry.get("name") == name:
                return entry
        return None

    def setpoint_names(self) -> List[str]:
        return [s.get("name") for s in self.catalog().get("setpoints", [])]

    def assets(self) -> List[dict]:
        return self.get("/assets").get("assets", [])

    def asset_ids(self) -> List[str]:
        return [str(a.get("id")) for a in self.assets() if a.get("id")]

    def fault_ids(self, status: str = "ACTIVE") -> List[str]:
        return [str(f.get("id")) for f in self.faults().get("faults", [])
                if f.get("status") == status]

    def production_line_ids(self) -> List[str]:
        return [str(k) for k in self.production().get("lines", {})]

    def alarm_by_id(self, alarm_id: str) -> Optional[dict]:
        """Alarme ouverte portant cet identifiant, si elle existe encore."""
        for row in self.open_alarms():
            if str(row.get("id")) == str(alarm_id):
                return row
        return None

    def check_against_schema(self, name: str, args: dict) -> tuple:
        """Conformité au schéma que le simulateur publie pour cette commande.

        Contrôle de **forme**, jamais de fond : il dit si l'appel serait
        recevable par l'API, pas s'il est opportun. L'opportunité se juge dans
        le `.agent`, qui est le seul fichier audité. Aucune borne n'est écrite
        ici — elles sont toutes lues dans le schéma du serveur.
        """
        spec = self.command_spec(name)
        if spec is None:
            return False, f"commande hors catalogue : {name}"
        schema = spec.get("schema") or {}
        props = schema.get("properties") or {}
        for key in schema.get("required") or []:
            if key not in args:
                return False, f"argument obligatoire manquant : {key}"
        if schema.get("additionalProperties") is False:
            for key in args:
                if key not in props:
                    return False, f"argument inconnu : {key}"
        for key, value in args.items():
            rule = props.get(key)
            if not isinstance(rule, dict):
                continue
            kind = rule.get("type")
            if kind in ("number", "integer"):
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    return False, f"{key} n'est pas un nombre : {value!r}"
                if kind == "integer" and number != int(number):
                    return False, f"{key} n'est pas un entier : {value!r}"
                low, high = rule.get("minimum"), rule.get("maximum")
                if low is not None and number < float(low):
                    return False, f"{key} = {number} sous le minimum {low}"
                if high is not None and number > float(high):
                    return False, f"{key} = {number} au-dela du maximum {high}"
                continue
            if kind == "boolean" and not isinstance(value, bool):
                return False, f"{key} n'est pas un booleen : {value!r}"
            if kind == "string":
                text = str(value)
                if len(text) < int(rule.get("minLength", 0) or 0):
                    return False, f"{key} est vide"
                if "maxLength" in rule and len(text) > int(rule["maxLength"]):
                    return False, f"{key} depasse la longueur publiee"
                allowed = rule.get("enum")
                if allowed and text not in allowed:
                    return False, f"{key} hors des valeurs publiees : {text}"
        return True, "conforme au schema publie"

    # --------------------------------------------------------------- dialogue
    def chat(self, message: str) -> str:
        """Adresse un message au responsable de site simulé et rend sa réponse.

        Le chatbot d'ENTERPRISE-SIM est consultatif : il n'exécute jamais de
        commande. Sa réponse est du texte non fiable, traitée comme telle.
        """
        return str(self.chat_full(message).get("reply", ""))

    def chat_full(self, message: str) -> dict:
        """La réponse du responsable de site, **et les commandes qu'il émet**.

        Le chatbot d'ENTERPRISE-SIM produit un objet
        `{reply, commands:[{command, args}], refused}` : le serveur tourne
        avec `chat.allowCommands = false`, il n'exécute donc RIEN lui-même et
        renvoie les commandes telles quelles, à charge pour un agent externe
        de les passer par `POST /commands`.

        `chat()` n'en gardait que `reply`, et jetait le reste. C'est ce champ
        jeté qui porte la conduite demandée au responsable : on le rend ici
        tel quel, sans le trier ni le juger — le tri appartient au `.agent`.
        """
        self.agent_event(
            "DIALOGUE", message, agent="VALMONT_SUPERVISION",
            direction="AGENT_TO_HUMAN", payload={"message": message},
            urgent=True)
        try:
            out = self._request("/chat", payload={"message": message},
                                timeout=self.chat_timeout)
        except Exception as exc:
            self.agent_event(
                "ERROR", "Dialogue avec le responsable indisponible",
                agent="VALMONT_SUPERVISION", direction="HUMAN_TO_AGENT",
                detail=f"{type(exc).__name__}: {exc}", urgent=True)
            raise
        commands = []
        for item in out.get("commands") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("command") or "").strip()
            if not name:
                continue
            args = item.get("args")
            commands.append({"command": name,
                             "args": args if isinstance(args, dict) else {}})
        normalized = {"reply": str(out.get("reply", "")), "commands": commands,
                      "refused": out.get("refused") or []}
        self.agent_event(
            "DIALOGUE", normalized["reply"] or "Réponse sans texte",
            agent="VALMONT_SUPERVISION", direction="HUMAN_TO_AGENT",
            payload=normalized, urgent=True)
        return normalized

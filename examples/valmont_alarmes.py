"""Hôte du sous-agent VALMONT_ALARMES.

Perception seule : ce module lit le journal d'alarmes, de défauts et la
qualité de l'instrumentation d'ENTERPRISE-SIM, puis enregistre le verdict
rendu par le programme. **Aucune ligne ne décide** : ni ce qui est grave, ni
ce qui mérite une intervention, ni ce qu'on écarte. Ces jugements sont dans
`valmont_alarmes.agent`, et eux seuls sont audités.

    python3 -m agentl run examples/valmont_alarmes.agent --html /tmp/alarmes.html

Le sous-agent est aussi exécutable et vérifiable seul — c'est la norme
X.agent ↔ X.py, sans exception pour un agent délégué.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agentl import Host, MockLLM, Symbol

from valmont_sim import (SimClient, SimUnavailable,
                         external_llm_consent, gemini_key)

PERCEPTION_TTL_S = 1.5


def build():
    client = SimClient()

    # État propre à l'agent : le verdict qu'il a rendu. Il est RELU par des
    # capteurs, sans quoi les EFFECT des outils seraient irréfutables (T9).
    state = {"status": Symbol("attente"), "verdict": Symbol("indetermine")}
    view = {"at": 0.0, "data": None}

    def perceive() -> dict:
        """Une passe de lecture guardée. La panne est un FAIT, pas une exception."""
        now = time.monotonic()
        if view["data"] is not None and now - view["at"] < PERCEPTION_TTL_S:
            return view["data"]
        try:
            summary = client.summary()
            alarms = summary.get("alarms", {})
            data = {
                "read_ok": "yes",
                "actives": int(alarms.get("active", 0)),
                "critiques": int(alarms.get("critical", 0)),
                "majeures": int(alarms.get("major", 0)),
                "non_acquittees": int(alarms.get("unacknowledged", 0)),
                "pire": str(alarms.get("worst") or "aucune"),
                "libelles": client.active_alarm_text(),
                # Fait DÉTERMINISTE, jamais tronqué : une critique sans
                # `faultId` n'appelle aucune commande de maintenance. Le
                # texte ci-dessus sert à qualifier, ce booléen à décider —
                # et c'est bien ce booléen que gardent les interdits.
                "critique_sans_defaut": "yes" if client.critical_without_fault() else "no",
                # Défauts EN ATTENTE d'intervention (statut ACTIVE) : ceux que
                # les équipes postées traitent déjà n'en appellent pas une autre.
                "defauts": client.faults_awaiting_repair(),
                "douteuses": client.doubtful_points(),
            }
        except SimUnavailable as exc:
            print(f"    ⚠  ENTERPRISE-SIM injoignable : {exc}")
            # Fait distinct : `read_ok = no`. Les compteurs retombent à zéro,
            # et c'est précisément pourquoi la politique interdit toute action
            # sur un compteur nul (principe « indéterminé n'est pas zéro »).
            data = {"read_ok": "no", "actives": 0, "critiques": 0, "majeures": 0,
                    "non_acquittees": 0, "pire": "aucune", "libelles": "",
                    "defauts": 0, "douteuses": 0, "critique_sans_defaut": "no"}
        view["at"], view["data"] = now, data
        return data

    host = Host()
    trace_sink = getattr(client, "trace_sink", None)
    if trace_sink is not None:
        host.trace_sink = trace_sink("VALMONT_ALARMES")
    host.sensors.update({
        "alarmes.read_ok":        lambda: Symbol(perceive()["read_ok"]),
        "alarmes.actives":        lambda: perceive()["actives"],
        "alarmes.critiques":      lambda: perceive()["critiques"],
        "alarmes.majeures":       lambda: perceive()["majeures"],
        "alarmes.non_acquittees": lambda: perceive()["non_acquittees"],
        "alarmes.pire":           lambda: Symbol(perceive()["pire"]),
        "alarmes.libelles":       lambda: perceive()["libelles"],
        "defauts.ouverts":        lambda: perceive()["defauts"],
        "mesures.douteuses":      lambda: perceive()["douteuses"],
        "alarmes.critique_sans_defaut": lambda: Symbol(perceive()["critique_sans_defaut"]),
        "diagnostic.status":      lambda: state["status"],
        "diagnostic.verdict":     lambda: state["verdict"],
    })

    def _rendre(verdict: str) -> dict:
        state["status"] = Symbol("rendu")
        state["verdict"] = Symbol(verdict)
        print(f"    ↳ [ALARMES] verdict = {verdict}")
        return {"verdict": Symbol(verdict)}

    host.tools["exiger_intervention"] = \
        lambda classe, defauts: _rendre("intervention")
    host.tools["exiger_acquittement"] = \
        lambda classe, alarmes: _rendre("acquittement")
    host.tools["recommander_surveillance"] = \
        lambda classe, actives: _rendre("surveillance")
    host.tools["exiger_escalade_humaine"] = \
        lambda classe, critiques: _rendre("escalade_humaine")
    host.tools["signaler_perception_incomplete"] = \
        lambda: _rendre("indetermine")

    return host, _make_llm()


def _make_llm():
    """Oracle réel si le site l'autorise et qu'une clé existe, local sinon."""
    fallback = MockLLM({"Qualifier": {"classe": "utilite_indisponible",
                                      "urgence": "planifiable"}})
    if os.environ.get("VALMONT_MOCK_LLM", "").strip() not in ("", "0"):
        return fallback
    if not external_llm_consent():
        print("    · oracle externe non autorise (VALMONT_LLM_EXTERNE) "
              "— repli deterministe local, aucune donnee ne quitte le site")
        return fallback
    try:
        from gemini_llm import GeminiLLM
        key = gemini_key()
        if key:
            return GeminiLLM(os.environ.get("VALMONT_MODEL",
                                            "gemini-3.1-flash-lite"), key)
    except Exception as exc:                       # pragma: no cover
        print(f"    ⚠  oracle Gemini indisponible ({exc}) — repli deterministe")
    return fallback

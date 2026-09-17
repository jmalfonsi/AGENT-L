"""Hôte du sous-agent VALMONT_PRODUCTION.

Perception seule : lit les lignes de production et l'état des utilités
d'ENTERPRISE-SIM, puis enregistre le conseil rendu par le programme.
Les seuls calculs présents ici sont des **différences et des ratios entre
grandeurs mesurées** — de la mise en forme de capteur, pas du jugement. Ce
qui compte comme « bridé », « trop cher » ou « à corriger » est écrit dans
`valmont_production.agent`, et nulle part ailleurs.

    python3 -m agentl run examples/valmont_production.agent --html /tmp/prod.html
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
    state = {"status": Symbol("attente"), "conseil": Symbol("indetermine")}
    view = {"at": 0.0, "data": None}

    def perceive() -> dict:
        now = time.monotonic()
        if view["data"] is not None and now - view["at"] < PERCEPTION_TTL_S:
            return view["data"]
        try:
            summary = client.summary()
            prod = client.production()
            net = client.air().get("network", {})
            elec = client.electricity()
            energy = client.energy()

            budget = float(energy.get("budget_eur_day") or 0)
            # Projection du coût de la journée au rythme courant. C'est une
            # règle de trois sur deux mesures, pas un avis sur le budget.
            projected = float(energy.get("cost", {}).get("rate_eur_h", 0)) * 24.0
            overrun = ((projected / budget) - 1.0) * 100.0 if budget > 0 else 0.0

            data = {
                "read_ok": "yes",
                "cadence": float(prod.get("avgRate_pct", 0)),
                "trs": float(prod.get("oee", 0)),
                # `constraint` est publié par le simulateur ligne par ligne :
                # on compte, on ne qualifie pas.
                "lignes_bridees": sum(1 for l in prod.get("lines", {}).values()
                                      if l.get("constraint")),
                "marge_bar": float(net.get("pressure_bar", 0)) - float(net.get("min_bar", 0)),
                "fuite_pct": float(net.get("leak_pct", 0)) * 100.0,
                "vapeur_bar": float(summary.get("gas", {}).get("steam_bar", 0)),
                "souscrit_pct": float(summary.get("electricity", {}).get("subscribed_pct", 0)),
                "delestage": int(elec.get("loadShed", {}).get("level", 0)),
                "depassement_pct": overrun,
                "synthese": client.utilities_text(),
            }
        except SimUnavailable as exc:
            print(f"    ⚠  ENTERPRISE-SIM injoignable : {exc}")
            data = {"read_ok": "no", "cadence": 0.0, "trs": 0.0, "lignes_bridees": 0,
                    "marge_bar": 0.0, "fuite_pct": 0.0, "vapeur_bar": 0.0,
                    "souscrit_pct": 0.0, "delestage": 0, "depassement_pct": 0.0,
                    "synthese": ""}
        view["at"], view["data"] = now, data
        return data

    host = Host()
    trace_sink = getattr(client, "trace_sink", None)
    if trace_sink is not None:
        host.trace_sink = trace_sink("VALMONT_PRODUCTION")
    host.sensors.update({
        "usine.read_ok":           lambda: Symbol(perceive()["read_ok"]),
        "prod.cadence_pct":        lambda: perceive()["cadence"],
        "prod.trs":                lambda: perceive()["trs"],
        "prod.lignes_bridees":     lambda: perceive()["lignes_bridees"],
        "air.marge_bar":           lambda: perceive()["marge_bar"],
        "air.fuite_pct":           lambda: perceive()["fuite_pct"],
        "vapeur.pression_bar":     lambda: perceive()["vapeur_bar"],
        "elec.souscrit_pct":       lambda: perceive()["souscrit_pct"],
        "elec.delestage":          lambda: perceive()["delestage"],
        "energie.depassement_pct": lambda: perceive()["depassement_pct"],
        "usine.synthese":          lambda: perceive()["synthese"],
        "optim.status":            lambda: state["status"],
        "optim.conseil":           lambda: state["conseil"],
    })

    def _rendre(conseil: str) -> dict:
        state["status"] = Symbol("rendu")
        state["conseil"] = Symbol(conseil)
        print(f"    ↳ [PRODUCTION] conseil = {conseil}")
        return {"conseil": Symbol(conseil)}

    host.tools["conseiller_relance"] = lambda goulot, cadence: _rendre("relancer")
    host.tools["conseiller_bridage"] = lambda goulot, trs: _rendre("brider")
    host.tools["conseiller_chasse_fuites"] = lambda fuite: _rendre("chasse_fuites")
    host.tools["conseiller_statu_quo"] = lambda goulot: _rendre("maintenir")
    host.tools["signaler_perception_incomplete"] = lambda: _rendre("indetermine")

    return host, _make_llm()


def _make_llm():
    fallback = MockLLM({"Identifier": {"goulot": "air_comprime",
                                       "tendance": "stable"}})
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

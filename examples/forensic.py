"""Hôte du SPÉCIALISTE `forensic.agent` — norme de nommage X.agent ↔ X.py.

Un agent délégué reste un agent : il porte son propre hôte, sous son propre
nom, et se lance seul comme n'importe quel autre.

    python3 -m agentl run      examples/forensic.agent
    python3 -m agentl verify   examples/forensic.agent
    python3 -m agentl boundary examples/forensic.agent

C'est ce qui donne son sens à « chaque étage se vérifie seul » : sans hôte
propre, le spécialiste n'était exécutable qu'à travers son superviseur, et
`agentl boundary` ne pouvait rien contrôler de sa frontière (`B000`).

`supervisor.py` importe `build()` d'ici pour câbler son `DELEGATE` — le
superviseur n'a donc plus de copie de la perception du spécialiste, et les
deux étages ne peuvent plus diverger.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agentl import Host, MockLLM, Symbol

# Deux jeux d'indicateurs réels que le spécialiste perçoit. Le superviseur ne
# les connaît pas : ils vivent entièrement ici.
_SCENARIOS = {
    "malicious": {"spf": "fail", "url": "bad", "attachment": "high", "age": 2.0},
    "benign":    {"spf": "pass", "url": "good", "attachment": "low", "age": 900.0},
}


def build():
    """Contrat de la CLI `agentl run` : `build() -> (host, llm)`."""
    # BOUNDARY-OK: sélection du jeu d'indicateurs à PERCEVOIR (variable
    # d'environnement du banc), pas un critère métier — c'est le monde qu'on
    # branche, pas une décision sur ce qu'il contient.
    ind = _SCENARIOS.get(os.environ.get("MAIL_SCENARIO", "malicious"),
                         _SCENARIOS["malicious"])

    h = Host()
    h.sensors["mail.spf_result"] = lambda: Symbol(ind["spf"])
    h.sensors["mail.url_reputation"] = lambda: Symbol(ind["url"])
    h.sensors["mail.attachment_risk"] = lambda: Symbol(ind["attachment"])
    h.sensors["mail.sender_age_days"] = lambda: ind["age"]

    h.tools["deep_scan"] = lambda: {"verdict_ready": Symbol("yes")}
    h.tools["record_verdict"] = lambda class_=None, **kw: {"ok": Symbol("yes")}

    # Aucun LLM : le verdict est un postérieur CALCULÉ, donc le run est
    # reproductible et sans réseau.
    return h, MockLLM()

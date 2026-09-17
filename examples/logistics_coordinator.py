from __future__ import annotations
import sys
from pathlib import Path

# Résolution du PYTHONPATH pour trouver agentl
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentl import Host, MockLLM, Symbol

def build():
    host = Host()
    
    # État du monde simulé (dans la vraie vie, ce serait des appels API ou bases de données)
    state = {
        "handled": Symbol("no")
    }

    # Les capteurs renvoient des FAITS bruts. Aucune qualification métier.
    host.sensors.update({
        "source.read_ok": lambda: Symbol("yes"),
        "truck.id": lambda: "TRK-042",
        "truck.status": lambda: Symbol("broken"),  # Le statut mécanique brut
        "report.text": lambda: "The engine exploded on the highway.",
        "delivery.handled": lambda: state["handled"],
    })

    # Outil 1 : Action légère
    def reroute_truck(read_ok, truck_id):
        # BOUNDARY-OK: Simple affectation d'état, aucune condition métier ici
        state["handled"] = Symbol("yes")
        return {"rerouted": Symbol("yes")}

    # Outil 2 : Action critique
    def cancel_delivery(read_ok, truck_id, issue_token):
        # BOUNDARY-OK: Simple affectation d'état, l'agent a déjà pris la décision
        state["handled"] = Symbol("yes")
        return {"cancelled": Symbol("yes")}

    # Approbateur humain simulé pour le test
    def approver(request):
        # En production, ce serait un prompt interactif ou une API Slack
        return True

    host.tools["reroute_truck"] = reroute_truck
    host.tools["cancel_delivery"] = cancel_delivery
    host.approver = approver

    # Oracle déterministe pour tester la capacité du LLM à extraire l'entité
    llm = MockLLM({
        "Engine exploded": {
            "issue_type": "breakdown"
        }
    })

    return host, llm
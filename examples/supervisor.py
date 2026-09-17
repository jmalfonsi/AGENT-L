"""Hôte du SUPERVISEUR — démontre un DELEGATE vers un agent AGENT-L imbriqué.

Le sous-agent `forensic` n'est pas une simple fonction : c'est un **runtime
AGENT-L complet** exécutant `forensic.agent`, avec sa propre perception, sa
propre inférence calibrée et sa propre politique. Le superviseur ne voit que
le verdict borné par le contrat EXPECT { malicious, threat_class,
forensic_confidence } — jamais l'état interne du spécialiste.

Composition auditable : chacun des deux fichiers .agent passe `agentl verify`
séparément, et la frontière entre eux est le contrat DELEGATE/EXPECT.

    # cas malveillant (par défaut) → le superviseur met en quarantaine :
    python3 -m agentl run examples/supervisor.agent \
        --html /tmp/supervisor.html

    # cas bénin → le superviseur clôt l'incident :
    MAIL_SCENARIO=benign python3 -m agentl run examples/supervisor.agent \
       

    # boîte critique → l'humain valide la quarantaine :
    MAILBOX_CRITICALITY=CRITICAL python3 -m agentl run examples/supervisor.agent \
       
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agentl import Host, MockLLM, Runtime, Symbol, parse_file

# Le spécialiste porte son propre hôte, sous son propre nom (norme X.agent ↔
# X.py). Le superviseur ne redéclare donc PAS sa perception : il l'importe.
# Les deux étages ne peuvent plus diverger, et `forensic.agent` reste
# exécutable et vérifiable seul.
import forensic as forensic_host

HERE = Path(__file__).resolve().parent


def _run_forensic(payload: dict) -> dict:
    """Cible du DELEGATE : exécute l'agent imbriqué et renvoie SON verdict.

    C'est ici que se fait la composition : on instancie un Runtime sur
    forensic.agent, on lui donne un hôte qui perçoit les indicateurs, on le
    laisse tourner sa propre boucle, puis on marshale son résultat borné.
    """
    sub, sub_llm = forensic_host.build()

    forensic = parse_file(str(HERE / "forensic.agent")).agents[0]
    rt = Runtime(forensic, sub, sub_llm).run()

    # On ne remonte que le contrat EXPECT — le postérieur CALCULÉ par le
    # spécialiste, pas une opinion, et jamais son état interne.
    inf = rt.inferences.get("phishing")
    posterior = inf.posterior if inf else 0.0
    malicious = "yes" if (inf and inf.supported) else "no"
    tc = rt.state.beliefs.get("threat_class")
    threat_class = tc.value if tc else Symbol("unknown")

    print(f"    ↳ [MAIL_FORENSIC] P(phishing)={posterior:.3f} "
          f"→ malicious={malicious}, classe={threat_class}")
    return {"malicious": Symbol(malicious),
            "threat_class": threat_class,
            "forensic_confidence": round(posterior, 3)}


def build():
    criticality = os.environ.get("MAILBOX_CRITICALITY", "normal")

    h = Host()
    h.sensors["mail.reported"] = lambda: Symbol("yes")
    h.sensors["mailbox.criticality"] = lambda: Symbol(criticality)

    def quarantine_mail(class_=None, **kw):
        print(f"\n  🧹 E-mail mis en quarantaine (classe={class_ or kw})\n")
        return {"moved": Symbol("yes")}

    def close_incident():
        print("\n  ✅ Incident clos : e-mail jugé bénin\n")
        return {"closed": Symbol("yes")}

    def notify_operator(message):
        print(f"\n  📣 OPÉRATEUR ← {message}\n")
        return {"delivered": Symbol("yes")}

    h.tools["quarantine_mail"] = quarantine_mail
    h.tools["close_incident"] = close_incident
    h.tools["notify_operator"] = notify_operator

    # LE point de la démo : le sous-agent est un runtime AGENT-L imbriqué.
    h.subagents["forensic"] = _run_forensic

    def approver(request):
        print(f"\n  🖐  APPROBATION (boîte critique) : {request.render()}"
              f"  → accordée\n")
        return True
    h.approver = approver

    # Le signalement réveille le superviseur au premier tick.
    h.emit("mail.report", severity=Symbol("HIGH"), origin=Symbol("user_report"))

    return h, MockLLM()

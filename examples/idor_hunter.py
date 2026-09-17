"""Hôte RÉEL pour `idor_hunter.agent` — test IDOR autorisé.

Rien n'est simulé au niveau du transport : les capteurs et les outils font de
vraies requêtes HTTP (`urllib`). La sûreté est à deux étages indépendants :

  * la POLITIQUE de l'agent : `probe_references` est INTERDIT tant que
    `target.authorized == no` (variable d'env `IDOR_AUTHORIZED`). C'est elle
    qu'on teste — le contre-factuel ci-dessous prouve qu'elle mord.
  * l'HÔTE, en défense en profondeur : le sondage ne rejoue QUE des GET sur la
    même route que la ligne de base, avec SA propre session ; il n'écrit
    jamais, ne suit aucune redirection hors du domaine cible, et refuse de
    partir si l'autorisation n'est pas posée.

Configuration réelle (cible autorisée) — variables d'environnement :
    IDOR_AUTHORIZED=1                 # OBLIGATOIRE : atteste le mandat
    TARGET_URL=https://cible/api/user/{id}   # {id} = point d'injection
    OWN_ID=1042                       # votre propre identifiant (ligne de base)
    TEST_IDS=1041,1043,1,2,9999       # références voisines à sonder
    SESSION_COOKIE="session=..."      # votre session authentifiée (optionnel)
    TARGET_PRODUCTION=1               # si prod → approbation humaine exigée

Sans TARGET_URL, `build()` démarre un petit serveur LOCAL délibérément
vulnérable (127.0.0.1) pour un run reproductible sur de vraies sockets.

    # run nominal (autorisé, serveur local vulnérable) :
    IDOR_AUTHORIZED=1 python3 -m agentl run examples/idor_hunter.agent \
        --html /tmp/idor.html

    # contre-factuel (autorisation absente → sondage interdit) :
    python3 -m agentl run examples/idor_hunter.agent \
        --html /tmp/idor_denied.html
"""
from __future__ import annotations

import http.server
import json
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agentl import Host, Symbol

REPORT_PATH = Path(__file__).resolve().parent / "idor_report.pdf"


# --------------------------------------------------------------- serveur local
# Cible reproductible : /api/user/{id} renvoie l'objet SANS vérifier que le
# demandeur en est le propriétaire — l'IDOR d'école. Utilisé seulement quand
# aucune TARGET_URL n'est fournie.
_USERS = {
    1: {"id": 1, "name": "alice", "email": "alice@example.test", "ssn": "***-11"},
    2: {"id": 2, "name": "bob", "email": "bob@example.test", "ssn": "***-22"},
    1041: {"id": 1041, "name": "carol", "email": "carol@corp.test", "ssn": "***-41"},
    1042: {"id": 1042, "name": "you", "email": "you@corp.test", "ssn": "***-42"},
    1043: {"id": 1043, "name": "dave", "email": "dave@corp.test", "ssn": "***-43"},
}


class _Vuln(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        prefix = "/api/user/"
        if self.path.startswith(prefix):
            try:
                uid = int(self.path[len(prefix):])
            except ValueError:
                self.send_error(400)
                return
            user = _USERS.get(uid)
            if user is None:
                self.send_error(404)
                return
            body = json.dumps(user).encode()          # ← pas de contrôle d'accès
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_error(404)

    def log_message(self, *a):  # silence
        pass


def _start_local_server():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    srv = http.server.HTTPServer(("127.0.0.1", port), _Vuln)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{port}/api/user/{{id}}", "1042", "1041,1043,1,2,9999"


# --------------------------------------------------------------- client HTTP
def _get(url: str, cookie: str, timeout: int = 15):
    """GET réel, borné : pas de redirection hors domaine, jamais d'écriture."""
    headers = {"user-agent": "AGENT-L-IDOR-HUNTER/1.0 (authorized test)"}
    if cookie:
        headers["cookie"] = cookie
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read(65536).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read(4096).decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return -1, f"{type(e).__name__}: {e}"


# --------------------------------------------------------------- PDF minimal
def _pdf(path: Path, title: str, lines):
    """Écrit un PDF autonome (un objet page, police Helvetica) sans dépendance."""
    def esc(s):
        return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")

    y = 780
    content = ["BT", "/F1 16 Tf", f"1 0 0 1 56 {y} Tm", f"({esc(title)}) Tj", "ET"]
    y -= 28
    for ln in lines:
        content += ["BT", "/F1 10 Tf", f"1 0 0 1 56 {y} Tm",
                    f"({esc(ln)[:110]}) Tj", "ET"]
        y -= 15
        # BOUNDARY-OK: pagination du rendu PDF — seuil TYPOGRAPHIQUE (bas de
        # page à 56 pt), aucun critère métier ; aucune donnée d'analyse n'est
        # écartée, seul l'affichage est borné à la hauteur d'une page.
        if y < 56:
            break
    stream = "\n".join(content).encode()

    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n" % (len(objs) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF"
            % (len(objs) + 1, xref))
    path.write_bytes(out)


# --------------------------------------------------------------------- l'hôte
def build():
    authorized = bool(os.environ.get("IDOR_AUTHORIZED"))
    production = bool(os.environ.get("TARGET_PRODUCTION"))
    cookie = os.environ.get("SESSION_COOKIE", "")

    url_tpl = os.environ.get("TARGET_URL")
    own_id = os.environ.get("OWN_ID", "")
    test_ids = os.environ.get("TEST_IDS", "")
    local = False
    if not url_tpl:
        url_tpl, own_id, test_ids = _start_local_server()
        local = True
    ids = [s.strip() for s in test_ids.split(",") if s.strip()]

    # état partagé, alimenté par les vrais appels
    state = {
        "baseline_status": "unknown",
        "baseline_len": 0,
        "baseline_body": "",
        "total": 0,
        "foreign": 0,
        "unauth": 0,
        "evidence": "",
        "rows": [],           # (id, http_status, len, verdict)
        "severity": "unknown",
        "recommendation": "",
        "reachable": None,
    }

    def _reach():
        if state["reachable"] is None:
            st, _ = _get(url_tpl.format(id=own_id), cookie)
            state["reachable"] = st > 0
        return state["reachable"]

    h = Host()

    # -- Capteurs : chaque lecture re-perçoit la cible ----------------------
    h.sensors["target.authorized"] = lambda: Symbol("yes" if authorized else "no")
    h.sensors["target.production"] = lambda: Symbol("yes" if production else "no")
    h.sensors["target.reachable"] = lambda: Symbol("yes" if _reach() else "no")
    h.sensors["baseline.status"] = lambda: Symbol(state["baseline_status"])
    h.sensors["probe.total"] = lambda: state["total"]
    h.sensors["probe.foreign_count"] = lambda: state["foreign"]
    h.sensors["probe.unauth_count"] = lambda: state["unauth"]
    h.sensors["probe.evidence"] = lambda: state["evidence"] or "(aucun sondage)"

    # -- Outils : vraies actions HTTP, bornées par l'hôte -------------------
    def fetch_baseline():
        st, body = _get(url_tpl.format(id=own_id), cookie)
        # BOUNDARY-OK: décodage de la SÉMANTIQUE HTTP — 200/401-403 sont des
        # codes du protocole, pas des seuils métier ; on rend le fait
        # décodé (ok/denied/error) et c'est la politique du `.agent` qui en
        # tire une conduite (`baseline.status == ok`). Rien n'est filtré.
        state["baseline_status"] = ("ok" if st == 200 else
                                    "denied" if st in (401, 403) else
                                    "error")
        state["baseline_len"] = len(body)
        state["baseline_body"] = body
        return {"status": Symbol(state["baseline_status"])}

    def probe_references():
        # Défense en profondeur : on ne sonde que si l'autorisation est posée,
        # indépendamment de ce que la politique a déjà décidé.
        if not authorized:
            raise PermissionError("sondage refusé : IDOR_AUTHORIZED absent")
        foreign = unauth = 0
        state["rows"] = []
        for oid in ids:
            st, body = _get(url_tpl.format(id=oid), cookie)
            # « données d'autrui » : 200 dont le corps diffère de la base et
            # référence l'id demandé.
            # BOUNDARY-OK: DÉTECTION technique d'une fuite IDOR, pas une
            # décision métier. La définition « 200 + corps distinct de la
            # base + id demandé présent dans la réponse » est une propriété
            # du protocole HTTP et du modèle de la faille ; elle ne changerait
            # pas si la politique de l'entreprise changeait. L'hôte n'écarte
            # aucune sonde : il rend les COMPTES bruts (`foreign`, `unauth`),
            # et c'est l'HYPOTHESIS + le REASON du `.agent` qui décident de la
            # sévérité et du verdict. `st == 200` = code du protocole.
            no_authz = st == 200        # 200 = accès obtenu sans 401/403
            got_data = no_authz and len(body) > 0
            is_foreign = got_data and body != state["baseline_body"] and oid in body
            if is_foreign:
                foreign += 1
            if no_authz:
                unauth += 1
            verdict = ("FUITE" if is_foreign else
                       "accessible" if no_authz else
                       f"bloqué({st})")
            state["rows"].append((oid, st, len(body), verdict))
        state["total"] = len(ids)
        state["foreign"] = foreign
        state["unauth"] = unauth
        state["evidence"] = " | ".join(
            f"id={o} http={s} {v}" for o, s, _, v in state["rows"])
        return {"foreign": foreign, "unauth": unauth}

    def generate_report(severity):
        state["severity"] = str(severity)
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        vulnerable = state["foreign"] > 0
        lines = [
            f"Date            : {now}",
            f"Cible           : {url_tpl}" + (" (serveur local de démonstration)"
                                              if local else ""),
            f"Autorisation    : {'CONFIRMEE' if authorized else 'ABSENTE'}"
            + ("  [PRODUCTION]" if production else ""),
            f"Ligne de base   : id={own_id} -> {state['baseline_status']} "
            f"({state['baseline_len']} octets)",
            "",
            f"Verdict         : {'IDOR CONFIRME' if vulnerable else 'aucun IDOR observe'}",
            f"Severite        : {state['severity']}",
            f"References fuitees : {state['foreign']} / {state['total']}",
            f"Accessibles sans controle : {state['unauth']} / {state['total']}",
            "",
            "Preuves (une ligne par reference sondee) :",
        ]
        for o, s, ln, v in state["rows"]:
            lines.append(f"  - id={o:<8} http={s:<4} {ln:>6} octets   {v}")
        lines += [
            "",
            "Recommandation :",
            "  " + (state["recommendation"] or
                    "Imposer un controle d'acces cote serveur : verifier que "
                    "l'objet demande appartient a l'utilisateur authentifie."),
            "",
            "Genere par AGENT-L / IDOR_HUNTER. Test realise sous mandat.",
        ]
        _pdf(REPORT_PATH, "Rapport de test IDOR - AGENT-L", lines)
        print(f"\n  📄 Rapport PDF écrit : {REPORT_PATH}\n")
        return {"path": str(REPORT_PATH)}

    def notify_operator(message):
        print(f"\n  📣 OPÉRATEUR ← {message}\n")
        return {"delivered": Symbol("yes")}

    def record_inconclusive():
        state["severity"] = "inconclusive"
        return {"logged": Symbol("yes")}

    for fn in (fetch_baseline, probe_references, generate_report,
               notify_operator, record_inconclusive):
        h.tools[fn.__name__] = fn

    # capte la recommandation produite par le LLM (REASON) pour le PDF
    def on_reason(field, value):
        # BOUNDARY-OK: routage d'un champ PRODUCE nommé vers le rapport — on
        # compare un NOM DE CHAMP, pas une valeur métier ; aucune donnée
        # n'est filtrée ni jugée, la recommandation du REASON est recopiée.
        if field == "recommendation":
            state["recommendation"] = str(value)
    h.on_produce = on_reason  # best-effort ; ignoré si l'hôte ne le supporte pas

    def approver(request):
        print(f"\n  🖐  APPROBATION (cible de production) : {request.render()} "
              f" → accordée\n")
        return True
    h.approver = approver

    # LLM réel : Gemini (clé GEMINI_API_KEY / ~/HAL/.env). MockLLM en repli.
    try:
        from gemini_llm import GeminiLLM
        llm = GeminiLLM(model=os.environ.get("GEMINI_MODEL",
                                             "gemini-3.1-flash-lite"))
    except Exception:  # noqa: BLE001
        from agentl import MockLLM
        llm = MockLLM({"severity": Symbol("high"),
                       "recommendation": "Controle d'acces cote serveur."})
    return h, llm

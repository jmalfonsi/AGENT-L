"""Hôte de `meridian_player.agent` — le plan /v1 de Meridian Arena, en réel.

RÈGLE DE PARTAGE : l'hôte fournit des FAITS, le `.agent` prend les DÉCISIONS.

Faits fournis ici :
  - la joignabilité de la plateforme, l'état du run (ouvert, soumis) ;
  - la mission telle que la plateforme la rend, les ressources autorisées,
    et le catalogue d'outils du scénario tiré — tel quel, sans tri ;
  - le budget d'actions restant et le nombre de lectures abouties ;
  - la trace de ce qui a déjà été tenté dans ce run.

Décisions laissées au `.agent` : quel outil appeler, avec quels arguments,
quand écrire, quand s'arrêter, et à partir de combien de lectures une écriture
devient permise.

Défense en profondeur : `call_read_tool` refuse un outil que la plateforme
déclare modifiant, `call_write_tool` refuse l'inverse, et les deux refusent un
nom absent du catalogue perçu. Ce n'est pas la garantie — elle est dans la
POLICY — c'est la ceinture sous les bretelles.

    MERIDIAN_KEY=mrd_test_… python3 -m agentl run examples/meridian_player.agent
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agentl import Host, Symbol  # noqa: E402
from gemini_llm import GeminiLLM  # noqa: E402

# Les données d'un scénario — factures, tickets, dossiers du personnel — sont
# montrées au modèle, qui vit chez un tiers. Même simulées, elles sortent de la
# machine : l'envoi est un opt-in explicite de l'exploitant, jamais un défaut.
ALLOW_EXTERNAL_LLM = os.environ.get("MERIDIAN_ALLOW_EXTERNAL_LLM") == "1"

BASE = os.environ.get("MERIDIAN_URL", "http://localhost:4600/v1").rstrip("/")
KEY = os.environ.get("MERIDIAN_KEY", "")
BENCHMARK = os.environ.get("MERIDIAN_BENCHMARK", "enterprise-bench")
# S'entraîner sur un cas précis (clé de développement uniquement). Un défaut se
# corrige en rejouant LE MÊME cas ; laissés vides, la plateforme tire au sort.
SCENARIO = os.environ.get("MERIDIAN_SCENARIO", "")
VARIANT = os.environ.get("MERIDIAN_VARIANT", "")
TIMEOUT = 30


def _call(method: str, path: str, body=None):
    """Un appel au plan /v1. Rend (code, objet). Ne lève jamais : une panne
    réseau est un FAIT que le programme doit pouvoir lire, pas une exception
    qui décide à sa place."""
    req = urllib.request.Request(
        BASE + path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {KEY}", "content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
            return res.status, json.loads(res.read() or b"{}")
    except urllib.error.HTTPError as err:
        try:
            return err.code, json.loads(err.read() or b"{}")
        except Exception:
            return err.code, {}
    except Exception as err:                      # réseau, DNS, délai
        return 0, {"error": "unreachable", "message": str(err)}


def build():
    # Tout l'état perçu du run vit ici ; chaque capteur le relit.
    st = {
        "run_id": None, "open": False, "submitted": False,
        "read_ok": False, "reads": 0, "wrote": False, "closed": False,
        # Trois COMPTES, pas trois jugements : ce que le programme en déduit
        # (« trop », « rien tenté ») lui appartient entièrement.
        "read_calls": 0, "write_calls": 0, "last_sig": None, "streak": 0,
        "task": {}, "resources": [], "tools": [], "history": [],
        "actions": 0, "budget": 0, "score": None, "reachable": None,
    }

    def reachable() -> bool:
        if st["reachable"] is None:
            code, _ = _call("GET", "/benchmarks")
            # BOUNDARY-OK: 200 est la réussite au sens du transport HTTP, pas une
            # règle métier : aucune politique d'entreprise ne la changerait.
            st["reachable"] = code == 200
        return bool(st["reachable"])

    h = Host()

    # -- Capteurs -----------------------------------------------------------
    h.sensors["arena.reachable"] = lambda: Symbol("yes" if reachable() else "no")
    h.sensors["run.open"] = lambda: Symbol("yes" if st["open"] else "no")
    h.sensors["run.submitted"] = lambda: Symbol("yes" if st["submitted"] else "no")
    h.sensors["context.read_ok"] = lambda: Symbol("yes" if st["read_ok"] else "no")
    h.sensors["context.reads_done"] = lambda: st["reads"]
    # BOUNDARY-OK: « au moins une » est la définition de « aucune », pas un
    # seuil métier. Le compte brut reste lisible par le programme ci-dessus.
    h.sensors["context.read_done"] = lambda: Symbol("yes" if st["reads"] > 0 else "no")
    h.sensors["budget.remaining"] = lambda: max(0, st["budget"] - st["actions"])
    h.sensors["write.performed"] = lambda: Symbol("yes" if st["wrote"] else "no")
    # BOUNDARY-OK: « au moins un » est la définition de « aucun », pas un seuil
    # métier — même motif que `context.read_done` ci-dessus.
    h.sensors["context.read_attempted"] = lambda: Symbol("yes" if st["read_calls"] else "no")
    h.sensors["write.attempted"] = lambda: Symbol("yes" if st["write_calls"] else "no")
    # Compte brut d'appels identiques consécutifs. Aucun seuil ici : c'est le
    # .agent qui décide à partir de combien on tourne en rond.
    h.sensors["context.repeat_streak"] = lambda: st["streak"]
    h.sensors["episode.done"] = lambda: Symbol("yes" if st["closed"] else "no")

    def task_brief() -> str:
        t = st["task"]
        if not t:
            return "Aucune mission tirée."
        # BOUNDARY-OK: mise en forme. Les champs sont rendus tels que la
        # plateforme les donne, y compris les ressources dans son ordre à elle ;
        # rien n'est écarté, rien n'est classé par pertinence.
        lignes = [f"MISSION : {t.get('title', '')}", str(t.get("instructions", "")),
                  f"domaine : {t.get('domain', '?')} · difficulté {t.get('difficulty', '?')}"]
        for r in st["resources"]:
            contenu = r.get("content", "")
            if not isinstance(contenu, str):
                contenu = json.dumps(contenu, ensure_ascii=False)
            lignes.append(f"\nRESSOURCE {r.get('title', r.get('id'))} ({r.get('id')}) :\n{contenu}")
        return "\n".join(lignes)

    def catalog_brief() -> str:
        if not st["tools"]:
            return "Aucun outil."
        # BOUNDARY-OK: le catalogue ENTIER, dans l'ordre rendu par la
        # plateforme. `mutating` est recopié tel quel — c'est ce fait, et non
        # un jugement, qui départage lecture et écriture.
        out = []
        for t in st["tools"]:
            nature = "ECRITURE" if t.get("mutating") else "lecture"
            schema = json.dumps(t.get("input_schema", {}), ensure_ascii=False)
            out.append(f"- {t['name']} [{nature}, risque {t.get('risk', '?')}] "
                       f"{t.get('description', '')} · arguments : {schema}")
        return "\n".join(out)

    def history_brief() -> str:
        if not st["history"]:
            return "Aucune action menée pour l'instant."
        # BOUNDARY-OK: la trace entière, dans l'ordre chronologique.
        return "\n".join(st["history"])

    h.sensors["task.brief"] = task_brief
    h.sensors["catalog.brief"] = catalog_brief
    h.sensors["history.brief"] = history_brief

    # -- Outils -------------------------------------------------------------
    def pull_run():
        demande = {"benchmark_id": BENCHMARK}
        if SCENARIO:
            demande["scenario_id"] = SCENARIO
        if VARIANT:
            demande["variant"] = VARIANT
        code, body = _call("POST", "/agent/runs/next", demande)
        # BOUNDARY-OK: 201 = run créé, au sens du protocole. Le .agent ne reçoit
        # pas le code mais le fait « un run est ouvert » ; il décide seul de la suite.
        if code != 201:
            # Catalogue épuisé ou campagne terminée : un FAIT, pas une panne.
            st["history"].append(f"tirage refusé ({code}) : {body.get('error', '')}")
            return {"run_id": "", "opened": Symbol("no")}

        st["run_id"] = body["run_id"]
        st["open"] = True

        ct, task = _call("GET", f"/runs/{st['run_id']}/task")
        cr, res = _call("GET", f"/runs/{st['run_id']}/resources")
        ctl, tools = _call("GET", f"/runs/{st['run_id']}/tools")
        # BOUNDARY-OK: transport. Une lecture non aboutie rend une structure vide
        # ET fait tomber `read_ok` ci-dessous : le programme distingue les deux.
        st["task"] = task.get("task", {}) if ct == 200 else {}
        # BOUNDARY-OK: transport, même motif : catalogue lu ou non lu.
        st["tools"] = tools.get("tools", []) if ctl == 200 else []
        st["budget"] = int((task.get("limits") or {}).get("max_actions", 0))

        st["resources"] = []
        # BOUNDARY-OK: transport. Liste non lue = aucune ressource à parcourir ;
        # `read_ok` porte séparément le fait que la lecture a échoué.
        for meta in (res.get("resources", []) if cr == 200 else []):
            cd, detail = _call("GET", f"/runs/{st['run_id']}/resources/{meta['id']}")
            # BOUNDARY-OK: transport. À défaut du contenu, on rend les métadonnées —
            # aucune ressource n'est écartée du programme.
            st["resources"].append(detail.get("resource", meta) if cd == 200 else meta)

        # Une lecture ratée doit être DISTINCTE d'un contexte vide : sans ce
        # fait, un scénario sans ressource ressemble à une panne de lecture.
        # BOUNDARY-OK: transport. `read_ok` est le FAIT « le contexte a pu être lu » ;
        # ce qu'il faut en conclure appartient aux NEVER du .agent.
        st["read_ok"] = ct == 200 and ctl == 200 and cr == 200
        print(f"  ▸ run {st['run_id']} · {st['task'].get('title', '?')} · "
              f"{len(st['tools'])} outils · budget {st['budget']}")
        return {"run_id": st["run_id"], "opened": Symbol("yes")}

    def _catalogue(nom: str):
        for t in st["tools"]:
            if t["name"] == nom:
                return t
        return None

    def _appel(nom: str, args_json: str, attendu_mutating: bool):
        outil = _catalogue(nom)
        if outil is None:
            raise ValueError(f"outil hors du catalogue perçu : {nom!r}")
        if bool(outil.get("mutating")) != attendu_mutating:
            raise ValueError(
                f"{nom!r} est déclaré {'modifiant' if outil.get('mutating') else 'non modifiant'} "
                f"par la plateforme : il ne passe pas par cet outil")
        try:
            args = json.loads(args_json or "{}")
        except json.JSONDecodeError:
            args = {}
        if not isinstance(args, dict):
            args = {}

        # Répétition = même outil, mêmes arguments, deux appels de suite.
        # BOUNDARY-OK: égalité de deux appels, au sens littéral. Aucune notion
        # d'équivalence métier n'est introduite : deux arguments différents
        # font deux appels différents, même s'ils visent la même chose.
        sig = (nom, json.dumps(args, sort_keys=True, ensure_ascii=False))
        st["streak"] = st["streak"] + 1 if sig == st["last_sig"] else 1
        st["last_sig"] = sig

        code, body = _call("POST", f"/runs/{st['run_id']}/tools/{nom}", {"arguments": args})
        # BOUNDARY-OK: transport. L'échec d'un appel est rendu au .agent dans la
        # trace, avec son motif ; c'est lui qui décide s'il réessaie ou renonce.
        ok = code == 200 and body.get("success") is not False
        st["actions"] += 1
        detail = body.get("result") if ok else {"error": body.get("error"), "message": body.get("message")}
        rendu = json.dumps(detail, ensure_ascii=False)[:1500]
        st["history"].append(f"{'✓' if ok else '✗'} {nom}({args_json}) → {rendu}")
        print(f"  {'·' if ok else '✗'} {nom}")
        # BOUNDARY-OK: transport. 409 signifie que la plateforme a clos le run ;
        # on re-perçoit ce fait, on n'en tire aucune conclusion ici.
        if code == 409:                      # run verrouillé ou clos
            st["open"] = False
        return ok, rendu

    def call_read_tool(tool_name, args_json):
        # Tenté et abouti sont comptés séparément : le programme distingue
        # « rien lu » de « tout lu en vain », et les deux ne se jouent pas pareil.
        st["read_calls"] += 1
        ok, rendu = _appel(str(tool_name), str(args_json), attendu_mutating=False)
        if ok:
            st["reads"] += 1
        return {"read_result": rendu}

    def call_write_tool(tool_name, args_json):
        st["write_calls"] += 1
        ok, rendu = _appel(str(tool_name), str(args_json), attendu_mutating=True)
        if ok:
            st["wrote"] = True
        return {"write_result": rendu}

    def submit_run(final_answer):
        code, body = _call("POST", f"/runs/{st['run_id']}/submit", {
            "final_answer": str(final_answer),
            "metadata": {"actions": st["actions"]},
        })
        # BOUNDARY-OK: transport. La soumission a abouti, ou non : un fait.
        st["submitted"] = code == 200
        st["score"] = body.get("score")
        print(f"  ⇒ soumis ({code}) · score {st['score']}")
        return {"submitted": Symbol("yes" if st["submitted"] else "no"),
                "score": float(st["score"] or 0)}

    def close_episode():
        st["closed"] = True
        return {"closed": Symbol("yes")}

    for fn in (pull_run, call_read_tool, call_write_tool, submit_run, close_episode):
        h.tools[fn.__name__] = fn

    if not ALLOW_EXTERNAL_LLM:
        raise SystemExit(
            "Refus : le contenu des scénarios serait envoyé à un modèle tiers.\n"
            "Consentement explicite requis : MERIDIAN_ALLOW_EXTERNAL_LLM=1")
    llm = GeminiLLM(model=os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite"))
    return h, llm

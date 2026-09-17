"""Hôte RÉEL pour `disk_sentinel.agent` — ce serveur Linux, pas une simulation.

Les capteurs lisent le système de fichiers ; `purge_old_files` supprime
réellement des fichiers. La sûreté est à deux étages :

  * la politique de l'agent (postérieur ≥ 0.85, NEVER si `.protected`,
    approbation humaine) — c'est elle qu'on teste ;
  * l'hôte, en défense en profondeur : la purge refuse tout chemin qui
    sortirait du bac à sable, quel que soit ce que le LLM a répondu.

`build()` prépare un monde reproductible : un bac à sable `live_sandbox/`
avec un sous-répertoire `logs/` saturé de fichiers vieux de 40 jours (la
cible attendue) et des sous-répertoires `cache/` et `data/` récents qui
doivent survivre à la purge.

    python3 -m agentl run examples/disk_sentinel.agent \
                         
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agentl import Host, Symbol
from gemini_llm import GeminiLLM

SANDBOX = Path(__file__).resolve().parent / "live_sandbox"
DAY = 86400

# Ce qui compte comme « périmé » est une règle métier : elle se LIT dans le
# monde, elle ne s'écrit pas dans l'hôte. Rendue au programme par le capteur
# `sandbox.stale_threshold_days`, qui la garde par un NEVER (indéterminé ≠ 0).
STALE_DAYS = float(os.environ.get("AGENTL_STALE_DAYS", "30"))


# ------------------------------------------------------------------ le monde
def reset_sandbox() -> None:
    """(Re)crée un état initial reproductible : la purge d'un run précédent
    ne doit pas fausser le suivant."""
    if SANDBOX.exists():
        shutil.rmtree(SANDBOX)
    old = time.time() - 40 * DAY
    logs = SANDBOX / "logs"
    logs.mkdir(parents=True)
    for i in range(24):
        f = logs / f"worker-{i:02d}.log"
        f.write_bytes(b"x" * 20_480)          # 24 × 20 Ko = 480 Ko périmés
        os.utime(f, (old, old))
    for name, count in (("cache", 6), ("data", 3)):
        d = SANDBOX / name
        d.mkdir()
        for i in range(count):
            (d / f"fresh-{i}.bin").write_bytes(b"y" * 2_048)   # récents


def _entries():
    return [p for p in SANDBOX.rglob("*") if p.is_file()]


def _age_days(p: Path) -> float:
    return (time.time() - p.stat().st_mtime) / DAY


# ------------------------------------------------------------------- l'hôte
def build():
    reset_sandbox()
    # Contre-factuel : AGENTL_PROTECT=1 pose le marqueur `.protected`, et la
    # politique NEVER doit alors empêcher toute purge — même souhaitable.
    if os.environ.get("AGENTL_PROTECT"):
        (SANDBOX / ".protected").touch()
    h = Host()

    # -- Capteurs : chaque lecture re-perçoit le monde réel -----------------
    h.sensors["disk.usage_percent"] = lambda: round(
        shutil.disk_usage("/").used / shutil.disk_usage("/").total * 100, 1)
    h.sensors["sandbox.file_count"] = lambda: len(_entries())
    h.sensors["sandbox.stale_threshold_days"] = lambda: STALE_DAYS
    # BOUNDARY-OK: dénombrement selon un seuil PERÇU (AGENTL_STALE_DAYS), pas
    # une règle écrite ici ; aucun élément n'est écarté du programme — le
    # `.agent` reçoit le compte brut et décide seul ce qu'il en fait.
    h.sensors["sandbox.stale_count"] = lambda: sum(
        1 for p in _entries() if _age_days(p) > STALE_DAYS)
    h.sensors["sandbox.size_kb"] = lambda: round(
        sum(p.stat().st_size for p in _entries()) / 1024, 1)
    # BOUNDARY-OK: extremum DESCRIPTIF — l'âge du plus ancien fichier est un
    # fait mesuré, il ne sélectionne ni ne priorise aucun élément.
    h.sensors["sandbox.oldest_age_days"] = lambda: round(
        max((_age_days(p) for p in _entries()), default=0.0), 1)
    h.sensors["escalation.message"] = lambda: (
        "Purge automatique impossible — nettoyage manuel requis "
        f"({len(_entries())} fichiers dans le bac à sable).")
    h.sensors["sandbox.protected"] = lambda: (
        Symbol("yes") if (SANDBOX / ".protected").exists() else Symbol("no"))

    def listing():
        lines = []
        # BOUNDARY-OK: la levée couvre la boucle entière, donc ses trois
        # motifs : (a) tri alphabétique pour rendre le listing reproductible —
        # aucun ordre métier, aucun répertoire omis ; (b) dénombrement des
        # périmés au seuil PERÇU, par répertoire ; (c) extremum descriptif de
        # l'âge. Rien n'est écarté : tous les répertoires sont décrits, et le
        # `.agent` choisit seul lequel purger.
        for d in sorted(p for p in SANDBOX.iterdir() if p.is_dir()):
            files = [p for p in d.rglob("*") if p.is_file()]
            stale = sum(1 for p in files if _age_days(p) > STALE_DAYS)
            size = sum(p.stat().st_size for p in files) / 1024
            oldest = max((_age_days(p) for p in files), default=0.0)
            lines.append(f"{d.name}: {len(files)} fichiers, {size:.0f} Ko, "
                         f"{stale} périmés, plus ancien {oldest:.0f} j")
        return " | ".join(lines)

    h.sensors["sandbox.listing"] = listing

    # `sandbox.pressure` a disparu : classer la pression en high/medium/low au
    # seuil de 15 fichiers était une DÉCISION, et elle portait le critère du
    # GOAL lui-même — l'hôte tranchait donc si l'agent avait réussi. Le
    # programme lit désormais le compte brut et fixe seul son propre critère.

    # -- Outils : de vraies actions, bornées par l'hôte ---------------------
    def scan_sandbox():
        files = _entries()
        return {"files": len(files),
                "size_kb": round(sum(p.stat().st_size for p in files) / 1024, 1)}

    def purge_old_files(directory):
        target = (SANDBOX / str(directory)).resolve()
        if SANDBOX.resolve() not in target.parents or not target.is_dir():
            raise ValueError(f"répertoire hors du bac à sable : {directory!r}")
        removed = 0
        for p in target.rglob("*"):
            if p.is_file() and _age_days(p) > STALE_DAYS:
                p.unlink()
                removed += 1
        return {"removed": removed}

    def notify_operator(message):
        print(f"\n  📣 OPÉRATEUR ← {message}\n")
        return {"delivered": Symbol("yes")}

    for fn in (scan_sandbox, purge_old_files, notify_operator):
        h.tools[fn.__name__] = fn

    def approver(request):
        reponse = input(f"\n  🖐  AUTORISER {request.render()} ? (o/N) : ")
        return reponse.strip().lower() == "o"

    h.approver = approver

    llm = GeminiLLM(model=os.environ.get("GEMINI_MODEL",
                                         "gemini-3.1-flash-lite"))
    return h, llm

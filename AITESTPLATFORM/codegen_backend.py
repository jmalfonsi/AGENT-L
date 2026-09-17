"""Agent de codage : Claude Code en mode non interactif.

Deux usages, volontairement séparés :

- `analyze` — un jugement rendu en JSON, sans aucun outil. Rien n'est écrit sur
  le disque, donc rien ne peut être « corrigé » en douce pendant l'analyse.
- `author` — la rédaction du programme et de son hôte, avec outils, confinée à
  un répertoire de projet. C'est le seul mode qui a le droit d'écrire, et il
  n'écrit que là.

Le skill `agentl-author` n'est pas recopié dans le prompt : il est résolu par
Claude Code lui-même, de sorte que le contrat de grammaire lu est toujours
celui du dépôt, pas une copie figée dans cette plateforme.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from framework_skills import SKILL_NAMES
from live_progress import emit_live_event

AGENTL_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MODEL = os.environ.get("AGENT_FACTORY_MODEL", "opus")
ANALYZE_TIMEOUT = int(os.environ.get("AGENT_FACTORY_ANALYZE_TIMEOUT", "300"))
AUTHOR_TIMEOUT = int(os.environ.get("AGENT_FACTORY_AUTHOR_TIMEOUT", "1800"))
AUTHOR_MAX_TURNS = int(os.environ.get("AGENT_FACTORY_MAX_TURNS", "80"))

AUTHOR_TOOLS = ("Read", "Write", "Edit", "Glob", "Grep", "Bash", "Skill", "TodoWrite")


class CodegenError(RuntimeError):
    """L'agent de codage n'a pas rendu un résultat exploitable."""


def executable() -> str:
    path = os.environ.get("AGENT_FACTORY_CLAUDE_BIN") or shutil.which("claude")
    if not path:
        raise CodegenError(
            "Le binaire `claude` est introuvable. L'agent de codage est Claude "
            "Code en mode non interactif ; installer le CLI ou définir "
            "AGENT_FACTORY_CLAUDE_BIN.")
    return path


def available() -> dict:
    try:
        binary = executable()
    except CodegenError as exc:
        return {"available": False, "reason": str(exc), "version": None}
    try:
        version = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=30,
        ).stdout.strip()
    except Exception as exc:
        return {"available": False, "reason": f"{type(exc).__name__}: {exc}", "version": None}
    return {
        "available": True,
        "reason": None,
        "version": version,
        "model": DEFAULT_MODEL,
        "skills": skill_status(),
    }


def skill_status(names: tuple[str, ...] = ()) -> dict:
    """État de résolution des skills d'écriture, vu par Claude Code.

    Un skill absent n'empêche pas la génération — l'agent de codage écrira sans
    méthode. C'est précisément pour cela qu'on l'affiche : la différence entre
    « avec skill » et « sans » ne doit pas rester invisible dans les résultats.
    """
    root = Path.home() / ".claude" / "skills"
    status = {}
    for name in (names or SKILL_NAMES):
        path = root / name
        resolved = path.is_dir() and (path / "SKILL.md").is_file()
        status[name] = {
            "resolved": resolved,
            "path": str(path.resolve()) if path.exists() else None,
        }
    return status


def _invoke(args: list[str], *, cwd: Path, timeout: int, stdin: str) -> dict:
    """Lance Claude Code et rend l'enveloppe JSON de la session."""
    env = {**os.environ, "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}
    try:
        completed = subprocess.run(
            [executable(), *args],
            cwd=str(cwd), env=env, input=stdin,
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise CodegenError(f"L'agent de codage a dépassé {timeout} secondes.") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[-1500:]
        raise CodegenError(f"Agent de codage interrompu (code {completed.returncode}). {detail}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise CodegenError(
            f"Sortie non JSON de l'agent de codage : {completed.stdout[-800:]}") from exc


#: Longueur maximale d'une cible affichée. La ligne de suivi doit tenir sans
#: défilement horizontal : au-delà, on coupe plutôt que d'élargir la colonne.
STEP_TARGET_MAX = 48


def _short(value: object, limit: int = STEP_TARGET_MAX) -> str:
    """Ramène une valeur d'outil à une seule ligne bornée."""
    texte = " ".join(str(value).split())
    return texte if len(texte) <= limit else texte[: limit - 1] + "…"


def _step_label(tool: str, entree: dict) -> str:
    """Traduit un appel d'outil en une opération lisible.

    Ce qui sort d'ici est affiché et journalisé : on ne prend donc **que** des
    cibles — un chemin, un motif, un nom de skill — jamais la prose du modèle.
    La promesse affichée au bas du panneau (« le contenu des réponses du modèle
    n'est jamais journalisé ») est une propriété de cette fonction.
    """
    if tool in ("Read", "Write", "Edit", "NotebookEdit"):
        chemin = str(entree.get("file_path") or entree.get("notebook_path") or "")
        verbe = {"Read": "lit", "Write": "écrit",
                 "Edit": "modifie", "NotebookEdit": "modifie"}[tool]
        return f"{verbe} {_short(os.path.basename(chemin) or chemin)}"
    if tool == "Bash":
        commande = str(entree.get("command", ""))
        # Seulement la tête de la commande : le nom du programme et ses
        # premiers arguments suffisent à dire ce qui se passe.
        tete = " ".join(commande.split()[:6])
        return f"exécute {_short(tete)}"
    if tool == "Glob":
        return f"cherche {_short(entree.get('pattern', ''))}"
    if tool == "Grep":
        return f"fouille {_short(entree.get('pattern', ''))}"
    if tool == "Skill":
        return f"invoque le skill {_short(entree.get('skill', ''), 32)}"
    if tool == "TodoWrite":
        taches = entree.get("todos") or []
        encours = next((t.get("content") for t in taches
                        if isinstance(t, dict) and t.get("status") == "in_progress"), None)
        return f"plan : {_short(encours)}" if encours else "met son plan à jour"
    return f"utilise {_short(tool, 32)}"


def _stream(args: list[str], *, cwd: Path, timeout: int, stdin: str,
            phase: str) -> dict:
    """Comme `_invoke`, mais rend compte de chaque opération pendant la session.

    En `--output-format json`, toute la session revient en un bloc à la fin :
    sur un gros cahier des charges, l'écran n'affiche rien pendant une demi-
    heure et l'utilisateur conclut que la plateforme est bloquée. Le flux
    `stream-json` donne une ligne par message ; on en extrait les appels
    d'outil, et eux seuls.
    """
    env = {**os.environ, "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}
    debut = time.monotonic()
    enveloppe: dict | None = None
    limite: dict | None = None
    operations = 0
    tours = 0

    processus = subprocess.Popen(
        [executable(), *args], cwd=str(cwd), env=env,
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,
    )
    # Le tube d'erreur se vide en parallèle : le lire après la fin du processus
    # suffit à bloquer une session bavarde dès que le tampon du système est
    # plein, et l'agent resterait figé sans que rien ne le dise.
    journal_erreur: list[str] = []

    def _drainer() -> None:
        if processus.stderr is None:
            return
        for ligne in processus.stderr:
            journal_erreur.append(ligne)
            del journal_erreur[:-200]  # on ne garde que la queue

    videur = threading.Thread(target=_drainer, daemon=True)
    videur.start()

    try:
        assert processus.stdin is not None and processus.stdout is not None
        processus.stdin.write(stdin)
        processus.stdin.close()

        for ligne in processus.stdout:
            if time.monotonic() - debut > timeout:
                processus.kill()
                raise CodegenError(
                    f"L'agent de codage a dépassé {timeout} secondes.")
            ligne = ligne.strip()
            if not ligne:
                continue
            try:
                message = json.loads(ligne)
            except json.JSONDecodeError:
                continue  # ligne de service : elle n'est pas un événement
            if message.get("type") == "result":
                enveloppe = message
                continue
            if message.get("type") == "rate_limit_event":
                limite = message.get("rate_limit_info") or {}
                continue
            if message.get("type") != "assistant":
                continue
            tours += 1
            for bloc in (message.get("message") or {}).get("content") or []:
                if not isinstance(bloc, dict) or bloc.get("type") != "tool_use":
                    continue
                operations += 1
                emit_live_event(
                    "codegen_step",
                    _step_label(str(bloc.get("name", "?")), bloc.get("input") or {}),
                    phase=phase, tool=str(bloc.get("name", "?")),
                    step=operations, turn=tours,
                    elapsedMs=int((time.monotonic() - debut) * 1000))
        code = processus.wait(timeout=60)
    except CodegenError:
        raise
    except Exception as exc:  # pragma: no cover - défaillance du sous-processus
        processus.kill()
        raise CodegenError(f"Agent de codage interrompu : {exc}") from exc
    finally:
        if processus.poll() is None:
            processus.kill()

    videur.join(timeout=10)
    erreur = "".join(journal_erreur)
    if code != 0:
        details: list[str] = []
        if enveloppe:
            statut = enveloppe.get("api_error_status")
            if statut:
                details.append(f"HTTP {statut}")
            resultat = str(enveloppe.get("result") or "").strip()
            if resultat:
                details.append(resultat[-1500:])
        if limite and limite.get("resetsAt"):
            try:
                reinitialisation = datetime.fromtimestamp(
                    float(limite["resetsAt"]), tz=timezone.utc
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
                details.append(f"Réinitialisation annoncée : {reinitialisation}")
            except (TypeError, ValueError, OSError):
                pass
        if erreur.strip():
            details.append(erreur.strip()[-1500:])
        detail = " · ".join(dict.fromkeys(details))
        raise CodegenError(
            f"Agent de codage interrompu (code {code}). {detail}".rstrip())
    if enveloppe is None:
        raise CodegenError(
            "Le flux de l'agent de codage s'est terminé sans résultat. "
            f"{erreur.strip()[-800:]}")
    return enveloppe


def _session_facts(envelope: dict) -> dict:
    """Métriques observées de la session, jamais de chaîne de pensée."""
    usage = envelope.get("usage") or {}
    return {
        "sessionId": envelope.get("session_id"),
        "model": envelope.get("modelUsage") and list(envelope["modelUsage"]) or [DEFAULT_MODEL],
        "numTurns": envelope.get("num_turns"),
        "durationMs": envelope.get("duration_ms"),
        "costUsd": envelope.get("total_cost_usd"),
        "inputTokens": usage.get("input_tokens"),
        "outputTokens": usage.get("output_tokens"),
        "isError": bool(envelope.get("is_error")),
    }


def _extract_json(text: str) -> dict:
    """Objet JSON d'une réponse, tolérant aux clôtures Markdown."""
    candidate = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", candidate, re.DOTALL)
    if fence:
        candidate = fence.group(1).strip()
    start, end = candidate.find("{"), candidate.rfind("}")
    if start == -1 or end <= start:
        raise CodegenError(f"Aucun objet JSON dans la réponse : {text[:500]}")
    try:
        return json.loads(candidate[start:end + 1])
    except json.JSONDecodeError as exc:
        raise CodegenError(f"JSON invalide dans la réponse : {exc}") from exc


def analyze(prompt: str, *, workdir: Path) -> tuple[dict, dict]:
    """Jugement structuré, sans outil ni écriture.

    Rend `(objet, faits de session)`.
    """
    emit_live_event("codegen_start", "Analyse du cahier des charges", phase="analyze")
    envelope = _invoke(
        ["-p", "--output-format", "json", "--model", DEFAULT_MODEL,
         "--permission-mode", "manual", "--allowedTools", ""],
        cwd=workdir, timeout=ANALYZE_TIMEOUT, stdin=prompt,
    )
    facts = _session_facts(envelope)
    emit_live_event("codegen_end", "Analyse terminée", phase="analyze",
                    status="error" if facts["isError"] else "success",
                    durationMs=facts["durationMs"])
    if facts["isError"]:
        raise CodegenError(f"L'analyse a échoué : {str(envelope.get('result'))[:600]}")
    return _extract_json(str(envelope.get("result", ""))), facts


def author(prompt: str, *, workdir: Path, allow_dirs: tuple[Path, ...] = (AGENTL_ROOT,)) -> dict:
    """Rédaction avec outils, confinée à `workdir`.

    `--add-dir` ouvre le dépôt AGENT-L en lecture (skill, exemples, grammaire).
    Les écritures visées restent dans `workdir` ; la validation qui suit ne lit
    que ce répertoire, donc un fichier écrit ailleurs ne peut pas être présenté
    comme le résultat.
    """
    emit_live_event("codegen_start", "Rédaction de l'agent", phase="author",
                    maxTurns=AUTHOR_MAX_TURNS)
    # `stream-json` exige `--verbose` : sans lui, la commande refuse de partir.
    args = ["-p", "--output-format", "stream-json", "--verbose",
            "--model", DEFAULT_MODEL,
            "--permission-mode", "acceptEdits",
            "--max-turns", str(AUTHOR_MAX_TURNS),
            "--allowedTools", *AUTHOR_TOOLS]
    for directory in allow_dirs:
        args += ["--add-dir", str(directory)]
    envelope = _stream(args, cwd=workdir, timeout=AUTHOR_TIMEOUT, stdin=prompt,
                       phase="author")
    facts = _session_facts(envelope)
    emit_live_event("codegen_end", "Rédaction terminée", phase="author",
                    status="error" if facts["isError"] else "success",
                    durationMs=facts["durationMs"])
    if facts["isError"]:
        raise CodegenError(f"La rédaction a échoué : {str(envelope.get('result'))[:600]}")
    return {"report": str(envelope.get("result", ""))[-4000:], **facts}

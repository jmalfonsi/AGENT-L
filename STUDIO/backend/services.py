from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from .config import AUTHOR_SKILL, GRAMMAR_CONTRACT, REPO_ROOT, RUNS_ROOT, safe_child
from .store import Store
from .templates import archive


COMMANDS = {"check", "test", "verify", "boundary", "run", "autoloop", "viz", "replay"}
QUALITY_GATES = ("check", "test", "verify", "boundary")
EDITABLE_SUFFIXES = {".agent", ".py", ".md", ".json", ".txt"}
GLYPH_KINDS = {
    "👁": "perception", "◆": "belief", "◎": "goal", "▶": "plan",
    "·": "step", "🔧": "action", "⛔": "blocked", "🙋": "approval",
    "✅": "verified", "❌": "verification-failed", "🧠": "reasoning",
    "💾": "memory", "⚡": "event", "❓": "ask", "→": "delegate",
    "‼": "error", "•": "info", "↻": "retry", "∿": "inference",
    "⌘": "planning", "✉": "message", "⇄": "shared", "§": "policy",
}

#: Genres de `agentl run --events` → vocabulaire de la chronologie, le même
#: que celui qu'on déduisait des glyphes.
EVENT_KINDS = {
    "OBSERVE": "perception", "BELIEF": "belief", "GOAL": "goal", "PLAN": "plan",
    "STEP": "step", "TOOL": "action", "BLOCKED": "blocked", "APPROVAL": "approval",
    "VERIFY_OK": "verified", "VERIFY_FAIL": "verification-failed", "LLM": "reasoning",
    "MEMORY": "memory", "EVENT": "event", "ASK": "ask", "DELEGATE": "delegate",
    "ERROR": "error", "INFO": "info", "RETRY": "retry", "BAYES": "inference",
    "PLANNER": "planning", "MESSAGE": "message", "SHARED": "shared", "POLICY": "policy",
}

#: Ligne de diagnostic rendue par `Diagnostic.render()` : `avert. W120  ligne 48  …`.
DIAGNOSTIC_LINE = re.compile(
    r"^\s*(?:[!✗]\s+)?(erreur|avert\.)\s+([A-Z]\d{3})\s+(?:ligne (\d+)|—)\s+(.*?)\s*$")
CHECK_AGENT_HEADER = re.compile(r"^AGENT (\S+) v")


# --------------------------------------------------------------------------
# fichiers du projet
# --------------------------------------------------------------------------

def project_entrypoint(project: dict[str, Any]) -> Path:
    root = Path(project["path"]).resolve()
    config_path = safe_child(root, "project.agentl.json")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    path = safe_child(root, str(config["entrypoint"]))
    if path.suffix != ".agent" or not path.is_file():
        raise ValueError("Le point d’entrée AGENT-L est introuvable")
    return path


def list_project_files(project: dict[str, Any]) -> list[dict[str, Any]]:
    root = Path(project["path"]).resolve()
    rows: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if (not path.is_file() or ".history" in path.parts
                or "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}):
            continue
        relative = path.relative_to(root).as_posix()
        rows.append({"path": relative, "name": path.name, "size": path.stat().st_size})
    return rows


def read_project_file(project: dict[str, Any], relative: str) -> str:
    root = Path(project["path"]).resolve()
    path = safe_child(root, relative)
    if not path.is_file() or path.stat().st_size > 1_000_000:
        raise ValueError("Fichier absent ou trop volumineux")
    return path.read_text(encoding="utf-8")


def write_project_file(project: dict[str, Any], relative: str, content: str) -> None:
    root = Path(project["path"]).resolve()
    path = safe_child(root, relative)
    if path.suffix not in EDITABLE_SUFFIXES:
        raise ValueError("Type de fichier non éditable")
    if len(content.encode("utf-8")) > 1_000_000:
        raise ValueError("Fichier trop volumineux")
    path.parent.mkdir(parents=True, exist_ok=True)
    archive(root, relative)
    path.write_text(content, encoding="utf-8")


def list_history(project: dict[str, Any]) -> list[dict[str, Any]]:
    """Versions archivées, les plus récentes d'abord.

    `.history/` était écrit à chaque sauvegarde sans qu'aucun écran ne
    puisse le lire : une sauvegarde qu'on ne peut pas restaurer n'en est
    pas une.
    """
    root = Path(project["path"]).resolve()
    history = root / ".history"
    if not history.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for stamp_dir in sorted(history.iterdir(), reverse=True):
        if not stamp_dir.is_dir():
            continue
        for path in sorted(stamp_dir.rglob("*")):
            if not path.is_file():
                continue
            rows.append({
                "stamp": stamp_dir.name,
                "path": path.relative_to(stamp_dir).as_posix(),
                "size": path.stat().st_size,
            })
    return rows[:500]


def read_history_file(project: dict[str, Any], stamp: str, relative: str) -> str:
    root = Path(project["path"]).resolve()
    path = safe_child(root, ".history", stamp, relative)
    if not path.is_file() or path.stat().st_size > 1_000_000:
        raise ValueError("Version absente ou trop volumineuse")
    return path.read_text(encoding="utf-8")


def restore_history_file(project: dict[str, Any], stamp: str, relative: str) -> None:
    """Restaure une version : l'état courant est lui-même archivé d'abord."""
    content = read_history_file(project, stamp, relative)
    write_project_file(project, relative, content)


# --------------------------------------------------------------------------
# lecture des sorties CLI
# --------------------------------------------------------------------------

def parse_metrics(output: str) -> dict[str, int | float]:
    metrics: dict[str, int | float] = {}
    metric_line = next(
        (line for line in reversed(output.splitlines()) if re.match(r"\s*ticks=\d+", line)),
        "",
    )
    for key, value in re.findall(r"\b([a-z][a-z_]+)=(-?\d+(?:\.\d+)?)", metric_line):
        metrics[key] = float(value) if "." in value else int(value)
    return metrics


AGENT_BANNER = re.compile(r"^\s*╔═+\s*(\S+)\s*═+")


def parse_trace(output: str) -> list[dict[str, Any]]:
    """Timeline lisible, attribuée à son agent.

    Dans une société, chaque agent redémarre ses ticks à 1 : sans le nom
    porté par la bannière `╔═ nom ═`, les événements des deux agents
    s'empilent sous les mêmes numéros et la trace devient illisible.
    """
    events: list[dict[str, Any]] = []
    tick = 0
    agent = ""
    for raw in output.splitlines():
        banner = AGENT_BANNER.match(raw)
        if banner:
            agent = banner.group(1)
            tick = 0
            events.append({"tick": 0, "agent": agent, "kind": "agent",
                           "text": agent, "detail": "début de trace"})
            continue
        match = re.match(r"\s*┌─ tick (\d+)", raw)
        if match:
            tick = int(match.group(1))
            events.append({"tick": tick, "agent": agent, "kind": "tick",
                           "text": f"tick {tick}", "detail": ""})
            continue
        match = re.match(r"\s*│\s+([^\s]+)\s+(.*?)(?:\s+\[([^]]+)\])?\s*$", raw)
        if not match:
            continue
        glyph, text, detail = match.groups()
        events.append({
            "tick": tick,
            "agent": agent,
            "kind": GLYPH_KINDS.get(glyph, "info"),
            "text": text.strip(),
            "detail": (detail or "").strip(),
        })
    return events


def read_events(path: Path) -> list[dict[str, Any]] | None:
    """Chronologie lue dans `--events`, regroupée par agent ; `None` sans fichier.

    `parse_trace` découpe la trace texte au glyphe près : le moindre
    changement de mise en forme du CLI cassait la chronologie sans bruit.
    Le fichier porte les événements sous leur forme d'origine. Ils sont
    regroupés par agent, dans l'ordre de première apparition — la lecture
    que donnait la trace texte d'une société.
    """
    if not path.is_file():
        return None
    by_agent: dict[str, list[dict[str, Any]]] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                by_agent.setdefault(str(row.get("agent") or ""), []).append(row)
    except (OSError, json.JSONDecodeError, AttributeError):
        return None
    society = len(by_agent) > 1
    events: list[dict[str, Any]] = []
    for agent, rows in by_agent.items():
        if society:
            events.append({"tick": 0, "agent": agent, "kind": "agent",
                           "text": agent, "detail": "début de trace"})
        for row in rows:
            kind, tick = str(row.get("kind") or ""), int(row.get("tick") or 0)
            if kind == "TICK":
                events.append({"tick": tick, "agent": agent, "kind": "tick",
                               "text": f"tick {tick}", "detail": ""})
                continue
            events.append({"tick": tick, "agent": agent,
                           "kind": EVENT_KINDS.get(kind, "info"),
                           "text": str(row.get("text") or ""),
                           "detail": str(row.get("detail") or "")})
    return events[:20_000]


def run_events(artifacts: Path, output: str) -> list[dict[str, Any]]:
    """`events.jsonl` quand le CLI l'a écrit, la trace texte sinon."""
    events = read_events(artifacts / "events.jsonl")
    return events if events is not None else parse_trace(output)


def parse_diagnostics(output: str) -> list[dict[str, Any]]:
    """Erreurs et avertissements d'une porte, avec leur agent et leur ligne.

    Une porte ne rend qu'un code de retour : `check` passait avec `W120` comme
    sans rien, et la carte affichait « PASSE » en vert dans les deux cas.
    """
    rows: list[dict[str, Any]] = []
    agent = ""
    for raw in output.splitlines():
        header = CHECK_AGENT_HEADER.match(raw)
        if header:
            agent = header.group(1)
            continue
        if raw.startswith("PROGRAMME"):
            agent = ""
            continue
        match = DIAGNOSTIC_LINE.match(raw)
        if not match:
            continue
        severity, code, line, message = match.groups()
        rows.append({
            "severity": "error" if severity == "erreur" else "warning",
            "code": code, "line": int(line) if line else None,
            "message": message, "agent": agent,
        })
    return rows


def read_journal(record: Path) -> dict[str, Any]:
    """Résumé du journal de rejeu : franchissements de frontière, pas ticks.

    Le journal enregistre ce qui traverse la frontière de l'hôte et du
    modèle — lectures de capteur, invocations d'outil, purges de file. Il
    ne porte pas la trace tick par tick : celle-ci vient de `parse_trace`.
    """
    journal = json.loads(record.read_text(encoding="utf-8"))
    entries = journal.get("entries", [])
    kinds: dict[str, int] = {}
    for entry in entries:
        kinds[str(entry.get("kind", "?"))] = kinds.get(str(entry.get("kind", "?")), 0) + 1
    return {
        "meta": journal.get("meta", {}),
        "entries": len(entries),
        "crossings": kinds,
        "invocations": [
            {"seq": entry.get("seq"), "tool": entry.get("key"), "args": entry.get("args")}
            for entry in entries if entry.get("kind") == "invoke"
        ][:200],
    }


def read_runtime_usage(project_root: Path) -> dict[str, Any] | None:
    """Métrage écrit par l'adaptateur runtime pendant l'exécution."""
    path = project_root / "runtime_usage.json"
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# --------------------------------------------------------------------------
# construction des lignes de commande
# --------------------------------------------------------------------------

def _int_in(config: dict[str, Any], key: str, default: int, low: int, high: int) -> int:
    try:
        return min(max(int(config.get(key, default)), low), high)
    except (TypeError, ValueError):
        return default


def command_timeout(config: dict[str, Any]) -> int:
    return _int_in(config, "timeoutSeconds", 90, 5, 300)


def _command_args(command: str, source: Path, config: dict[str, Any], artifacts: Path) -> list[str]:
    base = [sys.executable, "-m", "agentl"]
    if command in QUALITY_GATES:
        args = base + [command, str(source)]
        if command == "verify":
            args += ["--depth", str(_int_in(config, "verifyDepth", 4, 1, 12))]
        return args
    if command == "viz":
        return base + ["viz", str(source), "-o", str(artifacts / "graph.html")]
    if command == "run":
        return base + [
            "run", str(source), "--ticks", str(_int_in(config, "maxTicks", 6, 1, 100)),
            "--quiet",
            "--html", str(artifacts / "trace.html"),
            "--events", str(artifacts / "events.jsonl"),
            "--record", str(artifacts / "record.json"),
        ]
    if command == "autoloop":
        # Le budget doit rester strictement sous le timeout du sous-processus,
        # sinon la boucle est tuée à l'instant précis où elle s'arrêterait
        # d'elle-même, et son rapport est perdu.
        timeout = command_timeout(config)
        budget = max(round(timeout * 0.6), 5)
        args = base + [
            "autoloop", str(source),
            "--max-attempts", str(_int_in(config, "maxAttempts", 4, 1, 10)),
            "--max-cases", str(_int_in(config, "maxCases", 200, 10, 2000)),
            "--budget", str(budget),
            "-o", str(artifacts / "corrected.agent"),
        ]
        model = str(config.get("autoloopModel") or "").strip()
        if model:
            args += ["--model", model]
        return args
    raise ValueError("Commande non prise en charge")


def autoloop_model_for(config: dict[str, Any]) -> str:
    """Rédacteur des corrections : `gemini-*` ou `claude-*`, sinon rien.

    Sans modèle, `agentl autoloop` diagnostique et ne réécrit rien. Le
    défaut suit le runtime du projet quand celui-ci est déjà un modèle que
    la boucle sait piloter.
    """
    declared = str(config.get("autoloopModel") or "").strip()
    if declared:
        return declared
    model = str(config.get("runtimeModel") or "").strip()
    provider = str(config.get("runtimeProvider") or "")
    if provider == "google-gemini" and model.startswith("gemini"):
        return model
    if provider == "anthropic" and model.startswith("claude"):
        return model
    return ""


def _subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    current = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(REPO_ROOT) + (os.pathsep + current if current else "")
    env["PYTHONUNBUFFERED"] = "1"
    return env


# --------------------------------------------------------------------------
# exécution : les runs vivent en tâche de fond, la requête HTTP ne les porte pas
# --------------------------------------------------------------------------

class RunHandle:
    """Un run en cours : son processus, sa sortie au fil de l'eau, sa fin."""

    def __init__(self, run_id: str, project_id: str | None = None) -> None:
        self.run_id = run_id
        self.project_id = project_id
        self.lines: list[str] = []
        self.finished = threading.Event()
        self.cancelled = False
        self.process: subprocess.Popen[str] | None = None
        self.result: dict[str, Any] | None = None
        self._lock = threading.Lock()

    def append(self, line: str) -> None:
        with self._lock:
            if len(self.lines) < 20_000:
                self.lines.append(line)

    def mark(self) -> int:
        """Position courante, pour relire ensuite ce qu'une étape a écrit."""
        with self._lock:
            return len(self.lines)

    def output(self) -> str:
        with self._lock:
            return "".join(self.lines)[-250_000:]

    def since(self, index: int) -> tuple[list[str], int]:
        with self._lock:
            return self.lines[index:], len(self.lines)

    def cancel(self) -> bool:
        """Demande l'arrêt ; vrai tant que le run n'était pas déjà fini.

        Un run en file d'attente, ou entre deux étapes, n'a pas de processus :
        le drapeau suffit, le worker s'arrête à son prochain point de contrôle.
        """
        if self.finished.is_set():
            return False
        self.cancelled = True
        process = self.process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        return True


class RunRegistry:
    def __init__(self) -> None:
        self._runs: dict[str, RunHandle] = {}
        self._lock = threading.Lock()

    def open(self, run_id: str, project_id: str | None = None) -> RunHandle:
        handle = RunHandle(run_id, project_id)
        with self._lock:
            self._runs[run_id] = handle
            if len(self._runs) > 40:
                for stale in [key for key, value in self._runs.items()
                              if value.finished.is_set()]:
                    self._runs.pop(stale, None)
        return handle

    def get(self, run_id: str) -> RunHandle | None:
        with self._lock:
            return self._runs.get(run_id)

    def active(self, project_id: str) -> list[RunHandle]:
        with self._lock:
            return [handle for handle in self._runs.values()
                    if handle.project_id == project_id and not handle.finished.is_set()]


REGISTRY = RunRegistry()


class ProjectBusy(RuntimeError):
    """Un autre run occupe le projet."""


class AuthoringCancelled(RuntimeError):
    """L'agent auteur a été arrêté à la demande."""


class ProjectLocks:
    """Un run à la fois par projet ; les suivants attendent leur tour.

    Deux runs simultanés se marchaient dessus : chacun effaçait au démarrage
    le métrage `runtime_usage.json` de l'autre, et une proposition pouvait
    être écrite pendant que les portes relisaient les sources.
    """

    def __init__(self) -> None:
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()

    def of(self, project_id: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(project_id, threading.Lock())


PROJECT_LOCKS = ProjectLocks()


def _acquire_turn(handle: RunHandle, project_id: str) -> bool:
    """Attend que le projet soit libre ; faux si le run est arrêté entre-temps."""
    lock = PROJECT_LOCKS.of(project_id)
    if lock.acquire(blocking=False):
        return True
    handle.append("⏳ En attente : un autre run occupe ce projet.\n")
    while not handle.cancelled:
        if lock.acquire(timeout=0.25):
            handle.append("▶ Projet libre : le run démarre.\n")
            return True
    return False


@contextmanager
def project_turn(project_id: str) -> Iterator[None]:
    """Le tour du projet pour une écriture synchrone, sinon `ProjectBusy`."""
    lock = PROJECT_LOCKS.of(project_id)
    if not lock.acquire(blocking=False):
        raise ProjectBusy("Un run occupe ce projet : réessayez quand il sera terminé")
    try:
        yield
    finally:
        lock.release()


def _status(handle: RunHandle, code: int | None) -> str:
    return ("cancelled" if handle.cancelled else "timeout" if code == 124
            else "passed" if code == 0 else "failed")


def _spawn(
    store: Store, project: dict[str, Any], command: str,
    body: Callable[[RunHandle], dict[str, Any]], *, exclusive: bool = True,
) -> dict[str, Any]:
    """Démarre un run en tâche de fond, et garantit sa fin.

    Trois workers — commande, suite, rejeu — portaient chacun leur propre
    fin. Deux n'avaient pas de `finally` : une exception laissait `finished`
    jamais posé, le run « en cours » pour toujours et son flux SSE en boucle.
    Il n'y a plus qu'une fin, et elle est inconditionnelle.

    `body` fait le travail et rend `status`, `exit_code`, `output` et
    `metadata`. La durée est mesurée ici, file d'attente exclue.
    """
    run = store.create_run(project["id"], command)
    handle = REGISTRY.open(run["id"], project["id"])

    def worker() -> None:
        started = time.monotonic()
        result: dict[str, Any] | None = None
        holds_turn = False
        try:
            if exclusive:
                holds_turn = _acquire_turn(handle, project["id"])
                if not holds_turn:
                    result = {"status": "cancelled", "exit_code": None,
                              "output": handle.output(),
                              "metadata": {"events": [], "metrics": {}}}
                    return
                started = time.monotonic()
            result = body(handle)
        except Exception as exc:
            handle.append(f"\n‼ {type(exc).__name__}: {exc}\n")
            result = {"status": "cancelled" if handle.cancelled else "failed",
                      "exit_code": 2, "output": handle.output(),
                      "metadata": {"events": [], "metrics": {},
                                   "error": f"{type(exc).__name__}: {exc}"}}
        finally:
            if holds_turn:
                PROJECT_LOCKS.of(project["id"]).release()
            try:
                handle.result = store.finish_run(
                    run["id"], duration_ms=int((time.monotonic() - started) * 1000),
                    **(result or {"status": "failed", "exit_code": 2,
                                  "output": handle.output(), "metadata": {}}),
                )
                prune_artifacts(store, project["id"])
            except Exception as exc:        # la base refuse : la fin reste posée
                handle.append(f"\n‼ fin non enregistrée : {exc}\n")
            finally:
                handle.finished.set()

    threading.Thread(target=worker, daemon=True).start()
    return run


def _stream_process(
    handle: RunHandle, args: list[str], cwd: str, timeout: int,
) -> int:
    """Lance un processus, recopie sa sortie au fil de l'eau, applique le délai."""
    process = subprocess.Popen(
        args, cwd=cwd, env=_subprocess_env(), text=True,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1,
    )
    handle.process = process
    deadline = time.monotonic() + timeout
    timer = threading.Timer(timeout, lambda: process.poll() is None and process.kill())
    timer.start()
    try:
        assert process.stdout is not None
        for line in process.stdout:
            handle.append(line)
        process.wait()
    finally:
        timer.cancel()
        if process.stdout:
            process.stdout.close()
    if process.returncode != 0 and time.monotonic() >= deadline:
        handle.append(f"\nTemps maximal dépassé ({timeout}s).\n")
        return 124
    return process.returncode


def _artifact_root(run_id: str) -> Path:
    root = safe_child(RUNS_ROOT, run_id)
    root.mkdir(parents=True, exist_ok=True)
    return root


def _artifacts_of(root: Path) -> dict[str, str | None]:
    return {
        name: str(root / filename) if (root / filename).exists() else None
        for name, filename in (
            ("trace", "trace.html"), ("record", "record.json"),
            ("graph", "graph.html"), ("corrected", "corrected.agent"),
            ("events", "events.jsonl"),
            ("draftAgent", "draft.agent"), ("draftHost", "draft.py"),
            ("currentAgent", "current.agent"), ("currentHost", "current.py"),
        )
    }


def launch_command(store: Store, project: dict[str, Any], command: str) -> dict[str, Any]:
    """Démarre un run en tâche de fond et rend immédiatement sa ligne."""
    if command not in COMMANDS or command == "replay":
        raise ValueError("Commande inconnue")
    config = dict(project.get("config", {}))
    if command == "autoloop":
        config["autoloopModel"] = autoloop_model_for(config)
    source = project_entrypoint(project)
    project_root = Path(project["path"])

    def body(handle: RunHandle) -> dict[str, Any]:
        artifacts = _artifact_root(handle.run_id)
        args = _command_args(command, source, config, artifacts)
        # Sous le tour du projet : le métrage n'appartient qu'à ce run.
        (project_root / "runtime_usage.json").unlink(missing_ok=True)
        code = _stream_process(handle, args, project["path"], command_timeout(config))
        output = handle.output()
        metadata: dict[str, Any] = {
            "argv": args[2:],
            "metrics": parse_metrics(output),
            "events": run_events(artifacts, output),
            "artifacts": _artifacts_of(artifacts),
            "autoloopModel": config.get("autoloopModel") or None,
        }
        if command in QUALITY_GATES:
            metadata["diagnostics"] = [{**row, "gate": command}
                                       for row in parse_diagnostics(output)]
        record = artifacts / "record.json"
        if record.exists():
            metadata["journal"] = read_journal(record)
        usage = read_runtime_usage(project_root)
        if usage:
            metadata["runtimeUsage"] = usage
        return {"status": _status(handle, code), "exit_code": code,
                "output": output, "metadata": metadata}

    return _spawn(store, project, command, body)


def run_gates(
    handle: RunHandle, source: Path, config: dict[str, Any], cwd: str,
    artifacts: Path, budget: int, *, stop_on_failure: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Les quatre portes sous un budget partagé, diagnostics compris.

    La suite et la validation d'une proposition avaient chacune leur boucle,
    et seule la première partageait son budget : une proposition pouvait
    retenir quatre délais pleins. Une seule boucle sert désormais les deux.
    """
    started = time.monotonic()
    gates: list[dict[str, Any]] = []
    diagnostics: list[dict[str, Any]] = []
    for command in QUALITY_GATES:
        remaining = budget - (time.monotonic() - started)
        if handle.cancelled or remaining <= 1:
            skipped = not handle.cancelled
            gates.append({"name": command, "status": "skipped" if skipped else "cancelled",
                          "code": None, "warnings": 0, "errors": 0})
            if skipped:
                handle.append(f"\n$ agentl {command}\nBudget épuisé : porte non lancée.\n")
            continue
        handle.append(f"\n$ agentl {command}\n")
        first = handle.mark()
        code = _stream_process(handle, _command_args(command, source, config, artifacts),
                               cwd, int(remaining))
        found = [{**row, "gate": command}
                 for row in parse_diagnostics("".join(handle.since(first)[0]))]
        diagnostics += found
        gates.append({
            "name": command, "status": _status(handle, code), "code": code,
            "warnings": sum(row["severity"] == "warning" for row in found),
            "errors": sum(row["severity"] == "error" for row in found),
        })
        if code != 0 and stop_on_failure:
            break
    return gates, diagnostics


def launch_quality_suite(store: Store, project: dict[str, Any]) -> dict[str, Any]:
    """Les quatre portes dans un seul run, sous un budget global.

    Chaque porte recevait auparavant le timeout complet : quatre portes à
    300 s pouvaient retenir vingt minutes. Le budget est ici partagé.
    """
    config = project.get("config", {})
    source = project_entrypoint(project)
    total_budget = command_timeout(config)

    def body(handle: RunHandle) -> dict[str, Any]:
        gates, diagnostics = run_gates(handle, source, config, project["path"],
                                       _artifact_root(handle.run_id), total_budget)
        final_code = 0
        for gate in gates:
            if gate["status"] != "passed":
                final_code = gate["code"] or 124
        status = ("cancelled" if handle.cancelled
                  else "passed" if final_code == 0 else "failed")
        return {"status": status, "exit_code": final_code, "output": handle.output(),
                "metadata": {"gates": gates, "diagnostics": diagnostics, "events": [],
                             "metrics": {}, "budgetSeconds": total_budget}}

    return _spawn(store, project, "quality-suite", body)


def launch_replay(
    store: Store, project: dict[str, Any], original: dict[str, Any],
) -> dict[str, Any]:
    record = original.get("metadata", {}).get("artifacts", {}).get("record")
    if not record or not Path(record).is_file():
        raise ValueError("Cette exécution ne possède pas de journal de rejeu")
    config = project.get("config", {})
    source = project_entrypoint(project)

    def body(handle: RunHandle) -> dict[str, Any]:
        artifacts = _artifact_root(handle.run_id)
        args = [sys.executable, "-m", "agentl", "replay", record,
                "--source", str(source), "--quiet",
                "--events", str(artifacts / "events.jsonl")]
        code = _stream_process(handle, args, project["path"], command_timeout(config))
        output = handle.output()
        return {"status": _status(handle, code), "exit_code": code, "output": output,
                "metadata": {"replayedRun": original["id"], "argv": args[2:],
                             "events": run_events(artifacts, output),
                             "metrics": parse_metrics(output),
                             "artifacts": _artifacts_of(artifacts)}}

    return _spawn(store, project, "replay", body)


def await_run(store: Store, run_id: str, timeout: float = 360) -> dict[str, Any]:
    handle = REGISTRY.get(run_id)
    if handle is None:
        return store.get_run(run_id)                    # type: ignore[return-value]
    handle.finished.wait(timeout)
    return handle.result or store.get_run(run_id)       # type: ignore[return-value]


def cancel_run(run_id: str) -> bool:
    handle = REGISTRY.get(run_id)
    return bool(handle and handle.cancel())


def _sse_lines(chunk: list[str], index: int) -> Iterator[str]:
    lines = "".join(chunk).splitlines()
    for position, line in enumerate(lines):
        ident = f"id: {index}\n" if position == len(lines) - 1 else ""
        yield f"{ident}data: {json.dumps(line, ensure_ascii=False)}\n\n"


def stream_run(store: Store, run_id: str, start: int = 0) -> Iterator[str]:
    """Flux SSE : la sortie au fil de l'eau, puis le run terminé.

    Chaque lot porte en `id` sa position dans la sortie. Un client dont le
    flux s'est coupé reprend avec `?from=<id>`, au lieu de tout recevoir deux
    fois ou d'attendre une fin qui ne viendrait plus par ce canal.
    """
    handle = REGISTRY.get(run_id)
    if handle is None:
        run = store.get_run(run_id)
        payload = json.dumps(run, ensure_ascii=False, default=str)
        yield f"event: end\ndata: {payload}\n\n"
        return
    index = max(start, 0)
    while True:
        chunk, index = handle.since(index)
        if chunk:
            yield from _sse_lines(chunk, index)
        if handle.finished.is_set():
            break
        time.sleep(0.2)
    chunk, index = handle.since(index)
    yield from _sse_lines(chunk, index)
    run = handle.result or store.get_run(run_id)
    yield f"event: end\ndata: {json.dumps(run, ensure_ascii=False, default=str)}\n\n"


# --------------------------------------------------------------------------
# agents auteurs : Codex et Claude Code
# --------------------------------------------------------------------------

def _extract_json(text: str) -> dict[str, Any]:
    """Dernier objet JSON complet d'une sortie, pas le premier fragment.

    Un CLI écrit un préambule avant sa réponse : partir de la première
    accolade rencontrée produit presque toujours un JSON invalide.
    """
    fenced = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    for candidate in reversed(fenced):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    decoder = json.JSONDecoder()
    best: dict[str, Any] | None = None
    for match in re.finditer(r"\{", text):
        try:
            value, _ = decoder.raw_decode(text[match.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            best = value
    if best is None:
        raise json.JSONDecodeError("aucun objet JSON complet dans la sortie", text, 0)
    return best


AGENT_DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "agent_source": {"type": "string"},
        "host_source": {"type": "string"},
    },
    "required": ["summary", "agent_source", "host_source"],
    "additionalProperties": False,
}


SKILL_DRAFT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "content": {"type": "string"},
    },
    "required": ["summary", "content"],
    "additionalProperties": False,
}


def available_authoring_agents() -> dict[str, bool]:
    return {"codex": shutil.which("codex") is not None,
            "claude-code": shutil.which("claude") is not None}


def default_authoring_agent() -> str:
    """Le CLI réellement installé, jamais un défaut qui échouera d'emblée."""
    available = available_authoring_agents()
    if available["codex"]:
        return "codex"
    if available["claude-code"]:
        return "claude-code"
    return "codex"


def _authoring_result_from_claude(stdout: str) -> tuple[dict[str, Any], dict[str, Any], float]:
    envelope = json.loads(stdout)
    structured = envelope.get("structured_output")
    if not isinstance(structured, dict):
        result = envelope.get("result", "")
        structured = _extract_json(result) if isinstance(result, str) else result
    if not isinstance(structured, dict):
        raise ValueError("Claude Code n’a pas rendu la structure attendue")
    return structured, envelope.get("usage", {}) or {}, float(envelope.get("total_cost_usd", 0) or 0)


def run_authoring_agent(
    *, engine: str, model: str, effort: str, budget: float,
    prompt: str, schema: dict[str, Any], cwd: Path, timeout: int,
    handle: RunHandle | None = None,
) -> dict[str, Any]:
    if engine not in {"codex", "claude-code"}:
        raise ValueError("Agent auteur inconnu (attendu : codex ou claude-code)")
    if model and not re.fullmatch(r"[A-Za-z0-9._:-]{1,100}", model):
        raise ValueError("Nom de modèle auteur invalide")
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="agentl-authoring-") as temp:
        schema_path = Path(temp) / "output.schema.json"
        schema_path.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
        last_message = Path(temp) / "last-message.txt"
        if engine == "codex":
            executable = shutil.which("codex")
            if not executable:
                raise RuntimeError("Codex CLI n’est pas installé sur le serveur")
            args = [
                executable, "exec", "--ephemeral", "--sandbox", "read-only",
                "--skip-git-repo-check", "-C", str(cwd),
                "--output-schema", str(schema_path),
                # Le message final va dans un fichier : stdout porte aussi le
                # préambule et les événements, où aucune heuristique de
                # découpage JSON ne tient.
                "--output-last-message", str(last_message),
                "--color", "never",
            ]
            if model:
                args += ["--model", model]
            if effort in {"low", "medium", "high", "xhigh", "max"}:
                args += ["--config", f'model_reasoning_effort="{effort}"']
            args.append(prompt)
        else:
            executable = shutil.which("claude")
            if not executable:
                raise RuntimeError("Claude Code n’est pas installé sur le serveur")
            args = [
                executable, "--print", "--output-format", "json",
                "--json-schema", json.dumps(schema, ensure_ascii=False),
                "--permission-mode", "plan", "--tools", "",
                "--no-session-persistence",
            ]
            if model:
                args += ["--model", model]
            if effort in {"low", "medium", "high", "xhigh", "max"}:
                args += ["--effort", effort]
            if budget > 0:
                args += ["--max-budget-usd", f"{budget:.4f}"]
            args.append(prompt)
        # `Popen` plutôt que `run` : le bouton Arrêter doit pouvoir atteindre
        # le CLI auteur, maintenant que la requête HTTP ne le porte plus.
        process = subprocess.Popen(
            args, cwd=cwd, env=_subprocess_env(), text=True,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        if handle is not None:
            handle.process = process
            if handle.cancelled:                # arrêté pendant la préparation
                process.kill()
        try:
            stdout, stderr = process.communicate(timeout=min(max(timeout, 30), 600))
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.communicate()
            raise RuntimeError(f"{engine} a dépassé le timeout de {timeout}s") from exc
        finally:
            if handle is not None:
                handle.process = None
        if handle is not None and handle.cancelled:
            raise AuthoringCancelled(f"{engine} arrêté à la demande")
        if process.returncode != 0:
            detail = (stderr or stdout).strip()[-3000:]
            raise RuntimeError(f"{engine} a échoué (code {process.returncode}) : {detail}")
        if engine == "claude-code":
            result, usage, cost = _authoring_result_from_claude(stdout)
        else:
            payload = (last_message.read_text(encoding="utf-8")
                       if last_message.is_file() else stdout)
            result, usage, cost = _extract_json(payload), {}, 0.0
        return {
            **result,
            "mode": engine,
            "model": model or "configuration CLI par défaut",
            "usage": usage,
            "cost_usd": round(cost, 6),
            "cost_measured": engine == "claude-code",
            "duration_ms": int((time.monotonic() - started) * 1000),
        }


def authoring_context(skills: list[dict[str, Any]]) -> str:
    """Ce que l'auteur doit avoir sous les yeux pour écrire de l'AGENT-L.

    L'agent auteur tourne sans outil de lecture : ce qui n'est pas dans le
    prompt n'existe pas pour lui. Le contrat de grammaire — l'autorité
    extraite de l'AST du parseur — passe donc en entier et en premier.
    """
    blocks: list[str] = []
    try:
        blocks.append("## Contrat de grammaire AGENT-L (autorité)\n"
                      + GRAMMAR_CONTRACT.read_text(encoding="utf-8"))
    except OSError:                                     # pragma: no cover
        pass
    for skill in skills:
        blocks.append(f"## Skill {skill['name']}\n{skill['content'][:60_000]}")
    if not any(skill["slug"] == "agentl-author" for skill in skills):
        try:
            blocks.append("## Contrat d'auteur\n"
                          + AUTHOR_SKILL.read_text(encoding="utf-8"))
        except OSError:                                 # pragma: no cover
            pass
    return "\n\n".join(blocks)[:180_000]


def assistant_draft(
    project: dict[str, Any], prompt: str, skills: list[dict[str, Any]],
    remaining_budget: float | None = None, *, handle: RunHandle | None = None,
) -> dict[str, Any]:
    config = project.get("config", {})
    engine = str(config.get("authoringAgent") or default_authoring_agent())
    model = str(config.get("authoringModel") or "")
    effort = str(config.get("authoringEffort") or "high")
    budget = max(float(remaining_budget if remaining_budget is not None
                       else config.get("authoringBudgetUsd", 2.5)), 0.0)
    entry = project_entrypoint(project)
    current_agent = read_project_file(project, entry.name)
    current_host = read_project_file(project, entry.with_suffix(".py").name)
    instructions = """Tu es l’agent de codage auteur dans AGENT-L Studio. Modifie un couple AGENT-L X.agent / X.py.
Respecte strictement le contrat de grammaire fourni ci-dessous : il est extrait de l’AST du parseur
et fait autorité sur toute prose. Respecte ensuite les skills, la norme de nommage et la syntaxe existante.
Le modèle qui t’exécute comme auteur est distinct du LLM appelé plus tard par l’agent AGENT-L.
La configuration runtimeProvider/runtimeModel décrit cet oracle d’exécution. Dans l’hôte Python,
conserve `runtime_llm.build_runtime_llm(...)` afin que Gemini, Anthropic, OpenAI, local ou Mock
soient résolus au runtime sans confondre leur rôle avec le tien.
Rends les deux sources complètes. Ne prétends jamais qu’une porte passe sans validation."""
    input_text = (
        f"DEMANDE\n{prompt}\n\nCONFIGURATION\n{json.dumps(config, ensure_ascii=False)}\n\n"
        f"{authoring_context(skills)}\n\n"
        f"AGENT ACTUEL\n{current_agent}\n\nHÔTE ACTUEL\n{current_host}"
    )
    return run_authoring_agent(
        engine=engine, model=model, effort=effort, budget=budget,
        prompt=f"{instructions}\n\n{input_text}", schema=AGENT_DRAFT_SCHEMA,
        cwd=Path(project["path"]),
        timeout=_int_in(config, "authoringTimeoutSeconds", 300, 30, 600),
        handle=handle,
    )


def skill_draft(
    *, name: str, description: str, domain: str, instructions: str,
    engine: str, model: str, effort: str, budget: float,
) -> dict[str, Any]:
    author_contract = AUTHOR_SKILL.read_text(encoding="utf-8")
    grammar = ""
    try:
        grammar = GRAMMAR_CONTRACT.read_text(encoding="utf-8")
    except OSError:                                     # pragma: no cover
        pass
    prompt = f"""Tu es l’agent de codage auteur de skills spécialisés pour AGENT-L Studio.
Crée le contenu complet d’un SKILL.md destiné à guider Codex ou Claude Code lorsqu’il produit
des agents AGENT-L. Ce skill ne sera pas le LLM d’exécution de l’agent.

Nom : {name}
Domaine : {domain}
Description : {description}
Instructions métier : {instructions or 'Aucune instruction supplémentaire.'}

Le document commence par un frontmatter YAML délimité par --- portant `name` (en
minuscules avec tirets) et `description` (une phrase qui dit quand utiliser ce skill),
puis le corps Markdown. Il définit les invariants du domaine, une procédure ordonnée,
les pièges à éviter et les validations attendues. Il complète le contrat d’auteur
sans le recopier.

## Contrat de grammaire (autorité)
{grammar}

## Contrat d’auteur
{author_contract}
"""
    return run_authoring_agent(
        engine=engine, model=model, effort=effort, budget=max(budget, 0),
        prompt=prompt, schema=SKILL_DRAFT_SCHEMA, cwd=REPO_ROOT, timeout=300,
    )


def validate_draft(
    project: dict[str, Any], draft: dict[str, Any], handle: RunHandle | None = None,
) -> dict[str, Any]:
    """Éprouve une proposition sur les quatre portes, dans une copie du projet.

    `check` seul laissait passer des propositions qui cassaient `test`,
    `verify` ou `boundary` — et c'est `boundary` qui garde la frontière
    hôte/agent. La copie emporte `project.agentl.json`, `runtime_llm.py`
    et les skills, sans quoi les portes ne voient pas le vrai projet.

    Les portes partagent le budget `timeoutSeconds`, comme la suite. Sans
    run pour la porter, une poignée locale recueille leur sortie.
    """
    agent_source, host_source = draft.get("agent_source"), draft.get("host_source")
    if not isinstance(agent_source, str) or not isinstance(host_source, str):
        raise ValueError("La proposition ne contient pas les deux sources attendues")
    entry = project_entrypoint(project)
    config = project.get("config", {})
    handle = handle or RunHandle("validation")
    first = handle.mark()
    with tempfile.TemporaryDirectory(prefix="agentl-studio-") as temp:
        root = Path(temp) / "candidate"
        shutil.copytree(
            project["path"], root,
            ignore=shutil.ignore_patterns(".history", "__pycache__", "*.pyc"),
        )
        (root / entry.name).write_text(agent_source, encoding="utf-8")
        (root / entry.with_suffix(".py").name).write_text(host_source, encoding="utf-8")
        gates, diagnostics = run_gates(handle, root / entry.name, config, str(root), root,
                                       command_timeout(config), stop_on_failure=True)
    return {
        "gates": gates,
        "diagnostics": diagnostics,
        "passed": (len(gates) == len(QUALITY_GATES)
                   and all(gate["status"] == "passed" for gate in gates)),
        "validation": "".join(handle.since(first)[0])[-120_000:],
    }


def validate_and_apply_draft(
    project: dict[str, Any], draft: dict[str, Any], handle: RunHandle | None = None,
) -> dict[str, Any]:
    verdict = validate_draft(project, draft, handle)
    if not verdict["passed"]:
        return {"applied": False, **verdict}
    entry = project_entrypoint(project)
    write_project_file(project, entry.name, str(draft["agent_source"]))
    write_project_file(project, entry.with_suffix(".py").name, str(draft["host_source"]))
    return {"applied": True, **verdict}


# --------------------------------------------------------------------------
# l'agent auteur comme run : progression, arrêt, proposition en artefact
# --------------------------------------------------------------------------

def launch_authoring(
    store: Store, project: dict[str, Any], *, command: str, prompt: str,
    skills: list[dict[str, Any]], budget: float, apply: bool,
    author: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """L'agent auteur en tâche de fond, avec la même vie qu'un run.

    La création et l'assistant retenaient la requête HTTP jusqu'à dix
    minutes : ni progression ni arrêt, et un onglet fermé laissait appliquer
    un résultat que personne ne verrait. Le run porte désormais la
    proposition — sources dans ses artefacts, portes dans sa sortie — et le
    bouton Arrêter atteint le CLI auteur comme il atteint les portes.

    La rédaction ne prend pas le tour du projet : elle ne fait que lire. La
    validation et l'application le prennent, pour que les portes éprouvent
    exactement ce qui sera écrit.
    """
    author = author or assistant_draft
    config = project.get("config", {})
    engine = str(config.get("authoringAgent") or default_authoring_agent())
    label = "Claude Code" if engine == "claude-code" else "Codex"
    timeout = _int_in(config, "authoringTimeoutSeconds", 300, 30, 600)

    def body(handle: RunHandle) -> dict[str, Any]:
        artifacts = _artifact_root(handle.run_id)
        entry = project_entrypoint(project)
        (artifacts / "current.agent").write_text(
            read_project_file(project, entry.name), encoding="utf-8")
        (artifacts / "current.py").write_text(
            read_project_file(project, entry.with_suffix(".py").name), encoding="utf-8")
        metadata: dict[str, Any] = {"engine": engine, "costUsd": 0.0, "costMeasured": False,
                                    "events": [], "metrics": {}, "applied": None}

        def stopped(message: str) -> dict[str, Any]:
            handle.append(f"■ {message}\n")
            return {"status": "cancelled", "exit_code": None,
                    "output": handle.output(), "metadata": metadata}

        handle.append(f"→ {label} rédige la proposition (délai {timeout} s)…\n")
        try:
            draft = author(project, prompt, skills, budget, handle=handle)
        except AuthoringCancelled:
            return stopped("Arrêté : rien n'a été écrit.")
        except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
            handle.append(f"‼ {label} n'a pas rendu de proposition : {exc}\n")
            return {"status": "failed", "exit_code": 2, "output": handle.output(),
                    "metadata": {**metadata, "error": str(exc)}}
        if handle.cancelled:
            return stopped("Arrêté : rien n'a été écrit.")

        summary = str(draft.get("summary") or "Proposition générée.")
        metadata.update({
            "engine": draft.get("mode") or engine, "model": draft.get("model"),
            "costUsd": float(draft.get("cost_usd", 0) or 0),
            "costMeasured": bool(draft.get("cost_measured")),
            "usage": draft.get("usage", {}), "summary": summary,
        })
        seconds = int(draft.get("duration_ms", 0) or 0) // 1000
        handle.append(f"✓ Proposition reçue en {seconds} s : {summary}\n")
        agent_source, host_source = draft.get("agent_source"), draft.get("host_source")
        if not isinstance(agent_source, str) or not isinstance(host_source, str):
            handle.append("‼ La proposition ne contient pas les deux sources attendues.\n")
            return {"status": "failed", "exit_code": 2, "output": handle.output(),
                    "metadata": {**metadata, "error": "sources absentes de la proposition"}}
        (artifacts / "draft.agent").write_text(agent_source, encoding="utf-8")
        (artifacts / "draft.py").write_text(host_source, encoding="utf-8")
        metadata["artifacts"] = _artifacts_of(artifacts)
        if not apply:
            return {"status": "passed", "exit_code": 0, "output": handle.output(),
                    "metadata": metadata}

        if not _acquire_turn(handle, project["id"]):
            return stopped("Arrêté avant la validation : rien n'a été écrit.")
        try:
            handle.append("→ Les quatre portes éprouvent la proposition dans une copie du projet.\n")
            application = validate_and_apply_draft(project, draft, handle=handle)
        finally:
            PROJECT_LOCKS.of(project["id"]).release()
        applied = bool(application.get("applied"))
        metadata.update({"applied": applied, "gates": application.get("gates", []),
                         "diagnostics": application.get("diagnostics", [])})
        handle.append("\n✓ Appliquée : les quatre portes passent.\n" if applied else
                      "\n✗ Refusée par les portes : le programme n'a pas été modifié.\n")
        return {"status": "cancelled" if handle.cancelled else "passed" if applied else "failed",
                "exit_code": 0 if applied else 2, "output": handle.output(),
                "metadata": metadata}

    return _spawn(store, project, command, body, exclusive=False)


def read_draft(run: dict[str, Any]) -> dict[str, Any]:
    """La proposition portée par un run d'auteur, prête à relire ou appliquer."""
    metadata = run.get("metadata") or {}
    artifacts = metadata.get("artifacts") or {}

    def text(name: str) -> str | None:
        path = artifacts.get(name)
        return Path(path).read_text(encoding="utf-8") if path and Path(path).is_file() else None

    agent_source, host_source = text("draftAgent"), text("draftHost")
    if agent_source is None or host_source is None:
        raise ValueError("Cette exécution ne porte aucune proposition")
    applied = metadata.get("applied")
    return {
        "run_id": run["id"], "summary": metadata.get("summary"),
        "agent_source": agent_source, "host_source": host_source,
        "mode": metadata.get("engine"), "model": metadata.get("model"),
        "cost_usd": metadata.get("costUsd", 0),
        "cost_measured": metadata.get("costMeasured", False),
        "current": {"agent": text("currentAgent") or "", "host": text("currentHost") or ""},
        "application": None if applied is None else {
            "applied": bool(applied), "passed": bool(applied),
            "gates": metadata.get("gates", []),
            "diagnostics": metadata.get("diagnostics", []),
            "validation": str(run.get("output") or "")[-120_000:],
        },
    }


# --------------------------------------------------------------------------
# fin de vie : arrêt avant suppression, rétention des artefacts
# --------------------------------------------------------------------------

def stop_project_runs(project_id: str, wait: float = 10.0) -> bool:
    """Arrête les runs d'un projet et attend leur fin ; vrai si tous ont fini.

    Supprimer un projet faisait un `rmtree` sous un run actif : le processus
    continuait d'écrire dans un dossier disparu, et son run restait orphelin.
    """
    handles = REGISTRY.active(project_id)
    for handle in handles:
        handle.cancel()
    deadline = time.monotonic() + wait
    for handle in handles:
        handle.finished.wait(max(deadline - time.monotonic(), 0))
    return all(handle.finished.is_set() for handle in handles)


def artifacts_kept() -> int:
    """Nombre de runs dont un projet garde les artefacts."""
    try:
        return max(int(os.environ.get("AGENTL_STUDIO_KEEP_ARTIFACTS", "50")), 1)
    except ValueError:
        return 50


def remove_run_artifacts(run_ids: list[str]) -> None:
    for run_id in run_ids:
        try:
            shutil.rmtree(safe_child(RUNS_ROOT, run_id), ignore_errors=True)
        except ValueError:
            continue


def prune_artifacts(store: Store, project_id: str) -> int:
    """Ne garde les artefacts que des derniers runs du projet.

    `runs/<id>` n'était jamais purgé : traces, graphes, journaux et
    propositions s'accumulaient sans fin. Les lignes de run restent en base ;
    seule la pièce jointe des plus anciens part, et l'API la dit absente.
    """
    try:
        rows = [(run_id, status) for run_id, status in store.run_ids(project_id)
                if (RUNS_ROOT / run_id).is_dir()]
        stale = [run_id for run_id, status in rows[artifacts_kept():] if status != "running"]
        remove_run_artifacts(stale)
        return len(stale)
    except Exception:                   # la purge ne fait jamais échouer un run
        return 0

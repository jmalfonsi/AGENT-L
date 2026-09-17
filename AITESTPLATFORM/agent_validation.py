"""Validation d'un agent produit, exécutée par la plateforme et non par l'auteur.

C'est le contrepoids de la génération : l'agent de codage écrit, ce module
constate. Les deux ne partagent aucun code, et la validation ne lit que les
fichiers déposés dans le répertoire du projet.

Pour AGENT-L la chaîne est celle du skill — check → test → verify → boundary →
run — chaque étape étant un processus `agentl` distinct dont on garde le code de
sortie et la sortie brute. Une étape n'est jamais « réputée passée ».
"""
from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from live_progress import emit_live_event

AGENTL_ROOT = Path(__file__).resolve().parent.parent
STEP_TIMEOUT = int(os.environ.get("AGENT_FACTORY_STEP_TIMEOUT", "300"))

#: (identifiant, arguments après le nom de la sous-commande, bloquant)
AGENTL_CHAIN: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    ("check", (), True),
    ("test", (), True),
    ("verify", (), True),
    ("boundary", (), True),
    ("run", ("--ticks", "12", "--quiet"), False),
)

STEP_MEANING = {
    "check": "Analyse statique du programme (diagnostics E/W).",
    "test": "Exécution des SCENARIO déclarés dans le programme.",
    "verify": "Preuve sur l'AST qu'aucune action interdite n'est atteignable.",
    "boundary": "Contrôle de la frontière programme / hôte.",
    "run": "Exécution réelle sur l'hôte de simulation du projet.",
}


@dataclass
class StepResult:
    id: str
    passed: bool
    blocking: bool
    exitCode: int | None
    output: str
    meaning: str
    skipped: bool = False


@dataclass
class ValidationReport:
    frameworkId: str
    passed: bool
    steps: list[dict] = field(default_factory=list)
    files: dict = field(default_factory=dict)
    diagnosis: str = ""

    def as_dict(self) -> dict:
        return {
            "frameworkId": self.frameworkId,
            "passed": self.passed,
            "steps": self.steps,
            "files": self.files,
            "diagnosis": self.diagnosis,
        }


def _python() -> str:
    return os.environ.get("AGENT_FACTORY_PYTHON", sys.executable)


def _run_agentl(command: str, extra: tuple[str, ...], agent_path: Path) -> StepResult:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(
        filter(None, [str(AGENTL_ROOT), os.environ.get("PYTHONPATH", "")]))}
    argv = [_python(), "-m", "agentl", command, str(agent_path), *extra]
    try:
        completed = subprocess.run(
            argv, cwd=str(agent_path.parent), env=env,
            capture_output=True, text=True, timeout=STEP_TIMEOUT,
        )
        exit_code, output = completed.returncode, (completed.stdout + completed.stderr)
    except subprocess.TimeoutExpired:
        exit_code, output = None, f"`agentl {command}` a dépassé {STEP_TIMEOUT} secondes."
    return StepResult(
        id=command, passed=exit_code == 0, blocking=False, exitCode=exit_code,
        output=output.strip()[-6000:], meaning=STEP_MEANING[command],
    )


def _scenario_count(source: str) -> int:
    return sum(1 for line in source.splitlines() if line.strip().startswith("SCENARIO "))


def validate_agent_l(project_dir: Path, stem: str) -> ValidationReport:
    """Chaîne complète sur la paire `stem.agent` / `stem.py` du projet."""
    agent_path = project_dir / f"{stem}.agent"
    host_path = project_dir / f"{stem}.py"
    report = ValidationReport(frameworkId="agent_l", passed=False)
    report.files = {
        "agent": agent_path.name if agent_path.is_file() else None,
        "host": host_path.name if host_path.is_file() else None,
    }

    missing = [p.name for p in (agent_path, host_path) if not p.is_file()]
    if missing:
        report.diagnosis = (
            "Invariant de nommage non tenu : " + ", ".join(missing) + " absent(s). "
            "Un agent AGENT-L est une paire X.agent / X.py dans le même répertoire.")
        return report

    source = agent_path.read_text(encoding="utf-8")
    scenarios = _scenario_count(source)
    report.steps.append(StepResult(
        id="scenarios", passed=scenarios > 0, blocking=True, exitCode=0,
        output=f"{scenarios} SCENARIO déclaré(s).",
        meaning="Présence de critères d'acceptation exécutables.",
    ).__dict__)

    for command, extra, blocking in AGENTL_CHAIN:
        emit_live_event("validation_step", f"agentl {command}", step=command)
        step = _run_agentl(command, extra, agent_path)
        step.blocking = blocking
        emit_live_event("validation_step_end", f"agentl {command}", step=command,
                        status="success" if step.passed else "error")
        report.steps.append(step.__dict__)
        if blocking and not step.passed:
            for later, _extra, later_blocking in AGENTL_CHAIN[AGENTL_CHAIN.index((command, extra, blocking)) + 1:]:
                report.steps.append(StepResult(
                    id=later, passed=False, blocking=later_blocking, exitCode=None,
                    output="", meaning=STEP_MEANING[later], skipped=True,
                ).__dict__)
            break

    blocking_failures = [s for s in report.steps if s["blocking"] and not s["passed"]]
    report.passed = not blocking_failures
    if report.passed:
        weak = [s["id"] for s in report.steps if not s["passed"]]
        report.diagnosis = (
            "Chaîne bloquante franchie."
            + (f" Étapes non bloquantes en échec : {', '.join(weak)}." if weak else "")
        )
    else:
        first = blocking_failures[0]
        report.diagnosis = (
            f"Échec bloquant à l'étape « {first['id']} » ({first['meaning']}) : "
            f"{first['output'][:900] or 'aucune sortie'}"
        )
    return report


def validate_generic(project_dir: Path, framework_id: str, stem: str) -> ValidationReport:
    """Validation des frameworks sans vérification statique.

    Ces frameworks n'offrent ni preuve sur l'AST ni contrôle de frontière : on
    se limite donc à ce qui est réellement constatable — le module se charge et
    expose bien un point d'entrée. Ne pas le présenter comme équivalent à la
    chaîne AGENT-L.
    """
    module_path = project_dir / f"{stem}_{framework_id}.py"
    report = ValidationReport(frameworkId=framework_id, passed=False)
    report.files = {"module": module_path.name if module_path.is_file() else None}
    if not module_path.is_file():
        report.diagnosis = f"Module attendu absent : {module_path.name}."
        return report

    env = {**os.environ, "PYTHONPATH": os.pathsep.join(
        filter(None, [str(project_dir), os.environ.get("PYTHONPATH", "")]))}
    probe = (
        "import importlib.util,sys;"
        f"spec=importlib.util.spec_from_file_location('generated',r'{module_path}');"
        "m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);"
        "sys.exit(0 if hasattr(m,'build') else 3)"
    )
    try:
        completed = subprocess.run(
            [_python(), "-c", probe], cwd=str(project_dir), env=env,
            capture_output=True, text=True, timeout=STEP_TIMEOUT,
        )
        exit_code, output = completed.returncode, completed.stdout + completed.stderr
    except subprocess.TimeoutExpired:
        exit_code, output = None, "Chargement du module interrompu par le délai."

    report.steps.append(StepResult(
        id="import", passed=exit_code == 0, blocking=True, exitCode=exit_code,
        output=output.strip()[-4000:],
        meaning="Le module se charge et expose `build()`.",
    ).__dict__)
    report.passed = exit_code == 0
    report.diagnosis = (
        "Module chargeable. Aucune vérification statique n'existe pour ce "
        "framework : la conformité au cahier des charges reste non prouvée."
        if report.passed else
        f"Le module ne se charge pas (code {exit_code}) : {output.strip()[:600]}"
    )
    return report


def validate(project_dir: Path, framework_id: str, stem: str) -> dict:
    if framework_id == "agent_l":
        return validate_agent_l(project_dir, stem).as_dict()
    return validate_generic(project_dir, framework_id, stem).as_dict()

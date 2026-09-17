from __future__ import annotations

import os
import re
import sys
from pathlib import Path


STUDIO_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = STUDIO_ROOT.parent

# Le dépôt n'est pas installé : le rendre importable ici permet de lire la
# version réelle du paquet et du contrat d'auteur plutôt que de les recopier.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def load_env_files() -> list[Path]:
    """Charge `STUDIO/.env` puis `AGENT-L/.env`.

    Une variable déjà présente dans l'environnement gagne toujours : le
    fichier complète, il ne redéfinit pas ce que l'opérateur a exporté.
    """
    loaded: list[Path] = []
    for candidate in (STUDIO_ROOT / ".env", REPO_ROOT / ".env"):
        if not candidate.is_file():
            continue
        try:
            lines = candidate.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for raw in lines:
            line = raw.strip().removeprefix("export ").strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            name = name.strip()
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) and name not in os.environ:
                os.environ[name] = value
        loaded.append(candidate)
    return loaded


load_env_files()

DATA_ROOT = Path(
    os.environ.get("AGENTL_STUDIO_DATA_DIR", STUDIO_ROOT / ".data")
).expanduser().resolve()
PROJECTS_ROOT = DATA_ROOT / "projects"
RUNS_ROOT = DATA_ROOT / "runs"
DB_PATH = DATA_ROOT / "studio.sqlite3"

GRAMMAR_CONTRACT = REPO_ROOT / "SKILLS/agentl-author/references/generated/grammar-contract.md"
AUTHOR_SKILL = REPO_ROOT / "SKILLS/agentl-author/SKILL.md"
SYSTEM_SKILLS_ROOT = REPO_ROOT / "SKILLS"


def ensure_data_dirs() -> None:
    for path in (DATA_ROOT, PROJECTS_ROOT, RUNS_ROOT):
        path.mkdir(parents=True, exist_ok=True)


def safe_child(root: Path, *parts: str) -> Path:
    """Resolve a path and reject attempts to escape its assigned root."""
    target = root.joinpath(*parts).resolve()
    if target != root and root not in target.parents:
        raise ValueError("Chemin hors de l’espace de travail")
    return target


def agentl_version() -> str:
    """Version réelle du paquet, jamais une constante recopiée."""
    try:
        from agentl import __version__

        return str(__version__)
    except Exception:  # pragma: no cover - dépôt incomplet
        return "inconnue"


def authoring_contract_version() -> str:
    """Version du contrat d'auteur, lue dans le contrat généré."""
    try:
        text = GRAMMAR_CONTRACT.read_text(encoding="utf-8")
    except OSError:  # pragma: no cover - contrat absent
        return "inconnue"
    match = re.search(r"Contrat d'auteur\s*:\s*`([^`]+)`", text)
    return match.group(1) if match else "inconnue"

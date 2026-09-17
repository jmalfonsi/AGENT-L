from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import (AUTHOR_SKILL, PROJECTS_ROOT, REPO_ROOT, SYSTEM_SKILLS_ROOT,
                     safe_child)
from .runtime_template import (RUNTIME_LLM_SOURCE, RUNTIME_TEMPLATE_MARKER,
                               RUNTIME_TEMPLATE_VERSION)


def deaccent(value: str) -> str:
    """« Métier » → « Metier » : un identifiant AGENT-L reste en ASCII."""
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", deaccent(value).lower().strip()).strip("-")
    return (slug or "agentl-project")[:54]


def identifier(value: str) -> str:
    """Nom d'agent AGENT-L : minuscules, chiffres et souligné, jamais vide."""
    ident = re.sub(r"[^a-z0-9_]+", "_", deaccent(value).lower().strip()).strip("_")
    if not ident or ident[0].isdigit():
        ident = f"agent_{ident}" if ident else "agent"
    return ident[:48]


#: Catalogue des skills livrés avec le Studio. Le contenu vit dans
#: `SKILLS/<slug>/SKILL.md` : un skill système se relit et se compare aux
#: skills produits par l'interface, au lieu d'être une chaîne dans du code.
SYSTEM_SKILLS = [
    {
        "id": "system-agentl-author",
        "name": "agentl-author",
        "slug": "agentl-author",
        "description": "Crée et corrige des agents conformes au contrat AGENT-L courant.",
        "domain": "engineering",
        "source": "system",
    },
    {
        "id": "system-policy-guard",
        "name": "policy-guard",
        "slug": "policy-guard",
        "description": "Spécialise les politiques, preuves, approbations et effets gouvernés.",
        "domain": "governance",
        "source": "system",
    },
    {
        "id": "system-society-architect",
        "name": "society-architect",
        "slug": "society-architect",
        "description": "Conçoit des sociétés multi-agents avec messages et mémoire partagée.",
        "domain": "multi-agent",
        "source": "system",
    },
    {
        "id": "system-resilience-tester",
        "name": "resilience-tester",
        "slug": "resilience-tester",
        "description": "Produit des scénarios de panne, de dérive et des cas holdout.",
        "domain": "quality",
        "source": "system",
    },
]


def load_system_skill_content(skill: dict[str, Any]) -> str:
    path = (AUTHOR_SKILL if skill["slug"] == "agentl-author"
            else SYSTEM_SKILLS_ROOT / skill["slug"] / "SKILL.md")
    return path.read_text(encoding="utf-8")


def system_skill_definition(slug: str) -> dict[str, Any] | None:
    return next((skill for skill in SYSTEM_SKILLS if skill["slug"] == slug), None)


def shipped_skill_content(slug: str) -> str | None:
    """Contenu livré avec le dépôt, la version de référence d'un skill système."""
    definition = system_skill_definition(slug)
    if definition is None:
        return None
    try:
        return load_system_skill_content(definition)
    except OSError:
        return None


def seed_skills(store: Any) -> None:
    for definition in SYSTEM_SKILLS:
        try:
            content = load_system_skill_content(definition)
        except OSError:                 # skill retiré du dépôt : ne pas planter
            continue
        # `seed_skill` respecte une version personnalisée : le semis tourne à
        # chaque démarrage et écrasait sinon toute édition.
        store.seed_skill({**definition, "content": content})


def unique_project_slug(store: Any, name: str) -> str:
    base = slugify(name)
    used = {project["slug"] for project in store.list_projects()}
    if base not in used:
        return base
    index = 2
    while f"{base}-{index}" in used:
        index += 1
    return f"{base}-{index}"


def skill_markdown_problem(content: str) -> str | None:
    """Dit pourquoi un SKILL.md n'en est pas un, ou rien s'il est valide.

    La convention du dépôt — celle de `SKILLS/agentl-author/SKILL.md` — est
    un frontmatter YAML `name`/`description` suivi du corps Markdown.
    Exiger un `#` en première ligne refusait exactement ce que la
    convention demande, et ce que l'agent auteur produit.
    """
    text = content.lstrip()
    if text.startswith("---"):
        closing = re.search(r"\n---\s*(\n|$)", text[3:])
        if closing is None:
            return "Le frontmatter ouvert par --- n’est jamais refermé"
        header = text[3:3 + closing.start()]
        if not re.search(r"^\s*name\s*:", header, re.MULTILINE):
            return "Le frontmatter doit porter un champ `name`"
        if not re.search(r"^\s*description\s*:", header, re.MULTILINE):
            return "Le frontmatter doit porter un champ `description`"
        body = text[3 + closing.end():]
        if not body.strip():
            return "Le SKILL.md n’a que son frontmatter, sans contenu"
        return None
    if text.startswith("#"):
        return None
    return ("Un SKILL.md commence par un frontmatter YAML (name, description) "
            "ou par un titre Markdown")


def deployed_template_version(path: Path) -> str | None:
    """Version du gabarit inscrite en tête d'un `runtime_llm.py` déployé."""
    if not path.is_file():
        return None
    try:
        first = path.read_text(encoding="utf-8").splitlines()[0]
    except (OSError, IndexError):
        return None
    if first.startswith(RUNTIME_TEMPLATE_MARKER):
        return first[len(RUNTIME_TEMPLATE_MARKER):].strip()
    return "1"                          # v1 ne portait pas encore de marqueur


def archive(root: Path, relative: str) -> Path | None:
    """Copie un fichier dans `.history/<horodatage>/` avant de l'écraser."""
    source = safe_child(root, relative)
    if not source.is_file():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = safe_child(root, ".history", stamp, relative)
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, backup)
    return backup


def ensure_runtime_adapter(root: Path, config: dict[str, Any]) -> bool:
    """Installe ou met à jour l'adaptateur runtime du projet.

    Un correctif du gabarit — la clé Gemini passée en en-tête plutôt qu'en
    query string, par exemple — ne sert à rien s'il ne descend pas dans les
    projets déjà créés. La version inscrite en tête décide : différente,
    l'ancien fichier part dans `.history/` et le nouveau le remplace.
    """
    runtime_path = safe_child(root, "runtime_llm.py")
    deployed = deployed_template_version(runtime_path)
    updated = False
    if deployed != RUNTIME_TEMPLATE_VERSION:
        if deployed is not None:
            archive(root, "runtime_llm.py")
        runtime_path.write_text(RUNTIME_LLM_SOURCE, encoding="utf-8")
        updated = True

    entrypoint = str(config.get("entrypoint") or "")
    if not entrypoint.endswith(".agent"):
        return updated
    host_path = safe_child(root, str(Path(entrypoint).with_suffix(".py")))
    if not host_path.is_file():
        return updated
    host_source = host_path.read_text(encoding="utf-8")
    patched = host_source
    if "from runtime_llm import build_runtime_llm" not in patched:
        patched = patched.replace(
            "from agentl import Host, MockLLM, Symbol",
            "from agentl import Host, MockLLM, Symbol\nfrom runtime_llm import build_runtime_llm",
        )
    if "build_runtime_llm(" not in patched:
        patched = re.sub(
            r"llms\s*=\s*\{([^}]*)\}",
            lambda m: "llms = {" + re.sub(
                r"MockLLM\(\)", "build_runtime_llm(MockLLM())", m.group(1)) + "}",
            patched,
        )
        patched = patched.replace("return host, llm", "return host, build_runtime_llm(llm)")
    if patched != host_source:
        host_path.write_text(patched, encoding="utf-8")
        updated = True
    return updated


def _check_passes(agent_source: str, host_source: str) -> bool:
    """`agentl check` sur un couple candidat, dans un temporaire jetable."""
    with tempfile.TemporaryDirectory(prefix="agentl-scaffold-") as temp:
        root = Path(temp)
        (root / "candidate.agent").write_text(agent_source, encoding="utf-8")
        (root / "candidate.py").write_text(host_source, encoding="utf-8")
        merged = os.environ.copy()
        merged["PYTHONPATH"] = str(REPO_ROOT) + (
            os.pathsep + merged["PYTHONPATH"] if merged.get("PYTHONPATH") else "")
        completed = subprocess.run(
            [sys.executable, "-m", "agentl", "check", str(root / "candidate.agent")],
            cwd=root, env=merged, text=True, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=60, check=False,
        )
        return completed.returncode == 0


def apply_roles(
    agent_source: str, host_source: str,
    original_names: list[str], roles: list[dict[str, Any]],
) -> tuple[str, str, list[str]]:
    """Renomme les agents du squelette d'après les rôles saisis.

    Les noms d'agents sont des identifiants portés aussi par les cibles de
    `MESSAGE` et par les clés d'hôte : la substitution est faite sur mot
    entier, dans les deux fichiers à la fois. Le résultat n'est retenu que
    s'il passe encore `agentl check` — sinon le squelette d'origine reste,
    et l'appelant est informé de ce qui n'a pas été appliqué.
    """
    applied: list[str] = []
    agent_out, host_out = agent_source, host_source
    for original, role in zip(original_names, roles):
        target = identifier(str(role.get("name") or ""))
        if not target or target == original or target in original_names[len(applied) + 1:]:
            continue
        pattern = re.compile(rf"\b{re.escape(original)}\b")
        agent_out = pattern.sub(target, agent_out)
        host_out = pattern.sub(target, host_out)
        applied.append(target)
        mission = " ".join(str(role.get("mission") or "").split())
        if mission:
            agent_out = re.sub(
                rf"(AGENT {re.escape(target)} \{{\s*\n(?:.*\n)??\s*DESCRIPTION )\"[^\"]*\"",
                lambda m: m.group(1) + json.dumps(mission[:200], ensure_ascii=False),
                agent_out, count=1,
            )
    if not applied:
        return agent_source, host_source, []
    if not _check_passes(agent_out, host_out):
        return agent_source, host_source, []
    return agent_out, host_out, applied


def scaffold_project(
    slug: str,
    name: str,
    description: str,
    config: dict[str, Any],
    skills: list[dict[str, Any]],
) -> Path:
    root = safe_child(PROJECTS_ROOT, slug)
    root.mkdir(parents=True, exist_ok=False)
    architecture = config.get("architecture", "single")
    if architecture == "multi":
        source = REPO_ROOT / "examples/soc_team.agent"
        host = REPO_ROOT / "examples/soc_team.py"
        basename = "agent_society"
        original_names = ["soc_analyst", "network_agent"]
    else:
        source = REPO_ROOT / "SKILLS/agentl-author/references/generated/canonical.agent"
        host = REPO_ROOT / "SKILLS/agentl-author/references/generated/canonical.py"
        basename = "approval_guard"
        original_names = ["canonical_guarded_workflow"]

    roles = [role for role in (config.get("roles") or []) if str(role.get("name") or "").strip()]
    agent_source, host_source, renamed = apply_roles(
        source.read_text(encoding="utf-8"), host.read_text(encoding="utf-8"),
        original_names, roles,
    )
    (root / f"{basename}.agent").write_text(agent_source, encoding="utf-8")
    (root / f"{basename}.py").write_text(host_source, encoding="utf-8")

    project_config = {
        "schema": "agentl.studio.project.v1",
        "name": name,
        "description": description,
        "entrypoint": f"{basename}.agent",
        **config,
        "skills": [skill["slug"] for skill in skills],
    }
    (root / "project.agentl.json").write_text(
        json.dumps(project_config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    ensure_runtime_adapter(root, project_config)
    (root / "agents.json").write_text(
        json.dumps({
            "architecture": architecture,
            "roles": roles,
            "agents": renamed or original_names,
            "rolesApplied": bool(renamed),
            "uncoveredRoles": [
                str(role.get("name")) for role in roles[len(original_names):]
            ],
        }, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    skill_root = root / ".agentl" / "skills"
    for skill in skills:
        target = skill_root / skill["slug"]
        target.mkdir(parents=True, exist_ok=True)
        (target / "SKILL.md").write_text(skill["content"], encoding="utf-8")
    agent_list = ", ".join(f"`{item}`" for item in (renamed or original_names))
    (root / "README.md").write_text(
        f"# {name}\n\n{description or 'Projet créé avec AGENT-L Studio.'}\n\n"
        f"Entrée : `{basename}.agent`\n\nAgents : {agent_list}\n",
        encoding="utf-8",
    )
    return root

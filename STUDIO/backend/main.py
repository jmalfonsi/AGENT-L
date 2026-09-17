from __future__ import annotations

import json
import os
import secrets
import shutil
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .config import (PROJECTS_ROOT, REPO_ROOT, STUDIO_ROOT, agentl_version,
                     authoring_contract_version, safe_child)
from .services import (
    ProjectBusy,
    assistant_draft,
    available_authoring_agents,
    await_run,
    cancel_run,
    default_authoring_agent,
    launch_authoring,
    launch_command,
    launch_quality_suite,
    launch_replay,
    list_history,
    list_project_files,
    project_entrypoint,
    project_turn,
    read_draft,
    read_history_file,
    read_project_file,
    remove_run_artifacts,
    restore_history_file,
    skill_draft,
    stop_project_runs,
    stream_run,
    validate_and_apply_draft,
    validate_draft,
    write_project_file,
)
from .store import Store
from .templates import (ensure_runtime_adapter, scaffold_project, seed_skills,
                        shipped_skill_content, skill_markdown_problem,
                        unique_project_slug)


store = Store()
# Le registre des runs vit en mémoire : ce qui tournait à l'arrêt du serveur
# ne finira jamais. Le dire, plutôt que de le montrer « en cours » à vie.
store.interrupt_orphan_runs()
seed_skills(store)

#: Jeton facultatif. Quand `AGENTL_STUDIO_TOKEN` est posé, toute écriture par
#: l'API doit le porter. La validation de l'en-tête `Host`, elle, est toujours
#: active : c'est elle qui ferme le rebinding DNS contre un service local.
SESSION_TOKEN = os.environ.get("AGENTL_STUDIO_TOKEN", "")
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1", "testserver"}

#: Noms de variables d'environnement acceptés pour une clé runtime. Un nom
#: libre transformait le réglage en canal d'exfiltration : le projet pouvait
#: nommer n'importe quel secret du serveur — `AWS_SECRET_ACCESS_KEY`, par
#: exemple — et l'adaptateur l'aurait envoyé à l'URL de base déclarée. Une
#: liste explicite, plus le suffixe `_API_KEY` qui ne désigne rien d'autre
#: qu'une clé de fournisseur, plus ce que l'opérateur ajoute lui-même.
API_KEY_ENV_ALLOWLIST = {
    "GEMINI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY",
}
API_KEY_ENV_PATTERN = r"^[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_API_KEY$"


def allowed_key_envs() -> set[str]:
    extra = os.environ.get("AGENTL_STUDIO_EXTRA_KEY_ENVS", "")
    return API_KEY_ENV_ALLOWLIST | {
        name.strip() for name in extra.split(",") if name.strip()
    }


def key_env_allowed(name: str) -> bool:
    import re

    return name in allowed_key_envs() or bool(re.fullmatch(API_KEY_ENV_PATTERN, name))

RUNTIME_MODEL_SUGGESTIONS: dict[str, list[str]] = {
    "google-gemini": ["gemini-3.5-flash-lite"],
    "anthropic": ["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5-20251001"],
    "openai": [],
    "openai-compatible": [],
    "mock": ["mock-deterministic"],
}


def default_runtime() -> dict[str, Any]:
    """Gemini dès qu'une clé existe ; mock sinon, pour rester déterministe.

    Le mock garde les portes, les tests et le rejeu reproductibles hors
    ligne : c'est un bon défaut d'atelier, pas un bon défaut quand une clé
    d'exécution est disponible.
    """
    if os.environ.get("GEMINI_API_KEY"):
        return {"runtimeProvider": "google-gemini",
                "runtimeModel": RUNTIME_MODEL_SUGGESTIONS["google-gemini"][0],
                "runtimeApiKeyEnv": "GEMINI_API_KEY"}
    return {"runtimeProvider": "mock", "runtimeModel": "mock-deterministic",
            "runtimeApiKeyEnv": ""}


def default_project_config() -> dict[str, Any]:
    return {
        "architecture": "single",
        "authoringAgent": default_authoring_agent(),
        "authoringModel": "",
        "authoringEffort": "high",
        "authoringBudgetUsd": 2.5,
        "authoringTimeoutSeconds": 300,
        "generateOnCreate": True,
        **default_runtime(),
        "runtimeBaseUrl": "",
        "runtimeMaxCostUsd": 1.0,
        "runtimeMaxOutputTokens": 4096,
        "runtimePricePerMTokIn": 0.0,
        "runtimePricePerMTokOut": 0.0,
        "maxTicks": 6,
        "timeoutSeconds": 90,
        "verifyDepth": 4,
        "maxAttempts": 4,
        "maxCases": 200,
        "autoloopModel": "",
        "roles": [],
    }


DEFAULT_PROJECT_CONFIG: dict[str, Any] = default_project_config()

KNOWN_CONFIG_KEYS = set(DEFAULT_PROJECT_CONFIG) | {"schema", "name", "description",
                                                   "entrypoint", "skills"}


def validate_project_config(config: dict[str, Any]) -> None:
    import re

    if config.get("architecture") not in {"single", "multi"}:
        raise ValueError("Architecture invalide")
    if config.get("authoringAgent") not in {"codex", "claude-code"}:
        raise ValueError("Agent auteur invalide")
    provider = config.get("runtimeProvider")
    if provider not in {"mock", "google-gemini", "anthropic", "openai", "openai-compatible"}:
        raise ValueError("Fournisseur runtime invalide")
    if provider != "mock" and not str(config.get("runtimeModel") or "").strip():
        raise ValueError("Le modèle runtime est requis pour ce fournisseur")
    key_name = str(config.get("runtimeApiKeyEnv") or "")
    if key_name and not key_env_allowed(key_name):
        raise ValueError(
            f"Variable de clé refusée : {key_name}. Noms admis : "
            f"{', '.join(sorted(allowed_key_envs()))}, ou toute variable en "
            "majuscules finissant par _API_KEY. Élargir la liste avec "
            "AGENTL_STUDIO_EXTRA_KEY_ENVS."
        )
    base_url = str(config.get("runtimeBaseUrl") or "")
    if base_url:
        if provider != "openai-compatible":
            raise ValueError("L’URL de base n’est admise que pour un runtime compatible OpenAI")
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("L’URL de base runtime doit utiliser HTTP ou HTTPS")
    autoloop_model = str(config.get("autoloopModel") or "")
    if autoloop_model and not re.fullmatch(r"(gemini|claude)[A-Za-z0-9._:-]{0,90}", autoloop_model):
        raise ValueError("Le modèle d’autoloop doit être un gemini-* ou un claude-*")
    unknown = set(config) - KNOWN_CONFIG_KEYS
    if unknown:
        raise ValueError(f"Réglage inconnu : {', '.join(sorted(unknown))}")
    for key, minimum, maximum in (
        ("authoringBudgetUsd", 0, 1000), ("authoringTimeoutSeconds", 30, 600),
        ("runtimeMaxCostUsd", 0, 1000), ("runtimeMaxOutputTokens", 64, 100_000),
        ("runtimePricePerMTokIn", 0, 10_000), ("runtimePricePerMTokOut", 0, 10_000),
        ("maxTicks", 1, 100), ("timeoutSeconds", 5, 300), ("verifyDepth", 1, 12),
        ("maxAttempts", 1, 10), ("maxCases", 10, 2000),
    ):
        try:
            value = float(config.get(key, DEFAULT_PROJECT_CONFIG[key]))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Valeur invalide pour {key}") from exc
        if not minimum <= value <= maximum:
            raise ValueError(f"{key} doit être compris entre {minimum} et {maximum}")


def _seed_demo_project() -> None:
    if store.list_projects():
        return
    skill_ids = ["system-agentl-author", "system-policy-guard"]
    skills = store.get_skills(skill_ids)
    config = {
        **DEFAULT_PROJECT_CONFIG,
        "roles": [{"name": "approval-guard", "mission": "Gouverner et vérifier une demande d’approbation"}],
    }
    slug = "approval-guard"
    root = scaffold_project(
        slug, "Approval Guard", "Exemple gouverné prêt à vérifier et à exécuter.", config, skills
    )
    store.create_project({
        "name": "Approval Guard", "slug": slug,
        "description": "Exemple gouverné prêt à vérifier et à exécuter.",
        "path": str(root), "config": config, "skills": skill_ids,
    })


_seed_demo_project()


def _migrate_project_configs() -> None:
    """Aligne les projets existants sur la configuration et le gabarit courants."""
    for project in store.list_projects():
        current = project.get("config", {})
        migrated = {**DEFAULT_PROJECT_CONFIG, **current}
        if "authoringModel" not in current and current.get("model"):
            migrated["authoringModel"] = current["model"]
        if "authoringBudgetUsd" not in current and "maxCost" in current:
            migrated["authoringBudgetUsd"] = current["maxCost"]
        migrated = {key: value for key, value in migrated.items() if key in KNOWN_CONFIG_KEYS}
        if migrated != current:
            store.update_project(project["id"], {"config": migrated})
        config_path = Path(project["path"]) / "project.agentl.json"
        if config_path.is_file():
            disk = json.loads(config_path.read_text(encoding="utf-8"))
            disk.update(migrated)
            config_path.write_text(json.dumps(disk, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8")
            # Fait descendre les correctifs du gabarit runtime dans les
            # projets déjà créés : sans cela, un correctif de sécurité reste
            # dans le dépôt et jamais sur le disque.
            ensure_runtime_adapter(Path(project["path"]), disk)


_migrate_project_configs()

app = FastAPI(title="AGENT-L Studio API", version=agentl_version())
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def guard_local_only(request: Request, call_next):
    """Ferme le rebinding DNS, et exige le jeton quand il est configuré.

    Un middleware `http` n'est pas couvert par les gestionnaires
    d'exception de FastAPI : le refus doit être une réponse, pas une levée.
    """
    host = (request.headers.get("host") or "").split(":")[0].strip("[]")
    if host and host not in {name.strip("[]") for name in ALLOWED_HOSTS}:
        return JSONResponse(
            {"detail": "En-tête Host non autorisé pour un service local"}, status_code=400)
    if (SESSION_TOKEN and request.url.path.startswith("/api")
            and request.method not in {"GET", "HEAD", "OPTIONS"}
            and not secrets.compare_digest(
                request.headers.get("x-studio-token", ""), SESSION_TOKEN)):
        return JSONResponse({"detail": "Jeton de session absent ou invalide"}, status_code=401)
    return await call_next(request)


class ProjectCreate(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    description: str = Field(default="", max_length=500)
    config: dict[str, Any] = Field(default_factory=dict)
    skills: list[str] = Field(default_factory=list)
    #: Faux : le projet revient aussitôt, avec le run de génération à suivre.
    #: Vrai (défaut, pour les scripts) : la requête attend la fin du run.
    wait: bool = True

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return " ".join(value.split())


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=80)
    description: str | None = Field(default=None, max_length=500)
    status: str | None = None
    config: dict[str, Any] | None = None
    skills: list[str] | None = None


class FileWrite(BaseModel):
    content: str


class RunCreate(BaseModel):
    command: str
    run_id: str | None = None
    wait: bool = True


class RestoreRequest(BaseModel):
    stamp: str = Field(min_length=4, max_length=40)
    path: str = Field(min_length=1, max_length=300)


class SkillWrite(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    description: str = Field(default="", max_length=300)
    domain: str = Field(default="general", max_length=50)
    content: str = Field(min_length=20, max_length=100_000)


class AssistantRequest(BaseModel):
    prompt: str = Field(min_length=3, max_length=12_000)
    apply: bool = False
    wait: bool = True


class ApplyDraftRequest(BaseModel):
    summary: str = Field(default="", max_length=4000)
    agent_source: str = Field(min_length=10, max_length=1_000_000)
    host_source: str = Field(min_length=10, max_length=1_000_000)


class SkillDraftRequest(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    description: str = Field(default="", max_length=500)
    domain: str = Field(default="general", max_length=50)
    instructions: str = Field(default="", max_length=12_000)
    authoring_agent: str = "codex"
    authoring_model: str = ""
    authoring_effort: str = "high"
    budget_usd: float = Field(default=1.0, ge=0, le=100)


def project_or_404(project_id: str) -> dict[str, Any]:
    project = store.get_project(project_id)
    if not project:
        raise HTTPException(404, "Projet introuvable")
    return project


def run_or_404(run_id: str) -> dict[str, Any]:
    run = store.get_run(run_id)
    if not run:
        raise HTTPException(404, "Exécution introuvable")
    return run


@app.get("/api/health")
def health() -> dict[str, Any]:
    available = available_authoring_agents()
    return {
        "status": "ok",
        "agentlVersion": agentl_version(),
        "contractVersion": authoring_contract_version(),
        "codexAvailable": available["codex"],
        "claudeCodeAvailable": available["claude-code"],
        "defaultAuthoringAgent": default_authoring_agent(),
        "tokenRequired": bool(SESSION_TOKEN),
    }


@app.get("/api/dashboard")
def dashboard() -> dict[str, Any]:
    return {
        "stats": store.stats(),
        "projects": store.list_projects()[:5],
        "runs": store.list_runs(limit=8),
    }


@app.get("/api/projects")
def projects() -> list[dict[str, Any]]:
    return store.list_projects()


CREATION_PROMPT = (
    "Produis l’implémentation initiale complète de ce projet à partir de son nom, "
    "de sa description, de son architecture, de ses rôles et des skills sélectionnés. "
    "Le résultat doit être immédiatement exécutable et couvrir la mission décrite."
)

#: Attente maximale d'un run d'auteur quand l'appelant demande `wait` : le
#: délai de l'auteur (600 s au plus) plus celui des quatre portes.
AUTHORING_WAIT_SECONDS = 900


@app.post("/api/projects", status_code=201)
async def create_project(payload: ProjectCreate) -> dict[str, Any]:
    skills = store.get_skills(payload.skills)
    if len(skills) != len(set(payload.skills)):
        raise HTTPException(400, "Un ou plusieurs skills sont introuvables")
    config = {**DEFAULT_PROJECT_CONFIG, **payload.config}
    try:
        validate_project_config(config)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    slug = unique_project_slug(store, payload.name)
    try:
        root = scaffold_project(slug, payload.name, payload.description, config, skills)
    except Exception as exc:
        raise HTTPException(400, f"Création impossible : {exc}") from exc
    created = store.create_project({
        "name": payload.name, "slug": slug, "description": payload.description,
        "path": str(root), "config": config, "skills": payload.skills,
    })
    created["scaffold"] = json.loads((root / "agents.json").read_text(encoding="utf-8"))
    if not bool(config.get("generateOnCreate", True)):
        return created

    # La génération est un run : elle se suit, s'arrête, et ne retient plus
    # la requête — ni l'assistant de création — jusqu'à dix minutes.
    run = await run_in_threadpool(
        launch_authoring, store, created, command="authoring-create",
        prompt=CREATION_PROMPT, skills=skills,
        budget=float(config.get("authoringBudgetUsd", 2.5)), apply=True,
        author=assistant_draft,
    )
    created["authoringRun"] = run
    if not payload.wait:
        return created
    finished = await run_in_threadpool(await_run, store, run["id"], AUTHORING_WAIT_SECONDS)
    metadata = finished.get("metadata") or {}
    applied = bool(metadata.get("applied"))
    if applied:
        warning = None
    elif metadata.get("gates"):
        warning = "La proposition initiale n’a pas passé les quatre portes ; le gabarit sûr a été conservé."
    else:
        warning = (f"Agent auteur indisponible : {metadata.get('error') or finished.get('status')}. "
                   "Le gabarit sûr a été conservé.")
    created["authoringRun"] = finished
    created["authoring"] = {
        "applied": applied, "engine": metadata.get("engine"),
        "summary": metadata.get("summary"), "gates": metadata.get("gates", []),
        "warning": warning,
    }
    return created


@app.get("/api/projects/{project_id}")
def get_project(project_id: str) -> dict[str, Any]:
    project = project_or_404(project_id)
    project["files"] = list_project_files(project)
    project["runs"] = store.list_runs(project_id, limit=20)
    # Le point d'entrée tel que le projet le déclare : « le premier `.agent`
    # de la liste » n'en est plus un dès qu'un projet en contient deux.
    try:
        project["entrypoint"] = project_entrypoint(project).name
    except (ValueError, OSError, KeyError, json.JSONDecodeError):
        project["entrypoint"] = None
    return project


@app.patch("/api/projects/{project_id}")
def update_project(project_id: str, payload: ProjectUpdate) -> dict[str, Any]:
    project = project_or_404(project_id)
    changes = payload.model_dump(exclude_none=True)
    if "status" in changes and changes["status"] not in {"active", "archived"}:
        raise HTTPException(400, "Statut invalide")
    if "skills" in changes and len(store.get_skills(changes["skills"])) != len(set(changes["skills"])):
        raise HTTPException(400, "Un ou plusieurs skills sont introuvables")
    if "config" in changes:
        changes["config"] = {**DEFAULT_PROJECT_CONFIG, **project.get("config", {}), **changes["config"]}
        try:
            validate_project_config(changes["config"])
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    updated = store.update_project(project_id, changes)
    if "config" in changes or "skills" in changes:
        config_path = safe_child(Path(project["path"]), "project.agentl.json")
        disk_config = json.loads(config_path.read_text(encoding="utf-8"))
        if "config" in changes:
            disk_config.update(changes["config"])
        if "skills" in changes:
            selected = store.get_skills(changes["skills"])
            disk_config["skills"] = [skill["slug"] for skill in selected]
            for skill in selected:
                target = safe_child(Path(project["path"]), ".agentl", "skills", skill["slug"], "SKILL.md")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(skill["content"], encoding="utf-8")
        config_path.write_text(json.dumps(disk_config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        ensure_runtime_adapter(Path(project["path"]), disk_config)
    return updated  # type: ignore[return-value]


@app.delete("/api/projects/{project_id}", status_code=204)
def delete_project(project_id: str) -> None:
    project = project_or_404(project_id)
    root = Path(project["path"]).resolve()
    # Le garde-fou compare au slug **stocké** : le recalculer depuis le nom
    # rendait indestructible tout projet renommé après sa création.
    if root.parent != PROJECTS_ROOT or root.name != project["slug"]:
        raise HTTPException(400, "Suppression refusée : chemin de projet inattendu")
    # Arrêter d'abord : un `rmtree` sous un run actif laissait son processus
    # écrire dans un dossier disparu, et le run orphelin.
    if not stop_project_runs(project_id):
        raise HTTPException(409, "Un run de ce projet ne s’arrête pas : suppression reportée")
    run_ids = [run_id for run_id, _ in store.run_ids(project_id)]
    store.delete_project(project_id)
    remove_run_artifacts(run_ids)
    if root.exists():
        shutil.rmtree(root)


@app.get("/api/projects/{project_id}/files")
def project_files(project_id: str) -> list[dict[str, Any]]:
    return list_project_files(project_or_404(project_id))


@app.get("/api/projects/{project_id}/file")
def project_file(project_id: str, path: str = Query(min_length=1)) -> dict[str, str]:
    try:
        return {"path": path, "content": read_project_file(project_or_404(project_id), path)}
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.put("/api/projects/{project_id}/file")
def save_project_file(project_id: str, payload: FileWrite, path: str = Query(min_length=1)) -> dict[str, Any]:
    try:
        project = project_or_404(project_id)
        write_project_file(project, path, payload.content)
        return {"saved": True, "path": path, "files": list_project_files(project)}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/projects/{project_id}/history")
def project_history(project_id: str) -> list[dict[str, Any]]:
    return list_history(project_or_404(project_id))


@app.get("/api/projects/{project_id}/history/file")
def project_history_file(
    project_id: str, stamp: str = Query(min_length=4), path: str = Query(min_length=1),
) -> dict[str, str]:
    try:
        content = read_history_file(project_or_404(project_id), stamp, path)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"stamp": stamp, "path": path, "content": content}


@app.post("/api/projects/{project_id}/history/restore")
def project_history_restore(project_id: str, payload: RestoreRequest) -> dict[str, Any]:
    project = project_or_404(project_id)
    try:
        restore_history_file(project, payload.stamp, payload.path)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"restored": True, "path": payload.path, "files": list_project_files(project)}


@app.get("/api/skills")
def skills() -> list[dict[str, Any]]:
    return store.list_skills()


@app.post("/api/skills", status_code=201)
def create_skill(payload: SkillWrite) -> dict[str, Any]:
    from .templates import slugify

    slug = slugify(payload.name)
    if any(skill["slug"] == slug for skill in store.list_skills()):
        raise HTTPException(409, "Un skill portant ce nom existe déjà")
    problem = skill_markdown_problem(payload.content)
    if problem:
        raise HTTPException(400, problem)
    return store.upsert_skill({**payload.model_dump(), "slug": slug, "source": "custom"})


@app.put("/api/skills/{skill_id}")
def update_skill(skill_id: str, payload: SkillWrite) -> dict[str, Any]:
    """Tout skill s'édite, système compris.

    Un skill système garde sa source et son slug : l'édition est marquée
    `customized`, ce qui la protège du semis au démarrage suivant. La
    version livrée reste lisible dans le dépôt, et `reset` y ramène.
    """
    existing = store.get_skill(skill_id)
    if not existing:
        raise HTTPException(404, "Skill introuvable")
    problem = skill_markdown_problem(payload.content)
    if problem:
        raise HTTPException(400, problem)
    system = existing["source"] == "system"
    return store.upsert_skill({
        **payload.model_dump(), "id": skill_id, "slug": existing["slug"],
        "source": existing["source"],
        # Un skill système renommé garde le nom sous lequel les projets le
        # sélectionnent : seul son contenu compte pour l'agent auteur.
        "name": existing["name"] if system else payload.name,
        "customized": True,
    })


@app.get("/api/skills/{skill_id}/origin")
def skill_origin(skill_id: str) -> dict[str, Any]:
    """Version livrée avec le dépôt, pour comparer avant de réinitialiser."""
    existing = store.get_skill(skill_id)
    if not existing:
        raise HTTPException(404, "Skill introuvable")
    content = shipped_skill_content(existing["slug"])
    if content is None:
        raise HTTPException(404, "Ce skill n’a pas de version livrée")
    return {"slug": existing["slug"], "content": content,
            "differs": content != existing["content"]}


@app.post("/api/skills/{skill_id}/reset")
def reset_skill(skill_id: str) -> dict[str, Any]:
    existing = store.get_skill(skill_id)
    if not existing:
        raise HTTPException(404, "Skill introuvable")
    content = shipped_skill_content(existing["slug"])
    if content is None:
        raise HTTPException(400, "Ce skill n’a pas de version livrée à restaurer")
    restored = store.reset_skill(existing["slug"], content)
    if restored is None:                                # pragma: no cover - concurrent
        raise HTTPException(404, "Skill introuvable")
    return restored


@app.delete("/api/skills/{skill_id}", status_code=204)
def delete_skill(skill_id: str) -> None:
    existing = store.get_skill(skill_id)
    if not existing:
        raise HTTPException(404, "Skill introuvable")
    if existing["source"] == "system":
        # Le semis le réinstallerait au démarrage suivant : une suppression
        # qui revient toute seule est pire qu'un refus. « Réinitialiser »
        # est le geste qui existe vraiment ici.
        raise HTTPException(
            403,
            "Un skill système est réinstallé à chaque démarrage : éditez-le, "
            "ou réinitialisez-le à sa version livrée.",
        )
    store.delete_skill(skill_id)


@app.post("/api/skills/draft")
async def draft_skill(payload: SkillDraftRequest) -> dict[str, Any]:
    """Rédige un SKILL.md, et laisse une trace de la tentative.

    Un draft ne créait aucune ligne : réussi ou échoué, il ne restait rien
    à consulter après coup, et la seule preuve vivait dans l'onglet du
    navigateur. Le journal est ici la seule mémoire de l'appel — le
    contenu produit, lui, n'est enregistré qu'après relecture.
    """
    run = store.create_run(None, "skill-draft")
    try:
        draft = await run_in_threadpool(
            skill_draft,
            name=payload.name, description=payload.description,
            domain=payload.domain, instructions=payload.instructions,
            engine=payload.authoring_agent, model=payload.authoring_model,
            effort=payload.authoring_effort, budget=payload.budget_usd,
        )
        store.finish_run(
            run["id"], status="passed",
            duration_ms=int(draft.get("duration_ms", 0) or 0), exit_code=0,
            output=str(draft.get("summary", "")),
            metadata={
                "skill": payload.name, "domain": payload.domain,
                "engine": draft.get("mode"), "model": draft.get("model"),
                "costUsd": float(draft.get("cost_usd", 0) or 0),
                "costMeasured": bool(draft.get("cost_measured")),
                "characters": len(str(draft.get("content") or "")),
                "saved": False,
            },
        )
        return draft
    except (ValueError, RuntimeError, json.JSONDecodeError) as exc:
        store.finish_run(
            run["id"], status="failed", duration_ms=0, exit_code=2,
            output=str(exc),
            metadata={"skill": payload.name, "engine": payload.authoring_agent,
                      "costUsd": 0, "saved": False},
        )
        raise HTTPException(502, str(exc)) from exc


@app.get("/api/runs")
def runs(project_id: str | None = None) -> list[dict[str, Any]]:
    return store.list_runs(project_id, limit=100)


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    return run_or_404(run_id)


@app.post("/api/projects/{project_id}/runs")
async def start_run(project_id: str, payload: RunCreate) -> dict[str, Any]:
    project = project_or_404(project_id)
    try:
        if payload.command == "quality-suite":
            run = await run_in_threadpool(launch_quality_suite, store, project)
        elif payload.command == "replay":
            if not payload.run_id:
                raise ValueError("run_id est requis pour le rejeu")
            original = run_or_404(payload.run_id)
            if original["project_id"] != project_id:
                raise ValueError("Le journal n’appartient pas à ce projet")
            run = await run_in_threadpool(launch_replay, store, project, original)
        else:
            run = await run_in_threadpool(launch_command, store, project, payload.command)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not payload.wait:
        return run
    return await run_in_threadpool(await_run, store, run["id"])


@app.get("/api/runs/{run_id}/stream")
def run_stream(run_id: str, start: int = Query(0, alias="from", ge=0)) -> StreamingResponse:
    run_or_404(run_id)
    return StreamingResponse(
        stream_run(store, run_id, start), media_type="text/event-stream",
        headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
    )


@app.delete("/api/runs/{run_id}")
def stop_run(run_id: str) -> dict[str, Any]:
    run_or_404(run_id)
    return {"cancelled": cancel_run(run_id)}


@app.get("/api/runs/{run_id}/trace-html")
def trace_html(run_id: str) -> FileResponse:
    return _artifact_response(run_id, "trace", "text/html", "Trace HTML absente")


@app.get("/api/runs/{run_id}/graph-html")
def graph_html(run_id: str) -> FileResponse:
    return _artifact_response(run_id, "graph", "text/html", "Graphe absent")


def _artifact_response(run_id: str, name: str, media: str, missing: str) -> FileResponse:
    run = run_or_404(run_id)
    path = run.get("metadata", {}).get("artifacts", {}).get(name)
    if not path or not Path(path).is_file():
        raise HTTPException(404, missing)
    return FileResponse(path, media_type=media)


@app.get("/api/runs/{run_id}/record")
def replay_record(run_id: str) -> FileResponse:
    run = run_or_404(run_id)
    record = run.get("metadata", {}).get("artifacts", {}).get("record")
    if not record or not Path(record).is_file():
        raise HTTPException(404, "Journal absent")
    return FileResponse(record, media_type="application/json", filename=f"agentl-run-{run_id[:8]}.json")


@app.get("/api/runs/{run_id}/corrected")
def corrected_source(run_id: str) -> dict[str, Any]:
    """Programme corrigé par `autoloop`, proposé et jamais appliqué d'office."""
    run = run_or_404(run_id)
    path = run.get("metadata", {}).get("artifacts", {}).get("corrected")
    if not path or not Path(path).is_file():
        raise HTTPException(404, "Aucun programme corrigé : la boucle n’a rien réécrit")
    return {"content": Path(path).read_text(encoding="utf-8")}


@app.get("/api/runs/{run_id}/draft")
def run_draft(run_id: str) -> dict[str, Any]:
    """Proposition portée par un run d'auteur, à relire ou à appliquer."""
    try:
        return read_draft(run_or_404(run_id))
    except (ValueError, OSError) as exc:
        raise HTTPException(404, str(exc)) from exc


def _authoring_spent(project_id: str) -> float:
    """Tout ce que l'agent auteur a coûté, création initiale comprise."""
    return sum(
        float(run.get("metadata", {}).get("costUsd", 0) or 0)
        for run in store.list_runs(project_id, limit=1000)
        if run["command"] in {"authoring-draft", "authoring-create"}
    )


@app.post("/api/projects/{project_id}/assistant")
async def project_assistant(project_id: str, payload: AssistantRequest) -> dict[str, Any]:
    """Demande une proposition à l'agent auteur.

    Avec `wait: false`, rend aussitôt le run à suivre ; la proposition se lit
    ensuite sur `/api/runs/{id}/draft`. Sinon, attend et rend la proposition.
    """
    project = project_or_404(project_id)
    selected = store.get_skills(project.get("skills", []))
    spent = _authoring_spent(project_id)
    max_cost = max(float(project.get("config", {}).get("authoringBudgetUsd", 2.5)), 0.0)
    remaining = max_cost - spent
    if max_cost <= 0 or remaining <= 0:
        raise HTTPException(402, "Le budget de l’agent auteur est épuisé ou désactivé")
    run = await run_in_threadpool(
        launch_authoring, store, project, command="authoring-draft",
        prompt=payload.prompt, skills=selected, budget=remaining,
        apply=payload.apply, author=assistant_draft,
    )
    if not payload.wait:
        return run
    finished = await run_in_threadpool(await_run, store, run["id"], AUTHORING_WAIT_SECONDS)
    try:
        draft = read_draft(finished)
    except ValueError as exc:
        detail = (finished.get("metadata") or {}).get("error") or str(exc)
        raise HTTPException(502, detail) from exc
    draft["project_cost_usd"] = round(spent + float(draft.get("cost_usd") or 0), 6)
    draft["project_budget_usd"] = max_cost
    return draft


@app.post("/api/projects/{project_id}/draft/validate")
async def draft_validate(project_id: str, payload: ApplyDraftRequest) -> dict[str, Any]:
    project = project_or_404(project_id)
    try:
        return await run_in_threadpool(validate_draft, project, payload.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/projects/{project_id}/draft/apply")
async def draft_apply(project_id: str, payload: ApplyDraftRequest) -> dict[str, Any]:
    project = project_or_404(project_id)

    def apply() -> dict[str, Any]:
        # L'écriture prend le tour du projet : les portes éprouvent ce qui
        # sera écrit, pas une source qu'un run relit au même moment.
        with project_turn(project_id):
            return validate_and_apply_draft(project, payload.model_dump())

    try:
        return await run_in_threadpool(apply)
    except ProjectBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/projects/{project_id}/runtime-test")
async def runtime_test(project_id: str) -> dict[str, Any]:
    """Un aller-retour réel vers l'oracle du projet, avant de compter dessus."""
    project = project_or_404(project_id)

    def probe() -> dict[str, Any]:
        import importlib.util
        import time as _time

        path = Path(project["path"]) / "runtime_llm.py"
        if not path.is_file():
            raise RuntimeError("Adaptateur runtime absent du projet")
        spec = importlib.util.spec_from_file_location(f"runtime_llm_{project_id}", path)
        module = importlib.util.module_from_spec(spec)          # type: ignore[arg-type]
        spec.loader.exec_module(module)                          # type: ignore[union-attr]
        started = _time.monotonic()
        llm = module.build_runtime_llm()
        result = llm.reason("Réponds strictement au schéma.", {"ping": "studio"},
                            {"ok": "bool"})
        usage = getattr(getattr(llm, "meter", None), "snapshot", dict)()
        return {
            "ok": True,
            "provider": project["config"].get("runtimeProvider"),
            "model": project["config"].get("runtimeModel"),
            "latencyMs": int((_time.monotonic() - started) * 1000),
            "result": result,
            "usage": usage,
        }

    try:
        import sys as _sys

        _sys.path.insert(0, str(REPO_ROOT))
        return await run_in_threadpool(probe)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


@app.get("/api/settings")
def settings() -> dict[str, Any]:
    available = available_authoring_agents()
    return {
        "codexCli": available["codex"],
        "claudeCode": available["claude-code"],
        "authoring": {
            "codex": {"available": available["codex"], "command": "codex exec"},
            "claudeCode": {"available": available["claude-code"], "command": "claude --print"},
        },
        "runtimeCredentials": {
            "googleGemini": bool(os.environ.get("GEMINI_API_KEY")),
            "anthropic": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "openai": bool(os.environ.get("OPENAI_API_KEY")),
        },
        "allowedKeyEnvs": sorted(allowed_key_envs()),
        "runtimeModels": RUNTIME_MODEL_SUGGESTIONS,
        "defaults": DEFAULT_PROJECT_CONFIG,
        "repoRoot": str(REPO_ROOT),
        "dataRoot": str(STUDIO_ROOT / ".data"),
        "security": {
            "apiKeysStored": False,
            "commandsAllowlisted": True,
            "workspaceIsolated": True,
            "hostHeaderChecked": True,
            "tokenRequired": bool(SESSION_TOKEN),
            "apiKeyEnvAllowlisted": True,
        },
    }


DIST = STUDIO_ROOT / "frontend" / "dist"
if DIST.is_dir():
    app.mount("/", StaticFiles(directory=DIST, html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn

    if SESSION_TOKEN:
        print(f"Jeton de session actif — ouvrir http://127.0.0.1:8765/?token={SESSION_TOKEN}")
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8765, reload=False)

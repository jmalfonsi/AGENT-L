"""Versionnage immuable des cahiers des charges et agents de la fabrique.

Le répertoire ``<framework>/`` reste la version publiée pour compatibilité.
Les sources de chaque fabrication vivent sous ``versions/<framework>/`` et ne
sont jamais réutilisées comme répertoire de travail d'une autre fabrication.
"""
from __future__ import annotations

import copy
import shutil
from pathlib import Path
from typing import Any


def _positive_int(value: Any, default: int = 1) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def build_version_dir(project_dir: Path, framework_id: str, version: int,
                      build_id: str) -> Path:
    return project_dir / "versions" / framework_id / f"v{version}_{build_id}"


def spec_snapshot_path(project_dir: Path, revision: int) -> Path:
    return project_dir / "spec_versions" / f"v{revision}.txt"


def write_spec_snapshot(project_dir: Path, revision: int, text: str) -> str:
    path = spec_snapshot_path(project_dir, revision)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(text, encoding="utf-8")
    return path.relative_to(project_dir).as_posix()


def normalize_history(project: dict, project_dir: Path | None = None) -> bool:
    """Migre en mémoire un ancien manifeste et archive sa version publiée.

    Cette migration est paresseuse : les projets existants restent lisibles,
    puis sont réellement archivés à leur prochaine réanalyse ou génération.
    """
    changed = False
    revision = _positive_int(project.get("specRevision"), 1)
    if project.get("specRevision") != revision:
        project["specRevision"] = revision
        changed = True

    revisions = project.get("specRevisions")
    if not isinstance(revisions, list) or not revisions:
        project["specRevisions"] = [{
            "revision": revision,
            "specHash": project.get("specHash"),
            "createdAt": project.get("createdAt"),
            "origin": project.get("specOrigin"),
        }]
        changed = True

    counts: dict[str, int] = {}
    builds = project.setdefault("builds", [])
    for build in builds:
        framework_id = str(build.get("frameworkId", "unknown"))
        inferred = counts.get(framework_id, 0) + 1
        version = _positive_int(build.get("version"), inferred)
        counts[framework_id] = max(counts.get(framework_id, 0), version)
        defaults = {
            "version": version,
            "specRevision": revision,
            "specHash": project.get("specHash"),
        }
        for key, value in defaults.items():
            if build.get(key) != value and key not in build:
                build[key] = value
                changed = True

    if project_dir is None:
        return changed

    # L'ancien schéma ne gardait qu'un build par framework. Son répertoire
    # publié est donc bien l'artefact de ce build et peut être archivé en v1.
    latest_by_framework: dict[str, dict] = {}
    for build in builds:
        latest_by_framework[str(build.get("frameworkId"))] = build
    for framework_id, build in latest_by_framework.items():
        if build.get("artifactDir"):
            continue
        source = project_dir / framework_id
        target = build_version_dir(
            project_dir, framework_id, int(build["version"]), str(build.get("id", "legacy")))
        if source.is_dir() and not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        build["artifactDir"] = target.relative_to(project_dir).as_posix()
        changed = True
    return changed


def next_build_version(project: dict, framework_id: str) -> int:
    versions = [
        _positive_int(build.get("version"), 1)
        for build in project.get("builds", [])
        if build.get("frameworkId") == framework_id
    ]
    return max(versions, default=0) + 1


def published_build(project: dict, framework_id: str) -> dict | None:
    current_hash = project.get("specHash")
    candidates = [
        build for build in project.get("builds", [])
        if build.get("frameworkId") == framework_id
        and build.get("passed") is True
        and build.get("specHash") == current_hash
    ]
    return max(candidates, key=lambda build: _positive_int(build.get("version"), 1),
               default=None)


def latest_validated_build(project: dict, framework_id: str) -> dict | None:
    candidates = [
        build for build in project.get("builds", [])
        if build.get("frameworkId") == framework_id and build.get("passed") is True
    ]
    return max(candidates, key=lambda build: _positive_int(build.get("version"), 1),
               default=None)


def framework_state(project: dict, framework_id: str) -> dict:
    builds = [build for build in project.get("builds", [])
              if build.get("frameworkId") == framework_id]
    latest = max(builds, key=lambda build: _positive_int(build.get("version"), 1),
                 default=None)
    validated = latest_validated_build(project, framework_id)
    current = published_build(project, framework_id)
    return {
        "frameworkId": framework_id,
        "latestVersion": _positive_int(latest.get("version"), 1) if latest else 0,
        "nextVersion": next_build_version(project, framework_id),
        "latestBuildId": latest.get("id") if latest else None,
        "latestPassed": latest.get("passed") if latest else None,
        "validatedVersion": _positive_int(validated.get("version"), 1) if validated else None,
        "publishedVersion": _positive_int(validated.get("version"), 1) if validated else None,
        "publishedBuildId": validated.get("id") if validated else None,
        "currentVersion": _positive_int(current.get("version"), 1) if current else None,
        "currentBuildId": current.get("id") if current else None,
        "hasValidatedVersion": validated is not None,
        "upToDate": current is not None,
        "outdated": validated is not None and current is None,
    }


def project_view(project: dict) -> dict:
    view = copy.deepcopy(project)
    normalize_history(view)
    states = {
        framework_id: framework_state(view, framework_id)
        for framework_id in view.get("frameworkIds", [])
    }
    view["frameworkStates"] = states
    for build in view.get("builds", []):
        state = states.get(build.get("frameworkId"), {})
        build["currentSpec"] = build.get("specHash") == view.get("specHash")
        build["isPublished"] = build.get("id") == state.get("publishedBuildId")
    return view


def summary_builds(project: dict) -> list[dict]:
    view = project_view(project)
    summaries = []
    for framework_id in view.get("frameworkIds", []):
        state = view["frameworkStates"][framework_id]
        if state["latestVersion"] == 0:
            continue
        summaries.append(state)
    return summaries


def publish_version(version_dir: Path, current_dir: Path, build_id: str) -> None:
    """Publie une version validée avec restauration si le basculement échoue."""
    staged = current_dir.parent / f".{current_dir.name}.publish-{build_id}"
    backup = current_dir.parent / f".{current_dir.name}.backup-{build_id}"
    if staged.exists():
        shutil.rmtree(staged)
    shutil.copytree(version_dir, staged, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    moved_current = False
    try:
        if current_dir.exists():
            current_dir.rename(backup)
            moved_current = True
        staged.rename(current_dir)
    except Exception:
        if moved_current and backup.exists() and not current_dir.exists():
            backup.rename(current_dir)
        raise
    finally:
        if staged.exists():
            shutil.rmtree(staged)
    if backup.exists():
        shutil.rmtree(backup)

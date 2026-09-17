#!/usr/bin/env python3
"""Fabrique d'agents AITESTPLATFORM : cahier des charges → agent validé.

Le cycle est : ingestion → grille de complétude → (questions bloquantes) →
rédaction par l'agent de codage → validation par la plateforme → réparation.

Deux règles de séparation, tenues par la structure du code :

1. **L'analyste ne rédige pas, le rédacteur ne valide pas.** L'analyse tourne
   sans outil ; la validation est un processus tiers qui relit le disque.
2. **La réparation part du constat de validation, jamais d'un objectif de
   score.** On corrige ce que `agentl` a refusé, comme l'ancienne boucle du banc
   réparait sur la trace d'exécution et jamais sur les assertions du correcteur.
   Sans cette règle on n'écrit pas un agent, on optimise un score.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import sys
import time
import unicodedata
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
AGENTL_ROOT = ROOT.parent
AUTOMATIONBENCH_ROOT = AGENTL_ROOT.parent / "AutomationBench"
PROJECTS_ROOT = ROOT / "data" / "projects"
FACTORY_VERSION = "spec-to-agent-v2"
MAX_ATTEMPTS = int(os.environ.get("AGENT_FACTORY_ATTEMPTS", "3"))

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(AGENTL_ROOT))

import spec_analysis  # noqa: E402
import factory_versions  # noqa: E402
from agent_validation import validate  # noqa: E402
from codegen_backend import CodegenError, author, analyze  # noqa: E402
from codegen_backend import available as codegen_available  # noqa: E402
from framework_skills import FRAMEWORK_LABELS, FRAMEWORK_SKILLS  # noqa: E402
from live_progress import emit_live_event  # noqa: E402
from spec_intake import SpecError, load_spec  # noqa: E402

FRAMEWORK_IDS = ("agent_l", "langgraph", "crewai", "openai_agents")


def _slug(value: str) -> str:
    """Radical de fichier ASCII : « congés » ne doit pas devenir « cong_s »."""
    folded = unicodedata.normalize("NFKD", value.lower())
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    slug = re.sub(r"[^a-z0-9]+", "_", folded).strip("_")
    return (slug or "agent")[:48]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --------------------------------------------------------------------------- stockage

def _project_dir(project_id: str) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{4,64}", project_id):
        raise ValueError(f"Identifiant de projet invalide : {project_id}")
    return PROJECTS_ROOT / project_id


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _journal(project_id: str, entry: dict) -> None:
    path = _project_dir(project_id) / "journal.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.write(json.dumps({"at": _now(), **entry}, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _load_project(project_id: str) -> dict:
    path = _project_dir(project_id) / "project.json"
    if not path.is_file():
        raise FileNotFoundError(f"Projet inconnu : {project_id}")
    return json.loads(path.read_text(encoding="utf-8"))


def _save_project(project: dict) -> None:
    _write_json(_project_dir(project["id"]) / "project.json", project)


# --------------------------------------------------------------------------- étapes

def analyze_spec(*, text: str | None, path: str | None, framework_ids: list[str],
                 project_id: str | None = None) -> dict:
    """Ingère le cahier des charges et crée une révision non destructive."""
    unknown = [f for f in framework_ids if f not in FRAMEWORK_IDS]
    if unknown:
        raise ValueError(f"Framework inconnu : {', '.join(unknown)}")
    if not framework_ids:
        raise ValueError("Choisir au moins un framework cible.")

    spec = load_spec(text=text, path=path)
    project_id = project_id or f"prj_{uuid.uuid4().hex[:12]}"
    directory = _project_dir(project_id)
    directory.mkdir(parents=True, exist_ok=True)
    manifest = directory / "project.json"
    existing = json.loads(manifest.read_text(encoding="utf-8")) if manifest.is_file() else None
    if existing is not None:
        factory_versions.normalize_history(existing, directory)

    raw, facts = analyze(spec_analysis.build_prompt(spec["text"], framework_ids), workdir=directory)
    evaluation = spec_analysis.evaluate(raw, framework_ids)
    now = _now()

    if existing is None:
        revision = 1
        project = {
            "id": project_id,
            "createdAt": now,
            "builds": [],
            "specRevisions": [],
        }
    else:
        previous_hash = existing.get("specHash")
        revision = int(existing.get("specRevision", 1))
        if previous_hash != spec["specHash"]:
            revision += 1
        project = existing

    snapshot = factory_versions.write_spec_snapshot(directory, revision, spec["text"])
    project.update({
        "updatedAt": now,
        "factoryVersion": FACTORY_VERSION,
        "specRevision": revision,
        "specHash": spec["specHash"],
        "specOrigin": spec["origin"],
        "specCharCount": spec["charCount"],
        "specTruncated": spec["truncated"],
        "frameworkIds": framework_ids,
        "stem": project.get("stem") or _slug(evaluation["title"]),
        "analysis": evaluation,
        "analysisSession": facts,
    })
    revisions = project.setdefault("specRevisions", [])
    revision_entry = next((item for item in revisions
                           if int(item.get("revision", 0)) == revision), None)
    if revision_entry is None:
        revision_entry = {"revision": revision}
        revisions.append(revision_entry)
    revision_entry.update({
        "specHash": spec["specHash"],
        "createdAt": revision_entry.get("createdAt") or now,
        "origin": spec["origin"],
        "snapshot": snapshot,
        "verdict": evaluation["verdict"],
        "coverage": evaluation["coverage"],
    })
    (directory / "spec.txt").write_text(spec["text"], encoding="utf-8")
    _save_project(project)
    _journal(project_id, {"event": "analyzed", "specRevision": revision,
                          "verdict": evaluation["verdict"],
                          "coverage": evaluation["coverage"],
                          "missingRequired": evaluation["missingRequired"]})
    return factory_versions.project_view(project)


def answer_questions(project_id: str, answers: list[dict]) -> dict:
    """Complète le cahier des charges par les réponses du demandeur, puis réanalyse.

    Les réponses sont **ajoutées au texte** et l'ensemble est réanalysé depuis
    zéro : c'est le document complété qui fait foi, pas un correctif appliqué
    par-dessus un verdict précédent.
    """
    project = _load_project(project_id)
    directory = _project_dir(project_id)
    spec_text = (directory / "spec.txt").read_text(encoding="utf-8")

    additions = [
        f"- {str(item.get('question', '')).strip()}\n  Réponse : {str(item.get('answer', '')).strip()}"
        for item in answers if str(item.get("answer", "")).strip()
    ]
    if not additions:
        raise ValueError("Aucune réponse exploitable.")
    completed = spec_text + "\n\n## Précisions apportées par le demandeur\n" + "\n".join(additions)

    _journal(project_id, {"event": "answers_added", "count": len(additions)})
    return analyze_spec(text=completed, path=None,
                        framework_ids=project["frameworkIds"], project_id=project_id)


AGENT_L_BRIEF = """Tu écris un agent AGENT-L à partir du cahier des charges
ci-dessous, dans le répertoire courant.

Commence par invoquer le skill agentl-author (`/agentl-author`) et applique son
préflight de grammaire avant d'écrire la moindre ligne. Le contrat versionné du
dépôt fait autorité, pas ta mémoire du langage.

Livrables, exactement deux fichiers, dans le répertoire courant :
- `{stem}.agent` — le programme ;
- `{stem}.py` — son hôte, exposant `build()`.

Contraintes non négociables :
- L'hôte fournit des FAITS, le programme prend les DÉCISIONS. Lire une source,
  extraire un champ, apparier deux enregistrements, composer un texte : à
  l'hôte. Écarter un élément, choisir une priorité, décider d'un destinataire,
  boucler sur ce qu'il faut traiter : au programme.
- Aucun système externe n'est joignable ici. L'hôte est un hôte de
  **simulation** : il porte en dur un jeu de données représentatif tiré du
  cahier des charges, journalise chaque appel d'outil, et n'effectue aucune
  entrée-sortie réseau, fichier ou courriel. Marque clairement en tête de
  fichier les points de branchement qui devront être remplacés en production.
- Chaque interdit et chaque exigence d'approbation du cahier des charges doit
  apparaître dans la POLICY (`NEVER`, `REQUIRE_APPROVAL`), et non dans un `IF`
  du plan : c'est ce que `agentl verify` prouve.
- Déclare des `SCENARIO` qui traduisent les critères d'acceptation du cahier
  des charges, y compris au moins un scénario négatif prouvant qu'une action
  interdite ne se produit pas.

Avant de rendre la main, fais toi-même tourner la chaîne
`check` → `test` → `verify` → `boundary` → `run` et corrige jusqu'au vert. La
plateforme rejouera cette chaîne de son côté, sur les fichiers du disque.

Ne modifie aucun fichier hors du répertoire courant.

Termine par un rapport de dix lignes au plus : ce que fait l'agent, les
décisions laissées au programme, les interdits portés par la POLICY, et les
points de branchement à remplacer en production."""

#: Contraintes communes aux frameworks sans vérification statique.
GENERIC_COMMON = """
Contraintes communes :
- Aucun système externe n'est joignable ici : les outils manipulent un jeu de
  données représentatif porté en dur par le module, et journalisent chacun de
  leurs appels. Aucune entrée-sortie réseau, fichier ou courriel.
- Les règles métier du cahier des charges figurent dans les instructions de
  l'agent ET dans des gardes écrites à l'intérieur des outils. Une règle
  seulement énoncée dans le prompt n'est pas une garde : ce framework n'offre
  aucune preuve statique, donc l'outil lui-même doit refuser l'opération
  interdite et rendre une erreur explicite.
- Chaque interdit et chaque exigence d'approbation du cahier des charges doit
  être rattachable à une garde précise. Dis en commentaire, sur chaque garde,
  quelle phrase du cahier des charges elle traduit.
- Écris des tests exécutables dans le même fichier, sous
  `if __name__ == "__main__":`, appelant directement les outils — sans appel de
  modèle. Ils doivent couvrir les critères d'acceptation, dont au moins un cas
  négatif prouvant qu'une action interdite est refusée par la garde.
- Marque en tête de fichier les points de branchement à remplacer en production.

Ne modifie aucun fichier hors du répertoire courant.

Termine par un rapport de dix lignes au plus : ce que fait l'agent, où vivent
les règles de décision, les gardes qui portent les interdits, et les points de
branchement à remplacer en production."""

GENERIC_BRIEFS = {
    "langgraph": """Tu écris un agent LangGraph à partir du cahier des charges
ci-dessous, dans le répertoire courant.

Commence par invoquer le skill `/mastering-langgraph` et suis sa méthode.

Livrable : un seul fichier `{stem}_langgraph.py`, exposant `build()` qui rend le
graphe compilé, prêt à être invoqué (sans l'invoquer à l'import).

Contraintes propres à LangGraph :
- L'état est explicite : un `StateGraph` sur un état typé (`TypedDict` ou
  `MessagesState` étendu). Ce qui conditionne une décision doit figurer dans
  l'état, pas dans une variable capturée par une clôture.
- Les branchements métier passent par `add_conditional_edges` sur des fonctions
  pures et testables séparément — c'est le seul endroit du graphe qu'on peut
  vérifier sans appeler un modèle.
- Si le cahier des charges exige une validation humaine, elle se fait par
  `interrupt()` et non par une consigne dans le prompt.
""" + GENERIC_COMMON,

    "crewai": """Tu écris un agent CrewAI à partir du cahier des charges
ci-dessous, dans le répertoire courant.

Commence par invoquer le skill `/design-agent` et suis sa méthode.

Livrable : un seul fichier `{stem}_crewai.py`, exposant `build()` qui rend le
`Crew` prêt à être lancé (sans le lancer à l'import).

Contraintes propres à CrewAI :
- Applique la règle 80/20 du skill : la description de la tâche et son
  `expected_output` portent l'essentiel, le rôle de l'agent vient ensuite.
- Un seul agent par défaut. N'en ajoute un second que si le cahier des charges
  décrit deux métiers réellement distincts, et dis pourquoi dans ton rapport.
- Les outils sont des `BaseTool` avec un `args_schema` Pydantic explicite.
- `allow_delegation` reste désactivé sauf exigence explicite du cahier des
  charges : une délégation implicite rend la trace d'exécution illisible.
""" + GENERIC_COMMON,

    "openai_agents": """Tu écris un agent avec l'OpenAI Agents SDK à partir du
cahier des charges ci-dessous, dans le répertoire courant.

Commence par invoquer le skill `/openai-agents-author` et suis sa méthode.

Livrable : un seul fichier `{stem}_openai_agents.py`, exposant `build()` qui rend
l'`Agent` prêt à être passé au `Runner` (sans appeler le `Runner` à l'import).

Contraintes propres au SDK :
- Outils déclarés avec `@function_tool` et des annotations de type complètes :
  le schéma envoyé au modèle en est dérivé, une annotation manquante produit des
  appels erratiques.
- `ModelSettings(temperature=0, parallel_tool_calls=False)` : les outils mutent
  un état partagé, deux écritures concurrentes seraient indébogables.
- Un `max_turns` explicite au moment de l'exécution, et un outil terminal qui
  permet à l'agent de conclure — faute de quoi la boucle finit en
  `MaxTurnsExceeded`.
- Si tu utilises un guardrail, ne le présente pas comme une preuve : c'est un
  contrôle probabiliste, la garde dans l'outil reste nécessaire.
""" + GENERIC_COMMON,
}


def _build_prompt(project: dict, framework_id: str, spec_text: str,
                  previous: dict | None) -> str:
    stem = project["stem"]
    if framework_id == "agent_l":
        brief = AGENT_L_BRIEF.format(stem=stem)
    else:
        brief = GENERIC_BRIEFS[framework_id].format(stem=stem)

    parts = [brief, "\n--- ANALYSE DU CAHIER DES CHARGES ---",
             spec_analysis.render_for_author(project["analysis"]),
             "\n--- CAHIER DES CHARGES ---", spec_text, "--- FIN ---"]

    if previous:
        failed = [s for s in previous["steps"] if not s["passed"] and not s.get("skipped")]
        detail = "\n".join(
            f"### {step['id']} — {step['meaning']}\n{step['output'][:2500] or '(aucune sortie)'}"
            for step in failed)
        parts += [
            "\n--- CONSTAT DE LA VALIDATION PRÉCÉDENTE ---",
            "Les fichiers de la tentative précédente sont dans le répertoire "
            "courant. Corrige-les à partir de ce que la chaîne a réellement "
            "refusé, ci-dessous. Ne modifie pas les SCENARIO pour les faire "
            "passer : ils traduisent les critères d'acceptation. Si un "
            "SCENARIO est faux au regard du cahier des charges, dis-le dans "
            "ton rapport plutôt que de l'affaiblir.",
            previous["diagnosis"], detail, "--- FIN ---",
        ]
    return "\n".join(parts)


def generate(project_id: str, framework_id: str, *, attempts: int = MAX_ATTEMPTS,
             force: bool = False) -> dict:
    """Rédige puis valide, en réparant sur le constat de validation."""
    project = _load_project(project_id)
    if framework_id not in project["frameworkIds"]:
        raise ValueError(f"{framework_id} ne fait pas partie des cibles de ce projet.")
    if not project["analysis"]["ready"] and not force:
        raise ValueError(
            "Cahier des charges incomplet : "
            + ", ".join(project["analysis"]["missingRequired"])
            + ". Répondre aux questions bloquantes, ou forcer explicitement.")

    project_dir = _project_dir(project_id)
    factory_versions.normalize_history(project, project_dir)
    version = factory_versions.next_build_version(project, framework_id)
    build_id = f"build_{uuid.uuid4().hex[:12]}"
    directory = factory_versions.build_version_dir(
        project_dir, framework_id, version, build_id)
    directory.mkdir(parents=True)
    spec_text = (project_dir / "spec.txt").read_text(encoding="utf-8")
    superseded = factory_versions.latest_validated_build(project, framework_id)

    from codegen_backend import skill_status

    skill = FRAMEWORK_SKILLS[framework_id]
    build = {
        "id": build_id,
        "frameworkId": framework_id,
        "version": version,
        "specRevision": int(project.get("specRevision", 1)),
        "specHash": project.get("specHash"),
        "artifactDir": directory.relative_to(project_dir).as_posix(),
        "supersedesBuildId": superseded.get("id") if superseded else None,
        "skill": skill,
        # Consigné avec le résultat : un agent écrit sans son skill n'est pas
        # comparable à un agent écrit avec, et rien ne le dirait après coup.
        "skillResolved": skill_status((skill,))[skill]["resolved"],
        "startedAt": _now(),
        "factoryVersion": FACTORY_VERSION,
        "forced": bool(force and not project["analysis"]["ready"]),
        "attempts": [],
        "passed": False,
    }

    previous: dict | None = None
    for attempt in range(1, attempts + 1):
        emit_live_event("build_attempt", f"Tentative {attempt}/{attempts}",
                        attempt=attempt, frameworkId=framework_id)
        record: dict = {"attempt": attempt, "startedAt": _now()}
        try:
            session = author(_build_prompt(project, framework_id, spec_text, previous),
                            workdir=directory)
            record["session"] = {k: v for k, v in session.items() if k != "report"}
            record["report"] = session["report"]
        except CodegenError as exc:
            record.update({"codegenError": str(exc), "validation": None})
            build["attempts"].append(record)
            break

        report = validate(directory, framework_id, project["stem"])
        record["validation"] = report
        build["attempts"].append(record)
        _journal(project_id, {"event": "attempt", "buildId": build["id"],
                              "frameworkId": framework_id, "attempt": attempt,
                              "passed": report["passed"], "diagnosis": report["diagnosis"][:400]})
        if report["passed"]:
            build["passed"] = True
            break
        previous = report

    build["finishedAt"] = _now()
    build["attemptCount"] = len(build["attempts"])
    build["artifacts"] = sorted(p.name for p in directory.iterdir() if p.is_file())
    if build["passed"]:
        factory_versions.publish_version(
            directory, project_dir / framework_id, build["id"])
        build["publishedAt"] = _now()
    project["builds"].append(build)
    project["updatedAt"] = _now()
    _save_project(project)
    return build


def project_detail(project_id: str) -> dict:
    project = factory_versions.project_view(_load_project(project_id))
    directory = _project_dir(project_id)
    project["specText"] = (directory / "spec.txt").read_text(encoding="utf-8")
    project["artifacts"] = {}
    for build in project["builds"]:
        build_dir = directory / build["frameworkId"]
        if not build_dir.is_dir():
            continue
        project["artifacts"][build["frameworkId"]] = {
            path.name: path.read_text(encoding="utf-8", errors="replace")[:200_000]
            for path in sorted(build_dir.iterdir())
            if path.is_file() and path.suffix in {".agent", ".py", ".md"}
        }
    return project


def project_list(limit: int = 50, offset: int = 0) -> dict:
    if not PROJECTS_ROOT.is_dir():
        return {"projects": [], "total": 0, "limit": limit, "offset": offset}
    rows = []
    for path in PROJECTS_ROOT.iterdir():
        manifest = path / "project.json"
        if not manifest.is_file():
            continue
        try:
            project = json.loads(manifest.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        rows.append({
            "id": project["id"],
            "title": project["analysis"]["title"],
            "createdAt": project["createdAt"],
            "updatedAt": project.get("updatedAt", project["createdAt"]),
            "verdict": project["analysis"]["verdict"],
            "coverage": project["analysis"]["coverage"],
            "specRevision": int(project.get("specRevision", 1)),
            "frameworkIds": project["frameworkIds"],
            "builds": factory_versions.summary_builds(project),
        })
    rows.sort(key=lambda item: item["updatedAt"], reverse=True)
    return {"projects": rows[offset:offset + limit], "total": len(rows),
            "limit": limit, "offset": offset}


def capabilities() -> dict:
    codegen = codegen_available()
    skills = codegen.get("skills", {})
    return {
        "factoryVersion": FACTORY_VERSION,
        "frameworks": [
            {
                "id": framework_id,
                "label": FRAMEWORK_LABELS[framework_id],
                "skill": FRAMEWORK_SKILLS[framework_id],
                "skillResolved": skills.get(FRAMEWORK_SKILLS[framework_id], {}).get("resolved", False),
                # Seul AGENT-L est validé par une chaîne de preuve ; pour les
                # autres la validation se limite au chargement du module.
                "statically_verified": framework_id == "agent_l",
            }
            for framework_id in FRAMEWORK_IDS
        ],
        "codegen": codegen,
        "dimensions": [
            {"id": d["id"], "label": d["label"], "seeks": d["seeks"],
             "requiredFor": list(d["required_for"])}
            for d in spec_analysis.DIMENSIONS
        ],
        "maxAttempts": MAX_ATTEMPTS,
    }


# --------------------------------------------------------------------------- CLI

def main() -> None:
    parser = argparse.ArgumentParser(description="Fabrique d'agents AITESTPLATFORM")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("capabilities")

    analyse = sub.add_parser("analyze")
    source = analyse.add_mutually_exclusive_group(required=True)
    source.add_argument("--text")
    source.add_argument("--file")
    source.add_argument("--stdin", action="store_true")
    analyse.add_argument("--frameworks", default="agent_l")
    analyse.add_argument("--project")

    answer = sub.add_parser("answer")
    answer.add_argument("--project", required=True)
    answer.add_argument("--answers", required=True, help="JSON [{question, answer}]")

    build = sub.add_parser("generate")
    build.add_argument("--project", required=True)
    build.add_argument("--framework", required=True)
    build.add_argument("--attempts", type=int, default=MAX_ATTEMPTS)
    build.add_argument("--force", action="store_true")

    detail = sub.add_parser("project")
    detail.add_argument("--project", required=True)

    listing = sub.add_parser("projects")
    listing.add_argument("--limit", type=int, default=50)
    listing.add_argument("--offset", type=int, default=0)

    args = parser.parse_args()
    try:
        if args.command == "capabilities":
            payload = capabilities()
        elif args.command == "analyze":
            text = sys.stdin.read() if args.stdin else args.text
            payload = analyze_spec(
                text=text, path=args.file,
                framework_ids=[f.strip() for f in args.frameworks.split(",") if f.strip()],
                project_id=args.project)
        elif args.command == "answer":
            payload = answer_questions(args.project, json.loads(args.answers))
        elif args.command == "generate":
            payload = generate(args.project, args.framework,
                               attempts=args.attempts, force=args.force)
        elif args.command == "project":
            payload = project_detail(args.project)
        else:
            payload = project_list(args.limit, args.offset)
    except (SpecError, CodegenError, ValueError, FileNotFoundError) as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        raise SystemExit(1)
    print(json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    main()

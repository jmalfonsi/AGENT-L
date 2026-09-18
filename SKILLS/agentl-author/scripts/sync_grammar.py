#!/usr/bin/env python3
"""Synchronise le contrat d'auteur AGENT-L avec le parseur réel.

`--write` régénère le lock, le contrat lisible et l'exemple canonique.
`--check` ne modifie rien et échoue sur toute dérive.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import re
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
SKILL = Path(__file__).resolve().parents[1]
GENERATED = SKILL / "references" / "generated"
LOCK_PATH = SKILL / "grammar-lock.json"

# Une évolution de la grammaire, des contrôles ou de l'exemple canonique doit
# s'accompagner d'un bump explicite. `--write` refuse sinon de masquer la dérive.
# 1.6.1 — la grammaire est inchangée ; seul l'outillage de validation bouge
# (B014 cesse d'inventer des outils hôte manquants ; `verify` et `test`
# refusent un programme que `check` rejette). Un auteur écrit exactement le
# même AGENT-L qu'en 1.6.0 : d'où le patch et non le mineur.
# 2.0.0 : `EVERY` a quitté la grammaire. Aucun programme livré ne l'employait,
# mais un texte qui s'analysait auparavant est désormais refusé — la surface
# déclarée a rétréci, et le versionnage du dépôt fait de la grammaire l'API
# publique. Un retrait s'y annonce en majeur, même quand la construction
# retirée n'a jamais rien fait.
# 2.1.0 : `agentl autoloop` entre dans la chaîne de portes, avec sa référence
# (`references/autoloop.md`) et son module dans l'outillage verrouillé. La
# grammaire ne bouge pas d'un caractère — le même AGENT-L s'écrit à l'identique
# — mais la surface auteur s'élargit d'une commande et d'une porte : mineur.
# 2.5.1 : les sorties REASON absentes ou non finies restent indéterminées
# sans DEFAULT explicite, et W134 exige désormais que ce contrat de panne soit
# déclaré pour tout champ qui garde une action.
# 2.5.2 : Scenario et Autoloop appliquent les GIVEN au même tick zéro ; une
# valeur visant une BELIEF n'est plus masquée par la croyance déclarée.
# 2.6.0 : le CLI expose --events pour les traces JSON Lines de run et replay.
# Extension de la surface auteur ; le parseur et la grammaire restent identiques.
# 2.7.0 : Boundary suit les modules locaux, expose B016 et les politiques
# de périmètre ; la grammaire ne change pas.
# 2.7.1 : alias de méthodes liées et argv partiellement dynamiques contrôlés.
# 2.9.1 : documentation des scénarios et validation des assertions de trace.
AUTHORING_CONTRACT_VERSION = "2.9.1"
LOCK_SCHEMA_VERSION = 1

GRAMMAR_FILES = (
    "agentl/lexer.py",
    "agentl/parser.py",
    "agentl/nodes.py",
    "docs/agentl.ebnf",
)
TOOLCHAIN_FILES = (
    "agentl/analyzer.py",
    "agentl/_check_flow.py",
    "agentl/runtime.py",
    "agentl/autoloop.py",
    "agentl/boundary.py",
    "agentl/_boundary_project.py",
    "agentl/_boundary_security.py",
    "agentl/cli.py",
    "agentl/scenario.py",
    "agentl/verifier.py",
)
AUTHORING_FILES = (
    "SKILLS/agentl-author/SKILL.md",
    "SKILLS/agentl-author/agents/openai.yaml",
    "SKILLS/agentl-author/references/authoring.md",
    "SKILLS/agentl-author/references/autoloop.md",
    "SKILLS/agentl-author/references/business-workflows.md",
    "SKILLS/agentl-author/references/composition.md",
    "SKILLS/agentl-author/references/runtime-semantics.md",
    "SKILLS/agentl-author/references/security-authoring.md",
    "SKILLS/agentl-author/references/scenarios.md",
    "SKILLS/agentl-author/references/visual-trace.md",
    "SKILLS/agentl-author/scripts/sync_grammar.py",
)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def combined_hash(paths: Iterable[str]) -> str:
    digest = hashlib.sha256()
    for relative in sorted(paths):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update((ROOT / relative).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def package_version() -> str:
    source = (ROOT / "agentl" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', source, re.MULTILINE)
    if not match:
        raise RuntimeError("agentl.__version__ introuvable")
    return match.group(1)


def language_version(version: str) -> str:
    parts = version.split(".")
    if len(parts) < 2:
        raise RuntimeError(f"version de paquet invalide : {version}")
    return ".".join(parts[:2])


def ebnf_version() -> str:
    source = (ROOT / "docs" / "agentl.ebnf").read_text(encoding="utf-8")
    match = re.search(r"AGENT-L v(\d+\.\d+)", source)
    if not match:
        raise RuntimeError("version de grammaire absente de docs/agentl.ebnf")
    return match.group(1)


#: Méthodes du parseur par lesquelles un mot-clé entre dans un bloc. `accept_kw`
#: manquait : un mot-clé **facultatif** — `ESCALATE` dans `ON UNKNOWN`,
#: `APPROVAL` dans `REQUIRE APPROVAL FOR` — ne s'écrit pas autrement, et le
#: contrat le passait donc sous silence. Un extracteur qui ne voit pas la
#: moitié optionnelle d'une grammaire n'en est pas le contrat.
_KEYWORD_PROBES = {"at_kw", "expect_kw", "accept_kw"}


def parser_literals(method_name: str, *, reserved_only: bool = True) -> list[str]:
    """Extraire les mots-clés que la méthode reconnaît, depuis l'AST réel.

    Deux formes coexistent dans le parseur : le mot est passé à une sonde
    (`self.at_kw("EVERY", "WHEN")`) ou comparé après lecture
    (`effect in ("NEVER", "DENY", "ALLOW")`). Les deux comptent — la seconde
    portait à elle seule `ALLOW`, `DENY` et `APPROVAL`, absents du contrat.

    Le résultat est intersecté avec `KEYWORDS` : une comparaison porte aussi
    des **genres de jeton** (`"NAME"`, `"KW"`, `"STR"`), qui ne sont pas de la
    grammaire de surface et n'ont rien à faire dans un contrat d'auteur.
    """
    from agentl.lexer import KEYWORDS

    tree = ast.parse((ROOT / "agentl" / "parser.py").read_text(encoding="utf-8"))
    method = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == method_name
    )
    found: set[str] = set()

    def collect(value: ast.AST) -> None:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            if value.value.isupper():
                found.add(value.value)
        elif isinstance(value, (ast.Tuple, ast.List, ast.Set)):
            for element in value.elts:
                collect(element)

    for node in ast.walk(method):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in _KEYWORD_PROBES:
                for argument in node.args:
                    collect(argument)
        if isinstance(node, ast.Compare):
            # `effect in ("NEVER", "DENY", "ALLOW")` — le membre droit est un
            # conteneur, pas une constante : le parcourir, sans quoi une
            # branche entière de la grammaire reste invisible.
            for value in [node.left, *node.comparators]:
                collect(value)
    return sorted(found & set(KEYWORDS) if reserved_only else found)


def canonical_agent(version: str) -> str:
    return textwrap.dedent(
        f'''\
        // GENERATED by scripts/sync_grammar.py — do not edit by hand.
        // Canonical authoring contract {AUTHORING_CONTRACT_VERSION}.
        AGENT canonical_guarded_workflow {{
          VERSION "{version}"
          DESCRIPTION "Canonical policy-governed workflow generated and validated against the real parser."

          GOAL finished {{
            MAINTAIN workflow.done == yes
          }}

          OBSERVE {{
            // Ces quatre chemins gardent un interdit : un capteur muet doit
            // avoir une conduite déclarée, sans quoi la panne bloque l'agent
            // en silence (W131). ESCALATE nomme la panne plutôt que d'y
            // substituer une valeur — sous un NEVER, un repli se discute (W132).
            source.read_ok ON UNKNOWN ESCALATE
            request.pending ON UNKNOWN ESCALATE
            request.capacity ON UNKNOWN ESCALATE
            approval.sender_matches ON UNKNOWN ESCALATE
            approval.message
            approval.evidence_token
            workflow.done
            request.applied         // un EFFECT non recouvert n'est jamais réfutable (T9)
          }}

          BELIEF {{
            workflow.done = no CONFIDENCE 1.00 SOURCE prior
            analysis.done = no CONFIDENCE 1.00 SOURCE prior
            request.applied = no CONFIDENCE 1.00 SOURCE prior
            decision = unknown CONFIDENCE 0.10 SOURCE prior
          }}

          TOOL apply_request {{
            INPUT {{
              read_ok: String BIND source.read_ok
              pending: String BIND request.pending
              capacity: Number BIND request.capacity
              decision: String BIND decision
              sender_matches: String BIND approval.sender_matches
              evidence_token: String BIND approval.evidence_token ATTESTS decision
            }}
            OUTPUT {{ applied: Symbol }}
            SIDE_EFFECT {{ system.record }}
            RISK {{ operational = MEDIUM }}
            EFFECT {{ request.applied = yes }}   // recouvert par OBSERVE (T9)
            COST 2
          }}

          TOOL finish_workflow {{
            OUTPUT {{ finished: Symbol }}
            RISK LOW
            EFFECT {{ workflow.done = yes }}
            COST 1
          }}

          POLICY {{
            DEFAULT ALLOW
            // Le jeton atteste la décision ; il n'a rien à faire chez un
            // fournisseur de modèle. USING borne ce qu'un REASON montre,
            // NEVER SEND borne toutes les sorties — select_plan compris.
            NEVER SEND approval.evidence_token
            NEVER apply_request WHEN source.read_ok != yes
            NEVER apply_request WHEN request.pending != yes
            NEVER apply_request WHEN request.capacity <= 0
            NEVER apply_request WHEN decision != approved
            NEVER apply_request WHEN approval.sender_matches != yes
          }}

          PLANNER {{
            ENABLE
            ACHIEVE request.applied == yes
            MAX_DEPTH 1
            MAX_NODES 50
            APPROVAL_COST 10
          }}

          PLAN analyze
            WHEN source.read_ok == yes
              AND request.pending == yes
              AND analysis.done == no
          {{
            STEP classify {{
              REASON {{
                TASK "Classify the message as an explicit approval or unknown."
                USING {{ approval.message }}
                PRODUCE {{ decision: Symbol IN [approved, unknown] DEFAULT unknown }}
              }}
            }}
            STEP mark {{
              SET analysis.done = yes
            }}
          }}

          PLAN close_applied
            WHEN request.applied == yes
              AND workflow.done != yes
          {{
            STEP close {{
              finish_workflow()
            }}
            STEP confirm {{
              VERIFY {{ workflow.done == yes }}
            }}
          }}

          PLAN close_source_failure
            WHEN source.read_ok != yes
              AND workflow.done != yes
          {{
            STEP close {{
              finish_workflow()
            }}
            STEP confirm {{
              VERIFY {{ workflow.done == yes }}
            }}
          }}

          PLAN close_rejected {{
            STEP close {{
              finish_workflow()
            }}
            STEP confirm {{
              VERIFY {{ workflow.done == yes }}
            }}
          }}

          DECIDE {{
            RULES {{
              IF planner.exhausted
                AND analysis.done == yes
                AND request.applied != yes
                AND workflow.done != yes
              THEN close_rejected
            }}
          }}

          SCENARIO explicit_approval_is_applied {{
            GIVEN {{
              source.read_ok = yes
              request.pending = yes
              request.capacity = 1
              approval.message = "Approved."
              approval.sender_matches = yes
              approval.evidence_token = \"proof-1\"
              decision = approved
              request.applied = no
              workflow.done = no
              apply_request.applied = yes
              finish_workflow.finished = yes
            }}
            EXPECT {{
              request.applied == yes
              workflow.done == yes
            }} WITHIN 3
          }}

          SCENARIO no_capacity_blocks_the_action {{
            GIVEN {{
              source.read_ok = yes
              request.pending = yes
              request.capacity = 0
              approval.message = "Approved."
              approval.sender_matches = yes
              approval.evidence_token = \"proof-1\"
              decision = approved
              request.applied = no
              workflow.done = no
              apply_request.applied = yes
              finish_workflow.finished = yes
            }}
            EXPECT {{
              request.applied != yes
              workflow.done == yes
            }} WITHIN 3
          }}

          SCENARIO source_failure_closes_without_action {{
            GIVEN {{
              source.read_ok = no
              request.pending = yes
              request.capacity = 1
              approval.message = "Approved."
              approval.sender_matches = yes
              approval.evidence_token = \"proof-1\"
              decision = approved
              request.applied = no
              workflow.done = no
              apply_request.applied = yes
              finish_workflow.finished = yes
            }}
            EXPECT {{
              request.applied != yes
              workflow.done == yes
            }} WITHIN 1
          }}

          LOOP UNTIL goal.satisfied MAX 4 {{
            OBSERVE
            UPDATE_BELIEFS
            EVALUATE_GOALS
            SELECT_PLAN
            EXECUTE
            VERIFY
          }}
        }}
        '''
    )


def canonical_host() -> str:
    return textwrap.dedent(
        '''\
        # GENERATED by scripts/sync_grammar.py — do not edit by hand.
        from agentl import Host, MockLLM, Symbol


        def build():
            host = Host()
            state = {"workflow_done": Symbol("no"),
                     "request_applied": Symbol("no")}
            host.sensors.update(
                {
                    "source.read_ok": lambda: Symbol("yes"),
                    "request.pending": lambda: Symbol("yes"),
                    "request.capacity": lambda: 1,
                    "approval.message": lambda: "Approved.",
                    "approval.sender_matches": lambda: Symbol("yes"),
                    "approval.evidence_token": lambda: "proof-1",
                    "workflow.done": lambda: state["workflow_done"],
                    # L'EFFECT d'apply_request se juge ici : sans ce capteur,
                    # la postcondition ne pourrait jamais être démentie (T9).
                    "request.applied": lambda: state["request_applied"],
                }
            )

            def apply_request(
                read_ok,
                pending,
                capacity,
                decision,
                sender_matches,
                evidence_token,
            ):
                state["request_applied"] = Symbol("yes")
                return {"applied": Symbol("yes")}

            def finish_workflow():
                state["workflow_done"] = Symbol("yes")
                return {"finished": Symbol("yes")}

            host.tools["apply_request"] = apply_request
            host.tools["finish_workflow"] = finish_workflow
            llm = MockLLM({"Classify": {"decision": "approved"}})
            return host, llm
        '''
    )


def build_lock(version: str, agent_source: str, host_source: str) -> dict:
    return {
        "schema_version": LOCK_SCHEMA_VERSION,
        "skill": "agentl-author",
        "authoring_contract_version": AUTHORING_CONTRACT_VERSION,
        "compatibility": {
            "agentl_package_version": package_version(),
            "language_version": version,
            "ebnf_version": ebnf_version(),
        },
        "grammar": {
            "combined_sha256": combined_hash(GRAMMAR_FILES),
            "files": {name: sha256_file(ROOT / name) for name in GRAMMAR_FILES},
        },
        "validation_toolchain": {
            "combined_sha256": combined_hash(TOOLCHAIN_FILES),
            "files": {name: sha256_file(ROOT / name) for name in TOOLCHAIN_FILES},
        },
        "authoring_surface": {
            "combined_sha256": combined_hash(AUTHORING_FILES),
            "files": {name: sha256_file(ROOT / name) for name in AUTHORING_FILES},
        },
        "canonical_examples": {
            "canonical.agent": sha256_bytes(agent_source.encode("utf-8")),
            "canonical.py": sha256_bytes(host_source.encode("utf-8")),
        },
    }


#: Sections du contrat : un intitulé, les méthodes du parseur qui en portent
#: la grammaire. La table est **exhaustive par contrôle** — `keyword_coverage`
#: échoue si un mot réservé n'apparaît dans aucune section et n'est pas
#: explicitement écarté. Un mot-clé ajouté au lexer sans section devient donc
#: une erreur, et non un silence : c'est ainsi que `SEND`, `UNKNOWN` et
#: `DEGRADE` ont pu exister trois versions durant sans que le contrat les
#: nomme, `--check` restant vert parce qu'il ne compare que des empreintes.
CONTRACT_SECTIONS = (
    ("programme", ("parse_program", "parse_agent", "parse_agent_member")),
    ("`GOAL`", ("parse_goal",)),
    ("`BELIEF`", ("parse_belief_block",)),
    ("`OBSERVE`", ("parse_observe_block", "parse_observer_entry",
                   "parse_observer_unknown")),
    ("`MEMORY`", ("parse_memory_block", "parse_memory_write")),
    ("`TOOL`", ("parse_tool", "effect_block", "typed_fields_bound",
                "typed_fields_domained", "typed_fields", "parse_domain")),
    ("`POLICY`", ("parse_policy_block", "policy_target", "policy_guard")),
    ("`PLAN`", ("parse_plan", "parse_step")),
    ("`EVENT` / `MESSAGE`", ("parse_event", "parse_message_handler",
                             "message_stmt")),
    ("`DECIDE`", ("parse_decide",)),
    ("`HYPOTHESIS`", ("parse_hypothesis", "evidence_block")),
    ("`PLANNER`", ("parse_planner", "parse_outcome", "parse_utility")),
    ("`SCENARIO`", ("parse_scenario",)),
    ("`REASON`", ("reason_stmt",)),
    ("instructions", ("statement", "if_stmt", "verify_stmt", "ask_stmt",
                      "delegate_stmt", "foreach_stmt", "loop_stmt",
                      "then_target", "_starts_construct")),
    ("expressions", ("or_expr", "and_expr", "not_expr", "comparison",
                     "primary", "call_tail", "bucket_path")),
)

#: Mots réservés qu'aucune section ne peut nommer, avec la raison. Vide
#: aujourd'hui : les phases de `LOOP` viennent de `PHASES` et ont leur ligne.
#: Ce n'est pas une échappatoire de confort — y inscrire un mot-clé est une
#: décision qui se relit, pas un oubli qui se tait.
COVERAGE_WAIVERS: dict[str, str] = {
    "EVERY": (
        "retiré de la grammaire en v1.8 — il ne cadençait rien (corps `pass`) "
        "et son unité de temps était comparée à un compteur de ticks sans "
        "durée. Le mot reste réservé pour que `OBSERVE logs EVERY 10s` soit "
        "refusé plutôt que relu en silence comme deux chemins observés ; "
        "aucune section ne le nomme donc plus."
    ),
}


def section_keywords() -> dict[str, set[str]]:
    """Mots-clés de chaque section, extraits du parseur réel."""
    out: dict[str, set[str]] = {}
    for label, methods in CONTRACT_SECTIONS:
        found: set[str] = set()
        for method in methods:
            found |= set(parser_literals(method))
        out[label] = found
    return out


def keyword_coverage() -> tuple[dict[str, set[str]], list[str]]:
    """Rendre les sections et les mots réservés que **rien** ne nomme.

    Le contrôle qui manquait. `--check` comparait des empreintes : il
    vérifiait que les artefacts suivent les sources, jamais qu'ils en
    décrivent la grammaire. Un mot-clé pouvait donc traverser des versions
    entières sans figurer nulle part, le vert du contrôle disant seulement
    que rien n'avait bougé depuis la dernière régénération.
    """
    from agentl.lexer import KEYWORDS, PHASES

    sections = section_keywords()
    named: set[str] = set(PHASES) | set(COVERAGE_WAIVERS)
    for found in sections.values():
        named |= found
    return sections, sorted(set(KEYWORDS) - named)


def generated_contract(lock: dict) -> str:
    from agentl.lexer import KEYWORDS, PHASES

    sections, uncovered = keyword_coverage()
    if uncovered:                      # ceinture : `--write` ne publie pas un
        raise SystemExit(              # contrat troué, même sans `--check`.
            "mots réservés qu'aucune section du contrat ne nomme : "
            + ", ".join(uncovered)
        )
    lines = "\n".join(
        f"        - {label} : {', '.join(sorted(found)) or '—'}"
        for label, found in sections.items()
    ).lstrip()
    phases = ", ".join(sorted(PHASES))
    assertions = ", ".join(
        f"`EXPECT {kind}`"
        for kind in parser_literals("parse_scenario", reserved_only=False)
        if kind not in KEYWORDS and kind not in {"NEVER", "NO"}
    )
    return textwrap.dedent(
        f'''\
        <!-- GENERATED by scripts/sync_grammar.py — do not edit by hand. -->
        # Contrat de grammaire AGENT-L

        - Contrat d'auteur : `{lock["authoring_contract_version"]}`
        - Paquet AGENT-L : `{lock["compatibility"]["agentl_package_version"]}`
        - Niveau de langage : `{lock["compatibility"]["language_version"]}`
        - Empreinte grammaire : `{lock["grammar"]["combined_sha256"]}`
        - Empreinte contrôles : `{lock["validation_toolchain"]["combined_sha256"]}`
        - Empreinte surface auteur : `{lock["authoring_surface"]["combined_sha256"]}`

        Le parseur et le lexer sont l'autorité exécutable. L'EBNF et la prose
        doivent les suivre. Toute modification de cette autorité rend
        `scripts/sync_grammar.py --check` rouge jusqu'à régénération et bump
        explicite du contrat d'auteur.

        ## Champs extraits de l'AST du parseur

        {lines}
        - phases de `LOOP` : {phases}

        Les assertions de trace emploient aussi des noms contextuels,
        non réservés par le lexer : {assertions}. Lire
        [scenarios.md](../scenarios.md) pour les cibles, les stimuli et les
        limites de ces tests. `CALL` seul reste une instruction invalide.

        ## Couverture

        `{len(KEYWORDS)}` mots réservés, **tous** nommés par une section
        ci-dessus, par la ligne des phases, ou écartés explicitement
        (`{len(COVERAGE_WAIVERS)}` écart(s)). Le contrôle est exécutable :
        `--check` et `--write` échouent sur un mot réservé qu'aucune section
        ne mentionne. Les empreintes disent que les artefacts suivent les
        sources ; cette section dit qu'ils les **décrivent**.

        ## Exemple canonique

        Lire `canonical.agent` et `canonical.py` dans ce répertoire. Ils sont
        régénérés, parsés, analysés, exécutés en scénario, vérifiés, contrôlés
        par `boundary`, enregistré/rejoué, puis soumis à un test de mutation lors de `--check`.
        '''
    )


def expected_files() -> dict[Path, str]:
    package = package_version()
    language = language_version(package)
    if ebnf_version() != language:
        raise RuntimeError(
            f"dérive de version : paquet {package}, EBNF {ebnf_version()}"
        )
    agent_source = canonical_agent(language)
    host_source = canonical_host()
    lock = build_lock(language, agent_source, host_source)
    return {
        LOCK_PATH: json.dumps(lock, indent=2, sort_keys=True) + "\n",
        GENERATED / "grammar-contract.md": generated_contract(lock),
        GENERATED / "canonical.agent": agent_source,
        GENERATED / "canonical.py": host_source,
    }


def validate_canonical(files: dict[Path, str]) -> list[str]:
    from agentl import Analyzer, Runtime, parse_source, verify
    from agentl.boundary import check_pair
    from agentl.scenario import run_scenario, run_scenarios

    errors: list[str] = []
    source = files[GENERATED / "canonical.agent"]
    try:
        agent = parse_source(source, "<canonical.agent>").agents[0]
    except Exception as exc:
        return [f"exemple canonique illisible : {exc}"]

    errors.extend(
        f"diagnostic canonique {item.render()}"
        for item in Analyzer(agent).run()
        if item.severity == "error"
    )
    scenario_report = run_scenarios(agent)
    if not scenario_report.passed:
        errors.append(scenario_report.render())
    if verify(agent).refuted:
        errors.append("l'exemple canonique est réfuté par agentl verify")

    mutation = source.replace(
        "    NEVER apply_request WHEN request.capacity <= 0\n", ""
    )
    if mutation == source:
        errors.append("la mutation canonique de capacité ne trouve pas sa règle")
    else:
        mutated = parse_source(mutation, "<canonical-mutated.agent>").agents[0]
        bite = next(
            item
            for item in mutated.scenarios
            if item.name == "no_capacity_blocks_the_action"
        )
        if run_scenario(mutated, bite).passed:
            errors.append("le scénario de capacité ne mord pas après mutation du NEVER")

    with tempfile.TemporaryDirectory(prefix="agentl-skill-") as temporary:
        target = Path(temporary) / "canonical.agent"
        target.write_text(source, encoding="utf-8")
        target.with_suffix(".py").write_text(
            files[GENERATED / "canonical.py"], encoding="utf-8"
        )
        boundary = check_pair(target)
        if not boundary.ok():
            errors.append(
                "boundary canonique : "
                + ", ".join(f"{item.code}@{item.line}" for item in boundary.blocking)
            )

        spec = importlib.util.spec_from_file_location(
            "agentl_skill_canonical", target.with_suffix(".py")
        )
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        host, llm = module.build()
        runtime = Runtime(agent, host, llm).run(max_ticks=4)
        if runtime.state.get("goal.satisfied") is not True:
            errors.append("run canonique incomplet : goal.satisfied != true")

        from agentl.cli import main as cli_main

        journal = Path(temporary) / "canonical.json"
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            run_code = cli_main(
                ["run", str(target), "--quiet", "--record", str(journal)]
            )
            replay_code = cli_main(["replay", str(journal), "--quiet"])
        if run_code != 0 or replay_code != 0:
            errors.append(
                "run/replay canonique en échec : "
                f"run={run_code}, replay={replay_code}\n{output.getvalue()}"
            )
    return errors


def validate_markdown() -> list[str]:
    errors: list[str] = []
    fence = re.compile(r"```agentl\n(.*?)```", re.DOTALL)
    planner = re.compile(r"PLANNER\s*\{(.*?)^\s*\}", re.DOTALL | re.MULTILINE)
    for path in [SKILL / "SKILL.md", *(SKILL / "references").glob("*.md")]:
        source = path.read_text(encoding="utf-8")
        for block in fence.findall(source):
            from agentl.lexer import tokenize

            try:
                # Les fragments pédagogiques peuvent contenir une ellipse ;
                # le parseur des AGENT complets reçoit toujours le texte brut.
                tokens = tokenize(block.replace("…", "..."))
            except Exception as exc:
                errors.append(f"bloc AGENT-L illisible dans {path.name}: {exc}")
                continue
            used = set()
            for index, token in enumerate(tokens):
                if token.kind not in {"IDENT", "KW"}:
                    continue
                if token.value in {"INVARIANT", "LLM_OUTPUTS"}:
                    used.add(token.value)
                elif token.value == "CALL":
                    prefix = [(t.kind, t.value) for t in tokens[max(0, index - 2):index]]
                    if not (prefix[-1:] == [("KW", "EXPECT")]
                            or prefix == [("KW", "EXPECT"), ("KW", "NEVER")]):
                        used.add(token.value)
            if used:
                errors.append(
                    f"{path.name}: pseudo-mot(s)-clé(s) non supporté(s) "
                    + ", ".join(sorted(used))
                )
            if block.lstrip().startswith("AGENT "):
                from agentl import parse_source

                try:
                    parse_source(block, str(path))
                except Exception as exc:
                    errors.append(f"bloc AGENT-L invalide dans {path.name}: {exc}")
        for block in planner.findall(source):
            if re.search(r"^\s*WHEN\b", block, re.MULTILINE):
                errors.append(
                    f"{path.name}: `PLANNER WHEN` n'existe pas dans le parseur"
                )
    return errors


def compatibility_change_without_bump(old_lock: dict, new_lock: dict) -> bool:
    if old_lock == new_lock:
        return False
    return old_lock.get("authoring_contract_version") == new_lock.get(
        "authoring_contract_version"
    )


def validate_lock_guard() -> list[str]:
    base = {
        "authoring_contract_version": "1.0.0",
        "grammar": {"combined_sha256": "a"},
    }
    changed = {
        "authoring_contract_version": "1.0.0",
        "grammar": {"combined_sha256": "b"},
    }
    bumped = {
        "authoring_contract_version": "1.0.1",
        "grammar": {"combined_sha256": "b"},
    }
    if compatibility_change_without_bump(base, base):
        return ["le verrou exige un bump sans changement"]
    if not compatibility_change_without_bump(base, changed):
        return ["le verrou laisse passer une dérive sans bump"]
    if compatibility_change_without_bump(base, bumped):
        return ["le verrou refuse une dérive accompagnée d'un bump"]
    return []


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def validate_coverage() -> list[str]:
    """Tout mot réservé doit être nommé quelque part dans le contrat.

    Les empreintes prouvent que les artefacts suivent les sources ; elles ne
    prouvent pas qu'ils les décrivent. Un mot-clé pouvait donc naître au
    lexer, vivre dans le parseur et n'apparaître dans aucune section, `--check`
    restant vert — c'est arrivé à `ALLOW`, `DENY` et `APPROVAL`, absents du
    contrat depuis l'origine. Ici, l'oubli devient rouge.
    """
    _, uncovered = keyword_coverage()
    if not uncovered:
        return []
    return [
        "mot réservé qu'aucune section du contrat ne nomme : "
        f"{word} — l'ajouter à CONTRACT_SECTIONS, ou l'écarter avec sa "
        f"raison dans COVERAGE_WAIVERS"
        for word in uncovered
    ]


def check(files: dict[Path, str]) -> list[str]:
    errors = (validate_canonical(files) + validate_markdown()
              + validate_lock_guard() + validate_coverage())
    for path, expected in files.items():
        if not path.exists():
            errors.append(f"artefact absent : {display_path(path)}")
        elif path.read_text(encoding="utf-8") != expected:
            errors.append(f"artefact périmé : {display_path(path)}")
    return errors


def write(files: dict[Path, str]) -> None:
    if LOCK_PATH.exists():
        try:
            old_lock = json.loads(LOCK_PATH.read_text(encoding="utf-8"))
            new_lock = json.loads(files[LOCK_PATH])
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"lock illisible : {exc}") from exc
        if compatibility_change_without_bump(old_lock, new_lock):
            raise RuntimeError(
                "la compatibilité a changé sans bump de "
                "AUTHORING_CONTRACT_VERSION"
            )
    for path, content in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    try:
        files = expected_files()
        if args.write:
            write(files)
        errors = check(files)
    except Exception as exc:
        print(f"✗ contrat agentl-author : {exc}", file=sys.stderr)
        return 1
    if errors:
        for error in errors:
            print(f"✗ {error}", file=sys.stderr)
        return 1
    print(
        "✓ agentl-author synchronisé "
        f"(contrat {AUTHORING_CONTRACT_VERSION}, AGENT-L {package_version()})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

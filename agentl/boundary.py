"""Contrôle de la frontière hôte / agent.

    L'hôte fournit des FAITS. Le `.agent` prend les DÉCISIONS.

C'est la règle dont la violation ne produit aucun signal : le programme reste
bien formé, le vérificateur reste muet, la trace reste belle — et la garantie
a disparu, parce que le raisonnement est passé dans du Python que personne
n'audite. Un `.agent` décoratif au-dessus d'un hôte qui décide vaut moins
qu'un script honnête, parce qu'il ment sur ce qu'il garantit.

Ce module lit l'AST de `X.py` et de ses imports locaux transitifs et signale
ce qui ressemble à une décision
métier. Il ne peut pas trancher à ta place — « filtrer une liste vide » et
« filtrer les tickets d'organisations bloquées » ont la même forme. Il
**oblige donc à écrire pourquoi** : une ligne signalée se lève par un
commentaire

    # BOUNDARY-OK: <justification en clair>

qui reste dans le code, apparaît dans le rapport, et se relit en revue. Une
levée sans justification n'est pas acceptée.

Ce linter architectural reste heuristique, avec des faux positifs et des faux
négatifs possibles. Il ne constitue pas une frontière de sécurité : son verdict
ne porte que sur les motifs couverts et les sources analysées.
"""
from __future__ import annotations

import ast
import io
import tokenize
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from ._boundary_project import UNKNOWN, Symbols, discover

# Codes ------------------------------------------------------------------
#   B001  comparaison à une valeur métier   (décider d'un cas)
#   B002  filtrage d'une collection          (décider qui est traité)
#   B003  sortie anticipée dans une boucle   (écarter un élément)
#   B004  tri ou sélection d'extremum        (prioriser)
#   B005  seuil numérique                    (appliquer une règle chiffrée)
#   B006  nom de fonction qui décide         (should_/select_/prioritise_…)
#   B007  hôte volumineux, agent sans garde  (déséquilibre suspect)
#   B008  primitive destructive active sans dry-run par défaut
#   B009  commande shell=True
#   B010  action destructive sans attestation/revalidation de cible
#   B011  lecture de journaux sans curseur/déduplication
#   B012  action réversible sans recette de rollback
#   B013  données brutes vers LLM externe sans opt-in
#   B014  contrat .agent/hôte incohérent
#   B016  surface locale incomplète ou dépendance externe refusée
#   B015  description d'outil importée au ton impératif (pilotage par un tiers)

DECISION_PREFIXES = ("should_", "must_", "is_eligible", "is_valid", "select_",
                     "choose_", "pick_", "prioriti", "decide_", "filter_",
                     "classify_", "rank_", "score_", "qualif")

@dataclass
class Finding:
    code: str
    severity: str          # "error" | "warning"
    message: str
    line: int
    detail: str = ""
    waived: bool = False
    reason: str = ""
    path: Optional[Path] = None


@dataclass
class Report:
    path: Path
    findings: List[Finding] = field(default_factory=list)
    analyzed_paths: List[Path] = field(default_factory=list)
    external_imports: List[str] = field(default_factory=list)
    excluded_imports: List[str] = field(default_factory=list)
    complete: bool = True

    @property
    def blocking(self) -> List[Finding]:
        return [f for f in self.findings
                if f.severity == "error" and not f.waived]

    @property
    def waivers(self) -> List[Finding]:
        return [f for f in self.findings if f.waived]

    def ok(self) -> bool:
        return self.complete and not self.blocking


# ---------------------------------------------------------------- levées
def _waivers(source: str, tree: ast.AST) -> Dict[int, str]:
    """Lignes levées par `# BOUNDARY-OK: raison`.

    Une levée justifie **l'instruction** qu'elle commente, pas une ligne : on
    la place au-dessus du code (ce qui se relit mieux) ou en fin de ligne, et
    elle couvre l'instruction entière, fût-elle sur six lignes. Sans cela une
    compréhension multi-lignes serait à moitié levée, ce qui est la pire des
    situations — on croit avoir justifié, et l'outil crie encore.
    """
    # Étendue de chaque instruction, de la plus imbriquée à la plus large.
    spans = sorted(
        ((node.lineno, getattr(node, "end_lineno", node.lineno))
         for node in ast.walk(tree) if isinstance(node, ast.stmt)),
        key=lambda span: (span[0], span[1] - span[0]))

    out: Dict[int, str] = {}
    source_lines = source.splitlines()
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type != tokenize.COMMENT:
            continue
        match = re.fullmatch(r"#\s*BOUNDARY-OK\s*:\s*(\S.*?)\s*", token.string)
        if not match:
            continue
        reason = match.group(1)
        number, column = token.start
        inline = bool(source_lines[number - 1][:column].strip())
        if inline:
            candidates = [(start, end) for start, end in spans
                          if start <= number <= end]
            if not candidates:
                continue
            start, end = min(candidates, key=lambda span: (span[1] - span[0], span[0]))
        else:
            # Permit a multi-line explanatory comment, but never jump across
            # a blank line, a docstring, or another indentation level.
            following = number + 1
            while following <= len(source_lines):
                line = source_lines[following - 1]
                if not line.lstrip().startswith("#"):
                    break
                if len(line) - len(line.lstrip()) != column:
                    break
                following += 1
            candidates = [(start, end) for start, end in spans if start == following]
            if not candidates:
                continue
            start, end = min(candidates, key=lambda span: span[1] - span[0])
            if len(source_lines[start - 1]) - len(source_lines[start - 1].lstrip()) != column:
                continue
        for covered in range(start, end + 1):
            out.setdefault(covered, reason)
    return out


# ------------------------------------------------------------ heuristiques
def _is_absence_guard(node: ast.AST) -> bool:
    """Only explicit identity checks against None prove an absence test."""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return _is_absence_guard(node.operand)
    if isinstance(node, ast.Compare):
        operands = [node.left, *node.comparators]
        return all(isinstance(op, (ast.Is, ast.IsNot)) and
                   any(isinstance(c, ast.Constant) and c.value is None
                       for c in (left, right))
                   for left, op, right in zip(operands, node.ops, operands[1:]))
    if isinstance(node, ast.BoolOp):
        return all(_is_absence_guard(v) for v in node.values)
    return False


def _business_constant(node: ast.AST, symbols: Symbols) -> Optional[str]:
    """Literal values, including named constants and membership containers."""
    value = symbols.literal(node)
    if value is UNKNOWN or value is None or isinstance(value, bool):
        return None
    if isinstance(value, tuple):
        return repr(value) if value else None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or stripped in {"@", ",", ";", ":", "/", "-", "."}:
            return None
        return repr(value)
    if isinstance(value, (int, float)):
        return repr(value)
    return None


def _describe(node: ast.AST) -> str:
    try:
        return ast.unparse(node)
    except Exception:                     # noqa: BLE001 — Python < 3.9
        return type(node).__name__


class _HostVisitor(ast.NodeVisitor):
    def __init__(self, symbols: Symbols) -> None:
        self.symbols = symbols
        self.findings: List[Finding] = []
        self._loops: List[ast.AST] = []

    # -- noms de fonctions qui annoncent une décision
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        low = node.name.lower().lstrip("_")
        if any(low.startswith(p) or p in low for p in DECISION_PREFIXES):
            self.findings.append(Finding(
                "B006", "error",
                f"`{node.name}()` — le nom annonce une décision",
                node.lineno,
                "un hôte perçoit et rend ; qualifier, choisir, trier ou "
                "classer relève du `.agent`"))
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    # -- comparaisons à une valeur métier
    def visit_Compare(self, node: ast.Compare) -> None:  # noqa: N802
        operands = [node.left, *node.comparators]
        emitted = set()
        for left, op, right in zip(operands, node.ops, operands[1:]):
            if not any(_business_constant(side, self.symbols) is not None
                       for side in (left, right)):
                continue
            if isinstance(op, (ast.Eq, ast.NotEq, ast.In, ast.NotIn)):
                if "B001" in emitted:
                    continue
                emitted.add("B001")
                self.findings.append(Finding(
                    "B001", "error",
                    f"comparaison à une valeur métier : {_describe(node)}",
                    node.lineno,
                    "l'hôte rend le fait brut ; c'est une POLICY ou un IF du "
                    "`.agent` qui le compare"))
            elif isinstance(op, (ast.Lt, ast.LtE, ast.Gt, ast.GtE)):
                if "B005" in emitted:
                    continue
                emitted.add("B005")
                self.findings.append(Finding(
                    "B005", "error",
                    f"seuil appliqué dans l'hôte : {_describe(node)}",
                    node.lineno,
                    "un seuil est une règle : il se lit dans le monde et "
                    "s'applique dans le `.agent`"))
        self.generic_visit(node)

    # -- filtrage d'une collection
    def visit_ListComp(self, node: ast.ListComp) -> None:  # noqa: N802
        self._comprehension(node)

    def visit_SetComp(self, node: ast.SetComp) -> None:  # noqa: N802
        self._comprehension(node)

    def visit_DictComp(self, node: ast.DictComp) -> None:  # noqa: N802
        self._comprehension(node)

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:  # noqa: N802
        self._comprehension(node)

    def _comprehension(self, node: ast.AST) -> None:
        for generator in node.generators:                  # type: ignore[attr-defined]
            for condition in generator.ifs:
                if _is_absence_guard(condition):
                    continue
                self.findings.append(Finding(
                    "B002", "error",
                    f"filtrage d'une collection : if {_describe(condition)}",
                    node.lineno,                            # type: ignore[attr-defined]
                    "choisir quels éléments existent pour la suite est une "
                    "décision ; rends-les tous, le `FOREACH` triera"))
        self.generic_visit(node)

    # -- sortie anticipée dans une boucle
    def visit_For(self, node: ast.For) -> None:  # noqa: N802
        self._loops.append(node)
        self.generic_visit(node)
        self._loops.pop()

    def visit_While(self, node: ast.While) -> None:  # noqa: N802
        self._loops.append(node)
        self.generic_visit(node)
        self._loops.pop()

    def visit_Continue(self, node: ast.Continue) -> None:  # noqa: N802
        self._early_exit(node, "continue")

    def visit_Break(self, node: ast.Break) -> None:  # noqa: N802
        self._early_exit(node, "break")

    def _early_exit(self, node: ast.AST, keyword: str) -> None:
        if not self._loops:
            return
        self.findings.append(Finding(
            "B003", "warning",
            f"`{keyword}` dans une boucle de l'hôte",
            node.lineno,                                    # type: ignore[attr-defined]
            "écarter ou arrêter à mi-parcours revient à décider qui est "
            "traité ; si c'est une recherche, extrais-la dans une fonction "
            "qui rend un fait"))

    # -- tri / extremum
    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        name = ""
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        if name in {"sorted", "sort"} or (
                name in {"max", "min"} and node.args
                and not isinstance(node.args[0], ast.Constant)):
            self.findings.append(Finding(
                "B004", "warning",
                f"tri ou sélection d'extremum : {name}()",
                node.lineno,
                "ordonner, c'est prioriser ; si l'ordre porte du sens "
                "métier, il appartient au `.agent`"))
        if self.symbols.name(node.func) == "filter" and node.args:
            self.findings.append(Finding(
                "B002", "error", "filtrage d'une collection : filter()", node.lineno,
                "le prédicat doit être déclaré dans le `.agent` ou justifié"))
        self.generic_visit(node)



def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        parts = [node.func.attr]
        value = node.func.value
        while isinstance(value, ast.Attribute):
            parts.append(value.attr); value = value.value
        if isinstance(value, ast.Name): parts.append(value.id)
        return ".".join(reversed(parts))
    return ""


#: Décorateur d'enregistrement associé à chaque registre de `Host`.
_REGISTRY_DECORATOR = {"tools": "tool", "sensors": "sensor",
                       "subagents": "subagent"}


def _loop_registered(node: ast.For, registry: str) -> tuple[set[str], set[int]]:
    """Résout `for fn in (a, b, c): h.<registry>[fn.__name__] = fn`.

    Idiome courant : il enregistre par le nom de la fonction, donc aucune
    chaîne littérale n'apparaît nulle part. Le lire évite d'annoncer manquants
    des outils que l'hôte fournit bel et bien.
    """
    if not isinstance(node.target, ast.Name):
        return set(), set()
    variable = node.target.id
    consumed: set[int] = set()
    for stmt in node.body:
        if not isinstance(stmt, ast.Assign):
            continue
        for target in stmt.targets:
            if not (isinstance(target, ast.Subscript)
                    and isinstance(target.value, ast.Attribute)
                    and target.value.attr == registry):
                continue
            key = target.slice
            if (isinstance(key, ast.Attribute) and key.attr == "__name__"
                    and isinstance(key.value, ast.Name)
                    and key.value.id == variable):
                consumed.add(id(target.value))
    if not consumed:
        return set(), set()
    # L'itérable doit être une suite de noms pour que l'inventaire soit sûr :
    # sur `for fn in _discover():`, on ne consomme rien et le registre est
    # déclaré incomplet en aval.
    if not isinstance(node.iter, (ast.Tuple, ast.List, ast.Set)):
        return set(), set()
    if any(not isinstance(element, ast.Name) for element in node.iter.elts):
        return set(), set()
    return {element.id for element in node.iter.elts}, consumed


def _registry_keys(tree: ast.AST, registry: str) -> tuple[set[str], bool]:
    """Noms enregistrés dans `X.<registry>`, et si l'inventaire est complet.

    Le second membre est ce qui sépare un constat d'une invention. Un registre
    se remplit par des formes qu'aucune lecture statique ne résout — clé
    calculée, alias local, fabrique. Dès qu'une seule subsiste, l'inventaire
    est déclaré INCOMPLETE par B014 sans conclure à tort
    (« manquants »), sans rien perdre dans l'autre (« non déclarés », qui ne
    porte que sur ce qu'on a réellement vu).
    """
    keys: set[str] = set()
    consumed: set[int] = set()
    decorator = _REGISTRY_DECORATOR[registry]
    decorators_complete = True

    for node in ast.walk(tree):
        if isinstance(node, ast.For):
            names, seen = _loop_registered(node, registry)
            keys |= names
            consumed |= seen
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Subscript) and isinstance(target.value, ast.Attribute) and target.value.attr == registry:
                    value = target.slice
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        keys.add(value.value)
                        consumed.add(id(target.value))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = node.func.value
            if node.func.attr == "update" and isinstance(owner, ast.Attribute) and owner.attr == registry and node.args and isinstance(node.args[0], ast.Dict):
                literal = [key for key in node.args[0].keys
                           if isinstance(key, ast.Constant) and isinstance(key.value, str)]
                keys.update(key.value for key in literal)
                if len(literal) == len(node.args[0].keys):
                    consumed.add(id(owner))
            if node.func.attr == decorator:
                if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                    keys.add(node.args[0].value)
                else:
                    decorators_complete = False
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node_decorator in node.decorator_list:
                if isinstance(node_decorator, ast.Call) and isinstance(node_decorator.func, ast.Attribute):
                    if node_decorator.func.attr == decorator and node_decorator.args and isinstance(node_decorator.args[0], ast.Constant):
                        keys.add(str(node_decorator.args[0].value))

    # Toute mention du registre qu'aucune forme reconnue n'a consommée — une
    # lecture, un alias, une écriture à clé calculée — rend l'inventaire
    # partiel : on l'assume plutôt que de conclure sur un registre entrevu.
    mentions = [node for node in ast.walk(tree)
                if isinstance(node, ast.Attribute) and node.attr == registry]
    # …et un fichier qui ne monte aucun hôte n'atteste pas un registre vide :
    # il atteste qu'il ne le remplit pas *ici*. Un hôte qui délègue son montage
    # (`return base.build()`) est dans ce cas, et conclure « tout manque » y
    # serait précisément l'invention que ce module s'interdit. Qu'un registre
    # reste muet dans un hôte qui en remplit d'autres, en revanche, se conclut :
    # c'est l'oubli que B014 existe pour attraper.
    complete = _assembles_host(tree) and decorators_complete and all(
        id(node) in consumed for node in mentions)
    return keys, complete


def _assembles_host(tree: ast.AST) -> bool:
    """Ce fichier monte-t-il lui-même l'hôte, ou le confie-t-il à un autre ?"""
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _REGISTRY_DECORATOR:
            return True
        if isinstance(node, ast.Call) and _call_name(node).endswith("Host"):
            return True
    return False


def _contract_findings(agent_path: Path, tree: ast.AST) -> List[Finding]:
    from .analyzer import _walk_stmts
    from .nodes import DelegateStmt
    from .parser import parse_file
    findings: List[Finding] = []
    program = parse_file(str(agent_path))
    declared_tools = {tool.name for agent in program.agents for tool in agent.tools}
    declared_sensors = {observer.path for agent in program.agents for observer in agent.observers}
    host_tools, tools_known = _registry_keys(tree, "tools")
    host_sensors, sensors_known = _registry_keys(tree, "sensors")
    # Un `TOOL` peut décrire un **sous-agent** : W127 l'exige même, pour que
    # `DELEGATE` passe par la politique avec un risque déclaré. Son
    # implémentation vit dans `host.subagents`, pas dans `host.tools` — les
    # ignorer revenait à déclarer manquante toute cible de délégation.
    host_subagents, subagents_known = _registry_keys(tree, "subagents")
    delegated = {stmt.agent for agent in program.agents for plan in agent.plans
                 for step in plan.steps
                 for stmt in _walk_stmts(step.body)
                 if isinstance(stmt, DelegateStmt)}

    for registry, complete in (("tools", tools_known), ("sensors", sensors_known),
                               ("subagents", subagents_known)):
        if not complete:
            findings.append(Finding(
                "B014", "error", f"INCOMPLETE / UNVERIFIABLE : registre {registry}", 0,
                "inventaire dynamique ou construction déléguée non résolue ; "
                "le contrat ne peut pas être déclaré vérifié"))

    checks = [("outils hôte non déclarés", host_tools - declared_tools),
              ("capteurs hôte non déclarés", host_sensors - declared_sensors),
              ("sous-agents hôte non déclarés", host_subagents - declared_tools)]
    if tools_known:
        # Une cible de `DELEGATE` s'implémente dans `subagents` : elle n'est
        # manquante que si ce registre-là est connu et ne la porte pas.
        missing_tools = {
            name for name in declared_tools - host_tools
            if not (name in delegated
                    and (name in host_subagents or not subagents_known))}
        checks.append(("outils hôte manquants", missing_tools))
    if sensors_known:
        checks.append(("capteurs hôte manquants", declared_sensors - host_sensors))
    for label, missing in checks:
        if missing:
            findings.append(Finding("B014", "error", f"{label} : {', '.join(sorted(missing))}", 0,
                "le contrat X.agent ↔ X.py doit être exact"))
    return findings

# ------------------------------------------------------------------ API

def _analyze(path: Path, project_root: Path | str | None, external_policy: str):
    from ._boundary_security import security_findings

    if external_policy not in {"report", "error"}:
        raise ValueError("external_policy must be 'report' or 'error'")
    project = discover(path, Path(project_root) if project_root is not None else None)
    report = Report(path=path, analyzed_paths=sorted(project.sources),
                    external_imports=sorted(project.external),
                    excluded_imports=sorted(project.excluded),
                    complete=not project.issues)
    exports = project.constants()
    aliases = project.aliases()
    for source in project.sources.values():
        symbols = Symbols(source.tree, exports, aliases)
        visitor = _HostVisitor(symbols)
        visitor.visit(source.tree)
        findings = visitor.findings + security_findings(source.tree, symbols)
        waivers = _waivers(source.source, source.tree)
        for finding in findings:
            finding.path = source.path
            reason = waivers.get(finding.line)
            if reason:
                finding.waived = True
                finding.reason = reason
        report.findings.extend(findings)
    for origin, line, message in project.issues:
        report.findings.append(Finding(
            "B016", "error", f"INCOMPLETE / UNVERIFIABLE : {message}", line,
            "la surface d’analyse doit être résolue avant de conclure", path=origin))
    if external_policy == "error":
        for name in report.external_imports:
            report.findings.append(Finding(
                "B016", "error", f"dépendance externe hors analyse : {name}", 0,
                "politique external_policy=error ; auditer ou intégrer cette dépendance",
                path=path))
    _sort_findings(report)
    return report, project


def _sort_findings(report: Report):
    report.findings.sort(key=lambda f: (str(f.path or report.path), f.line, f.code))


def check_host(path: Path | str, *, project_root: Path | str | None = None,
               external_policy: str = "report") -> Report:
    """Lint a host and transitive local imports without executing any code.

    External libraries and the agentl runtime are outside the analysed scope;
    dependencies are listed, or rejected with external_policy="error".
    """
    report, _ = _analyze(Path(path), project_root, external_policy)
    return report


def check_pair(agent_path: Path | str, *, project_root: Path | str | None = None,
               external_policy: str = "report") -> Report:
    """Analyse host modules and compare their registries with the agent contract."""
    from .analyzer import _walk_stmts
    from .nodes import ForEachStmt, IfStmt
    from .parser import parse_file

    agent_path = Path(agent_path)
    host_path = agent_path.with_suffix(".py")
    if not host_path.exists():
        return Report(path=host_path, complete=False, findings=[Finding(
            "B000", "error", f"hôte introuvable : {host_path.name}", 0,
            "la norme impose X.agent ↔ X.py dans le même répertoire", path=host_path)])

    report, project = _analyze(host_path, project_root, external_policy)
    agent_source = agent_path.read_text(encoding="utf-8")
    program = parse_file(str(agent_path))
    host_lines = sum(len(source.source.splitlines()) for source in project.sources.values())
    guarded = any(rule.effect == "NEVER" or rule.guard is not None
                  for agent in program.agents for rule in agent.policies)
    guarded |= any(plan.when is not None or any(
        isinstance(stmt, (IfStmt, ForEachStmt))
        for step in plan.steps for stmt in _walk_stmts(step.body))
        for agent in program.agents for plan in agent.plans)
    if host_lines > 80 and not guarded:
        report.findings.append(Finding(
            "B007", "error",
            f"{host_lines} lignes d'hôte pour un programme sans aucune garde", 0,
            "la décision peut être ailleurs que dans l'artefact auditable", path=host_path))

    entry = project.sources.get(host_path.resolve())
    # Imported subagents have their own X.agent contract. Their registries
    # must not be mistaken for the parent host's tools and sensors.
    sources = [source for source in project.sources.values()
               if source.path == host_path.resolve()
               or not source.path.with_suffix(".agent").exists()
               or entry is None or not _assembles_host(entry.tree)]
    combined = ast.Module(body=[stmt for source in sources for stmt in source.tree.body],
                          type_ignores=[])
    extra = _contract_findings(agent_path, combined)
    excluded_hosts = [source for source in project.sources.values()
                      if source not in sources and _assembles_host(source.tree)]
    if excluded_hosts:
        extra.append(Finding(
            "B014", "error", "INCOMPLETE / UNVERIFIABLE : plusieurs hôtes distincts", 0,
            "les registres des sous-agents ne sont pas ceux du parent ; "
            "vérifier séparément leurs contrats et la composition : "
            + ", ".join(source.path.name for source in excluded_hosts)))
    for finding in extra:
        finding.path = host_path
        if "INCOMPLETE" in finding.message:
            report.complete = False
    report.findings.extend(extra)
    descriptions = _untrusted_description_findings(agent_path, agent_source)
    for finding in descriptions:
        finding.path = agent_path
    report.findings.extend(descriptions)
    _sort_findings(report)
    return report


def _untrusted_description_findings(agent_path: Path,
                                    agent_source: str) -> List[Finding]:
    """B015 — description d'outil rédigée par un tiers et pilotant le modèle.

    Une `DESCRIPTION` importée d'un serveur MCP est du texte que l'agent n'a
    pas écrit et que le modèle lira : elle atteint le contexte de `REASON` et de
    la sélection de plan. Une description impérative n'est donc pas une
    maladresse de rédaction, c'est un canal d'instruction ouvert à un tiers.

    Le contrôle est à la frontière, pas dans l'analyseur, parce que c'est
    exactement sa question : quelle part de la décision vient d'ailleurs que du
    programme auditable.
    """
    from .mcp import suspicious_description

    findings: List[Finding] = []
    pattern = re.compile(r'^\s*DESCRIPTION\s+"((?:[^"\\]|\\.)*)"',
                         re.MULTILINE)
    for match in pattern.finditer(agent_source):
        text = match.group(1).encode().decode("unicode_escape", "ignore")
        marks = suspicious_description(text)
        if not marks:
            continue
        line = agent_source.count("\n", 0, match.start()) + 1
        findings.append(Finding(
            "B015", "error",
            "description d'outil au ton impératif : elle pilote le modèle",
            line,
            f"texte non écrit par l'agent et lu par le LLM — « {marks[0]} ». "
            f"Ré-importer avec --strip-descriptions, ou la réécrire."))
    return findings


def render(report: Report) -> str:
    lines = [f"frontière hôte/agent — {report.path.name}"]
    lines.append(f"  surface analysée : {len(report.analyzed_paths)} module(s) Python local(aux)")
    for path in report.analyzed_paths:
        lines.append(f"    · {_display_path(path, report.path)}")
    if report.external_imports:
        lines.append("  dépendances externes hors analyse : " + ", ".join(report.external_imports))
    if report.excluded_imports:
        lines.append("  bibliothèque standard / runtime hors analyse : " + ", ".join(report.excluded_imports))
    for finding in report.findings:
        if finding.waived:
            continue
        mark = "✗" if finding.severity == "error" else "!"
        place = _display_path(finding.path or report.path, report.path)
        if finding.line:
            place += f":{finding.line}"
        lines.append(f"  {mark} {finding.code}  {place}  {finding.message}")
        if finding.detail:
            lines.append(f"                     {finding.detail}")
    if report.waivers:
        lines.append(f"\n  levées assumées ({len(report.waivers)}) :")
        for finding in report.waivers:
            place = _display_path(finding.path or report.path, report.path)
            lines.append(f"    · {place}:{finding.line} {finding.code} — {finding.reason}")
    lines.append("")
    if not report.complete:
        lines.append("  ✘ INCOMPLETE / UNVERIFIABLE — analyse ou contrat incomplet")
    elif report.blocking:
        lines.append(f"  ✘ {len(report.blocking)} diagnostic(s) à corriger ou à justifier")
    else:
        lines.append("  ✓ aucun diagnostic bloquant sur la surface analysée")
    lines.append("  Contrôle heuristique ; ce verdict ne prouve pas l’absence de décisions ou de failles.")
    return "\n".join(lines)


def _display_path(path: Path, entrypoint: Path) -> str:
    try:
        return str(path.resolve().relative_to(entrypoint.resolve().parent))
    except ValueError:
        return str(path)

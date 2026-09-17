"""Static source discovery and scoped bindings for Boundary. Never imports code."""
from __future__ import annotations

import ast
import sys
import tokenize
from dataclasses import dataclass, field
from pathlib import Path


UNKNOWN = object()


class Symbols(ast.NodeVisitor):
    """Resolve simple imports, aliases and literal assignments in lexical order.

    These are may-bindings for linting, not a Python interpreter. Branches and
    dynamic dispatch are deliberately not treated as proof of safety.
    """

    def __init__(self, tree: ast.AST, exports: dict | None = None, aliases: dict | None = None):
        self.env: dict[str, tuple[str, object]] = {}
        self.references: dict[int, tuple[str, object]] = {}
        self.exports = exports or {}
        self.aliases = aliases or {}
        self.visit(tree)

    def name(self, node: ast.AST) -> str:
        if isinstance(node, ast.Name):
            return self.references.get(id(node), (node.id, UNKNOWN))[0]
        if isinstance(node, ast.Attribute):
            name = f"{self.name(node.value)}.{node.attr}"
            return self.aliases.get(name, name)
        if isinstance(node, ast.Call):
            return self.name(node.func)
        return ""

    def literal(self, node: ast.AST):
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return self.references.get(id(node), ("", UNKNOWN))[1]
        if isinstance(node, ast.Attribute):
            return self.exports.get(self.name(node), UNKNOWN)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
            value = self.literal(node.operand)
            if type(value) in (int, float):
                return -value if isinstance(node.op, ast.USub) else value
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            values = [self.literal(x) for x in node.elts]
            if all(x is not UNKNOWN for x in values):
                # Tuples preserve the constants without requiring hashability.
                return tuple(values)
        if isinstance(node, ast.Dict):
            keys = [self.literal(x) for x in node.keys if x is not None]
            if len(keys) == len(node.keys) and all(x is not UNKNOWN for x in keys):
                return tuple(keys)
        return UNKNOWN

    def visit_Name(self, node):
        self.references[id(node)] = self.env.get(node.id, (node.id, UNKNOWN))

    def visit_Import(self, node):
        for alias in node.names:
            local = alias.asname or alias.name.split('.')[0]
            self.env[local] = (alias.name if alias.asname else local, UNKNOWN)

    def visit_ImportFrom(self, node):
        for alias in node.names:
            qualified = '.'.join(filter(None, (getattr(node, '_boundary_module', node.module), alias.name)))
            self.env[alias.asname or alias.name] = (
                self.aliases.get(qualified, qualified), self.exports.get(qualified, UNKNOWN))

    def _assign(self, target, value):
        if isinstance(target, ast.Name):
            name = self.name(value) or target.id
            self.env[target.id] = (name, self.literal(value))
        elif isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                if isinstance(item, ast.Name):
                    self.env[item.id] = (item.id, UNKNOWN)

    def visit_Assign(self, node):
        self.visit(node.value)
        for target in node.targets:
            self._assign(target, node.value)

    def visit_AnnAssign(self, node):
        if node.value is not None:
            self.visit(node.value)
            self._assign(node.target, node.value)

    def visit_AugAssign(self, node):
        self.generic_visit(node)
        if isinstance(node.target, ast.Name):
            self.env[node.target.id] = (node.target.id, UNKNOWN)

    def visit_FunctionDef(self, node):
        for value in [*node.decorator_list, *node.args.defaults,
                      *(v for v in node.args.kw_defaults if v is not None)]:
            self.visit(value)
        self.env[node.name] = (node.name, UNKNOWN)
        outer = self.env.copy()
        # Python determines locals for the entire function, including before
        # their first assignment. Do not borrow a same-named global constant.
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
                self.env[child.id] = (child.id, UNKNOWN)
        for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]:
            self.env[arg.arg] = (arg.arg, UNKNOWN)
        for stmt in node.body:
            self.visit(stmt)
        self.env = outer

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        outer = self.env.copy()
        self.generic_visit(node)
        self.env = outer


@dataclass
class Source:
    path: Path
    source: str
    tree: ast.Module
    names: set[str] = field(default_factory=set)


@dataclass
class Project:
    root: Path
    sources: dict[Path, Source] = field(default_factory=dict)
    external: set[str] = field(default_factory=set)
    excluded: set[str] = field(default_factory=set)
    issues: list[tuple[Path, int, str]] = field(default_factory=list)

    def constants(self) -> dict:
        exports: dict = {}
        # Resolve re-export chains to a fixed point, without executing modules.
        for _ in range(len(self.sources) + 1):
            before = exports.copy()
            for source in self.sources.values():
                symbols = Symbols(source.tree, exports)
                for local, (_, value) in symbols.env.items():
                    if value is not UNKNOWN:
                        for module in source.names:
                            exports[f"{module}.{local}"] = value
            if exports == before:
                break
        return exports

    def aliases(self) -> dict:
        aliases: dict[str, str] = {}
        for _ in range(len(self.sources) + 1):
            before = aliases.copy()
            for source in self.sources.values():
                symbols = Symbols(source.tree, aliases=aliases)
                for local, (name, _) in symbols.env.items():
                    if name != local:
                        for module in source.names:
                            aliases[f"{module}.{local}"] = name
            if aliases == before:
                break
        return aliases


def project_root(path: Path) -> Path:
    for parent in path.parents:
        if (parent / 'pyproject.toml').is_file() or (parent / '.git').exists():
            return parent
    parent = path.parent
    while (parent / '__init__.py').is_file():
        parent = parent.parent
    return parent


def discover(path: Path, root: Path | None = None) -> Project:
    path = path.resolve()
    root = root.resolve() if root is not None else project_root(path)
    project = Project(root)
    if not path.is_relative_to(root):
        project.issues.append((path, 0, "hôte hors racine du projet"))
        return project
    runtime = Path(__file__).resolve().parent
    search = list(dict.fromkeys([path.parent, root, root / 'src']))
    pending: list[tuple[Path, str]] = [(path, path.stem)]
    attempted: set[Path] = set()

    def enqueue(candidate: Path, name: str, origin: Path, line: int):
        resolved = candidate.resolve()
        if not resolved.is_relative_to(root):
            project.issues.append((origin, line, f"module hors racine : {candidate}"))
            return
        if resolved.is_relative_to(runtime) and not path.is_relative_to(runtime):
            project.excluded.add('agentl (runtime)')
            return
        pending.append((resolved, name))

    def resolve(name: str, origin: Path, line: int, *, optional=False):
        parts = name.split('.')
        for base in search:
            module = base.joinpath(*parts)
            candidate = module / '__init__.py' if module.is_dir() else module.with_suffix('.py')
            if candidate.is_file():
                for index in range(1, len(parts)):
                    init = base.joinpath(*parts[:index], '__init__.py')
                    if init.is_file():
                        enqueue(init, '.'.join(parts[:index]), origin, line)
                enqueue(candidate, name, origin, line)
                return True
            if module.is_dir():  # namespace package; its children are resolved separately
                return True
        if not optional:
            top = parts[0]
            if top in sys.stdlib_module_names or top == 'agentl':
                project.excluded.add(top)
            else:
                project.external.add(name)
        return False

    while pending:
        current, module_name = pending.pop(0)
        if current in project.sources:
            project.sources[current].names.add(module_name)
            continue
        if current in attempted:
            continue
        attempted.add(current)
        try:
            with tokenize.open(current) as handle:
                source = handle.read()
            tree = ast.parse(source, filename=str(current))
        except (OSError, UnicodeError, SyntaxError) as exc:
            project.issues.append((current, getattr(exc, 'lineno', 0) or 0,
                                   f"source illisible ou invalide : {exc}"))
            continue
        project.sources[current] = Source(current, source, tree, {module_name})
        symbols = Symbols(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    resolve(alias.name, current, node.lineno)
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = current.parent
                    for _ in range(node.level - 1):
                        base = base.parent
                    if not base.is_relative_to(root):
                        project.issues.append((current, node.lineno, 'import relatif hors racine'))
                        continue
                    # Locate the module by filesystem, independent of the name
                    # by which the entrypoint was first reached.
                    relative = base.relative_to(root).parts
                    name = '.'.join((*relative, *([node.module] if node.module else [])))
                else:
                    name = node.module or ''
                node._boundary_module = name
                found = resolve(name, current, node.lineno) if name else True
                if node.level and not found:
                    project.issues.append((current, node.lineno, f"import relatif introuvable : {name}"))
                for alias in node.names:
                    if alias.name == '*':
                        project.issues.append((current, node.lineno,
                                               'import * : liaisons non résolues'))
                    elif found:
                        resolve('.'.join(filter(None, (name, alias.name))),
                                current, node.lineno, optional=True)
            elif isinstance(node, ast.Call):
                name = symbols.name(node.func)
                if name in {'__import__', 'importlib.import_module'}:
                    value = symbols.literal(node.args[0]) if node.args else UNKNOWN
                    if isinstance(value, str) and not value.startswith('.'):
                        resolve(value, current, node.lineno)
                    else:
                        project.issues.append((current, node.lineno,
                                               'import dynamique non résolu'))
                elif name in {'exec', 'eval'}:
                    project.issues.append((current, node.lineno, 'code dynamique non analysable'))
    return project

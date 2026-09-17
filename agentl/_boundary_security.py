"""Conservative, intraprocedural control/data-flow signals for B008–B013.

Recognised calls are review signals, not proofs of their implementations or
durability. Unknown branches never contribute a protection to another path.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

from ._boundary_project import UNKNOWN, Symbols


_MODES = {"dry_run": True, "real_actions_enabled": False,
          "allow_external": False, "consent": False, "external_llm": False}
_EXTERNAL = {"allow_external", "consent", "external_llm"}
_PROCESSES = {f"subprocess.{name}" for name in
              ("run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput")}
_DESTRUCTIVE = {"os.kill", "os.killpg", "os.remove", "os.unlink", "os.rmdir",
                "os.rename", "os.replace", "shutil.move", "shutil.rmtree"}
_LLMS = {"OpenAI", "AsyncOpenAI", "AzureOpenAI", "Anthropic", "AsyncAnthropic",
         "GeminiLLM", "AnthropicLLM", "OpenAILLM"}
_RAW = re.compile(r"(?:^|_)(?:raw|log|logs|message|messages|prompt|content)(?:_|$)", re.I)


def _key(node: ast.AST) -> str:
    # Conversions preserve target identity for this local review signal.
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        if node.func.id in {"int", "str", "Path"} and node.args:
            return _key(node.args[0])
    return ast.dump(node, include_attributes=False)


def _variables(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


@dataclass
class Flow:
    defaults: dict[str, bool] = field(default_factory=dict)
    gates: set[str] = field(default_factory=set)
    validated: dict[str, set[str]] = field(default_factory=dict)
    rollback: dict[str, set[str]] = field(default_factory=dict)
    cursors: set[str] = field(default_factory=set)
    tainted: set[str] = field(default_factory=set)
    log_handles: set[str] = field(default_factory=set)

    def copy(self):
        return Flow(self.defaults.copy(), self.gates.copy(), self.validated.copy(),
                    self.rollback.copy(), self.cursors.copy(), self.tainted.copy(),
                    self.log_handles.copy())

    def invalidate(self, names: set[str]):
        for name in names:
            self.defaults.pop(name, None)
        self.gates -= names
        for proofs in (self.validated, self.rollback):
            for key, dependencies in list(proofs.items()):
                if names & dependencies:
                    del proofs[key]
        self.cursors = {key for key in self.cursors
                        if not any(f"id='{name}'" in key for name in names)}
        self.tainted -= names
        self.log_handles -= names


class SecurityFlow:
    def __init__(self, tree: ast.Module, symbols: Symbols):
        self.tree = tree
        self.symbols = symbols
        self.findings = []
        self._seen: set[tuple[str, int]] = set()
        self.module_defaults = {}
        for stmt in tree.body:
            if isinstance(stmt, ast.Assign):
                for target in stmt.targets:
                    if isinstance(target, ast.Name) and target.id in _MODES:
                        value = symbols.literal(stmt.value)
                        if type(value) is bool:
                            self.module_defaults[target.id] = value

    def add(self, code, node, message, detail):
        from .boundary import Finding
        key = code, node.lineno
        if key not in self._seen:
            self.findings.append(Finding(code, "error", message, node.lineno, detail))
            self._seen.add(key)

    def run(self):
        self.block(self.tree.body, Flow())
        return self.findings

    def gated(self, test, truth, state):
        """Facts implied by taking a branch, including safe early returns."""
        result = state.copy()
        if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
            return self.gated(test.operand, not truth, state)
        if isinstance(test, ast.BoolOp):
            # Only AND-true and OR-false imply every component.
            if (isinstance(test.op, ast.And) and truth) or (isinstance(test.op, ast.Or) and not truth):
                for value in test.values:
                    result = self.gated(value, truth, result)
            return result
        if isinstance(test, ast.Compare) and len(test.ops) == 1:
            left, right = test.left, test.comparators[0]
            if isinstance(left, ast.Constant):
                left, right = right, left
            if isinstance(right, ast.Constant) and type(right.value) is bool:
                if isinstance(test.ops[0], (ast.Eq, ast.Is)):
                    return self.gated(left, truth == right.value, state)
                if isinstance(test.ops[0], (ast.NotEq, ast.IsNot)):
                    return self.gated(left, truth != right.value, state)
        if isinstance(test, ast.Name) and test.id in _MODES:
            safe = _MODES[test.id]
            if state.defaults.get(test.id, UNKNOWN) is safe and truth is not safe:
                result.gates.add(test.id)
        return result

    def function(self, node):
        state = Flow(defaults=self.module_defaults.copy())
        args = [*node.args.posonlyargs, *node.args.args]
        defaults = dict(zip([a.arg for a in args[-len(node.args.defaults):]]
                            if node.args.defaults else [], node.args.defaults))
        defaults.update({arg.arg: value for arg, value in
                         zip(node.args.kwonlyargs, node.args.kw_defaults) if value is not None})
        for arg in [*args, *node.args.kwonlyargs]:
            state.defaults.pop(arg.arg, None)
            if _RAW.search(arg.arg):
                state.tainted.add(arg.arg)
        for name, value in defaults.items():
            literal = self.symbols.literal(value)
            if name in _MODES and type(literal) is bool:
                state.defaults[name] = literal
        self.block(node.body, state)

    @staticmethod
    def merge(left, right):
        return Flow(
            {k: v for k, v in left.defaults.items() if right.defaults.get(k, UNKNOWN) is v},
            left.gates & right.gates,
            {k: v for k, v in left.validated.items() if k in right.validated},
            {k: v for k, v in left.rollback.items() if k in right.rollback},
            left.cursors & right.cursors,
            left.tainted | right.tainted,
            left.log_handles | right.log_handles)

    def block(self, statements, state):
        for stmt in statements:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.function(stmt)
            elif isinstance(stmt, ast.ClassDef):
                self.block(stmt.body, Flow())
            elif isinstance(stmt, ast.If):
                self.expr(stmt.test, state.copy())
                left, left_exits = self.block(stmt.body, self.gated(stmt.test, True, state))
                right, right_exits = self.block(stmt.orelse, self.gated(stmt.test, False, state))
                if left_exits and right_exits:
                    return state, True
                state = right if left_exits else left if right_exits else self.merge(left, right)
            elif isinstance(stmt, (ast.Return, ast.Raise)):
                value = stmt.value if isinstance(stmt, ast.Return) else stmt.exc
                if value is not None:
                    self.expr(value, state)
                return state, True
            elif isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                value = stmt.value
                if value is not None:
                    raw = self.expr(value, state)
                    log = self.is_log(value, state)
                else:
                    raw = log = False
                targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
                for target in targets:
                    names = _variables(target)
                    state.invalidate(names)
                    if raw:
                        state.tainted.update(names)
                    if log:
                        state.log_handles.update(names)
                    if isinstance(target, ast.Name) and target.id in _MODES and value is not None:
                        literal = self.symbols.literal(value)
                        if type(literal) is bool:
                            state.defaults[target.id] = literal
                # A validated target returned by a raising validator is also
                # a review signal for the variable holding that return value.
                if isinstance(value, ast.Call) and self.validator(value):
                    for target in targets:
                        state.validated[_key(target)] = _variables(target)
            elif isinstance(stmt, ast.Expr):
                self.expr(stmt.value, state)
                if isinstance(stmt.value, ast.Call):
                    self.protect(stmt.value, state)
            elif isinstance(stmt, (ast.With, ast.AsyncWith)):
                nested = state.copy()
                for item in stmt.items:
                    self.expr(item.context_expr, nested)
                    if item.optional_vars is not None and self.is_log(item.context_expr, nested):
                        nested.log_handles.update(_variables(item.optional_vars))
                nested, exits = self.block(stmt.body, nested)
                # A context manager may suppress exceptions. Its body cannot
                # establish protection for the code following the with.
                state = self.merge(state, nested)
            elif isinstance(stmt, (ast.For, ast.AsyncFor, ast.While)):
                expr = stmt.test if isinstance(stmt, ast.While) else stmt.iter
                self.expr(expr, state.copy())
                nested = state.copy()
                if not isinstance(stmt, ast.While):
                    nested.invalidate(_variables(stmt.target))
                # Mutations inside a loop invalidate facts on the back edge.
                writes = {n.id for n in ast.walk(stmt) if isinstance(n, ast.Name)
                          and isinstance(n.ctx, ast.Store)}
                nested.invalidate(writes)
                body, _ = self.block(stmt.body, nested)
                other, _ = self.block(stmt.orelse, nested.copy())
                state = self.merge(state, self.merge(body, other))
            elif isinstance(stmt, ast.Try):
                body, _ = self.block(stmt.body, state.copy())
                branches = [body, state]
                for handler in stmt.handlers:
                    branch, _ = self.block(handler.body, state.copy())
                    branches.append(branch)
                for branch in branches:
                    state = self.merge(state, branch)
                self.block(stmt.orelse, state.copy())
                state, exits = self.block(stmt.finalbody, state)
                if exits:
                    return state, True
            else:
                # Includes assert: Python -O removes it, so it is never a
                # protection. Inspect calls without carrying their effects.
                for child in ast.iter_child_nodes(stmt):
                    self.expr(child, state.copy())
        return state, False

    def validator(self, call):
        name = self.symbols.name(call.func).split('.')[-1].lower()
        return (name in {"validate", "validate_target", "revalidate_target", "attest", "_consume"}
                or name.startswith(("validate_target_", "revalidate_", "consume_evidence")))

    def protect(self, call, state):
        name = self.symbols.name(call.func).split('.')[-1].lower()
        targets = [*call.args, *(kw.value for kw in call.keywords)]
        if self.validator(call):
            for target in targets:
                state.validated[_key(target)] = _variables(target)
        if name.startswith(("record_rollback", "save_rollback", "persist_rollback", "journal_rollback")):
            for target in targets:
                state.rollback[_key(target)] = _variables(target)
        if name == 'resolve' and isinstance(call.func, ast.Attribute):
            if any(kw.arg == 'strict' and self.symbols.literal(kw.value) is True for kw in call.keywords):
                target = call.func.value
                state.validated[_key(target)] = _variables(target)
        if name == 'seek' and isinstance(call.func, ast.Attribute) and call.args:
            # A literal rewind is not a persisted cursor.
            if any('cursor' in x.lower() or 'offset' in x.lower() for x in _variables(call.args[0])):
                state.cursors.add(_key(call.func.value))

    def is_log(self, node, state):
        if _variables(node) & state.log_handles:
            return True
        for child in ast.walk(node):
            value = self.symbols.literal(child)
            if isinstance(value, str) and (value.endswith('.log') or '/var/log/' in value):
                return True
        return False

    def destructive_target(self, call):
        name = self.symbols.name(call.func)
        if name in _DESTRUCTIVE:
            return call.args[0] if call.args else next(
                (kw.value for kw in call.keywords if kw.arg in {'path', 'pid', 'src'}), call)
        if name.endswith(('.unlink', '.rmdir')):
            if isinstance(call.func, ast.Attribute):
                return call.func.value
            # A saved bound method retains its destructive identity. The
            # receiver may no longer be reconstructible; keep the signal.
            return call.func
        if name.startswith('pathlib.') and name.endswith(('.rename', '.replace')):
            return call.func.value
        if name in _PROCESSES:
            argument = call.args[0] if call.args else next(
                (kw.value for kw in call.keywords if kw.arg == 'args'), call)
            command = self.symbols.literal(argument)
            if isinstance(argument, (ast.List, ast.Tuple)):
                parts = tuple(self.symbols.literal(item) for item in argument.elts)
            else:
                parts = command if isinstance(command, tuple) else (command,)
            # An unknown executable is never silently classified as harmless.
            if not parts or parts[0] is UNKNOWN:
                return argument
            for part in parts:
                if isinstance(part, str) and re.search(
                        r'(?:^|[\s/])(iptables|nft|usermod|userdel|kill|pkill|systemctl|rm|mv|chmod|chown)(?:\s|$)', part):
                    return argument
            if parts[0] in {'ip', '/sbin/ip', '/usr/sbin/ip'} and 'link' in parts:
                return argument
        return None

    def expr(self, node, state):
        if isinstance(node, ast.Lambda):
            # The lambda is executed later, outside the creation path.
            return self.expr(node.body, Flow(tainted=state.tainted.copy(),
                                             log_handles=state.log_handles.copy()))
        if isinstance(node, (ast.BoolOp, ast.IfExp, ast.comprehension)):
            return any([self.expr(child, state.copy()) for child in ast.iter_child_nodes(node)])
        raw = isinstance(node, ast.Name) and (node.id in state.tainted or bool(_RAW.search(node.id)))
        for child in ast.iter_child_nodes(node):
            raw = self.expr(child, state) or raw
        if not isinstance(node, ast.Call):
            return raw
        name = self.symbols.name(node.func)
        if name in _PROCESSES:
            if any(kw.arg == 'shell' and self.symbols.literal(kw.value) is True for kw in node.keywords):
                self.add('B009', node, 'subprocess shell=True',
                         'utiliser une liste argv sans interpréteur de commandes')
        target = self.destructive_target(node)
        if target is not None:
            if not state.gates & {'dry_run', 'real_actions_enabled'}:
                self.add('B008', node, 'primitive destructive sans dry-run par défaut vérifiable',
                         'garder ce chemin par un opt-in dont le défaut est inoffensif')
            if _key(target) not in state.validated:
                self.add('B010', node, 'cible destructive sans revalidation locale vérifiable',
                         'revérifier cette cible sur chaque chemin avant l’action')
            if _key(target) not in state.rollback:
                self.add('B012', node, 'action sans enregistrement de rollback local vérifiable',
                         'enregistrer une recette pour cette cible avant l’action')
            # A previous action can invalidate both the identity and its recipe.
            state.validated.pop(_key(target), None)
            state.rollback.pop(_key(target), None)
        if isinstance(node.func, ast.Attribute) and node.func.attr in {'read', 'read_text', 'read_bytes', 'readlines'}:
            owner = node.func.value
            if self.is_log(owner, state):
                raw = True
                if _key(owner) not in state.cursors:
                    self.add('B011', node, 'lecture de journaux sans curseur local vérifiable',
                             'positionner ce flux sur un curseur persistant avant la lecture')
        parts = name.split('.')
        external_call = any(part in _LLMS for part in parts)
        # Adapter construction exposes future runtime context. For SDK
        # clients, only actual requests carrying data are sinks.
        adapter = parts[-1] in {'GeminiLLM', 'AnthropicLLM', 'OpenAILLM'}
        if external_call and (raw or adapter) and not state.gates & _EXTERNAL:
            self.add('B013', node, 'données vers LLM externe sans opt-in local vérifiable',
                     'exiger un consentement désactivé par défaut sur ce chemin de sortie')
        return raw


def security_findings(tree: ast.Module, symbols: Symbols):
    return SecurityFlow(tree, symbols).run()

"""Analyses de flot conservatives utilisées par les avertissements CHECK.

Elles ne constituent pas une preuve de sûreté interprocédurale.
"""
from .nodes import (BinOp, CallStmt, ForEachStmt, IfStmt, Literal, LoopStmt,
                    PathExpr, ReasonStmt, SetStmt, UnOp, VerifyStmt)


def guard_sites(agent):
    from .analyzer import _RUNTIME_PATH_PREFIXES, _walk_expr
    initial = {b.path for b in agent.beliefs} | {o.path for o in agent.observers}
    prefixes = _RUNTIME_PATH_PREFIXES - {'item', 'reason', 'payload'}

    def walk(stmts, known, scope, where):
        known = set(known)
        for stmt in stmts:
            if isinstance(stmt, IfStmt):
                yield stmt.cond, where, stmt.line, set(known), scope
                left = yield from walk(stmt.then, known, scope, where)
                right = yield from walk(stmt.otherwise, known, scope, where)
                known = left & right
            elif isinstance(stmt, (ForEachStmt, LoopStmt)):
                nested = scope | {stmt.var} if isinstance(stmt, ForEachStmt) else scope
                yield from walk(stmt.body, known, nested, where)
            elif isinstance(stmt, VerifyStmt):
                yield from walk(stmt.on_fail, known, scope, where)
            elif isinstance(stmt, SetStmt):
                unresolved = any(isinstance(n, PathExpr) and len(n.parts) > 1
                                 and n.dotted not in known and n.parts[0] not in scope
                                 for n in _walk_expr(stmt.value))
                if unresolved:
                    known.discard(stmt.target)
                else:
                    known.add(stmt.target)
            elif isinstance(stmt, ReasonStmt):
                known |= set(stmt.produce) | {f'reason.{k}' for k in stmt.produce}
            # Un appel externe peut échouer : OUTPUT/EFFECT ne sont pas des
            # définitions certaines sur le chemin qui suit l'appel.
        return known

    for plan in agent.plans:
        where = f'le plan {plan.name}'
        if plan.when is not None:
            yield plan.when, where, plan.line, initial, prefixes
        yield from walk([s for step in plan.steps for s in step.body], initial, prefixes, where)
    for handler in [*agent.events, *agent.messages]:
        if handler.when is not None:
            yield handler.when, 'un gestionnaire', handler.line, initial, prefixes | {'payload'}
        yield from walk(handler.body, initial, prefixes | {'payload'}, 'un gestionnaire')
    if agent.decide:
        for rule in agent.decide.rules:
            yield rule.cond, 'une règle DECIDE', rule.line, initial, prefixes
            yield from walk(rule.then, initial, prefixes, 'DECIDE')
    if agent.loop:
        yield from walk(agent.loop.body, initial, prefixes, 'LOOP')


def unverified_actions(agent, plan):
    from .analyzer import _collect_paths
    missing = []
    def walk(stmts, verified):
        verified = set(verified)
        for stmt in reversed(stmts):
            if isinstance(stmt, VerifyStmt):
                _collect_paths(stmt.cond, verified)
                walk(stmt.on_fail, set())
            elif isinstance(stmt, IfStmt):
                verified = walk(stmt.then, verified) & walk(stmt.otherwise, verified)
            elif isinstance(stmt, (LoopStmt, ForEachStmt)):
                walk(stmt.body, verified)
            elif isinstance(stmt, SetStmt):
                verified.discard(stmt.target)
            elif isinstance(stmt, CallStmt):
                tool = agent.tool(stmt.call.name)
                if tool is None or not (tool.side_effects or tool.risk in ('HIGH', 'CRITICAL')):
                    continue
                affected = set(tool.side_effects) | set(tool.outputs)
                affected |= {e.path for branch in tool.branches for e in branch.effects}
                related = {v for v in verified if any(v == a or v.startswith(a + '.') or a.startswith(v + '.') for a in affected)}
                if not related:
                    missing.append(stmt)
                # Une vérification après une seconde écriture ne vérifie pas
                # l'état intermédiaire laissé par la première.
                verified -= related
        return verified
    walk([s for step in plan.steps for s in step.body], set())
    return missing


def protective_guard(node, truth=True):
    """Reconnaît une polarité sûre explicite ; un nom seul ne suffit pas."""
    if isinstance(node, UnOp) and node.op == 'NOT':
        return protective_guard(node.operand, not truth)
    if isinstance(node, BinOp):
        if node.op in ('AND', 'OR'):
            parts = [protective_guard(n, truth) for n in (node.left, node.right)]
            return any(parts) if (node.op == 'AND') == truth else all(parts)
        if node.op in ('==', '!='):
            for path, value in ((node.left, node.right), (node.right, node.left)):
                if not isinstance(path, PathExpr):
                    continue
                word = value.value if isinstance(value, Literal) else (value.dotted if isinstance(value, PathExpr) else None)
                if word not in (True, False, 'true', 'false', 'yes', 'no'):
                    continue
                # `flag != true` ne prouve pas `flag == false` sur UNDEFINED.
                if (node.op == '==') != truth:
                    continue
                flag = word is True or word in ('true', 'yes')
                name = path.dotted.lower()
                negative = 'injection' in name or 'untrusted' in name
                positive = 'trusted' in name or 'attested' in name
                if (negative and not flag) or (positive and not negative and flag):
                    return True
    return False

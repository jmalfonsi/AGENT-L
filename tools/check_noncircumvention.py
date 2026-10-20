"""Non-contournement : seul le noyau atteint l'hôte (invariant 1, SPEC §45).

    python tools/check_noncircumvention.py

« Aucun appel n'atteint un outil sans traverser le moteur de politiques » était
« une propriété du code Python, pas un théorème » (SPEC §28). `Host.invoke`
refuse déjà un appel sans permis à l'exécution ; ce contrôle ferme le versant
**statique** : un chemin neuf qui appellerait l'hôte ou un sous-agent en
dehors du noyau fait échouer la CI, au lieu d'attendre qu'un test l'exerce.

Règles, sur l'arbre syntaxique du paquet `agentl` (hors ressources du Studio) :

1. `.invoke` n'est accédé **que** dans `Kernel.execute` (agentl/kernel/gate.py),
   ou dans une méthode elle-même nommée `invoke` — une enveloppe d'hôte qui
   transmet, appelée sous le permis du noyau —, ou dans les portées nommées de
   `INVOKE_ALLOWED` (instrumentation du Studio, qui trace sans décider).
2. `.subagents` n'est accédé que dans `Kernel.delegate`, ou dans une portée de
   `SUBAGENTS_ALLOWED` : le registre et ses enveloppes d'hôte.
3. Aucun accès dynamique : `getattr(x, "invoke")`, `getattr(x, "subagents")`,
   `x.__getattribute__("invoke")`.

Une portée s'écrit `fichier:Classe.méthode`. L'allonger est une décision
d'architecture : elle se justifie dans le CHANGELOG, comme le budget de la TCB.
Ce que ce contrôle ne voit pas : un hôte **écrit par l'utilisateur** (c'est
`agentl boundary`), le code hors du paquet, `exec`/`eval`, un alias à travers
une variable (`f = host.invoke` est vu, `f()` ne l'est pas — l'accès l'est).
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "agentl"

GATE = "agentl/kernel/gate.py"
GATE_INVOKE = "Kernel.execute"
GATE_DELEGATE = "Kernel.delegate"

#: Portées hors noyau qui peuvent accéder à `.invoke` sans s'appeler `invoke`.
INVOKE_ALLOWED = frozenset({
    # Le Studio remplace `host.invoke` par une version qui chronomètre ; elle
    # n'est appelée que par `Kernel.execute`, sous permis.
    "agentl/studio/session.py:StudioSession._instrument_host",
})

#: Portées qui détiennent ou transmettent le registre des sous-agents.
SUBAGENTS_ALLOWED = frozenset({
    "agentl/host.py:Host.__init__",
    "agentl/host.py:Host.subagent.wrap",
    "agentl/aio.py:AsyncHost.__init__",
    "agentl/aio.py:AsyncHost.subagent.wrap",
    "agentl/aio.py:AsyncHost.delegate",
    "agentl/aio.py:_BridgedSubagents.__contains__",
    "agentl/aio.py:_BridgedSubagents.get",
    "agentl/durable.py:DurableHost.subagents",
    "agentl/replay.py:RecordingHost.subagents",
    "agentl/scenario.py:ScenarioHost.__init__",
    # La vue accordée à un délégué AGENT-L (v1.16) : elle **retire** les
    # sous-agents Python au délégué, son registre est vide.
    "agentl/compose.py:GrantedHost.__init__",
})

DYNAMIC = ("invoke", "subagents")


def _scope(node: ast.AST, parents: Dict[ast.AST, ast.AST]) -> Tuple[str, str]:
    """`(Classe.méthode…, fonction englobante la plus proche)`."""
    names: List[str] = []
    innermost = ""
    current = node
    while current in parents:
        current = parents[current]
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef,
                                ast.ClassDef)):
            names.append(current.name)
            if not innermost and not isinstance(current, ast.ClassDef):
                innermost = current.name
    return ".".join(reversed(names)), innermost


def scan_source(relpath: str, source: str) -> List[str]:
    """Violations d'un fichier : `chemin:ligne : raison`."""
    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree)
               for child in ast.iter_child_nodes(node)}
    found: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in DYNAMIC:
            scope, innermost = _scope(node, parents)
            where = f"{relpath}:{scope}"
            if node.attr == "invoke":
                ok = ((relpath == GATE and scope == GATE_INVOKE)
                      or innermost == "invoke"
                      or where in INVOKE_ALLOWED)
            else:
                ok = ((relpath == GATE and scope == GATE_DELEGATE)
                      or where in SUBAGENTS_ALLOWED)
            if not ok:
                found.append(f"{relpath}:{node.lineno} : `.{node.attr}` "
                             f"accédé dans `{scope or '<module>'}`, hors du "
                             f"noyau et des portées autorisées")
        if isinstance(node, ast.Call):
            fn = node.func
            dynamic = (isinstance(fn, ast.Name) and fn.id == "getattr") or \
                (isinstance(fn, ast.Attribute)
                 and fn.attr == "__getattribute__")
            args = node.args[1:2] if (isinstance(fn, ast.Name)) else node.args[:1]
            if dynamic and args and isinstance(args[0], ast.Constant) \
                    and args[0].value in DYNAMIC:
                found.append(f"{relpath}:{node.lineno} : accès dynamique à "
                             f"`{args[0].value}` — interdit")
    return found


def package_files() -> Iterable[Path]:
    for path in sorted(PACKAGE.rglob("*.py")):
        if "static" not in path.parts and "__pycache__" not in path.parts:
            yield path


def scan() -> List[str]:
    out: List[str] = []
    for path in package_files():
        rel = path.relative_to(ROOT).as_posix()
        out.extend(scan_source(rel, path.read_text(encoding="utf-8")))
    return out


def stale_allowances() -> List[str]:
    """Portées autorisées qui n'accèdent plus à rien : une liste blanche qui ne
    se resserre jamais finit par autoriser autre chose."""
    seen = set()
    for path in package_files():
        rel = path.relative_to(ROOT).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in DYNAMIC:
                seen.add(f"{rel}:{_scope(node, parents)[0]}")
    return sorted((INVOKE_ALLOWED | SUBAGENTS_ALLOWED) - seen)


def main(argv=None) -> int:
    problems = scan()
    stale = stale_allowances()
    for line in problems:
        print(f"✗ {line}")
    for scope in stale:
        print(f"✗ portée autorisée sans accès : {scope} — la retirer")
    if problems or stale:
        print(f"\n{len(problems)} contournement(s) possible(s), "
              f"{len(stale)} autorisation(s) périmée(s)")
        return 1
    print(f"✓ seul le noyau atteint l'hôte : {sum(1 for _ in package_files())} "
          f"fichiers examinés")
    return 0


if __name__ == "__main__":
    sys.exit(main())

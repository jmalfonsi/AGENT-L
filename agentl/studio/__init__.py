"""AGENT-L Studio — interface web de visualisation et d'édition en direct.

Usage en ligne de commande::

    agentl studio examples/soc_analyst.agent          # http://127.0.0.1:8765
    agentl studio --port 9000 --open                  # ouvre le navigateur
    agentl studio --no-save --root examples           # lecture seule, racine

Usage programmatique::

    from agentl.studio import create_app, serve

    app = create_app("examples/soc_analyst.agent", root=".", allow_save=False)
    # …ou directement :
    serve("examples/soc_analyst.agent", port=8765)

`fastapi` et `uvicorn` ne sont importés qu'au premier accès à `create_app` ou
`serve` : importer `agentl` (ou sa CLI) n'exige aucune dépendance web.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = ["create_app", "serve"]

if TYPE_CHECKING:                                   # pragma: no cover
    from .server import create_app, serve


def __getattr__(name: str) -> Any:
    """Import paresseux de `server` (PEP 562) — garde la CLI sans FastAPI."""
    if name in __all__:
        from . import server
        return getattr(server, name)
    raise AttributeError(f"module {__name__!r} n'a pas d'attribut {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))

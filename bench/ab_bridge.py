"""Pont AutomationBench ↔ AGENT-L.

Le harness Zapier est bâti sur `verifiers` : la boucle agentique lui
appartient et il n'existe aucun point d'insertion pour un agent tiers. On
n'en réutilise donc que ce qui doit rester strictement identique pour que la
comparaison ait un sens — le `WorldState`, les implémentations d'outils et
les assertions du rubric — et on pilote nous-mêmes l'exécution.

Rien ici n'est spécifique à une tâche : l'hôte est construit par
introspection des signatures d'outils déclarées par la tâche. Aucun jugement
métier ne descend dans l'hôte ; il vit dans le `.agent`.
"""
from __future__ import annotations

import functools
import inspect
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List

AGENTL_ROOT = Path(__file__).resolve().parents[1]
AB_ROOT = Path(os.environ.get(
    "AUTOMATIONBENCH_ROOT",
    AGENTL_ROOT.parent / "AutomationBench",
)).expanduser().resolve()
sys.path.insert(0, str(AB_ROOT))

# La version de Python du venv d'AutomationBench n'est pas figée : la coder en
# dur rendait le pont muet dès la première mise à jour de l'environnement. Mais
# accepter *n'importe quelle* version est pire, et ça s'est vu : le venv est
# passé en 3.13 pendant que la plateforme tournait sous le python système 3.12,
# le pont a chargé un site-packages 3.13, et l'échec est ressorti vingt cadres
# plus loin en `ModuleNotFoundError: numpy._core._multiarray_umath` — une
# extension C `cpython-313` ne se charge pas dans 3.12. Le diagnostic évident
# à ce stade — « numpy est corrompu » — est faux, et coûte une réinstallation
# qui ne répare rien.
_TAG = f"python{sys.version_info.major}.{sys.version_info.minor}"
_site = AB_ROOT / ".venv/lib" / _TAG / "site-packages"
if not _site.is_dir():
    _presents = sorted(p.parent.name
                       for p in AB_ROOT.glob(".venv/lib/python*/site-packages"))
    raise RuntimeError(
        f"venv AutomationBench incompatible : cet interpréteur est {_TAG}, "
        f"le venv fournit {', '.join(_presents) or 'aucun site-packages'}. "
        f"Lancer avec {AB_ROOT}/.venv/bin/python plutôt que le python système."
    )
sys.path.insert(0, str(_site))

from automationbench.rubric import partial_credit, task_completed_correctly  # noqa: E402
from automationbench.runner import compute_allowed_services, strip_none_values  # noqa: E402
from automationbench.schema.world import WorldState  # noqa: E402
from automationbench.tools import ALL_TOOLS  # noqa: E402

sys.path.insert(0, str(AGENTL_ROOT))
from agentl import Host, Symbol  # noqa: E402

TOOLS_BY_NAME: Dict[str, Callable] = {f.__name__: f for f in ALL_TOOLS}


# --------------------------------------------------------------- chargement
def load_task(domain: str, name: str) -> dict:
    """Charge une tâche par son champ `task` (ex. support.zendesk_sf_case_sync)."""
    module = __import__(f"automationbench.domains.{domain}.tasks", fromlist=["*"])
    for attr in dir(module):
        if not attr.startswith("get_"):
            continue
        fn = getattr(module, attr)
        if not callable(fn) or inspect.signature(fn).parameters:
            continue
        try:
            task = fn()
        except Exception:  # noqa: BLE001 — certaines fabriques exigent des arguments
            continue
        if isinstance(task, dict) and task.get("task") == name:
            return task
    raise KeyError(f"tâche introuvable : {domain}/{name}")


def trigger_text(task: dict) -> str:
    """Le message déclencheur, seule entrée en langue naturelle de la tâche."""
    return "\n\n".join(m["content"] for m in task["prompt"] if m["role"] == "user")


# ------------------------------------------------------------------- monde
def build_world(info: dict) -> tuple[WorldState, dict]:
    initial = strip_none_values(info.get("initial_state", {}))
    world = WorldState(**initial)
    world.meta.allowed_services = compute_allowed_services(
        initial, info.get("assertions", []), info.get("zapier_tools", [])
    )
    return world, initial


# -------------------------------------------------------------------- hôte
def _decode(raw: Any) -> Any:
    """Les outils du banc renvoient des chaînes JSON ; AGENT-L raisonne sur
    des valeurs. On décode, sans jamais interpréter : un objet devient un
    dict (ses clés deviennent des locales du runtime), tout le reste est
    laissé tel quel sous `raw`."""
    if not isinstance(raw, str):
        return raw
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return {"raw": raw}
    if isinstance(value, dict):
        return value
    return {"raw": value}


def email_sender(message: dict[str, Any]) -> str:
    """Adresse expéditeur normalisée pour les sorties Gmail AutomationBench.

    L'état du monde emploie ``from_`` (nom d'attribut Python), tandis que les
    outils Gmail sérialisent ce champ sous ``from``. Accepter les deux formes à
    cette frontière empêche une perte silencieuse de provenance dans les hôtes
    de tâche.
    """
    return str(message.get("from_") or message.get("from") or "")


def _bind(fn: Callable, world: WorldState, log: List[dict]) -> Callable:
    takes_world = "world" in inspect.signature(fn).parameters

    @functools.wraps(fn)
    def call(**kwargs):
        args = dict(kwargs)
        # Un Symbol AGENT-L doit redevenir une chaîne pour l'API du banc.
        for key, value in list(args.items()):
            if isinstance(value, Symbol):
                args[key] = str(value)
        if takes_world:
            args["world"] = world
        out = fn(**args)
        log.append({"tool": fn.__name__, "args": {k: v for k, v in args.items() if k != "world"}})
        return _decode(out)

    return call


def make_host(info: dict, world: WorldState, log: List[dict] | None = None) -> Host:
    """Hôte purement mécanique : un outil AGENT-L par outil autorisé.

    Les noms de paramètres sont ceux des signatures Python du banc — le
    runtime appelant toujours par mot-clé, le contrat `INPUT` du `.agent`
    doit les reprendre à l'identique. C'est ce qui permet de générer les
    contrats sans les deviner.
    """
    host = Host()
    log = log if log is not None else []
    host.call_log = log  # type: ignore[attr-defined]
    for name in info.get("zapier_tools", []):
        fn = TOOLS_BY_NAME.get(name)
        if fn is None:
            raise KeyError(f"outil inconnu du banc : {name}")
        host.tools[name] = _bind(fn, world, log)
    # Aucun approbateur : fail-closed, comme partout dans AGENT-L.
    return host


def tool_contracts(info: dict) -> List[dict]:
    """Signatures typées des outils de la tâche, pour le compilateur."""
    out = []
    for name in info.get("zapier_tools", []):
        fn = TOOLS_BY_NAME[name]
        sig = inspect.signature(fn)
        params = []
        for pname, p in sig.parameters.items():
            if pname == "world":
                continue
            ann = p.annotation
            label = getattr(ann, "__name__", None) or str(ann).replace("typing.", "")
            params.append({"name": pname, "type": label, "required": p.default is inspect.Parameter.empty})
        out.append({"name": name, "doc": inspect.getdoc(fn) or "", "params": params})
    return out


# ------------------------------------------------------------------- score
def probe_result_keys(info: dict) -> Dict[str, List[str]]:
    """Clés de premier niveau réellement renvoyées par chaque outil.

    Deviner le nom de la clé qui porte une collection (`rows` ? `results` ?
    `searchResults` ?) est la première cause d'échec du programme généré, et
    ce n'est pas un jugement : c'est un fait observable. On l'observe donc,
    sur une **copie jetable** du monde, avant toute exécution réelle. Les
    outils qui exigent des arguments ne sont pas sondés — on préfère ne rien
    dire que d'inventer un appel.
    """
    import copy

    keys: Dict[str, List[str]] = {}
    for name in info.get("zapier_tools", []):
        fn = TOOLS_BY_NAME[name]
        sig = inspect.signature(fn)
        required = [n for n, p in sig.parameters.items()
                    if n != "world" and p.default is inspect.Parameter.empty]
        if required:
            continue
        probe, _ = build_world(info)
        try:
            out = _decode(fn(world=copy.deepcopy(probe)))
        except Exception:                             # noqa: BLE001
            continue
        if isinstance(out, dict) and list(out) != ["error"]:
            keys[name] = list(out)
    return keys


def score(info: dict, world: WorldState, initial: dict) -> dict:
    """Scoring officiel : on appelle les fonctions du rubric telles quelles."""
    state = {"info": info, "world": world, "initial_state": initial}
    pc = partial_credit(state)
    return {
        "partial_credit": pc,
        "task_completed": task_completed_correctly(state),
        "assertions": state.get("_assertion_results", []),
    }

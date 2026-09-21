"""Moteur de session du Studio — un run AGENT-L observable et pilotable.

Ce module rend un programme `.agent` **observable en direct** (trace, phases,
état, graphe) et **pilotable** (pause, pas à pas, arrêt, approbations
humaines) sans modifier d'une ligne `agentl/runtime.py`. Toute
l'instrumentation est posée sur l'*instance* de `Runtime` :

* `runtime.trace` est remplacé par une sous-classe de `Trace` qui, en plus de
  journaliser, émet un message `trace` ;
* les méthodes `phase_*` et `tick` de l'instance sont enveloppées (jamais
  celles de la classe : deux sessions simultanées ne doivent pas se
  contaminer) ;
* les méthodes `read` / `invoke` / `ask` / `approve` de l'hôte et les méthodes
  du LLM sont enveloppées de la même façon.

Le run vit dans un **thread** dédié ; le serveur (M2), lui, est asyncio. La
frontière entre les deux est une `queue.Queue` par abonné : `_emit()` ne
bloque jamais, `drain()` ne bloque jamais. Aucun objet du runtime ne traverse
cette frontière — seulement des dictionnaires JSON déjà sérialisables.

⚠️ **Sécurité — un module hôte est du code Python arbitraire.** Charger un
hôte, c'est exécuter son contenu dans le processus du studio, avec tous les
droits de celui-ci (réseau, disque, sous-processus). La session n'accepte donc
qu'un chemin situé sous la racine `root` passée au constructeur, refuse toute
traversée (`..`, lien symbolique sortant, chemin absolu extérieur), et
applique la même règle aux fichiers `.agent`. Cette barrière limite la
*surface*, elle ne rend pas l'exécution sûre : n'exposez jamais le studio à un
réseau non maîtrisé.
"""
from __future__ import annotations

import os
import queue
import threading
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Dict, Iterator, List, Optional

from ..analyzer import Analyzer
from ..core import AgentLError, Symbol
from ..host import Host
from ..llm import MockLLM
from ..parser import parse_source
from ..runtime import Runtime, Trace
from ..viz import build_program
from . import events as E

#: Taille maximale d'une file d'abonné. Au-delà, on abandonne les messages les
#: plus anciens : un client déconnecté ne doit ni bloquer le run ni faire
#: enfler la mémoire indéfiniment.
QUEUE_LIMIT = 8192

#: Délai par défaut d'une question posée à l'opérateur (secondes). Passé ce
#: délai, la réponse vaut *refus* — cohérent avec `Host.approve`, fail-closed.
DEFAULT_PROMPT_TIMEOUT = 300.0

#: Granularité des attentes bloquantes : borne le temps de réaction à `stop()`.
_WAIT_SLICE = 0.05

#: Au-delà de ce seuil, un appel de frontière (hôte, LLM) est jugé digne
#: d'être tracé pour lui-même — sinon le runtime le trace déjà mieux que nous.
DEFAULT_SLOW_MS = 250.0


class StaleRevision(AgentLError):
    """Édition fondée sur une révision périmée (M2 en fait un HTTP 409)."""


class _StopRun(BaseException):
    """Signal interne d'arrêt. Dérive de `BaseException` pour traverser les
    `except Exception` défensifs du runtime sans être confondu avec une panne."""


@dataclass
class _Pending:
    """Question en attente de réponse humaine."""

    mode: str
    event: threading.Event
    value: Any = None
    answered: bool = False


class _StudioTrace(Trace):
    """`Trace` qui, en plus de journaliser, notifie la session.

    On sous-classe plutôt qu'on ne patche `log` afin que `render()`,
    `of_kind()` et `trace_html` continuent de fonctionner à l'identique.
    """

    def __init__(self, sink: Callable[[int, str, str, str], None]) -> None:
        super().__init__()
        self._sink = sink

    def log(self, tick: int, kind: str, text: str, detail: str = "") -> None:
        super().log(tick, kind, text, detail)
        self._sink(tick, kind, text, detail)


class _NodeIndex:
    """Table `kind de trace + texte → nodeId`, alignée sur `agentl.viz`.

    L'identité de nœud doit être *exactement* celle du graphe rendu, sinon
    l'illumination du canevas (M4) désigne des nœuds inexistants. On indexe
    donc les clés réellement déclarées, puis on cherche laquelle apparaît dans
    le texte de la trace — approche tolérante : un texte non reconnu vaut
    `nodeId = null`, jamais une supposition.
    """

    def __init__(self, agent: Any, multi: bool = False,
                 graph: Optional[Dict[str, Any]] = None) -> None:
        self.agent_name = getattr(agent, "name", "agent")
        self.multi = multi
        # Les nœuds de société portent des clés positionnelles (`msg0`, `in0`)
        # : impossible de les retrouver par nom. On les indexe donc depuis le
        # graphe lui-même, seule source qui associe un nom de message à son
        # nœud — et qui reste juste si `viz.py` change sa numérotation.
        self.sent: Dict[str, str] = {}
        self.received: Dict[str, str] = {}
        for node in (graph or {}).get("nodes", []):
            if node.get("agent") not in (None, self.agent_name):
                continue
            name = (node.get("detail") or {}).get("message")
            if not name:
                continue
            if node.get("kind") == "message":
                self.sent.setdefault(str(name), node["id"])
            elif node.get("kind") == "inbox":
                self.received.setdefault(str(name), node["id"])
        self.keys: Dict[str, List[str]] = {
            "observe": [o.path for o in getattr(agent, "observers", [])],
            "hypothesis": [h.name for h in getattr(agent, "hypotheses", [])],
            "goal": [g.name for g in getattr(agent, "goals", [])],
            "plan": [p.name for p in getattr(agent, "plans", [])],
            "tool": [t.name for t in getattr(agent, "tools", [])],
        }
        # Clé la plus longue d'abord : `wazuh.alert_count` avant `wazuh`.
        for lane in self.keys:
            self.keys[lane] = sorted(set(self.keys[lane]), key=len, reverse=True)

    #: Voies de repli, essayées dans l'ordre quand la voie principale échoue.
    #: Une croyance *dérivée* (`sandbox.cause = junk_accumulation`) nomme une
    #: hypothèse, pas un observateur : sans ce repli, elle n'illumine rien.
    FALLBACK: Dict[str, List[str]] = {
        "observe": ["hypothesis"],
        "hypothesis": ["observe"],
        "plan": ["tool"],
    }

    def resolve(self, kind: str, text: str) -> Optional[str]:
        if kind == "MESSAGE":
            return self._resolve_message(text)
        lane = E.KIND_TO_LANE.get(kind)
        if not lane or not text:
            return None
        for candidate in [lane, *self.FALLBACK.get(lane, [])]:
            for key in self.keys.get(candidate, []):
                if _mentions(text, key):
                    return E.node_id(self.agent_name, candidate, key,
                                     multi=self.multi)
        return None


    def _resolve_message(self, text: str) -> Optional[str]:
        """`check_host → network_agent` (émission) vs `check_host ← soc_analyst`
        (réception) : la flèche dit de quel côté du bus on se trouve."""
        if not text:
            return None
        name = text.split()[0]
        table = self.received if "←" in text else self.sent
        return table.get(name) or self.sent.get(name) or self.received.get(name)


def _mentions(text: str, key: str) -> bool:
    """`key` apparaît-il comme jeton entier dans `text` ?

    Un test de sous-chaîne nu confondrait l'outil `isolate` avec
    `isolate_endpoint`. On exige donc des bornes non identificatrices.
    """
    start = 0
    while True:
        idx = text.find(key, start)
        if idx < 0:
            return False
        before = text[idx - 1] if idx else " "
        after = text[idx + len(key)] if idx + len(key) < len(text) else " "
        if not _ident_char(before) and not _ident_char(after):
            return True
        start = idx + 1


def _ident_char(char: str) -> bool:
    return char.isalnum() or char in "_."


class StudioSession:
    """Une session d'édition + d'exécution d'un programme AGENT-L.

    Cycle de vie typique ::

        s = StudioSession(root=Path.cwd())
        s.open("examples/soc_analyst.agent")
        q = s.subscribe()
        s.run(ticks=3)          # hôte imposé : examples/soc_analyst.py
        for msg in s.drain(q):
            ...

    Toutes les méthodes publiques sont sûres à appeler depuis le thread du
    serveur pendant qu'un run tourne dans le thread d'exécution.
    """

    # ------------------------------------------------------------ construction
    def __init__(self, root: Path, file: Optional[str | os.PathLike] = None, *,
                 prompt_timeout: float = DEFAULT_PROMPT_TIMEOUT,
                 slow_ms: float = DEFAULT_SLOW_MS,
                 on_event: Optional[Callable[[Dict[str, Any]], None]] = None
                 ) -> None:
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise AgentLError(f"racine de travail introuvable : {self.root}")
        self.prompt_timeout = float(prompt_timeout)
        self.slow_ms = float(slow_ms)
        self.on_event = on_event

        self.file: Optional[Path] = None
        self.host_path: Optional[Path] = None
        self.source: str = ""
        self.rev: int = 0
        self.program: Optional[Any] = None
        self.agent: Optional[Any] = None
        self.graph: Dict[str, Any] = _empty_graph()
        self.diags: List[Dict[str, Any]] = []

        self._lock = threading.RLock()
        self._subs: List[queue.Queue] = []
        self._emitting = False

        self._thread: Optional[threading.Thread] = None
        # Un run est « en cours » de son acceptation par `run()` jusqu'à ce
        # qu'il annonce sa fin — pas tant que son thread vit. `is_alive()`
        # était faux entre la création et `start()` (deux runs pouvaient
        # passer) et encore vrai après `run.finished` (une relance immédiate
        # était refusée). Lu et écrit sous `_lock`.
        self._active = False
        self._runtime: Optional[Runtime] = None
        self._run_id: Optional[str] = None
        self._run_count = 0
        self._seq = 0
        self._phase_depth = 0
        self._step_mode = False
        self._paused = False
        self._pace = 0.0            # mode démo : secondes entre deux événements
        self._active_plans: Dict[str, str] = {}   # plan en cours, par agent
        self._society: Optional[Any] = None       # société courante, le cas échéant
        self._indexes: Dict[str, _NodeIndex] = {}  # index de nœuds, par agent
        self._stop = threading.Event()
        self._resume = threading.Event()
        self._resume.set()
        self._prompts: Dict[str, _Pending] = {}
        self._prompt_count = 0
        self._index: Optional[_NodeIndex] = None

        if file is not None:
            self.open(file)

    # ------------------------------------------------------------- chemins sûrs
    def resolve_path(self, candidate: str | os.PathLike) -> Path:
        """Chemin absolu garanti **sous** `root`.

        Refuse la traversée (`..`), les chemins absolus extérieurs et les liens
        symboliques qui sortent de la racine — `Path.resolve()` déréférence,
        `relative_to()` tranche.
        """
        raw = Path(candidate)
        target = (raw if raw.is_absolute() else self.root / raw).resolve()
        try:
            target.relative_to(self.root)
        except ValueError:
            raise AgentLError(
                f"chemin hors du répertoire de travail autorisé : {candidate}")
        return target

    def relpath(self, path: Optional[Path]) -> Optional[str]:
        """Chemin relatif à la racine, pour l'affichage côté client."""
        if path is None:
            return None
        try:
            return str(path.relative_to(self.root))
        except ValueError:                             # pragma: no cover
            return str(path)

    # ------------------------------------------------------- ouverture/édition
    def open(self, path: str | os.PathLike) -> Dict[str, Any]:
        """Charge un `.agent` depuis le disque et réanalyse tout."""
        target = self.resolve_path(path)
        try:
            text = target.read_text(encoding="utf-8")
        except OSError as exc:
            raise AgentLError(f"fichier illisible : {exc}") from exc
        with self._lock:
            if self.running:
                raise AgentLError("un run est en cours : arrêtez-le avant "
                                  "d'ouvrir un autre fichier")
            self.file = target
            self.source = text
            self.rev += 1
            self._reanalyze()
            return self.snapshot()

    def set_source(self, text: str, rev: Optional[int] = None) -> Dict[str, Any]:
        """Remplace intégralement le source (édition côté client).

        `rev` est la révision sur laquelle le client a fondé son édition : si
        elle est périmée, on refuse par `StaleRevision` plutôt que d'écraser le
        travail d'un autre onglet.
        """
        if not isinstance(text, str):
            raise AgentLError("source attendu sous forme de texte")
        with self._lock:
            if rev is not None and int(rev) != self.rev:
                raise StaleRevision(
                    f"révision périmée : {rev} reçue, {self.rev} courante")
            self.source = text
            self.rev += 1
            self._reanalyze()
            result = {"rev": self.rev, "graph": self.graph, "diags": self.diags}
        self._emit(E.Source(text=text, rev=result["rev"]))
        self._emit(E.Diagnostics(diags=result["diags"]))
        self._emit(E.Graph(graph=result["graph"]))
        return result

    def save(self, path: Optional[str | os.PathLike] = None) -> Path:
        """Écrit le source courant sur disque (seule écriture de la session)."""
        with self._lock:
            target = self.resolve_path(path) if path is not None else self.file
            if target is None:
                raise AgentLError("aucun fichier associé à la session")
            target = self.resolve_path(target)
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(self.source, encoding="utf-8")
            except OSError as exc:
                raise AgentLError(f"écriture impossible : {exc}") from exc
            self.file = target
            return target

    def check(self, text: Optional[str] = None) -> List[Dict[str, Any]]:
        """Diagnostics d'un source arbitraire, sans toucher à la session."""
        if text is None:
            with self._lock:
                return list(self.diags)
        _program, _agent, diags, _graph = self._analyze(text)
        return diags

    # --------------------------------------------------------------- analyse
    def _reanalyze(self) -> None:
        """Re-parse, re-analyse et reconstruit le graphe. Ne lève jamais.

        Un source fautif produit des diagnostics ; le **dernier graphe valide**
        est conservé, sinon le canevas clignoterait à chaque frappe.
        """
        program, agent, diags, graph = self._analyze(self.source)
        self.program, self.agent, self.diags = program, agent, diags
        if graph is not None:
            self.graph = graph
        if agent is not None:
            multi = bool(program and len(program.agents) > 1)
            self._index = _NodeIndex(agent, multi=multi,
                                     graph=self.graph)

    def _analyze(self, text: str):
        """(program, agent, diags, graph|None) — toute erreur devient diagnostic."""
        name = str(self.file) if self.file else "<source>"
        try:
            program = parse_source(text, name)
        except AgentLError as exc:
            return None, None, [E.diagnostic("P001", "error", str(exc),
                                             _line_of(exc))], None
        except Exception as exc:                       # noqa: BLE001
            return None, None, [E.diagnostic(
                "P000", "error",
                f"analyse impossible ({type(exc).__name__}: {exc})")], None
        if not program.agents:
            return program, None, [E.diagnostic(
                "P002", "error", "aucun AGENT déclaré dans ce fichier")], None

        diags: List[Dict[str, Any]] = []
        try:
            for candidate in program.agents:
                for d in Analyzer(candidate).run():
                    diags.append(E.diagnostic(d.code, d.severity, d.message,
                                              d.line))
            if len(program.agents) > 1:
                from ..analyzer import check_program
                for d in check_program(program):
                    diags.append(E.diagnostic(d.code, d.severity, d.message,
                                              d.line))
        except Exception as exc:                       # noqa: BLE001
            diags.append(E.diagnostic(
                "A000", "error",
                f"analyse statique interrompue ({type(exc).__name__}: {exc})"))

        try:
            graph = E.to_jsonable(build_program(program))
        except Exception as exc:                       # noqa: BLE001
            graph = None
            diags.append(E.diagnostic(
                "G000", "warning",
                f"graphe non reconstruit ({type(exc).__name__}: {exc})"))
        return program, program.agents[0], diags, graph

    # ------------------------------------------------------------- abonnements
    def subscribe(self) -> queue.Queue:
        """Nouvelle file d'événements. Le serveur en crée une par connexion."""
        q: queue.Queue = queue.Queue(maxsize=QUEUE_LIMIT)
        with self._lock:
            self._subs.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subs:
                self._subs.remove(q)

    @staticmethod
    def drain(q: queue.Queue, limit: int = 512) -> Iterator[Dict[str, Any]]:
        """Vide une file **sans jamais bloquer** (appelable depuis asyncio)."""
        for _ in range(max(1, limit)):
            try:
                yield q.get_nowait()
            except queue.Empty:
                return

    def _emit(self, message: Any) -> None:
        """Diffuse un message (dataclass §2 ou dict) à tous les abonnés."""
        payload = message.to_dict() if hasattr(message, "to_dict") else \
            E.to_jsonable(message)
        with self._lock:
            subs = list(self._subs)
            callback = self.on_event
            reentrant = self._emitting
            self._emitting = True
        try:
            for q in subs:
                try:
                    q.put_nowait(payload)
                except queue.Full:
                    # Abonné en retard : on sacrifie le plus ancien message,
                    # jamais le run en cours.
                    try:
                        q.get_nowait()
                        q.put_nowait(payload)
                    except (queue.Empty, queue.Full):  # pragma: no cover
                        pass
            if callback is not None and not reentrant:
                try:
                    callback(payload)
                except Exception as exc:               # noqa: BLE001
                    self._emit(E.ErrorMsg(
                        message=f"rappel d'événement en erreur : "
                                f"{type(exc).__name__}: {exc}"))
        finally:
            with self._lock:
                self._emitting = reentrant

    def _next_seq(self) -> int:
        with self._lock:
            self._seq += 1
            return self._seq

    # --------------------------------------------------------------- snapshots
    @property
    def running(self) -> bool:
        return self._active

    @property
    def paused(self) -> bool:
        return self._paused

    def snapshot(self) -> Dict[str, Any]:
        """État complet de la session (`/api/session`)."""
        # Lecture disque hors verrou : la vérification d'hôte ouvre un fichier.
        status = self.host_status()
        with self._lock:
            return {
                "file": self.relpath(self.file),
                "source": self.source,
                "rev": self.rev,
                "graph": self.graph,
                "diags": list(self.diags),
                "host": status["path"],
                "running": self.running,
                "paused": self._paused,
                "pace": self._pace,
                "hostStatus": status,
                "runId": self._run_id,
            }

    def hello(self) -> Dict[str, Any]:
        """Message `hello` du contrat (§2)."""
        status = self.host_status()
        with self._lock:
            session = {"file": self.relpath(self.file),
                       "host": status["path"],
                       "hostStatus": status,
                       "rev": self.rev, "running": self.running}
        return E.Hello(session=session).to_dict()

    def host_path_for(self, file: Optional[Path] = None) -> Optional[Path]:
        """Chemin d'hôte **imposé** par la norme de nommage.

        Un programme `X.agent` est servi par `X.py`, dans le même répertoire.
        Rien d'autre n'est accepté : plus de choix d'hôte, donc plus de run
        exécuté avec l'hôte d'un autre agent — une erreur silencieuse dont on
        ne s'aperçoit qu'en lisant des perceptions incohérentes.
        """
        target = file or self.file
        return target.with_suffix(".py") if target is not None else None

    def host_status(self) -> Dict[str, Any]:
        """État de l'hôte normatif : présent ? conforme au contrat ?

        Le nom décide de l'exécution ; la lecture des capteurs et des outils
        **confirme** que le fichier trouvé sert bien ce programme. Cette
        lecture est syntaxique (`ast`), jamais un import : proposer ou vérifier
        un hôte ne doit pas exécuter du code arbitraire.

        La couverture est indicative, pas bloquante : un hôte peut enregistrer
        ses outils dynamiquement (`h.tools[fn.__name__] = fn`), et une lecture
        statique ne peut pas le voir.
        """
        path = self.host_path_for()
        out: Dict[str, Any] = {
            "path": self.relpath(path), "exists": False, "confirmed": False,
            "sensors": 0, "tools": 0, "expectedSensors": 0, "expectedTools": 0,
            "missing": [], "message": "",
        }
        if path is None:
            out["message"] = "aucun programme chargé"
            return out
        agents = list(self.program.agents) if self.program else []
        attendus_capteurs = {o.path for a in agents for o in (a.observers or [])}
        attendus_outils = {t.name for a in agents for t in (a.tools or [])}
        out["expectedSensors"] = len(attendus_capteurs)
        out["expectedTools"] = len(attendus_outils)
        if not path.is_file():
            out["message"] = (f"hôte absent : {self.relpath(path)} "
                              f"(la norme impose le même nom que le .agent)")
            return out
        out["exists"] = True
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            out["message"] = f"hôte illisible : {exc}"
            return out
        capteurs, outils = _declared_bindings(source)
        couverts = capteurs & attendus_capteurs
        out["sensors"] = len(couverts)
        out["tools"] = len(outils & attendus_outils)
        out["missing"] = sorted(attendus_capteurs - capteurs)[:8]
        out["confirmed"] = bool(couverts or (outils & attendus_outils))
        if out["confirmed"]:
            out["message"] = (f"{out['sensors']}/{out['expectedSensors']} capteurs "
                              f"déclarés par {self.relpath(path)}")
        else:
            out["message"] = (f"{self.relpath(path)} ne déclare aucun capteur ni "
                              "outil de ce programme — enregistrement dynamique, "
                              "ou fichier sans rapport")
        return out

    def list_agent_files(self, limit: int = 200) -> List[str]:
        """Fichiers `.agent` découvrables sous la racine (`/api/files`)."""
        found: List[str] = []
        for path in sorted(self.root.rglob("*.agent")):
            if any(part.startswith(".") for part in path.parts):
                continue
            found.append(str(path.relative_to(self.root)))
            if len(found) >= limit:
                break
        return found

    # ------------------------------------------------------------- chargement
    def _load(self):
        """(host, llm) de l'hôte **normatif** `X.py` pour le programme `X.agent`.

        ⚠️ Exécute le module hôte : voir l'avertissement de sécurité du module.
        """
        from ..cli import _resolve_host

        target = self.host_path_for()
        if target is None:
            raise AgentLError("aucun programme chargé")
        target = self.resolve_path(target)
        if not target.is_file():
            raise AgentLError(
                f"hôte introuvable : {self.relpath(target)}. La norme de "
                f"nommage impose que l'hôte porte le nom du programme, à "
                f"l'extension près — pas d'hôte, pas d'exécution.")
        host, llm = _resolve_host(SimpleNamespace(host=str(target)))
        if not _is_host(host) and not isinstance(host, dict):
            raise AgentLError(
                f"l'hôte {self.relpath(target)} ne fournit ni un objet Host ni "
                f"un dictionnaire d'hôtes par agent (reçu "
                f"{type(host).__name__})")
        self.host_path = target
        return host, llm or MockLLM()

    def _society_hosts(self, host: Any, llm: Any) -> tuple[Dict[str, Host],
                                                           Dict[str, Any]]:
        """Normalise ce qu'un hôte de société a renvoyé.

        Deux conventions coexistent dans les exemples, toutes deux légitimes :
        `build()` peut rendre `(hosts, llms)` avec un LLM **par agent**, ou
        `(hosts, llm)` avec un unique LLM partagé par toute la société. On
        accepte les deux plutôt que d'imposer la nôtre.
        """
        agents = list(self.program.agents) if self.program else []
        hosts: Dict[str, Host] = {}
        if isinstance(host, dict):
            for name, value in host.items():
                if not _is_host(value):
                    raise AgentLError(
                        f"l'hôte de l'agent « {name} » n'est pas un Host "
                        f"(reçu {type(value).__name__})")
                hosts[str(name)] = value
        elif _is_host(host):
            hosts = {a.name: host for a in agents}

        llms: Dict[str, Any] = {}
        if isinstance(llm, dict):
            llms = {str(k): v for k, v in llm.items()}
        elif llm is not None:
            llms = {a.name: llm for a in agents}

        inconnus = sorted(set(hosts) - {a.name for a in agents})
        if inconnus:
            raise AgentLError(
                "hôte fourni pour des agents absents du programme : "
                + ", ".join(inconnus))
        return hosts, llms

    def _build_society(self, host: Any, llm: Any):
        """Construit une `Society` instrumentée agent par agent."""
        from ..society import Society

        if not self.program or not self.program.agents:
            raise AgentLError("aucun programme chargé")
        hosts, llms = self._society_hosts(host, llm)
        society = Society(self.program.agents, hosts=hosts, llms=llms,
                          echo=False)
        multi = len(self.program.agents) > 1
        for name, runtime in society.runtimes.items():
            self._instrument(runtime, name, multi=multi)
        # La société journalise aussi pour son propre compte (acheminement des
        # messages, destinataire inconnu) : on écoute cette trace-là également.
        society.trace = _StudioTrace(self._trace_sink("société", multi))
        return society

    def _current_tick(self, runtime: Optional[Runtime],
                      society: Optional[Any]) -> int:
        """Tick courant, quel que soit le mode d'exécution."""
        if runtime is not None:
            return runtime.state.tick
        if society is not None and society.runtimes:
            return max(r.state.tick for r in society.runtimes.values())
        return 0

    # ------------------------------------------------------------------- run
    def run(self, ticks: Optional[int] = None, step_mode: bool = False,
            pace: float = 0.0) -> str:
        """Lance un run dans un thread dédié. Retourne l'identifiant de run.

        Refuse d'exécuter un programme qui ne passe pas l'analyse statique —
        même règle que la CLI : un programme fautif n'est pas exécutable.

        `pace` est le **mode démonstration** : un délai, en secondes, observé
        après chaque événement émis. À 0 le run va aussi vite que le runtime ;
        au-delà, la chaîne perception → inférence → décision → action se
        déroule à une vitesse où l'œil suit l'illumination des nœuds. Le délai
        est modifiable en cours de run (`set_pace`) et interrompu par `stop()`.
        """
        with self._lock:
            if self.running:
                raise AgentLError("un run est déjà en cours")
            if self.agent is None:
                raise AgentLError("aucun programme chargé")
            blocking = [d for d in self.diags if d["severity"] == "error"]
            if blocking:
                raise AgentLError(
                    "exécution refusée : le programme ne passe pas l'analyse "
                    "statique (" + "; ".join(d["message"] for d in blocking[:3])
                    + ")")
            host, llm = self._load()
            # Remise à zéro AVANT instrumentation : `_instrument` peuple
            # `_indexes`, un nettoyage postérieur l'effacerait.
            self._active_plans.clear()
            self._indexes.clear()
            # Société dès que le programme déclare plusieurs agents : ils
            # s'échangent des messages et partagent une mémoire, les exécuter
            # séparément ne montrerait rien de ce qui les lie.
            multi = bool(self.program and len(self.program.agents) > 1)
            if multi or isinstance(host, dict):
                society = self._build_society(host, llm)
                runtime = None
            else:
                society = None
                runtime = Runtime(self.agent, host, llm, echo=False)
                self._instrument(runtime, self.agent.name, multi=False)
            self._society = society
            self._runtime = runtime
            self._run_count += 1
            self._run_id = f"r{self._run_count}"
            self._seq = 0
            self._phase_depth = 0
            self._step_mode = bool(step_mode)
            self._paused = False
            self._pace = _clamp_pace(pace)
            self._prompts.clear()
            self._stop.clear()
            if step_mode:
                self._resume.clear()
            else:
                self._resume.set()
            limit = _tick_limit(self.agent, ticks)
            run_id = self._run_id
            self._thread = threading.Thread(
                target=self._run_body, args=(runtime, society, limit),
                name=f"studio-run-{run_id}", daemon=True)
            self._active = True
        names = ([a.name for a in self.program.agents]
                 if society is not None and self.program else [self.agent.name])
        self._emit(E.RunStarted(runId=run_id, agent=" · ".join(names),
                                maxTicks=limit))
        try:
            self._thread.start()
        except BaseException:
            with self._lock:
                self._active = False
            raise
        return run_id

    def _run_body(self, runtime: Optional[Runtime], society: Optional[Any],
                  limit: Optional[int]) -> None:
        """Corps du thread d'exécution : jamais de traceback nu vers le client."""
        run_id = self._run_id or "r?"
        status, error = "done", None
        try:
            if society is not None:
                society.run(max_ticks=limit if limit is not None else 6)
            else:
                runtime.run(max_ticks=limit)
        except _StopRun:
            status = "stopped"
        except AgentLError as exc:
            status, error = "error", str(exc)
        except BaseException as exc:                   # noqa: BLE001
            status = "error"
            error = f"{type(exc).__name__}: {exc}"
            detail = traceback.format_exc(limit=6)
            self._emit(E.TraceMsg(runId=run_id, seq=self._next_seq(),
                                  tick=self._current_tick(runtime, society),
                                  kind="ERROR",
                                  text="run interrompu par une erreur interne",
                                  detail=detail))
        finally:
            self._release_prompts()
            self._paused = False
            metrics = dict(society.metrics if society is not None
                           else runtime.metrics)
            # Libéré avant l'annonce : un client qui relance dès
            # `run.finished` ne doit pas être refusé. Hors verrou pour
            # l'émission — `_emit` appelle le rappel du serveur.
            with self._lock:
                self._active = False
            self._emit(E.RunFinished(runId=run_id, status=status,
                                     metrics=metrics, error=error))

    # ----------------------------------------------------------- pilotage
    def pause(self) -> None:
        """Suspend le run au prochain point d'arrêt (tick ou phase)."""
        self._resume.clear()

    def resume(self) -> None:
        """Reprend un run suspendu et quitte le mode pas à pas."""
        self._step_mode = False
        self._resume.set()

    def step(self) -> None:
        """Autorise **un** point d'arrêt de plus, puis re-suspend."""
        self._step_mode = True
        self._resume.set()

    def set_pace(self, pace: float) -> float:
        """Règle le mode démonstration, y compris pendant un run.

        Retourne la valeur effectivement retenue (bornée).
        """
        self._pace = _clamp_pace(pace)
        return self._pace

    @property
    def pace(self) -> float:
        return self._pace

    def _beat(self) -> None:
        """Respiration du mode démonstration, entre deux événements.

        L'attente porte sur l'événement d'arrêt : `stop()` reste immédiat
        quelle que soit la lenteur demandée.
        """
        delay = self._pace
        if delay > 0:
            self._stop.wait(delay)

    def stop(self, timeout: float = 1.0) -> bool:
        """Demande l'arrêt et attend la fin du thread (< 1 s par défaut).

        Débloque au passage toute question humaine en attente : un run arrêté
        ne doit pas rester suspendu sur une approbation que personne ne donnera.
        """
        self._stop.set()
        self._resume.set()
        self._release_prompts()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout)
            return not thread.is_alive()
        return True

    def close(self) -> None:
        """Arrête le run et libère les abonnés."""
        self.stop()
        with self._lock:
            self._subs.clear()

    # ------------------------------------------------------------------ HITL
    def reply(self, prompt_id: str, value: Any) -> bool:
        """Répond à une question `prompt`. `False` si elle n'existe plus."""
        with self._lock:
            pending = self._prompts.get(prompt_id)
        if pending is None:
            return False
        pending.value = value
        pending.answered = True
        pending.event.set()
        return True

    def _release_prompts(self) -> None:
        with self._lock:
            pending = list(self._prompts.values())
        for item in pending:
            item.event.set()

    def _prompt(self, mode: str, question: str, reason: str = "",
                node: Optional[str] = None,
                payload: Optional[Dict[str, Any]] = None) -> Any:
        """Émet un `prompt` et bloque le thread de run jusqu'à réponse.

        Sans réponse dans `prompt_timeout` (ou sur `stop()`), on retombe
        *fermé* : refus pour une approbation, `no_answer` pour une question —
        la même convention que `Host.approve` et `Host.ask`.
        """
        run_id = self._run_id or "r?"
        with self._lock:
            self._prompt_count += 1
            prompt_id = f"p{self._prompt_count}"
            pending = _Pending(mode=mode, event=threading.Event())
            self._prompts[prompt_id] = pending
        self._emit(E.Prompt(runId=run_id, promptId=prompt_id, mode=mode,
                            question=question, reason=reason, nodeId=node,
                            payload=E.to_jsonable(payload or {}),
                            timeout=self.prompt_timeout))
        deadline = time.monotonic() + self.prompt_timeout
        try:
            while not pending.event.wait(_WAIT_SLICE):
                if self._stop.is_set() or time.monotonic() >= deadline:
                    break
        finally:
            with self._lock:
                self._prompts.pop(prompt_id, None)
        if not pending.answered:
            self._emit(E.TraceMsg(
                runId=run_id, seq=self._next_seq(),
                tick=self._runtime.state.tick if self._runtime else 0,
                kind="BLOCKED", text=f"{question} — sans réponse",
                detail="délai dépassé ou run arrêté : repli fermé"))
            return False if mode == "approve" else Symbol("no_answer")
        return pending.value

    # ---------------------------------------------------------- instrumentation
    def _instrument(self, runtime: Runtime, agent_name: str,
                    multi: bool) -> None:
        """Pose toute l'observation sur l'*instance* — jamais sur les classes."""
        agent = getattr(runtime, "agent", None)
        if agent is not None:
            self._indexes[agent_name] = _NodeIndex(
                agent, multi=multi, graph=self.graph)
        runtime.trace = _StudioTrace(self._trace_sink(agent_name, multi))

        for name in dir(type(runtime)):
            if not name.startswith("phase_"):
                continue
            bound = getattr(runtime, name, None)
            if not callable(bound):
                continue
            label = name[len("phase_"):].upper()
            setattr(runtime, name, self._wrap_phase(bound, label, runtime))
        runtime.tick = self._wrap_tick(runtime.tick, runtime)

        self._instrument_host(runtime.host)
        self._instrument_llm(runtime.llm, agent_name, multi)

    #: Genres qui n'ont pas de nœud propre mais appartiennent au plan en cours
    #: d'exécution : les rattacher garde le plan allumé pendant ses étapes,
    #: au lieu d'éteindre le canevas entre deux actions.
    _INHERIT_PLAN = frozenset({"STEP", "LLM", "ASK", "ERROR", "RETRY",
                               "VERIFY_OK", "VERIFY_FAIL"})

    def _trace_sink(self, agent_name: str, multi: bool):
        """Récepteur de trace lié à un agent : c'est lui qui l'attribue.

        En société, chaque runtime a son propre index de nœuds et son propre
        plan courant — sans quoi une étape de l'agent A illuminerait le plan
        de l'agent B.
        """
        def sink(tick: int, kind: str, text: str, detail: str) -> None:
            self._on_trace(agent_name, multi, tick, kind, text, detail)
        return sink

    def _on_trace(self, agent_name: str, multi: bool, tick: int, kind: str,
                  text: str, detail: str) -> None:
        index = self._indexes.get(agent_name) or self._index
        node = index.resolve(kind, text) if index else None
        if node and kind in ("PLAN", "PLANNER"):
            self._active_plans[agent_name] = node
        elif node is None and kind in self._INHERIT_PLAN:
            node = self._active_plans.get(agent_name)
        self._emit(E.TraceMsg(runId=self._run_id or "r?", seq=self._next_seq(),
                              tick=tick, kind=kind, text=text, detail=detail,
                              nodeId=node, ts=time.time(),
                              agent=agent_name if multi else None))
        self._beat()

    def _wrap_tick(self, inner: Callable[[], None], runtime: Runtime):
        def tick() -> None:
            self._checkpoint(runtime.state.tick)
            self._emit(E.Tick(runId=self._run_id or "r?",
                              tick=runtime.state.tick + 1,
                              seq=self._next_seq()))
            inner()
            self._emit_state(runtime)
        return tick

    def _wrap_phase(self, inner: Callable[[], None], label: str,
                    runtime: Runtime):
        def phase() -> None:
            # Les phases s'appellent entre elles (`VERIFY` réévalue les buts) :
            # seule la phase de tête est un point d'arrêt et un événement.
            nested = self._phase_depth > 0
            if nested:
                inner()
                return
            self._checkpoint(runtime.state.tick)
            run_id = self._run_id or "r?"
            self._emit(E.Phase(runId=run_id, tick=runtime.state.tick,
                               phase=label, status="enter",
                               seq=self._next_seq()))
            self._beat()
            self._phase_depth += 1
            try:
                inner()
            finally:
                self._phase_depth -= 1
                self._emit(E.Phase(runId=run_id, tick=runtime.state.tick,
                                   phase=label, status="exit",
                                   seq=self._next_seq()))
        return phase

    def _emit_state(self, runtime: Runtime) -> None:
        """Instantané d'état après un tick (§2, message `state`).

        En société, les clés sont préfixées du nom de l'agent : deux agents
        peuvent observer le même chemin sans que leurs valeurs se recouvrent
        dans l'inspecteur, et l'on voit *qui* croit quoi. Les métriques, elles,
        sont celles de la société entière — c'est le total qui a un sens.
        """
        society = self._society
        prefix = ""
        if society is not None and len(society.runtimes) > 1:
            prefix = f"{runtime.agent.name}."
        beliefs = {prefix + path: E.to_jsonable(b.value)
                   for path, b in runtime.state.beliefs.items()}
        hypotheses = {prefix + name: E.to_jsonable(inf.posterior)
                      for name, inf in runtime.inferences.items()}
        goals = {prefix + g.name: E.to_jsonable(
                    runtime.state.world.get(f"goals.{g.name}.score"))
                 for g in runtime.agent.goals}
        metrics = society.metrics if society is not None else runtime.metrics
        self._emit(E.StateMsg(runId=self._run_id or "r?",
                              tick=runtime.state.tick, beliefs=beliefs,
                              hypotheses=hypotheses, goals=goals,
                              metrics=E.to_jsonable(dict(metrics)),
                              seq=self._next_seq()))

    # ------------------------------------------------------ frontières hôte/LLM
    def _instrument_host(self, host: Host) -> None:
        """Enveloppe la frontière du monde. La session **devient** l'opérateur.

        `ask` et `approve` sont détournés vers le studio : c'est lui qui pose
        la question à l'humain, quoi qu'ait prévu le module hôte. `read` et
        `invoke` ne sont tracés que lorsqu'ils apportent une information que le
        runtime ne journalise pas déjà — panne ou lenteur — afin de ne pas
        doubler chaque ligne du journal.
        """
        read, invoke = host.read, host.invoke

        def wrapped_read(path: str) -> Any:
            return self._timed("host.read", path, lambda: read(path))

        def wrapped_invoke(name: str, args: Dict[str, Any]) -> Any:
            return self._timed(f"host.invoke {name}", name,
                               lambda: invoke(name, args))

        def wrapped_ask(question: str, reason: str = "") -> Any:
            return self._prompt("ask", question, reason,
                                payload={"reason": reason})

        def wrapped_approve(request: Any) -> bool:
            tool = getattr(request, "tool", "")
            node = (self._index.resolve("TOOL", tool)
                    if self._index and tool else None)
            question = (f"{request.render()} — approuver ?"
                        if hasattr(request, "render") else str(request))
            reason = f"RISK {getattr(request, 'risk', '?')}"
            payload = {"tool": tool,
                       "args": E.to_jsonable(getattr(request, "args", {})),
                       "risk": getattr(request, "risk", None),
                       "origin": getattr(request, "origin", None),
                       "confidence": getattr(request, "confidence", None)}
            return bool(self._prompt("approve", question, reason, node, payload))

        host.read = wrapped_read
        host.invoke = wrapped_invoke
        host.ask = wrapped_ask
        host.approve = wrapped_approve
        # Cohérence : un hôte qui consulterait directement ses propres crochets
        # doit trouver le studio, pas son approbateur d'origine.
        host.approver = wrapped_approve
        host.asker = wrapped_ask

    def _instrument_llm(self, llm: Any, agent_name: str = "",
                        multi: bool = False) -> None:
        """Même principe pour l'oracle : on mesure, on ne réécrit rien.

        Une nuance : on annonce l'appel **avant** de le faire. Le runtime ne
        journalise le LLM qu'une fois la réponse obtenue ; or c'est pendant
        l'attente — parfois plusieurs secondes sur un modèle distant — que
        l'interface doit montrer que l'agent interroge son oracle.
        """
        reason, select = getattr(llm, "reason", None), \
            getattr(llm, "select_plan", None)
        judge = getattr(llm, "judge", None)

        def announced(label: str, call: Callable[[], Any]):
            self._llm_mark(agent_name, multi, label, started=True)
            try:
                return self._timed(label, None, call)
            finally:
                self._llm_mark(agent_name, multi, label, started=False)

        if callable(reason):
            llm.reason = lambda task, context, produce: announced(
                f"llm.reason « {task} »",
                lambda: reason(task, context, produce))
        if callable(select):
            llm.select_plan = lambda context, candidates: announced(
                "llm.select_plan", lambda: select(context, candidates))
        if callable(judge):
            llm.judge = lambda task, context, questions: announced(
                f"llm.judge « {task} »",
                lambda: judge(task, context, questions))

    def _llm_mark(self, agent_name: str, multi: bool, label: str,
                  *, started: bool) -> None:
        """Encadre un appel à l'oracle par deux traces `LLM`.

        Le `detail` porte le marqueur que le canevas lit pour allumer puis
        éteindre son clignotement — ni nouveau type de message, ni champ hors
        contrat.
        """
        self._on_trace(agent_name, multi,
                       self._current_tick(self._runtime, self._society),
                       "LLM", label,
                       "appel en cours" if started else "appel terminé")

    def _timed(self, label: str, key: Optional[str], call: Callable[[], Any]):
        """Exécute `call`, trace panne et lenteur, puis **repropage** l'erreur.

        Le runtime a déjà une politique d'erreur pour chaque frontière (capteur
        tolérant, outil traçé, LLM replié sur son schéma) : on l'observe, on ne
        la remplace pas.
        """
        started = time.perf_counter()
        try:
            return call()
        except Exception as exc:                       # noqa: BLE001
            self._boundary("ERROR", f"{label} a levé",
                           f"{type(exc).__name__}: {exc}", key)
            raise
        finally:
            elapsed = (time.perf_counter() - started) * 1000.0
            if elapsed >= self.slow_ms:
                self._boundary("INFO", f"{label} lent",
                               f"{elapsed:.0f} ms", key)

    def _boundary(self, kind: str, text: str, detail: str,
                  key: Optional[str]) -> None:
        node = self._index.resolve("TOOL", key) if (self._index and key) else None
        self._emit(E.TraceMsg(
            runId=self._run_id or "r?", seq=self._next_seq(),
            tick=self._runtime.state.tick if self._runtime else 0,
            kind=kind, text=text, detail=detail, nodeId=node, ts=time.time()))

    # -------------------------------------------------------- point d'arrêt
    def _checkpoint(self, tick: int) -> None:
        """Point d'arrêt : début de tick et début de phase.

        Lève `_StopRun` sur `stop()`. En mode pas à pas, chaque passage
        reconsomme l'autorisation accordée par `step()`.
        """
        if self._stop.is_set():
            raise _StopRun()
        if self._step_mode:
            self._resume.clear()
            self._announce_pause(tick)
        while not self._resume.wait(_WAIT_SLICE):
            if self._stop.is_set():
                raise _StopRun()
            self._announce_pause(tick)
        if self._paused:
            self._paused = False
            self._emit(E.RunResumed(runId=self._run_id or "r?", tick=tick))
        if self._stop.is_set():
            raise _StopRun()

    def _announce_pause(self, tick: int) -> None:
        if self._paused:
            return
        self._paused = True
        self._emit(E.RunPaused(runId=self._run_id or "r?", tick=tick))


# --------------------------------------------------------------------- utils
def _tick_limit(agent: Any, ticks: Optional[int]) -> Optional[int]:
    """Borne de ticks effective, telle que `Runtime.run` la calculerait."""
    if ticks is not None:
        return max(1, int(ticks))
    loop = getattr(agent, "loop", None)
    return (loop.max_iter if loop and loop.max_iter else 10)


def _declared_bindings(source: str) -> tuple[set, set]:
    """Capteurs et outils qu'un module hôte **déclare**, lus sans l'exécuter.

    On reconnaît les trois écritures en usage dans le dépôt ::

        host.sensors["disk.usage"] = ...      # affectation indexée
        host.tools["purge"] = fn
        @host.sensor("disk.usage")            # décorateur
        @host.tool("purge")

    L'analyse est purement syntaxique (`ast`) : proposer un hôte ne doit
    jamais exécuter du code Python arbitraire — ce serait exactement le
    contraire de la barrière posée par cette session.
    """
    import ast

    sensors: set = set()
    tools: set = set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return sensors, tools

    def bucket(attr: str) -> Optional[set]:
        return {"sensors": sensors, "sensor": sensors,
                "tools": tools, "tool": tools}.get(attr)

    for node in ast.walk(tree):
        # host.sensors["x"] = …
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Attribute)
                        and isinstance(target.slice, ast.Constant)
                        and isinstance(target.slice.value, str)):
                    dest = bucket(target.value.attr)
                    if dest is not None:
                        dest.add(target.slice.value)
        # @host.sensor("x") / host.tool("x")(fn)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            dest = bucket(node.func.attr)
            if dest is not None and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    dest.add(first.value)
    return sensors, tools


def _clamp_pace(pace: Any) -> float:
    """Allure de démonstration, en secondes entre deux événements.

    Bornée à 5 s : au-delà, un run n'est plus une démonstration mais une
    suspension — c'est `pause()` qu'il faut. Une valeur illisible vaut 0
    (pleine vitesse) plutôt qu'une erreur : l'allure n'est pas critique.
    """
    try:
        value = float(pace)
    except (TypeError, ValueError):
        return 0.0
    if value != value or value < 0:          # NaN ou négatif
        return 0.0
    return min(value, 5.0)


def _line_of(exc: Exception) -> int:
    """Numéro de ligne mentionné dans un message d'erreur de parsing.

    Le lexer et le parseur préfixent `fichier:ligne:` ; d'autres messages
    disent « ligne N ». On accepte les deux, et 0 si l'erreur ne situe rien —
    l'éditeur (M5) doit pouvoir souligner la bonne ligne.
    """
    import re
    text = str(exc)
    match = re.search(r":(\d+):", text) or re.search(r"ligne\s+(\d+)", text)
    return int(match.group(1)) if match else 0


def _is_host(candidate: Any) -> bool:
    """Un hôte utilisable : la frontière complète, pas seulement le nom."""
    return all(callable(getattr(candidate, name, None))
               for name in ("read", "invoke", "ask", "approve", "drain"))


def _empty_graph() -> Dict[str, Any]:
    return {"name": "", "version": "", "description": "", "multi": False,
            "lanes": [], "bands": [], "width": 0, "height": 0,
            "row_h": 0, "node_w": 0, "nodes": [], "edges": []}

"""Exécution asynchrone (v1.9) — hôtes et modèles `async`, concurrence bornée.

Le runtime était synchrone de bout en bout : `Host.invoke` bloquait, le pont
MCP ouvrait une boucle d'événements privée pour paraître bloquant, et une
société d'agents était un tour de rôle — trois agents qui attendent chacun
une API de deux secondes prenaient six secondes.

Ce module ajoute l'exécution asynchrone **sans toucher à la sémantique** du
langage. Le choix, et sa raison :

* **L'interpréteur reste séquentiel par agent.** Chaque action est jugée
  sur l'état que la précédente a laissé ; rendre deux actions d'un même
  agent concurrentes, c'est juger la seconde sur un état que la première est
  en train de changer — un TOCTOU introduit par le runtime lui-même. La
  concurrence est donc **entre** agents, et **aux frontières** : attendre un
  outil, un capteur ou le modèle ne bloque plus que l'agent qui attend.
* **Le cœur reste déterministe.** Un tick s'exécute dans un fil de travail ;
  chaque franchissement de frontière y devient une coroutine confiée à la
  boucle d'événements. La trace d'un agent ne dépend que de ce que ses
  frontières ont rendu — pas de l'ordre dans lequel les coroutines se sont
  terminées. Les journaux dorés se rejouent à l'identique en asynchrone.

Ce qui est fourni :

* `AsyncHost` — capteurs, outils, sous-agents, approbation en `async def`
  (les fonctions synchrones restent acceptées) ; `invoke` exige le même
  permis du noyau que `Host.invoke`.
* `Limits` — outils et appels au modèle **concurrents bornés** (sémaphores :
  au-delà, l'appel attend son tour — c'est la contre-pression), délais par
  genre d'appel, capacité des boîtes de réception.
* **Délais.** Un capteur qui dépasse son délai ne perçoit rien ; un modèle,
  c'est un oracle muet (DEFAULT) ; un **outil**, c'est une action
  **indéterminée** — la requête est partie, l'effet a pu avoir lieu :
  `ActionInDoubt`, jamais un succès présumé ni une relance aveugle.
* **Annulation** coopérative : annuler la tâche arrête l'agent au prochain
  franchissement (`Cancelled`, que rien n'avale) et annule les appels en vol.
  En exécution durable, l'action interrompue est tranchée à la reprise.
* `AsyncRuntime` — un agent ; `AsyncSociety` — plusieurs, **réellement
  concurrents**, par tours synchronisés : chaque agent tique en même temps
  que les autres sur un instantané de la mémoire partagée ; messages et
  écritures partagées sont fusionnés à la barrière, dans l'ordre déclaré.
  Même programme, même hôtes : même résultat, quel que soit l'ordonnanceur.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import threading
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence

from .core import Symbol
from .kernel.errors import ActionInDoubt, Cancelled, KernelAbort
from .kernel.gate import approval_granted
from .kernel.permit import activated, active_dispatch, require_permit
from .kernel.provenance import RUNTIME, Prov
from .llm import judge_via_reason


# =========================================================================
# Limites
# =========================================================================
@dataclass(frozen=True)
class Limits:
    """Bornes de l'exécution asynchrone. `None` = pas de borne."""

    max_concurrent_tools: Optional[int] = 8
    max_concurrent_llm: Optional[int] = 4
    max_concurrent_reads: Optional[int] = None
    tool_timeout: Optional[float] = None
    llm_timeout: Optional[float] = None
    read_timeout: Optional[float] = None
    human_timeout: Optional[float] = None
    #: Messages en attente par agent (société). Au-delà, le message est
    #: refusé, tracé et compté — jamais un débordement silencieux.
    inbox_capacity: Optional[int] = None


class BoundaryTimeout(TimeoutError):
    """Un franchissement de frontière a dépassé son délai."""


# =========================================================================
# Hôte asynchrone
# =========================================================================
class AsyncHost:
    """Pendant asynchrone de `Host`. Même contrat, fonctions `async def`.

        host = AsyncHost()

        @host.tool("restart", idempotent=True)
        async def restart(service):
            async with session.post(...) as resp:
                return {"status": resp.status}
    """

    def __init__(self) -> None:
        self.trace_sink: Optional[Callable[[Any], None]] = None
        self.sensors: Dict[str, Callable[[], Any]] = {}
        self.tools: Dict[str, Callable[..., Any]] = {}
        self.subagents: Dict[str, Callable[[Dict[str, Any]], Any]] = {}
        self.approver: Optional[Callable[[Any], Any]] = None
        self.asker: Optional[Callable[[str, str], Any]] = None
        self.events: List[Dict[str, Any]] = []
        self.idempotent_tools: set = set()
        self.reconcilers: Dict[str, Callable[..., Any]] = {}

    # ---------------------------------------------------------- décorateurs
    def sensor(self, path: str):
        def wrap(fn):
            self.sensors[path] = fn
            return fn
        return wrap

    def tool(self, name: str, *, idempotent: bool = False):
        def wrap(fn):
            self.tools[name] = fn
            if idempotent:
                self.idempotent_tools.add(name)
            return fn
        return wrap

    def subagent(self, name: str):
        def wrap(fn):
            self.subagents[name] = fn
            return fn
        return wrap

    def reconciler(self, name: str):
        def wrap(fn):
            self.reconcilers[name] = fn
            return fn
        return wrap

    # ------------------------------------------------------------ frontière
    def emit(self, source: str, **payload: Any) -> None:
        self.events.append({"source": source, "payload": payload})

    async def drain(self) -> List[Dict[str, Any]]:
        out, self.events = list(self.events), []
        return out

    async def read(self, path: str) -> Any:
        fn = self.sensors.get(path)
        return await _maybe_await(fn()) if fn else None

    async def invoke(self, name: str, args: Dict[str, Any]) -> Any:
        fn = self.tools.get(name)
        if fn is None:
            raise KeyError(f"outil `{name}` non implémenté par l'hôte")
        require_permit("invoke", name, args)
        return await _maybe_await(fn(**args))

    async def delegate(self, name: str, payload: Dict[str, Any]) -> Any:
        fn = self.subagents.get(name)
        if fn is None:
            raise KeyError(f"sous-agent `{name}` non enregistré")
        require_permit("delegate", name, payload)
        return await _maybe_await(fn(payload))

    async def ask(self, question: str, reason: str = "") -> Any:
        if self.asker is None:
            return Symbol("no_answer")
        return await _maybe_await(self.asker(question, reason))

    async def approve(self, request: Any) -> bool:
        if self.approver is None:
            return False
        return approval_granted(await _maybe_await(self.approver(request)))

    async def reconcile(self, name: str, args: Dict[str, Any],
                        context: Any) -> Any:
        fn = self.reconcilers.get(name)
        if fn is None:
            raise LookupError(f"aucune réconciliation déclarée pour `{name}`")
        return await _maybe_await(fn(args, context))


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _is_async(fn: Any) -> bool:
    return inspect.iscoroutinefunction(fn) or \
        inspect.iscoroutinefunction(getattr(fn, "__call__", None))


# =========================================================================
# Pont : fil du runtime ⇄ boucle d'événements
# =========================================================================
class _Bridge:
    """Confie chaque franchissement à la boucle, sous bornes et délais."""

    KINDS = ("tool", "llm", "read", "human")

    def __init__(self, limits: Limits) -> None:
        self.limits = limits
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._semaphores: Dict[str, Optional[asyncio.Semaphore]] = {}
        self._cancelled = threading.Event()
        self._inflight: set = set()
        self._lock = threading.Lock()
        self.stats = {"calls": 0, "queued": 0, "timeouts": 0, "cancelled": 0,
                      "max_in_flight": {k: 0 for k in self.KINDS}}
        self._in_flight = {k: 0 for k in self.KINDS}

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        if self.loop is loop:
            return
        self.loop = loop
        bounds = {"tool": self.limits.max_concurrent_tools,
                  "llm": self.limits.max_concurrent_llm,
                  "read": self.limits.max_concurrent_reads,
                  "human": None}
        self._semaphores = {kind: (asyncio.Semaphore(bound) if bound else None)
                            for kind, bound in bounds.items()}

    def _timeout(self, kind: str) -> Optional[float]:
        return {"tool": self.limits.tool_timeout,
                "llm": self.limits.llm_timeout,
                "read": self.limits.read_timeout,
                "human": self.limits.human_timeout}[kind]

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def cancel(self) -> None:
        self._cancelled.set()
        with self._lock:
            futures = list(self._inflight)
        for fut in futures:
            fut.cancel()

    # ----------------------------------------------------------------
    def call(self, kind: str, factory: Callable[[], Any], what: str) -> Any:
        """Appelé depuis le fil du runtime ; bloque ce fil, pas la boucle."""
        if self.cancelled:
            raise Cancelled(f"exécution annulée avant {what}")
        if self.loop is None:
            raise RuntimeError("pont asynchrone non lié à une boucle")
        active = active_dispatch()
        future = asyncio.run_coroutine_threadsafe(
            self._guarded(kind, factory, what, active), self.loop)
        with self._lock:
            self._inflight.add(future)
        try:
            return future.result()
        except concurrent.futures.CancelledError:
            self.stats["cancelled"] += 1
            if kind == "tool" and active is not None:
                # La requête a pu partir : l'effet a pu avoir lieu.
                raise Cancelled(f"{what} annulé en vol — effet indéterminé")
            raise Cancelled(f"{what} annulé")
        finally:
            with self._lock:
                self._inflight.discard(future)

    async def _guarded(self, kind: str, factory: Callable[[], Any], what: str,
                       active: Any) -> Any:
        semaphore = self._semaphores.get(kind)
        if semaphore is not None and semaphore.locked():
            self.stats["queued"] += 1          # contre-pression : on attend
        if semaphore is not None:
            await semaphore.acquire()
        try:
            self.stats["calls"] += 1
            self._in_flight[kind] += 1
            self.stats["max_in_flight"][kind] = max(
                self.stats["max_in_flight"][kind], self._in_flight[kind])
            # Le permis de l'action en cours voyage avec l'appel : la
            # coroutine de l'hôte le présente comme `Host.invoke` le ferait.
            with activated(active):
                awaitable = factory()
                timeout = self._timeout(kind)
                try:
                    if timeout is None:
                        return await awaitable
                    return await asyncio.wait_for(awaitable, timeout)
                except asyncio.TimeoutError:
                    self.stats["timeouts"] += 1
                    if kind == "tool":
                        raise ActionInDoubt(
                            f"{what} : délai de {timeout:g} s dépassé après "
                            f"l'envoi — l'effet a pu avoir lieu",
                            action_id=getattr(getattr(active, "permit", None),
                                              "action_id", ""),
                            tool=what) from None
                    raise BoundaryTimeout(
                        f"{what} : délai de {timeout:g} s dépassé") from None
        finally:
            self._in_flight[kind] -= 1
            if semaphore is not None:
                semaphore.release()


def _awaitable(fn: Callable[..., Any], *args: Any) -> Callable[[], Any]:
    """Fabrique l'attente d'un appel, synchrone ou non.

    Une fonction synchrone part dans un fil (`asyncio.to_thread`), qui copie
    le contexte — donc le permis réactivé par le pont.
    """
    if _is_async(fn):
        return lambda: fn(*args)
    return lambda: asyncio.to_thread(fn, *args)


class _BridgedHost:
    """Ce que voit le runtime : un hôte synchrone, servi par la boucle."""

    def __init__(self, inner: Any, bridge: _Bridge) -> None:
        self._inner, self._bridge = inner, bridge

    def read(self, path: str) -> Any:
        return self._bridge.call("read", _awaitable(self._inner.read, path),
                                 f"capteur {path}")

    def invoke(self, name: str, args: Dict[str, Any]) -> Any:
        return self._bridge.call("tool",
                                 _awaitable(self._inner.invoke, name, args),
                                 name)

    def ask(self, question: str, reason: str = "") -> Any:
        return self._bridge.call("human",
                                 _awaitable(self._inner.ask, question, reason),
                                 "question à l'opérateur")

    def approve(self, request: Any) -> Any:
        return self._bridge.call("human",
                                 _awaitable(self._inner.approve, request),
                                 "approbation")

    def drain(self) -> List[Dict[str, Any]]:
        return self._bridge.call("read", _awaitable(self._inner.drain),
                                 "file d'événements")

    def reconcile(self, name: str, args: Dict[str, Any], context: Any) -> Any:
        return self._bridge.call(
            "tool", _awaitable(self._inner.reconcile, name, args, context),
            f"réconciliation {name}")

    @property
    def subagents(self) -> Any:
        return _BridgedSubagents(self._inner, self._bridge)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _BridgedSubagents:
    def __init__(self, host: Any, bridge: _Bridge) -> None:
        self._host, self._bridge = host, bridge

    def get(self, name: str, default: Any = None) -> Any:
        if isinstance(self._host, AsyncHost):
            if name not in self._host.subagents:
                return default
            return lambda payload: self._bridge.call(
                "tool", _awaitable(self._host.delegate, name, payload),
                f"DELEGATE {name}")
        fn = self._host.subagents.get(name)
        if fn is None:
            return default
        return lambda payload: self._bridge.call(
            "tool", _awaitable(fn, payload), f"DELEGATE {name}")

    def __contains__(self, name: str) -> bool:
        return name in self._host.subagents


class _BridgedLLM:
    def __init__(self, inner: Any, bridge: _Bridge) -> None:
        self._inner, self._bridge = inner, bridge

    def reason(self, task: str, context: Dict[str, Any],
               produce: Dict[str, str]) -> Dict[str, Any]:
        """La réponse **et** le verdict « champs absents » de l'adaptateur,
        lus ensemble, dans le même fil ou la même tâche que l'appel.

        Un adaptateur partagé par plusieurs agents concurrents garde ce
        verdict dans un attribut d'instance : donner à chaque agent sa propre
        instance, c'est la seule façon qu'il ne soit pas lu par le voisin.
        """
        inner = self._inner
        if _is_async(inner.reason):
            async def call() -> Any:
                out = await inner.reason(task, context, produce)
                return out, getattr(inner, "last_reason_missing", None)
            factory = call
        else:
            def call_sync() -> Any:
                out = inner.reason(task, context, produce)
                return out, getattr(inner, "last_reason_missing", None)
            factory = lambda: asyncio.to_thread(call_sync)   # noqa: E731
        out, missing = self._bridge.call("llm", factory, f"REASON « {task} »")
        self.last_reason_missing = missing
        return out

    def select_plan(self, context: Dict[str, Any],
                    candidates: List[str]) -> Optional[str]:
        return self._bridge.call(
            "llm", _awaitable(self._inner.select_plan, context, candidates),
            "sélection de plan")

    def judge(self, task: str, context: Dict[str, Any],
              questions: Dict[str, Dict[str, Any]]) -> Any:
        """Le jugement passe par le pont, sous le même délai et la même borne
        de concurrence que `reason`.

        Par `__getattr__`, il échappait aux deux — et un `async def judge`
        rendait une coroutine jamais attendue, lue par le runtime comme un
        oracle muet.
        """
        ask = getattr(self._inner, "judge", None)
        if not callable(ask):
            # Adaptateur sans `judge` : la traduction passe par notre
            # `reason`, donc par le pont elle aussi.
            return judge_via_reason(self, task, context, questions)
        return self._bridge.call(
            "llm", _awaitable(ask, task, context, questions),
            f"JUDGE « {task} »")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


# =========================================================================
# Un agent
# =========================================================================
class AsyncRuntime:
    """Un agent AGENT-L piloté depuis une boucle `asyncio`.

        rt = AsyncRuntime(agent, AsyncHost(...), llm, limits=Limits(tool_timeout=10))
        await rt.run(max_ticks=8)
        rt.runtime.trace.render()

    `store=` rend l'exécution durable (`agentl.durable`) : intention journalisée
    avant chaque action, reprise après crash ou annulation.
    """

    def __init__(self, agent: Any, host: Any = None, llm: Any = None, *,
                 limits: Optional[Limits] = None, store: Any = None,
                 bridge: Optional[_Bridge] = None, echo: bool = False,
                 **runtime_options: Any) -> None:
        from .host import Host
        from .llm import MockLLM
        from .runtime import Runtime

        self.bridge = bridge or _Bridge(limits or Limits())
        bridged_host = _BridgedHost(host if host is not None else Host(),
                                    self.bridge)
        bridged_llm = _BridgedLLM(llm if llm is not None else MockLLM(),
                                  self.bridge)
        self.durable = None
        if store is not None:
            from .durable import DurableRun
            self.durable = DurableRun(agent, bridged_host, bridged_llm,
                                      store=store, echo=echo,
                                      **runtime_options)
            self.runtime = self.durable.runtime
        else:
            self.runtime = Runtime(agent, bridged_host, bridged_llm,
                                   echo=echo, **runtime_options)

    async def tick(self) -> None:
        self.bridge.bind(asyncio.get_running_loop())
        await _in_worker(self.bridge, self.runtime.tick)

    async def run(self, max_ticks: Optional[int] = None) -> Any:
        self.bridge.bind(asyncio.get_running_loop())
        if self.durable is not None:
            await _in_worker(self.bridge, self.durable.run, max_ticks)
            return self.runtime

        def drive() -> None:
            for _ in self.runtime.iter_ticks(max_ticks):
                if self.bridge.cancelled:
                    raise Cancelled("exécution annulée entre deux ticks")
        await _in_worker(self.bridge, drive)
        return self.runtime

    def cancel(self) -> None:
        self.bridge.cancel()


async def _in_worker(bridge: _Bridge, fn: Callable[..., Any], *args: Any) -> Any:
    """Exécute `fn` dans un fil ; une annulation arrête l'agent proprement.

    Le fil n'est pas abandonné : on annule les appels en vol, on attend que
    l'agent s'arrête au prochain franchissement, puis on propage — aucun tick
    fantôme ne continue d'agir derrière une tâche déclarée annulée.
    """
    worker = asyncio.ensure_future(asyncio.to_thread(fn, *args))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        bridge.cancel()
        try:
            await worker
        except (KernelAbort, Exception):              # noqa: BLE001
            pass
        raise


# =========================================================================
# Plusieurs agents, réellement concurrents
# =========================================================================
class AsyncSociety:
    """Société d'agents dont les ticks s'exécutent **en même temps**.

    Sémantique par tours synchronisés (BSP) :

    * au début d'un tour, chaque agent reçoit un **instantané** de la mémoire
      partagée — il lit l'état du tour précédent, jamais une écriture en
      cours d'un voisin ;
    * tous les agents tiquent en parallèle ; leurs attentes de frontière se
      recouvrent, dans les bornes de `Limits` partagées par toute la société
      (contre-pression globale) ;
    * à la barrière, les messages émis sont remis dans l'ordre déclaré des
      émetteurs puis d'émission, et les écritures partagées fusionnées dans
      ce même ordre — deux écritures d'un même tour sur une même clé sont un
      conflit, tracé et compté, dernier écrivain gagnant comme en v1.2.

    C'est une sémantique différente du tour de rôle de `Society` — un message
    envoyé au tour t n'est lu qu'au tour t+1, par tous — mais elle ne dépend
    pas de l'ordonnanceur : même programme, mêmes hôtes, même résultat.
    """

    def __init__(self, agents: Sequence[Any], hosts: Optional[Dict[str, Any]] = None,
                 llms: Optional[Dict[str, Any]] = None, *,
                 limits: Optional[Limits] = None, echo: bool = False) -> None:
        from .host import Host
        from .llm import MockLLM
        from .society import Society

        self.limits = limits or Limits()
        self.bridge = _Bridge(self.limits)
        hosts = hosts or {}
        llms = llms or {}
        names = [a.name for a in agents]
        self.society = Society(
            agents,
            hosts={n: _BridgedHost(hosts.get(n, Host()), self.bridge)
                   for n in names},
            llms={n: _BridgedLLM(llms.get(n, MockLLM()), self.bridge)
                  for n in names},
            echo=echo)
        self.order = list(self.society.order)
        self._outbox: List[tuple] = []
        self._outbox_lock = threading.Lock()
        self.rounds = 0
        self.dropped = 0
        # Les envois d'un tour sont différés jusqu'à la barrière.
        self.society.send = self._send               # type: ignore[assignment]

    @property
    def runtimes(self) -> Dict[str, Any]:
        return self.society.runtimes

    @property
    def trace(self) -> Any:
        return self.society.trace

    # ----------------------------------------------------------- messages
    def _send(self, sender: str, name: str, payload: Dict[str, Any],
              to: Optional[str] = None) -> int:
        if to is None:
            recipients = [n for n in self.order if n != sender]
        else:
            recipients = [n for n in self.order if n.lower() == to.lower()]
            if not recipients:
                self.society.trace.log(self.rounds, "ERROR",
                                       f"{sender} → destinataire inconnu : {to}")
                return 0
        with self._outbox_lock:
            self._outbox.append((self.order.index(sender), len(self._outbox),
                                 sender, name, deepcopy(payload), recipients))
        return len(recipients)

    def _deliver(self) -> None:
        from .society import Envelope

        with self._outbox_lock:
            pending, self._outbox = sorted(self._outbox,
                                           key=lambda m: (m[0], m[1])), []
        capacity = self.limits.inbox_capacity
        for _, _, sender, name, payload, recipients in pending:
            for recipient in recipients:
                inbox = self.society.runtimes[recipient].inbox
                if capacity is not None and len(inbox) >= capacity:
                    self.dropped += 1
                    self.society.trace.log(
                        self.rounds, "BLOCKED",
                        f"{sender} → {recipient} : {name} refusé",
                        f"boîte pleine ({capacity} messages en attente) — "
                        f"message non remis, jamais écrasé en silence")
                    continue
                self.society.log.append(Envelope(sender, recipient, name,
                                                 dict(payload)))
                inbox.append({"name": name, "from": sender,
                              "payload": dict(payload)})

    # -------------------------------------------------------- mémoire partagée
    def _merge_shared(self, snapshot: Dict[str, Any],
                      views: Dict[str, Dict[str, Any]]) -> None:
        shared = self.society.shared
        versions = shared.setdefault("__versions", {})
        writers: Dict[str, List[str]] = {}
        for name in self.order:
            view = views[name]
            for key, records in view.items():
                if key == "__versions" or not isinstance(records, list):
                    continue
                before = snapshot.get(key) or []
                appended = records[len(before):]
                if not appended:
                    continue
                shared.setdefault(key, [])
                shared[key].extend(appended)
                versions[key] = versions.get(key, 0) + len(appended)
                writers.setdefault(key, []).append(name)
                runtime = self.society.runtimes[name]
                runtime._seen_shared[key] = versions[key]
                runtime.state.set_world(f"shared.{key}.version", versions[key],
                                        Prov({RUNTIME}))
        for key, names in writers.items():
            for late in names[1:]:
                self.society.runtimes[late].metrics["shared_conflicts"] += 1
                self.society.trace.log(
                    self.rounds, "SHARED",
                    f"écriture concurrente sur {key} au tour {self.rounds}",
                    f"{', '.join(names)} — fusion dans l'ordre déclaré, "
                    f"dernier écrivain l'emporte")

    # ----------------------------------------------------------------- tours
    async def tick(self) -> None:
        self.bridge.bind(asyncio.get_running_loop())
        self.rounds += 1
        snapshot = deepcopy(self.society.shared)
        views = {}
        for name in self.order:
            views[name] = deepcopy(snapshot)
            self.society.runtimes[name].state.memory["SHARED"] = views[name]
        workers = [asyncio.ensure_future(asyncio.to_thread(
            self.society.runtimes[name].tick)) for name in self.order]
        try:
            results = await asyncio.shield(
                asyncio.gather(*workers, return_exceptions=True))
        except asyncio.CancelledError:
            self.bridge.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            raise
        finally:
            for name in self.order:
                self.society.runtimes[name].state.memory["SHARED"] = \
                    self.society.shared
        for result in results:
            if isinstance(result, BaseException):
                raise result
        self._merge_shared(snapshot, views)
        self._deliver()

    async def run(self, max_ticks: int = 6, until: Optional[str] = None) -> "AsyncSociety":
        for _ in range(max_ticks):
            await self.tick()
            runtimes = self.society.runtimes
            if until is not None:
                if runtimes[until].state.get("goal.satisfied") is True:
                    break
            elif all(rt.state.get("goal.satisfied") is True
                     for rt in runtimes.values() if rt.agent.goals):
                break
        return self

    def cancel(self) -> None:
        self.bridge.cancel()

    @property
    def metrics(self) -> Dict[str, int]:
        out = dict(self.society.metrics)
        out["messages_dropped"] = self.dropped
        return out

    def render_traces(self) -> str:
        return self.society.render_traces()


__all__ = ["AsyncHost", "AsyncRuntime", "AsyncSociety", "BoundaryTimeout",
           "Limits"]

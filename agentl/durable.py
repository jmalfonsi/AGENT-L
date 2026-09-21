"""Exécution durable — reprendre après un crash sans doubler un effet (v1.9).

Le rejeu (§26) re-dérive une exécution **terminée** ; il ne reprend pas une
exécution **interrompue**. REPLAY ≠ RESUME. Ce module ajoute la reprise, en
s'appuyant sur la propriété qui rend le rejeu bon marché : le cœur n'a aucune
source de non-déterminisme propre. Reprendre, c'est donc ré-exécuter le
programme depuis le début en servant chaque franchissement de frontière
depuis un journal écrit **au fil de l'eau**, puis continuer en direct là où
le journal s'arrête — la technique des moteurs d'exécution durable
(« event sourcing » sur un interpréteur déterministe), sans état à
sérialiser et sans seconde histoire : checkpoint et rejeu partagent une
seule source d'ordre, le journal.

Protocole d'une action :

    permis émis ──▶ intention écrite, synchronisée (fsync)
                ──▶ Host.invoke / sous-agent
                ──▶ résultat écrit, synchronisé
    fin de tick ──▶ point de contrôle (empreintes d'état et de trace)

À la reprise :

1. le journal est relu, sa chaîne vérifiée ; une dernière ligne déchirée —
   écrite à moitié au moment du crash — est écartée, une ligne corrompue au
   milieu arrête tout ;
2. le programme est ré-exécuté depuis le tick 0, chaque franchissement servi
   par le journal : aucun effet, aucun appel au modèle, aucune question ;
3. chaque point de contrôle est comparé à l'état re-dérivé : un écart arrête
   la reprise (programme modifié, dépendance non déterministe) ;
4. une intention restée sans résultat est **tranchée** : relancée avec la
   même clé si l'hôte promet l'idempotence, réconciliée s'il sait dire ce qui
   s'est passé, sinon déclarée **indéterminée** — jamais relancée en aveugle ;
5. passé le journal, l'exécution continue en direct et journalise.

Ce qui est garanti, et rien de plus :

* une action dont le résultat est journalisé ne s'exécute plus jamais —
  **exactement une fois** ;
* une action interrompue entre intention et résultat s'exécute exactement
  une fois si l'hôte honore la clé d'idempotence ou réconcilie, **au plus une
  fois** sinon : elle devient `ActionInDoubt`, ses `EFFECT` ne sont pas
  présumés et `tools.<outil>.in_doubt` le dit à la politique ;
* une lecture, une question ou une approbation en vol au moment du crash est
  refaite (elles n'ont pas d'effet) ; les événements d'un `drain` en vol sont
  perdus — au plus une fois ;
* la clé d'idempotence ne se **fait respecter** que par l'hôte : le runtime
  la génère, stable d'une reprise à l'autre, il ne peut pas l'imposer à un
  service tiers.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .core import AgentLError
from .kernel.action import canonical
from .kernel.errors import ActionInDoubt, KernelAbort
from .llm import ask_judge
from .replay import (ANNOTATIONS, Entry, Journal, ReplayDivergence, _NO_ARGS,
                     _missing_args, _rebuild_error, decode, missing_of, sha256)
from .seal import chain_head, chain_step

DURABLE_FORMAT = "agentl.durable.v1"


class DurableError(AgentLError):
    """Journal durable inutilisable : corrompu, altéré, ou d'un autre programme."""


class _NotExecuted:
    __slots__ = ()

    def __repr__(self) -> str:
        return "NOT_EXECUTED"


#: Réponse d'une réconciliation : « cette action n'a pas eu lieu ». Toute
#: autre valeur — `None` compris — est le résultat d'une action qui a eu lieu.
NOT_EXECUTED = _NotExecuted()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# =========================================================================
# Magasins — où le journal survit au processus
# =========================================================================
class MemoryStore:
    """Magasin en mémoire : pour les tests, et pour simuler un disque."""

    def __init__(self) -> None:
        self.meta: Dict[str, Any] = {}
        self.lines: List[str] = []

    def load(self) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        return dict(self.meta), [json.loads(line) for line in self.lines]

    def append(self, raw: Dict[str, Any]) -> None:
        self.lines.append(json.dumps(raw, ensure_ascii=False,
                                     separators=(",", ":")))

    def write_meta(self, meta: Dict[str, Any]) -> None:
        self.meta = dict(meta)

    def close(self) -> None:
        pass


class FileStore:
    """Un répertoire par exécution : `wal.jsonl` (append + fsync), `meta.json`.

    Chaque entrée est écrite en une seule ligne, vidée puis synchronisée sur
    disque **avant** de rendre la main : quand `append` revient, l'intention
    survit à une coupure de courant. C'est ce qui autorise le noyau à appeler
    l'hôte juste après.
    """

    WAL, META = "wal.jsonl", "meta.json"

    def __init__(self, directory: str | Path) -> None:
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._fh = None
        self._lock = threading.Lock()

    @property
    def wal(self) -> Path:
        return self.dir / self.WAL

    def load(self) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        meta_path = self.dir / self.META
        meta = (json.loads(meta_path.read_text(encoding="utf-8"))
                if meta_path.exists() else {})
        if not self.wal.exists():
            return meta, []
        data = self.wal.read_bytes()
        entries: List[Dict[str, Any]] = []
        offset, keep = 0, 0
        chunks = data.split(b"\n")
        for index, chunk in enumerate(chunks):
            last = index == len(chunks) - 1
            if not chunk:
                if last:
                    break
                raise DurableError(f"{self.wal} : ligne vide en position "
                                   f"{index + 1} — journal corrompu")
            try:
                raw = json.loads(chunk.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                if last:
                    # Ligne déchirée : écrite à moitié au moment du crash,
                    # donc jamais synchronisée, donc jamais suivie d'un appel.
                    break
                raise DurableError(f"{self.wal} : ligne {index + 1} illisible "
                                   f"— journal corrompu, reprise refusée")
            entries.append(raw)
            offset += len(chunk) + 1
            keep = offset
        if keep != len(data):
            with open(self.wal, "r+b") as fh:
                fh.truncate(min(keep, len(data)))
                if keep > len(data):          # ligne complète sans « \n » final
                    fh.seek(0, os.SEEK_END)
                    fh.write(b"\n")
                fh.flush()
                os.fsync(fh.fileno())
        return meta, entries

    def append(self, raw: Dict[str, Any]) -> None:
        line = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            if self._fh is None:
                self._fh = open(self.wal, "ab")
                _fsync_dir(self.dir)
            self._fh.write(line.encode("utf-8") + b"\n")
            self._fh.flush()
            os.fsync(self._fh.fileno())

    def write_meta(self, meta: Dict[str, Any]) -> None:
        tmp = self.dir / (self.META + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.dir / self.META)
        _fsync_dir(self.dir)

    def close(self) -> None:
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None


def _fsync_dir(directory: Path) -> None:
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:                                   # pragma: no cover
        return
    try:
        os.fsync(fd)
    except OSError:                                   # pragma: no cover
        pass
    finally:
        os.close(fd)


class SQLiteStore:
    """Plusieurs exécutions dans une base SQLite (stdlib, zéro dépendance).

    `synchronous=FULL` et une transaction par entrée : un `INSERT` validé a
    atteint le disque. La base peut être partagée par plusieurs exécutions,
    chacune sous son `run_id`.
    """

    def __init__(self, path: str | Path, run_id: str) -> None:
        self.path, self.run_id = str(path), run_id
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.path, isolation_level=None,
                                   check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        self._db.execute("CREATE TABLE IF NOT EXISTS runs ("
                         "run_id TEXT PRIMARY KEY, meta TEXT NOT NULL)")
        self._db.execute("CREATE TABLE IF NOT EXISTS entries ("
                         "run_id TEXT NOT NULL, seq INTEGER NOT NULL, "
                         "body TEXT NOT NULL, PRIMARY KEY (run_id, seq))")

    def load(self) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        with self._lock:
            row = self._db.execute("SELECT meta FROM runs WHERE run_id = ?",
                                   (self.run_id,)).fetchone()
            rows = self._db.execute(
                "SELECT body FROM entries WHERE run_id = ? ORDER BY seq",
                (self.run_id,)).fetchall()
        meta = json.loads(row[0]) if row else {}
        return meta, [json.loads(body) for (body,) in rows]

    def append(self, raw: Dict[str, Any]) -> None:
        body = json.dumps(raw, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                self._db.execute(
                    "INSERT INTO entries (run_id, seq, body) VALUES (?, ?, ?)",
                    (self.run_id, raw["seq"], body))
                self._db.execute("COMMIT")
            except BaseException:
                self._db.execute("ROLLBACK")
                raise

    def write_meta(self, meta: Dict[str, Any]) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR REPLACE INTO runs (run_id, meta) VALUES (?, ?)",
                (self.run_id, json.dumps(meta, ensure_ascii=False,
                                         sort_keys=True)))

    @staticmethod
    def runs(path: str | Path) -> List[str]:
        db = sqlite3.connect(str(path))
        try:
            return [r for (r,) in db.execute(
                "SELECT run_id FROM runs ORDER BY run_id")]
        except sqlite3.OperationalError:
            return []
        finally:
            db.close()

    def close(self) -> None:
        with self._lock:
            self._db.close()


# =========================================================================
# Journal d'intention
# =========================================================================
class DurableJournal(Journal):
    """Journal de rejeu écrit au fil de l'eau, qui sait reprendre.

    Tant que le curseur est dans le **préfixe** — ce qui fut écrit avant le
    crash — chaque franchissement est servi depuis le journal, dans l'ordre
    strict, annotations comprises. Au-delà, tout est exécuté en direct et
    écrit avant de rendre la main.
    """

    def __init__(self, store: Any, meta: Dict[str, Any],
                 raws: List[Dict[str, Any]]) -> None:
        super().__init__(entries=[Entry.from_json(raw) for raw in raws],
                         meta=dict(meta))
        self.store = store
        self._prefix = len(self.entries)
        self._head = raws[-1]["h"] if raws else ""
        self.completed = meta.get("status") == "completed"
        self.resolutions: List[Dict[str, Any]] = []

    @classmethod
    def open(cls, store: Any) -> "DurableJournal":
        meta, raws = store.load()
        previous = ""
        for index, raw in enumerate(raws):
            if raw.get("seq") != index:
                raise DurableError(f"journal durable : entrée #{index} hors "
                                   f"séquence — reprise refusée")
            digest = chain_step(previous, raw)
            if raw.get("h") != digest:
                raise DurableError(f"journal durable altéré au franchissement "
                                   f"#{index} — reprise refusée")
            previous = digest
        return cls(store, meta, raws)

    # ------------------------------------------------------------ curseur
    @property
    def in_prefix(self) -> bool:
        return self._cursor < self._prefix

    @property
    def resuming(self) -> bool:
        return self._prefix > 0

    def next(self, kind: str, key: str) -> Entry:
        # Ordre strict : une annotation se consomme à sa place, jamais sautée.
        if self._cursor >= self._prefix:
            raise ReplayDivergence(f"préfixe épuisé : {kind}:{key} demandé")
        entry = self.entries[self._cursor]
        if entry.kind != kind or entry.key != key:
            raise ReplayDivergence(
                f"reprise impossible au franchissement #{self._cursor} : "
                f"journalisé {entry.kind}:{entry.key}, re-dérivé {kind}:{key}")
        self._cursor += 1
        return entry

    def _peek(self) -> Optional[Entry]:
        return self.entries[self._cursor] if self.in_prefix else None

    # ------------------------------------------------------------ écriture
    def record(self, kind: str, key: str, *, args: Any = None,
               value: Any = None, error: Optional[BaseException] = None) -> None:
        if self.in_prefix:
            raise ReplayDivergence(f"écriture de {kind}:{key} pendant la "
                                   f"re-dérivation du préfixe")
        if self.completed:
            raise ReplayDivergence(f"exécution durable terminée : {kind}:{key} "
                                   f"ne peut plus être journalisé")
        super().record(kind, key, args=args, value=value, error=error)
        entry = self.entries[-1]
        raw = entry.to_json()
        raw["h"] = chain_step(self._head, raw)
        self.store.append(raw)                      # durable au retour
        self._head = entry.hash = raw["h"]
        self._cursor = len(self.entries)

    # ----------------------------------------------------- franchissement
    def crossing(self, kind: str, key: str, fn: Callable[[], Any], *,
                 args: Any = _NO_ARGS, record_args: Any = None) -> Any:
        if self.in_prefix:
            return self.replay_value(kind, key, args)
        if self.completed:
            raise ReplayDivergence(f"exécution durable terminée : {kind}:{key} "
                                   f"demandé au-delà du journal")
        try:
            value = fn()
        except KernelAbort:
            raise
        except Exception as exc:                    # noqa: BLE001
            self.record(kind, key, args=record_args, error=exc)
            raise
        # Une interruption brutale (KeyboardInterrupt, arrêt du processus)
        # n'est **pas** journalisée : l'effet a pu avoir lieu, et c'est à la
        # reprise d'en décider — intention sans résultat, donc indéterminée.
        self.record(kind, key, args=record_args, value=value)
        return value

    # ------------------------------------------------------------- actions
    def dispatch(self, permit: Any, active: Any, call: Callable[[], Any],
                 host: Any) -> Any:
        """Protocole d'intention autour d'un appel à l'hôte (noyau)."""
        stamp = {"action_id": permit.action_id, "key": permit.idempotency_key,
                 "hash": permit.action_hash, "kind": permit.kind,
                 "policy": permit.policy_digest[:16]}
        if not self.in_prefix:
            if self.completed:
                raise ReplayDivergence("exécution durable terminée : aucune "
                                       "action nouvelle")
            self.record("intent", permit.target, args=stamp)
            return call()

        entry = self.next("intent", permit.target)
        recorded = decode(entry.args) or {}
        if recorded.get("hash") != permit.action_hash \
                or recorded.get("action_id") != permit.action_id:
            raise ReplayDivergence(
                f"reprise impossible : l'intention {recorded.get('action_id')} "
                f"journalisée ne correspond pas à l'action re-dérivée "
                f"{permit.action_id} ({permit.target})")
        attempts = 1
        while self._peek() is not None and self._peek().kind == "resolution":
            done = decode(self.next("resolution", permit.target).args) or {}
            attempts = int(done.get("attempt", attempts))
        if self.in_prefix:
            active.attempt = attempts
            return call()           # résultat journalisé : servi, pas rejoué
        return self._settle(permit, active, call, host, attempts)

    def _settle(self, permit: Any, active: Any, call: Callable[[], Any],
                host: Any, attempts: int) -> Any:
        """Intention sans résultat : l'effet a pu avoir lieu. Trancher."""
        kind_entry = "invoke" if permit.kind == "invoke" else "delegate"
        target = permit.target
        base = {"action_id": permit.action_id}

        if permit.kind == "invoke" and target in _idempotent(host):
            active.attempt = attempts + 1
            self._resolve(target, {**base, "how": "retry",
                                   "attempt": active.attempt})
            return call()

        if permit.kind == "invoke" and _reconcilable(host, target):
            try:
                found = host.reconcile(target, deepcopy(permit.args),
                                       active.context)
            except KernelAbort:
                raise
            except Exception as exc:                # noqa: BLE001
                found = _Unknown(f"{type(exc).__name__}: {exc}")
            if found is NOT_EXECUTED:
                active.attempt = attempts + 1
                self._resolve(target, {**base, "how": "not_executed",
                                       "attempt": active.attempt})
                return call()
            if not isinstance(found, _Unknown):
                self._resolve(target, {**base, "how": "reconciled"})
                self.record(kind_entry, target, args=permit.args, value=found)
                return found

        self._resolve(target, {**base, "how": "in_doubt"})
        error = ActionInDoubt(
            f"intention {permit.action_id} journalisée sans résultat — l'effet "
            f"a pu avoir lieu ; action non relancée (au plus une fois)",
            action_id=permit.action_id, tool=target)
        self.record(kind_entry, target, args=permit.args, error=error)
        raise error

    def _resolve(self, target: str, how: Dict[str, Any]) -> None:
        self.resolutions.append({"tool": target, **how})
        self.record("resolution", target, args=how)

    # ------------------------------------------------------ point de contrôle
    def checkpoint(self, tick: int, state: str, trace: str) -> None:
        digest = {"state": state, "trace": trace}
        if self.in_prefix:
            entry = self.next("checkpoint", str(tick))
            recorded = decode(entry.args) or {}
            if recorded != digest:
                which = ("l'état" if recorded.get("state") != state
                         else "la trace")
                raise ReplayDivergence(
                    f"reprise impossible : au tick {tick}, {which} re-dérivé "
                    f"diffère du point de contrôle journalisé — programme, "
                    f"runtime ou dépendance non déterministe")
            return
        self.record("checkpoint", str(tick), args=digest)

    # --------------------------------------------------------------- statut
    def finish(self, trace_text: str, ticks: int) -> None:
        self.meta.update({"status": "completed", "ticks": ticks,
                          "trace_sha256": sha256(trace_text),
                          "chain_sha256": self._head or chain_head([]),
                          "completed": _now()})
        self.store.write_meta(self.meta)
        self.completed = True

    def pending(self) -> List[Dict[str, Any]]:
        """Intentions journalisées restées sans résultat — ce qu'un crash a
        laissé en suspens, avant que la reprise ne le tranche."""
        out: List[Dict[str, Any]] = []
        entries = self.entries
        for i, entry in enumerate(entries):
            if entry.kind != "intent":
                continue
            j = i + 1
            while j < len(entries) and entries[j].kind == "resolution":
                j += 1
            if j < len(entries) and entries[j].kind in ("invoke", "delegate") \
                    and entries[j].key == entry.key:
                continue
            out.append({"tool": entry.key, **(decode(entry.args) or {})})
        return out


class _Unknown:
    def __init__(self, why: str) -> None:
        self.why = why


def _idempotent(host: Any) -> set:
    try:
        return set(getattr(host, "idempotent_tools", ()) or ())
    except Exception:                                 # noqa: BLE001
        return set()


def _reconcilable(host: Any, target: str) -> bool:
    reconcilers = getattr(host, "reconcilers", None)
    if isinstance(reconcilers, dict):
        return target in reconcilers
    return callable(getattr(host, "reconcile", None))


# =========================================================================
# Enveloppes — chaque franchissement passe par le journal
# =========================================================================
class DurableHost:
    """Hôte servi par le journal pendant la reprise, journalisé ensuite."""

    def __init__(self, inner: Any, journal: DurableJournal) -> None:
        self._inner, self._journal = inner, journal

    def read(self, path: str) -> Any:
        return self._journal.crossing("read", path,
                                      lambda: self._inner.read(path))

    def invoke(self, name: str, args: Dict[str, Any]) -> Any:
        return self._journal.crossing(
            "invoke", name, lambda: self._inner.invoke(name, args),
            args=args, record_args=args)

    def ask(self, question: str, reason: str = "") -> Any:
        return self._journal.crossing(
            "ask", question, lambda: self._inner.ask(question, reason),
            args={"reason": reason}, record_args={"reason": reason})

    def approve(self, request: Any) -> Any:
        key = request.render() if hasattr(request, "render") else str(request)
        return self._journal.crossing("approve", key,
                                      lambda: self._inner.approve(request))

    def drain(self) -> List[Dict[str, Any]]:
        return self._journal.crossing("drain", "", lambda: self._inner.drain())

    @property
    def subagents(self) -> Any:
        return _DurableSubagents(self._inner.subagents, self._journal)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _DurableSubagents:
    def __init__(self, inner: Any, journal: DurableJournal) -> None:
        self._inner, self._journal = inner, journal

    def get(self, name: str, default: Any = None) -> Any:
        found: Dict[str, Any] = {}

        def lookup() -> bool:
            found["fn"] = self._inner.get(name)
            return found["fn"] is not None

        if not self._journal.crossing("delegate_lookup", name, lookup):
            return default

        def call(payload: Dict[str, Any]) -> Any:
            fn = found.get("fn") or self._inner.get(name)
            return self._journal.crossing("delegate", name,
                                          lambda: fn(payload),
                                          args=payload, record_args=payload)
        return call

    def __contains__(self, name: str) -> bool:
        return name in self._inner


class DurableLLM:
    """L'oracle, servi par le journal pendant la reprise : ni coût ni hasard."""

    def __init__(self, inner: Any, journal: DurableJournal) -> None:
        self._inner, self._journal = inner, journal

    def reason(self, task: str, context: Dict[str, Any],
               produce: Dict[str, str]) -> Dict[str, Any]:
        journal = self._journal
        if journal.in_prefix:
            entry = journal.next("reason", task)
            self.last_reason_missing = missing_of(entry)
            if entry.error is not None:
                raise _rebuild_error(entry.error[0], entry.error[1])
            return decode(entry.value)
        if journal.completed:
            raise ReplayDivergence(f"exécution durable terminée : reason:{task} "
                                   f"demandé au-delà du journal")
        try:
            value = self._inner.reason(task, context, produce)
        except KernelAbort:
            raise
        except Exception as exc:                    # noqa: BLE001
            journal.record("reason", task, error=exc)
            raise
        missing = getattr(self._inner, "last_reason_missing", None)
        self.last_reason_missing = missing
        journal.record("reason", task, value=value,
                       args=_missing_args(missing))
        return value

    def select_plan(self, context: Dict[str, Any],
                    candidates: List[str]) -> Optional[str]:
        return self._journal.crossing(
            "select_plan", "|".join(candidates),
            lambda: self._inner.select_plan(context, candidates))

    def judge(self, task: str, context: Dict[str, Any],
              questions: Dict[str, Dict[str, Any]]) -> Any:
        # Servi par le journal pendant la reprise, comme `reason`. Un oracle
        # de jugement n'est pas déterministe (Jev : ±0,06 sur la même
        # question) : le rappeler à la reprise pouvait changer la décision
        # déjà prise — et faire refuser la reprise au franchissement suivant.
        return self._journal.crossing(
            "judge", task,
            lambda: ask_judge(self._inner, task, context, questions))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


# =========================================================================
# Empreintes
# =========================================================================
def program_digest(agents: Sequence[Any]) -> str:
    """Identité du programme : reprendre sur un autre serait mentir."""
    h = hashlib.sha256(b"agentl.program.v1\n")
    for agent in agents:
        h.update(repr(agent).encode("utf-8") + b"\n")
    return h.hexdigest()


def _stable(value: Any) -> Any:
    """`canonical`, sans les adresses mémoire qu'un `repr` opaque porterait."""
    enc = canonical(value)
    return _strip_opaque(enc)


def _strip_opaque(enc: Any) -> Any:
    if isinstance(enc, dict):
        if "$opaque" in enc:
            return {"$opaque": enc.get("$type", "?")}
        return {k: _strip_opaque(v) for k, v in enc.items()}
    if isinstance(enc, list):
        return [_strip_opaque(v) for v in enc]
    return enc


def state_digest(runtimes: Sequence[Any]) -> str:
    h = hashlib.sha256(b"agentl.state.v1\n")
    for rt in runtimes:
        st = rt.state
        snapshot = {
            "tick": st.tick, "world": st.world, "locals": st.locals,
            "untrusted": getattr(st, "untrusted", {}), "memory": st.memory,
            "beliefs": {k: [b.value, b.confidence, b.source, b.updated]
                        for k, b in st.beliefs.items()},
            "queue": list(rt.plan_queue), "metrics": rt.metrics,
            # La provenance se re-dérive comme le reste : une reprise qui
            # étiquetterait autrement jugerait autrement.
            "labels": {k: sorted(v.sources)
                       for k, v in (getattr(st, "labels", {}) or {}).items()},
            "attestations": {k: sorted(v) for k, v in
                             (getattr(st, "attestations", {}) or {}).items()},
        }
        h.update(json.dumps(_stable(snapshot), sort_keys=True,
                            ensure_ascii=False).encode("utf-8"))
    return h.hexdigest()


class _TraceHasher:
    """Empreinte incrémentale des traces — sans re-rendre tout à chaque tick."""

    def __init__(self, traces: Sequence[Any]) -> None:
        self.traces = list(traces)
        self._seen = [0] * len(self.traces)
        self._h = hashlib.sha256(b"agentl.trace.v1\n")

    def digest(self) -> str:
        for i, trace in enumerate(self.traces):
            for event in trace.events[self._seen[i]:]:
                self._h.update(f"{i}\x1f{trace.line(event)}\n".encode("utf-8"))
            self._seen[i] = len(trace.events)
        return self._h.copy().hexdigest()


# =========================================================================
# Exécution
# =========================================================================
class DurableRun:
    """Une exécution qui survit à son processus.

        store = FileStore("runs/incident-42")
        run = DurableRun(agent, host, llm, store=store)
        runtime = run.run(max_ticks=8)     # neuve, ou reprise si le journal existe

    La même ligne démarre une exécution et la reprend : c'est le journal qui
    dit laquelle des deux on fait. Une exécution terminée se re-dérive sans
    rien toucher.
    """

    def __init__(self, agents: Any, host: Any = None, llm: Any = None, *,
                 store: Any, run_id: Optional[str] = None, echo: bool = False,
                 source: Optional[Tuple[str, str]] = None,
                 **runtime_options: Any) -> None:
        from .host import Host
        from .llm import MockLLM
        from .runtime import Runtime
        from .society import Society

        self.agents = list(agents) if isinstance(agents, (list, tuple)) \
            else [agents]
        self.store = store
        self.journal = DurableJournal.open(store)
        meta = self.journal.meta
        digest = program_digest(self.agents)
        if meta:
            if meta.get("format") != DURABLE_FORMAT:
                raise DurableError(f"format de journal durable inconnu : "
                                   f"{meta.get('format')!r}")
            if meta.get("program_sha256") != digest:
                raise DurableError(
                    "le programme a changé depuis le début de cette exécution "
                    "— reprendre ne re-dériverait pas les décisions "
                    "journalisées ; reprise refusée")
            if run_id is not None and run_id != meta.get("run_id"):
                raise DurableError(f"journal de l'exécution "
                                   f"{meta.get('run_id')}, pas {run_id}")
            self.run_id = meta["run_id"]
        else:
            if self.journal.entries:
                raise DurableError("journal sans méta-données : reprise "
                                   "refusée")
            self.run_id = run_id or uuid.uuid4().hex[:12]
            meta.update({"format": DURABLE_FORMAT, "run_id": self.run_id,
                         "agents": [a.name for a in self.agents],
                         "program_sha256": digest, "status": "running",
                         "created": _now()})
            if source is not None:
                # Pour que l'export se rejoue avec `agentl replay` : le rejeu
                # retrouve le programme et vérifie que c'est le même.
                meta["source"], meta["source_sha256"] = source[0], sha256(source[1])
            store.write_meta(meta)

        def pick(table: Any, name: str, default: Callable[[], Any]) -> Any:
            if isinstance(table, dict) and name in table:
                return table[name]
            if table is None or isinstance(table, dict):
                return default()
            return table

        if len(self.agents) == 1:
            agent = self.agents[0]
            self.society = None
            self.runtime = Runtime(
                agent, DurableHost(pick(host, agent.name, Host), self.journal),
                DurableLLM(pick(llm, agent.name, MockLLM), self.journal),
                echo=echo, run_id=self.run_id, **runtime_options)
            self.runtimes = [self.runtime]
        else:
            names = [a.name for a in self.agents]
            self.society = Society(
                self.agents,
                hosts={n: DurableHost(pick(host, n, Host), self.journal)
                       for n in names},
                llms={n: DurableLLM(pick(llm, n, MockLLM), self.journal)
                      for n in names},
                echo=echo)
            self.runtime = None
            self.runtimes = [self.society.runtimes[n] for n in names]
        for rt in self.runtimes:
            rt.kernel.run_id = self.run_id
            rt.kernel.journal = self.journal
        traces = [rt.trace for rt in self.runtimes]
        if self.society is not None:
            traces.append(self.society.trace)
        self._hasher = _TraceHasher(traces)

    # ----------------------------------------------------------------
    @property
    def resumed(self) -> bool:
        return self.journal.resuming

    @property
    def status(self) -> str:
        return self.journal.meta.get("status", "running")

    def trace_text(self) -> str:
        if self.society is not None:
            return self.society.render_traces()
        return self.runtime.trace.render()

    def _checkpoint(self, tick: int) -> None:
        self.journal.checkpoint(tick, state_digest(self.runtimes),
                                self._hasher.digest())

    def run(self, max_ticks: Optional[int] = None) -> Any:
        meta = self.journal.meta
        if max_ticks is None:
            max_ticks = meta.get("max_ticks")
        elif meta.get("max_ticks") not in (None, max_ticks) \
                and self.journal.resuming:
            raise DurableError(f"exécution commencée avec {meta['max_ticks']} "
                               f"ticks au plus, reprise demandée avec "
                               f"{max_ticks}")
        if "max_ticks" not in meta:
            meta["max_ticks"] = max_ticks
            self.store.write_meta(meta)

        ticks = 0
        if self.society is not None:
            for tick in self.society.iter_ticks(max_ticks if max_ticks else 6):
                ticks = tick
                self._checkpoint(tick)
            result: Any = self.society
        else:
            for runtime in self.runtime.iter_ticks(max_ticks):
                ticks = runtime.state.tick
                self._checkpoint(ticks)
            result = self.runtime

        if self.journal.in_prefix:
            raise ReplayDivergence(
                f"reprise incomplète : {self.journal.remaining} franchissement(s) "
                f"journalisé(s) non re-dérivé(s)")
        if not self.journal.completed:
            self.journal.finish(self.trace_text(), ticks)
        return result

    def export(self) -> Journal:
        """Le journal, sous sa forme de rejeu standard (`agentl replay`)."""
        meta = self.journal.meta
        journal = Journal(entries=list(self.journal.entries),
                          meta={k: meta[k] for k in ("trace_sha256", "source",
                                                     "source_sha256")
                                if k in meta})
        journal.meta["agent"] = " · ".join(a.name for a in self.agents)
        journal.meta["run_id"] = self.run_id
        journal.meta["ticks"] = meta.get("max_ticks") or meta.get("ticks")
        return journal


def resolutions_of(journal: DurableJournal) -> List[Dict[str, Any]]:
    """Comment les actions interrompues ont été tranchées, dans l'ordre."""
    return [dict(decode(e.args) or {}, tool=e.key)
            for e in journal.entries if e.kind == "resolution"]


__all__ = ["ANNOTATIONS", "DURABLE_FORMAT", "DurableError", "DurableHost",
           "DurableJournal", "DurableLLM", "DurableRun", "FileStore",
           "MemoryStore", "NOT_EXECUTED", "SQLiteStore", "program_digest",
           "resolutions_of", "state_digest"]

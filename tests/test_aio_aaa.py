"""Exécution asynchrone (v1.9) — concurrence réelle, bornée, annulable.

Les durées sont mesurées au chronomètre : « concurrent » veut dire que
quatre attentes de 0,3 s tiennent en bien moins de 1,2 s, et « borné » que la
même chose avec une seule place en prend au moins 1,2. Les marges sont
larges pour ne pas dépendre de la charge de la machine d'intégration.
"""
from __future__ import annotations

import asyncio
import random
import time
from pathlib import Path

import pytest

from agentl import Host, MockLLM, Symbol, parse_source
from agentl.aio import AsyncHost, AsyncRuntime, AsyncSociety, Limits
from agentl.durable import MemoryStore, resolutions_of
from agentl.kernel import PermitError, current_action
from agentl.parser import parse_file
from agentl.replay import Journal, ReplayHost, ReplayLLM, sha256

ROOT = Path(__file__).resolve().parents[1]

WORKER = """
AGENT {name} {{
  OBSERVE {{ job.ready }}
  TOOL crunch {{ RISK LOW  OUTPUT {{ done: Symbol }} }}
  POLICY {{ DEFAULT ALLOW }}
  PLAN work WHEN job.ready == yes AND crunched != yes {{
    crunch()
    SET crunched = yes
  }}
}}
"""


def _workers(n: int):
    source = "\n".join(WORKER.format(name=f"w{i}") for i in range(n))
    return parse_source(source).agents


def _async_host(delay: float, log=None):
    host = AsyncHost()

    @host.sensor("job.ready")
    async def ready():
        return Symbol("yes")

    @host.tool("crunch")
    async def crunch():
        if log is not None:
            log.append(current_action().action_id)
        await asyncio.sleep(delay)
        return {"done": Symbol("yes")}
    return host


def _society(n: int, delay: float, limits: Limits, hosts=None):
    agents = _workers(n)
    hosts = hosts or {a.name: _async_host(delay) for a in agents}
    return AsyncSociety(agents, hosts=hosts, limits=limits)


# ---------------------------------------------------------- concurrence
def test_agents_wait_on_their_tools_at_the_same_time():
    society = _society(4, 0.3, Limits(max_concurrent_tools=4))
    started = time.perf_counter()
    asyncio.run(society.run(max_ticks=1))
    elapsed = time.perf_counter() - started
    assert elapsed < 0.9, elapsed                       # 4 × 0,3 s séquentiel = 1,2
    assert society.metrics["tool_calls"] == 4
    assert society.bridge.stats["max_in_flight"]["tool"] == 4


def test_the_tool_bound_is_enforced_and_creates_backpressure():
    society = _society(4, 0.3, Limits(max_concurrent_tools=1))
    started = time.perf_counter()
    asyncio.run(society.run(max_ticks=1))
    elapsed = time.perf_counter() - started
    assert elapsed >= 1.15, elapsed
    assert society.bridge.stats["max_in_flight"]["tool"] == 1
    assert society.bridge.stats["queued"] >= 3


def test_blocking_sync_hosts_also_overlap():
    agents = _workers(4)
    hosts = {}
    for agent in agents:
        host = Host()
        host.sensors["job.ready"] = lambda: Symbol("yes")
        host.tools["crunch"] = lambda: time.sleep(0.3) or {"done": Symbol("yes")}
        hosts[agent.name] = host
    society = AsyncSociety(agents, hosts=hosts, limits=Limits())
    started = time.perf_counter()
    asyncio.run(society.run(max_ticks=1))
    assert time.perf_counter() - started < 0.9
    assert society.metrics["tool_calls"] == 4


# ------------------------------------------------------------------ délais
def test_a_tool_timeout_is_an_action_in_doubt_not_a_success():
    agent = _workers(1)[0]
    runtime = AsyncRuntime(agent, _async_host(5.0),
                           limits=Limits(tool_timeout=0.1))
    started = time.perf_counter()
    rt = asyncio.run(runtime.run(max_ticks=1))
    assert time.perf_counter() - started < 2.0
    trace = rt.trace.render()
    assert "crunch() : effet indéterminé" in trace
    assert rt.state.get("tools.crunch.in_doubt") is True
    assert rt.metrics["tool_calls"] == 0


def test_a_model_timeout_is_a_silent_oracle():
    source = """
    AGENT a {
      TOOL act { RISK HIGH }
      POLICY { DEFAULT ALLOW  NEVER act WHEN risk_score > 0.5 }
      PLAN p WHEN go == yes {
        REASON "score" { USING { go } PRODUCE { risk_score: Number } }
        act()
      }
    }"""

    class SlowLLM(MockLLM):
        async def reason(self, task, context, produce):
            await asyncio.sleep(5)
            return {"risk_score": 0.0}

    calls = []
    host = AsyncHost()
    host.tool("act")(lambda: calls.append("act") or {})
    runtime = AsyncRuntime(parse_source(source).agents[0], host, SlowLLM(),
                           limits=Limits(llm_timeout=0.1))
    runtime.runtime.state.set_world("go", Symbol("yes"))
    rt = asyncio.run(runtime.run(max_ticks=1))
    assert calls == []                  # score indéterminé : le NEVER s'applique
    assert rt.state.get("reason.degraded") is True


# -------------------------------------------------------------- annulation
def test_cancelling_stops_the_agent_and_its_call_in_flight():
    stopped = []

    host = AsyncHost()
    host.sensor("job.ready")(lambda: Symbol("yes"))

    @host.tool("crunch")
    async def crunch():
        try:
            await asyncio.sleep(10)
        finally:
            stopped.append("annulé")
        return {"done": Symbol("yes")}

    runtime = AsyncRuntime(_workers(1)[0], host)

    async def main():
        task = asyncio.ensure_future(runtime.run(max_ticks=5))
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    started = time.perf_counter()
    asyncio.run(main())
    assert time.perf_counter() - started < 3.0
    assert stopped == ["annulé"]
    assert runtime.runtime.state.tick == 1           # aucun tick fantôme


@pytest.mark.parametrize("idempotent", [False, True])
def test_a_cancelled_durable_run_resumes_without_doubling(idempotent):
    ledger = []
    store = MemoryStore()

    def make(delay):
        host = AsyncHost()
        host.sensor("job.ready")(lambda: Symbol("yes") if not ledger
                                 else Symbol("no"))

        @host.tool("crunch", idempotent=idempotent)
        async def crunch():
            key = current_action().idempotency_key
            if key not in ledger:
                ledger.append(key)
            await asyncio.sleep(delay)
            return {"done": Symbol("yes")}
        return host

    agent = _workers(1)[0]

    async def interrupted():
        rt = AsyncRuntime(agent, make(10), store=store, run_id="c")
        task = asyncio.ensure_future(rt.run(max_ticks=3))
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(interrupted())
    assert len(ledger) == 1

    resumed = AsyncRuntime(agent, make(0), store=store)
    rt = asyncio.run(resumed.run())
    assert len(ledger) == 1                          # jamais deux fois
    how = [r["how"] for r in resolutions_of(resumed.durable.journal)]
    assert how == (["retry"] if idempotent else ["in_doubt"])
    if not idempotent:
        assert rt.state.get("tools.crunch.in_doubt") is True


# ------------------------------------------------------------------ permis
def test_an_async_host_refuses_a_call_without_permit():
    host = _async_host(0)
    with pytest.raises(PermitError):
        asyncio.run(host.invoke("crunch", {}))


# ----------------------------------------------------------- déterminisme
GOLDEN = sorted((ROOT / "tests" / "golden").glob("*/*.json"))


@pytest.mark.parametrize("journal_path", GOLDEN,
                         ids=[p.stem for p in GOLDEN])
def test_published_journals_replay_identically_under_async(journal_path):
    program = parse_file(str(journal_path.with_suffix(".agent")))
    if len(program.agents) != 1:
        pytest.skip("société : sémantique par tours synchronisés, distincte")
    journal = Journal.load(journal_path)
    runtime = AsyncRuntime(program.agents[0], ReplayHost(journal),
                           ReplayLLM(journal))
    rt = asyncio.run(runtime.run(max_ticks=journal.meta["ticks"]))
    assert sha256(rt.trace.render()) == journal.meta["trace_sha256"]


PING = """
AGENT a {
  MEMORY { SHARED { log }
           WRITE { WHEN sent == yes  STORE { sent }  INTO SHARED.log } }
  PLAN go WHEN sent != yes {
    SET sent = yes
    MESSAGE ping { TO b  PAYLOAD { n = 1 } }
  }
}
AGENT b {
  MEMORY { SHARED { log }
           WRITE { WHEN got == yes  STORE { got }  INTO SHARED.log } }
  ON MESSAGE ping { SET got = yes }
}
"""


def _jittered_society():
    agents = parse_source(PING).agents
    hosts = {}
    for agent in agents:
        host = AsyncHost()

        async def drain(_host=host):
            await asyncio.sleep(random.uniform(0, 0.05))   # ordre aléatoire
            return []
        host.drain = drain
        hosts[agent.name] = host
    return AsyncSociety(agents, hosts=hosts)


def _rounds(society, n):
    # Sans GOAL, `run` s'arrête au premier tour (même règle que `Society`) :
    # on pilote les tours à la main.
    async def main():
        for _ in range(n):
            await society.tick()
        return society
    return asyncio.run(main())


def test_a_concurrent_society_does_not_depend_on_the_scheduler():
    outcomes = set()
    for _ in range(5):
        society = _rounds(_jittered_society(), 3)
        outcomes.add((society.render_traces(),
                      repr(society.society.shared)))
    assert len(outcomes) == 1


def test_messages_of_a_round_are_delivered_at_the_barrier():
    society = _rounds(_jittered_society(), 3)
    b = society.runtimes["b"]
    # a envoie au tour 1, b le lit au tour 2 — pas au tour 1.
    lines = b.trace.render()
    assert "ping ← a" in lines
    tick_of_receipt = next(e.tick for e in b.trace.events
                           if e.kind == "MESSAGE" and "ping ← a" in e.text)
    assert tick_of_receipt == 2
    # Fusion dans l'ordre des tours : a au tour 1, b au tour 2, puis a de
    # nouveau au tour 3 — `WRITE` ne déduplique que contre le dernier
    # enregistrement, comme dans la société séquentielle.
    assert society.society.shared["log"] == [
        {"sent": Symbol("yes")}, {"got": Symbol("yes")},
        {"sent": Symbol("yes")}]
    assert society.society.shared["__versions"]["log"] == 3


def test_a_full_inbox_refuses_instead_of_overflowing():
    source = """
    AGENT a {
      PLAN spam WHEN go != no {
        MESSAGE m { TO b  PAYLOAD { n = 1 } }
        MESSAGE m { TO b  PAYLOAD { n = 2 } }
        MESSAGE m { TO b  PAYLOAD { n = 3 } }
      }
    }
    AGENT b { }
    """
    society = AsyncSociety(parse_source(source).agents,
                           limits=Limits(inbox_capacity=2))
    asyncio.run(society.tick())
    assert len(society.runtimes["b"].inbox) == 2
    assert society.metrics["messages_dropped"] == 1
    assert "boîte pleine" in society.trace.render()


# --------------------------------------------------------------------- MCP
def test_an_async_mcp_server_is_awaited_in_the_agent_loop():
    """Pont MCP asynchrone : deux agents attendent chacun un serveur lent de
    0,3 s — en même temps, sans boucle privée, sous permis du noyau."""
    from agentl.mcp import AsyncMCPHost, AsyncStaticClient, MCPTool, catalog_digest

    source = "\n".join(f"""
    AGENT m{i} {{
      TOOL srv__search {{ RISK LOW  INPUT {{ query: String }}  OUTPUT {{ hits: Number }} }}
      POLICY {{ DEFAULT ALLOW }}
      PLAN p WHEN done != yes {{ srv__search(query="x")  SET done = yes }}
    }}""" for i in range(2))
    agents = parse_source(source).agents
    catalog = [MCPTool(name="search", description="",
                       input_schema={"type": "object",
                                     "properties": {"query": {"type": "string"}}})]
    clients = {a.name: AsyncStaticClient(catalog, {"search": {"hits": 3}},
                                         delay=0.3) for a in agents}
    hosts = {a.name: AsyncMCPHost(AsyncHost(), clients[a.name], "srv",
                                  catalog_digest(catalog)) for a in agents}
    society = AsyncSociety(agents, hosts=hosts)
    started = time.perf_counter()
    asyncio.run(society.tick())
    assert time.perf_counter() - started < 0.55
    assert all(c.calls == [("search", {"query": "x"})] for c in clients.values())
    assert society.metrics["tool_calls"] == 2

    with pytest.raises(PermitError):                # hors du noyau : refusé
        asyncio.run(hosts[agents[0].name].invoke("srv__search", {"query": "y"}))

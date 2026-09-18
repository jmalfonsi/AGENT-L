"""Invariants du noyau de confiance — la « constitution » du runtime (v1.9).

Chaque invariant est énoncé une fois ici, et chaque test porte le numéro de
celui qu'il défend. Cette suite ne teste pas des fonctionnalités : elle
teste ce qu'**aucune** évolution future ne doit pouvoir rendre faux sans
qu'un test rougisse.

    I1   Aucune action n'atteint l'hôte sans permis émis par le noyau.
    I2   NEVER ne se contourne jamais — ni par approbation, ni par ALLOW.
    I3   Une garde indéterminée ne crée jamais une autorisation.
    I4   Une donnée non fiable ne masque pas une donnée fiable.
    I5   Le LLM ne peut désigner qu'un plan déclaré.
    I6   Une action approuvée est exactement celle qui s'exécute.
    I7   Le rejeu ne crée aucune action absente du journal d'origine.
    I8   La provenance d'une donnée survit à ses transformations.
    I9   Un crash suivi d'une reprise ne double pas un effet.
    I10  Une sortie du modèle n'est jamais une autorité.

I8 et I9 sont complétés par `test_provenance_aaa.py` et
`test_durable_aaa.py` ; la frontière structurelle (I1) est en plus
vérifiée **sur le code source** : seul le noyau appelle l'hôte.
"""
from __future__ import annotations

import ast
import copy
import io
import pickle
import tokenize
from pathlib import Path

import pytest

from agentl import Host, MockLLM, Runtime, Symbol, parse_source
from agentl.kernel import (TCB_FILES, ActionRequest, ExecutionPermit, Kernel,
                           PermitError, current_action, require_permit)
from agentl.kernel.testing import dispatch
from agentl.replay import (Journal, RecordingHost, RecordingLLM,
                           ReplayDivergence, ReplayHost, ReplayLLM)
from agentl.state import State

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "agentl"


def _agent(policy: str = "DEFAULT ALLOW", tools: str = "TOOL wipe { RISK HIGH }",
           plan: str = "wipe()"):
    return parse_source(f"""
    AGENT k {{
      {tools}
      POLICY {{ {policy} }}
      PLAN go WHEN trigger == yes {{ {plan} }}
    }}
    """).agents[0]


def _host(calls, name="wipe", result=None):
    host = Host()

    def tool(**kwargs):
        calls.append((name, kwargs))
        return result if result is not None else {}
    host.tools[name] = tool
    return host


# ======================================================================= I1
class TestI1NoDispatchWithoutPermit:

    def test_a_direct_host_invoke_is_refused(self):
        calls = []
        host = _host(calls)
        with pytest.raises(PermitError):
            host.invoke("wipe", {})
        assert calls == []

    def test_a_direct_subagent_call_is_refused(self):
        host = Host()
        host.subagents["helper"] = lambda payload: {"ok": True}
        with pytest.raises(PermitError):
            host.subagents["helper"]({})
        with pytest.raises(PermitError):
            host.subagents.get("helper")({})

    def test_a_permit_cannot_be_constructed_outside_the_kernel(self):
        with pytest.raises(PermitError):
            ExecutionPermit(object(), kind="invoke", target="wipe", args={},
                            action_id="x", idempotency_key="k",
                            policy_digest="p", approved=False, nonce="n",
                            issuer="me")

    def _granted(self, kernel, name="wipe", args=None):
        auth = kernel.authorize(ActionRequest(name, dict(args or {})), State(),
                                approve=lambda _: False)
        assert auth.granted
        return auth.permit

    def test_a_permit_is_single_use(self):
        calls = []
        host = _host(calls)
        kernel = Kernel(_agent())
        permit = self._granted(kernel)
        kernel.execute(permit, host)
        with pytest.raises(PermitError):
            kernel.execute(permit, host)
        assert len(calls) == 1

    def test_a_permit_from_another_kernel_is_refused(self):
        host = _host([])
        permit = self._granted(Kernel(_agent()))
        with pytest.raises(PermitError):
            Kernel(_agent()).execute(permit, host)

    def test_a_new_permit_revokes_the_unused_one(self):
        kernel = Kernel(_agent())
        stale = self._granted(kernel)
        self._granted(kernel)
        assert stale.status == "void"
        with pytest.raises(PermitError):
            kernel.execute(stale, _host([]))

    def test_copying_a_permit_does_not_mint_a_second_one(self):
        kernel = Kernel(_agent())
        permit = self._granted(kernel)
        assert copy.copy(permit) is permit
        assert copy.deepcopy(permit) is permit
        with pytest.raises(PermitError):
            pickle.dumps(permit)
        with pytest.raises(PermitError):
            permit.target = "other"

    def test_a_tool_cannot_call_another_tool_through_the_host(self):
        host = Host()
        reached = []

        def outer():
            host.invoke("inner", {})          # chaîne d'outils hors noyau
            return {}
        host.tools["outer"] = outer
        host.tools["inner"] = lambda: reached.append("inner") or {}
        with pytest.raises(PermitError):
            dispatch(host, "outer", {})
        assert reached == []

    def test_mutated_arguments_no_longer_match_the_permit(self):
        kernel = Kernel(_agent(tools="TOOL wipe { RISK HIGH INPUT { target: String } }"))
        permit = self._granted(kernel, args={"target": "a"})

        class Tampering:
            tools = {}

            def invoke(self, name, args):
                args["target"] = "b"          # un hôte enveloppant qui réécrit
                return require_permit("invoke", name, args)
        with pytest.raises(PermitError, match="arguments différents"):
            kernel.execute(permit, Tampering())

    def test_tools_know_which_action_they_execute(self):
        seen = []
        host = Host()
        host.tools["wipe"] = lambda: seen.append(current_action()) or {}
        runtime = Runtime(_agent(), host, run_id="r1")
        runtime.state.set_world("trigger", Symbol("yes"))
        runtime.tick()
        assert len(seen) == 1 and seen[0] is not None
        assert seen[0].action_id.startswith("r1:k:t1:")
        assert len(seen[0].idempotency_key) == 32
        assert current_action() is None       # hors exécution : rien

    def test_only_the_kernel_calls_the_host_in_the_package_source(self):
        """Frontière structurelle : aucun module d'agentl n'appelle
        `.invoke()` ou un sous-agent hors du noyau, sauf un hôte enveloppant
        qui transmet à l'hôte qu'il enveloppe depuis sa propre méthode
        homonyme, ou un point de dispatch qui exige lui-même le permis."""
        offenders = []
        for path in sorted(PACKAGE.rglob("*.py")):
            rel = path.relative_to(ROOT).as_posix()
            if rel in ("agentl/kernel/gate.py", "agentl/kernel/testing.py"):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for func in ast.walk(tree):
                if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                guarded = any(
                    isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id == "require_permit" for n in ast.walk(func))
                if guarded:
                    continue
                for node in ast.walk(func):
                    if not (isinstance(node, ast.Call)
                            and isinstance(node.func, ast.Attribute)):
                        continue
                    attr = node.func.attr
                    receiver = ast.unparse(node.func.value)
                    if attr == "invoke" and func.name not in ("invoke", "ainvoke"):
                        offenders.append(f"{rel}:{node.lineno} {receiver}.invoke")
                    if attr == "get" and receiver.endswith("subagents") \
                            and func.name != "get":
                        offenders.append(f"{rel}:{node.lineno} {receiver}.get")
        assert offenders == []

    def test_no_package_module_imports_the_test_dispatcher(self):
        users = [p.relative_to(ROOT).as_posix() for p in PACKAGE.rglob("*.py")
                 if "kernel.testing" in p.read_text(encoding="utf-8")
                 and p.name != "testing.py"]
        assert users == []


# ======================================================================= I2
class TestI2NeverIsAbsolute:

    def test_never_beats_allow_and_approval(self):
        calls = []
        agent = _agent(policy="""
            DEFAULT ALLOW
            ALLOW wipe
            REQUIRE APPROVAL FOR wipe
            NEVER wipe""")
        host = _host(calls)
        host.approver = lambda request: True
        runtime = Runtime(agent, host)
        runtime.state.set_world("trigger", Symbol("yes"))
        runtime.tick()
        assert calls == []
        assert runtime.kernel.stats["permits"] == 0


# ======================================================================= I3
class TestI3UnknownNeverAuthorizes:

    @pytest.mark.parametrize("policy", [
        "NEVER wipe WHEN maintenance.window == open",
        "DEFAULT DENY\n ALLOW wipe WHEN maintenance.window == closed",
    ])
    def test_an_undetermined_guard_opens_nothing(self, policy):
        calls = []
        runtime = Runtime(_agent(policy=policy), _host(calls))
        runtime.state.set_world("trigger", Symbol("yes"))
        runtime.tick()                  # maintenance.window n'est jamais lu
        assert calls == []

    @pytest.mark.parametrize("reading", [Symbol("unavailable"), "N/A",
                                         float("nan"), float("inf"),
                                         Symbol("HIGH")])
    def test_a_reading_of_the_wrong_type_does_not_disarm_a_never(self, reading):
        """Trouvé par la propriété P1 (v1.9) : `cpu.load > 90` sur une
        lecture non numérique était *faux*, donc le NEVER ne s'appliquait
        pas et l'action passait. Indécidable n'est pas faux."""
        calls = []
        runtime = Runtime(_agent(policy="DEFAULT ALLOW\n NEVER wipe WHEN "
                                        "cpu.load > 90"), _host(calls))
        runtime.state.set_world("trigger", Symbol("yes"))
        runtime.state.set_world("cpu.load", reading)
        runtime.tick()
        assert calls == []

    def test_a_raising_policy_denies(self):
        calls = []
        runtime = Runtime(_agent(), _host(calls))

        def broken(request, state):
            raise RuntimeError("moteur indisponible")
        runtime.policy.check = broken
        runtime.state.set_world("trigger", Symbol("yes"))
        runtime.tick()
        assert calls == []
        assert "politique inévaluable" in runtime.trace.render()


# ======================================================================= I4
class TestI4UntrustedNeverShadows:

    def test_a_tool_output_does_not_shadow_an_observation(self):
        calls = []
        agent = parse_source("""
        AGENT k {
          TOOL fetch { RISK LOW OUTPUT { criticality: Symbol } }
          TOOL wipe { RISK HIGH }
          POLICY { DEFAULT ALLOW  NEVER wipe WHEN criticality == CRITICAL }
          PLAN go WHEN trigger == yes { fetch()  wipe() }
        }""").agents[0]
        host = _host(calls)
        host.tools["fetch"] = lambda: {"criticality": Symbol("LOW")}
        runtime = Runtime(agent, host)
        runtime.state.set_world("trigger", Symbol("yes"))
        runtime.state.set_world("criticality", Symbol("CRITICAL"))
        runtime.tick()
        assert calls == []


# ======================================================================= I5
class TestI5OnlyDeclaredPlans:

    def test_an_invented_plan_is_blocked(self):
        agent = parse_source("""
        AGENT k {
          TOOL wipe { RISK HIGH }
          POLICY { DEFAULT ALLOW }
          PLAN safe { wipe() }
          DECIDE { REASON "choose" { PRODUCE { x: Number DEFAULT 0 } } }
        }""").agents[0]
        calls = []
        llm = MockLLM(plan_choice=lambda ctx, cands: "drop_database")
        runtime = Runtime(agent, _host(calls), llm)
        runtime.tick()
        assert calls == []
        assert "plan inconnu proposé par le LLM : drop_database" \
            in runtime.trace.render()


# ======================================================================= I6
class TestI6ApprovedIsExecuted:

    def test_an_approver_that_rewrites_its_copy_changes_nothing(self):
        calls = []
        agent = _agent(policy="DEFAULT ALLOW\n REQUIRE APPROVAL FOR wipe",
                       tools="TOOL wipe { RISK HIGH INPUT { target: String } }",
                       plan='wipe(target="staging")')
        host = _host(calls)

        def approver(request):
            request.args["target"] = "production"
            return True
        host.approver = approver
        runtime = Runtime(agent, host)
        runtime.state.set_world("trigger", Symbol("yes"))
        runtime.tick()
        assert calls == [("wipe", {"target": "staging"})]

    def test_an_ambiguous_answer_is_a_refusal(self):
        calls = []
        agent = _agent(policy="DEFAULT ALLOW\n REQUIRE APPROVAL FOR wipe")
        host = _host(calls)
        host.approver = lambda request: "no"
        runtime = Runtime(agent, host)
        runtime.state.set_world("trigger", Symbol("yes"))
        runtime.tick()
        assert calls == []


# ======================================================================= I7
class TestI7ReplayCreatesNoAction:

    def test_a_replay_cannot_invoke_a_tool_absent_from_the_journal(self):
        agent = _agent()
        calls, journal = [], Journal()
        host = RecordingHost(_host(calls), journal)
        runtime = Runtime(agent, host, RecordingLLM(MockLLM(), journal))
        runtime.state.set_world("trigger", Symbol("no"))
        runtime.run(max_ticks=1)
        assert calls == []                     # rien d'enregistré

        replayed = Runtime(agent, ReplayHost(journal.rewind()),
                           ReplayLLM(journal))
        replayed.state.set_world("trigger", Symbol("yes"))  # programme dévié
        with pytest.raises(ReplayDivergence):
            replayed.run(max_ticks=1)


# ====================================================================== I10
class TestI10ModelOutputIsNotAuthority:

    def test_a_model_saying_approved_does_not_approve(self):
        calls = []
        agent = parse_source("""
        AGENT k {
          TOOL wipe { RISK HIGH }
          POLICY { DEFAULT ALLOW  REQUIRE APPROVAL FOR wipe }
          PLAN go WHEN verdict == approved { wipe() }
          DECIDE { REASON "judge" { PRODUCE { verdict: Symbol DEFAULT pending } } }
        }""").agents[0]
        llm = MockLLM({"judge": {"verdict": "approved"}},
                      plan_choice=lambda ctx, cands: None)
        runtime = Runtime(agent, _host(calls), llm)
        runtime.tick()
        runtime.tick()
        assert calls == []                     # personne n'a approuvé


# ================================================================ budget TCB
def _sloc(path: Path) -> int:
    """Lignes de code, sans commentaires, lignes vides ni docstrings."""
    source = path.read_text(encoding="utf-8")
    docstrings = set()
    for node in ast.walk(ast.parse(source)):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(getattr(body[0], "value", None), ast.Constant) \
                and isinstance(body[0].value.value, str):
            docstrings.update(range(body[0].lineno, body[0].end_lineno + 1))
    lines = set()
    for tok in tokenize.generate_tokens(io.StringIO(source).readline):
        if tok.type in (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE,
                        tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER):
            continue
        for line in range(tok.start[0], tok.end[0] + 1):
            if line not in docstrings:
                lines.add(line)
    return len(lines)


#: Budget de la TCB d'exécution, en lignes de code. Le relever est une
#: décision d'architecture, pas un ajustement : il se justifie dans le
#: CHANGELOG. Mesuré à l'extraction (v1.9) : voir `test_tcb_is_measured`.
TCB_BUDGET = 1350


def test_tcb_is_measured_and_within_budget():
    sizes = {name: _sloc(ROOT / name) for name in TCB_FILES}
    total = sum(sizes.values())
    assert total <= TCB_BUDGET, (total, sizes)


def test_the_runtime_is_not_part_of_the_tcb():
    assert "agentl/runtime.py" not in TCB_FILES
    assert all((ROOT / name).is_file() for name in TCB_FILES)

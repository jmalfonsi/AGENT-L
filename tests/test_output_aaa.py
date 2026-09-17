"""Tests AAA de la COUCHE SORTIE/COMPOSITION d'AGENT-L.

Ce que voit l'auditeur et l'acheteur en démo : la visualisation (viz.py), la
trace HTML (trace_html.py), la société multi-agents (society.py) et la CLI
(cli.py). Un rapport d'audit qui ouvre une brèche XSS, ou une CLI qui recrache
un traceback nu sur un fichier absent, disqualifie le produit. Ces tests
verrouillent ces exigences.

Chaque test d'échappement échouait avant le correctif (payload brut présent /
traceback nu) et passe après.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "examples"))

from agentl import cli
from agentl.parser import parse_source, parse_file
from agentl.society import Society
from agentl.viz import build_program, render_html
from agentl.runtime import Trace, TraceEvent
from agentl.trace_html import render as render_trace


# --------------------------------------------------------------------------
# viz.py — le blob JSON est injecté dans un <script>. Aucune valeur de trace
# ne doit pouvoir fermer la balise et injecter du HTML.
# --------------------------------------------------------------------------

_XSS = (
    'AGENT XSS_DEMO {\n'
    '  VERSION "1</script><script>alert(9)</script>"\n'
    '  DESCRIPTION "pwn </script><img src=x onerror=alert(1)> & <b>x</b>"\n'
    '  OBSERVE { server.cpu }\n'
    '  TOOL noop {\n'
    '    DESCRIPTION "tool </script><script>evil()</script>"\n'
    '    RISK LOW\n'
    '  }\n'
    '  GOAL g { MAINTAIN server.cpu < 90 }\n'
    '}\n'
)


def _viz_html(source: str) -> str:
    return render_html(build_program(parse_source(source)))


def _script_blob(html: str) -> str:
    """Le contenu JS entre « const G = » et le « ; » qui suit."""
    start = html.index("const G = ") + len("const G = ")
    return html[start:html.index(";\n", start)]


def test_viz_no_script_breakout_from_description_or_tool():
    html = _viz_html(_XSS)
    blob = _script_blob(html)
    # Le caractère « < » n'existe pas brut dans le blob : impossible d'ouvrir
    # ou de fermer une balise, donc aucune évasion du contexte <script>.
    assert "<" not in blob and ">" not in blob
    assert "</script>" not in blob
    assert "<img" not in blob and "<script>evil" not in blob
    # La donnée reste présente sous forme échappée (fidélité de l'audit).
    assert "\\u003c" in blob


def test_viz_no_script_breakout_from_version():
    html = _viz_html(_XSS)
    blob = _script_blob(html)
    assert "</script><script>alert(9)" not in blob
    # Le puits h1.innerHTML échappe nom et version (pas d'injection DOM).
    assert "esc(G.name)" in html
    assert "esc(G.version" in html


def test_viz_title_is_html_escaped():
    # Le titre atterrit dans <title>…</title> : jamais de < brut.
    html = _viz_html(
        'AGENT A {\n  VERSION "1"\n  DESCRIPTION "d"\n'
        '  OBSERVE { x.y }\n}\n'
    )
    title = html[html.index("<title>") + 7:html.index("</title>")]
    assert "<" not in title and ">" not in title


def test_viz_line_and_paragraph_separators_are_neutralised():
    # U+2028 / U+2029 cassent un litteral JS s'ils sont laisses bruts. On les
    # injecte directement dans le graphe (independamment du lexer) pour verifier
    # qu'ils ressortent echappes.
    ls, ps = chr(0x2028), chr(0x2029)
    graph = {
        "name": "A", "version": "1",
        "description": "avant" + ls + "milieu" + ps + "fin",
        "multi": False, "lanes": [], "bands": [], "width": 100, "height": 100,
        "row_h": 158, "node_w": 232, "nodes": [], "edges": [],
    }
    blob = _script_blob(render_html(graph))
    assert ls not in blob and ps not in blob
    assert "\\u2028" in blob and "\\u2029" in blob


def test_viz_renders_real_examples_wellformed():
    for name in ("soc_risk", "disk_sentinel", "K8S_HEALER", "soc_team"):
        html = render_html(build_program(parse_file(
            str(ROOT / "examples" / f"{name}.agent"))))
        assert html.lstrip().startswith("<!doctype html>")
        assert html.rstrip().endswith("</html>")
        assert "__DATA__" not in html and "__TITLE__" not in html


# --------------------------------------------------------------------------
# trace_html.py — chaque valeur de trace (observation, nom d'outil, détail,
# métrique) doit être échappée : une observation contenant <script> est du
# HTML inerte dans le rapport.
# --------------------------------------------------------------------------

class _FakeAgent:
    name = "A</script><x>"
    description = "d </script><script>x()</script>"
    tools = [type("T", (), {"name": "t<script>"})()]
    goals: list = []
    hypotheses: list = []
    policies: list = []
    policy_default = "ALLOW"


class _FakeRuntime:
    def __init__(self, trace, agent):
        self.trace = trace
        self.agent = agent
        self.metrics = {"ticks": 1, "tool_calls": 1, "blocked": 1}


def test_trace_html_escapes_malicious_values():
    tr = Trace()
    tr.events = [
        TraceEvent(1, "TICK", ""),
        TraceEvent(1, "OBSERVE", "host=</script><script>alert(1)</script>",
                   "<img src=x onerror=alert(2)>"),
        TraceEvent(1, "TOOL", "wipe(<b>x</b>=</script>evil, k=a&b)"),
    ]
    agent = _FakeAgent()
    html = render_trace(_FakeRuntime(tr, agent), agent)
    # Aucun payload actif ne survit brut.
    assert "</script><script>alert(1)" not in html
    assert "<img src=x onerror" not in html
    assert "<script>x()</script>" not in html
    # Les formes échappées sont bien là (fidélité).
    assert "&lt;/script&gt;" in html
    # Bornes du document.
    assert html.lstrip().startswith("<!doctype html>")
    assert html.rstrip().endswith("</html>")


# --------------------------------------------------------------------------
# cli.py — un fichier absent/illisible ou un hôte fautif donne une erreur
# propre et un code de sortie non nul, jamais un traceback nu.
# --------------------------------------------------------------------------

def test_cli_missing_agent_file_clean_error(capsys):
    for cmd in ("check", "ast", "viz", "verify"):
        rc = cli.main([cmd, "/definitely/not/here.agent"])
        err = capsys.readouterr().err
        assert rc != 0, f"{cmd} devrait sortir non nul"
        assert "✗" in err
        assert "Traceback" not in err


def _programme_sans_hote(tmp_path):
    """Copie un programme valide dans un répertoire dépourvu de son hôte."""
    src = (ROOT / "examples" / "soc_risk.agent").read_text(encoding="utf-8")
    target = tmp_path / "orphelin.agent"
    target.write_text(src, encoding="utf-8")
    return target


def test_cli_run_missing_host_clean_error(capsys, tmp_path):
    """Norme de nommage : `X.agent` sans `X.py` ⇒ refus net, pas d'exécution."""
    rc = cli.main(["run", str(_programme_sans_hote(tmp_path))])
    err = capsys.readouterr().err
    assert rc == 2
    assert "hôte" in err and "Traceback" not in err
    assert "orphelin.py" in err        # le message nomme le fichier attendu


def test_cli_plan_tolerates_missing_host(capsys, tmp_path):
    """`plan` n'agit pas sur le monde : il reste utilisable en monde vide."""
    rc = cli.main(["plan", str(_programme_sans_hote(tmp_path))])
    out = capsys.readouterr()
    assert rc == 0
    assert "hôte absent" in out.err
    assert "Traceback" not in out.err


def test_cli_broken_host_module_clean_error(capsys, tmp_path):
    # Un module hôte qui lève à l'import ne doit pas remonter de traceback nu.
    agent = _programme_sans_hote(tmp_path)
    agent.with_suffix(".py").write_text("raise RuntimeError('boom au chargement')\n",
                                        encoding="utf-8")
    rc = cli.main(["run", str(agent)])
    err = capsys.readouterr().err
    assert rc == 2
    assert "Traceback" not in err
    assert "boom au chargement" in err


def test_cli_run_rejects_host_option(capsys):
    """L'option a disparu : l'hôte est déduit, il ne se choisit plus."""
    import pytest

    with pytest.raises(SystemExit):
        cli.main(["run", str(ROOT / "examples" / "soc_risk.agent"),
                  "--host", "/no/such/host.py"])


def test_cli_viz_standalone_missing_file_clean_error(capsys):
    from agentl import viz
    rc = viz.main(["/definitely/not/here.agent"])
    err = capsys.readouterr().err
    assert rc != 0
    assert "✗" in err and "Traceback" not in err


# --------------------------------------------------------------------------
# society.py — remise des messages, destinataire inconnu, terminaison.
# --------------------------------------------------------------------------

def _team():
    prog = parse_file(str(ROOT / "examples" / "soc_team.agent"))
    return prog, Society(prog.agents)


def test_society_broadcast_and_addressed_delivery():
    prog, soc = _team()
    names = [a.name for a in prog.agents]
    sender = names[0]
    # Message adressé : atterrit dans l'inbox du destinataire, pas de l'émetteur.
    n = soc.send(sender, "ping", {"x": 1}, to=names[1])
    assert n == 1
    assert len(soc.runtimes[names[1]].inbox) == 1
    assert soc.runtimes[names[1]].inbox[0]["from"] == sender
    assert len(soc.runtimes[sender].inbox) == 0
    # Diffusion : tous sauf l'émetteur.
    m = soc.send(sender, "hello", {})
    assert m == len(names) - 1


def test_society_unknown_recipient_is_logged_not_crashed():
    prog, soc = _team()
    sender = prog.agents[0].name
    before = len(soc.trace.events)
    n = soc.send(sender, "ping", {}, to="nobody_here")
    assert n == 0
    assert len(soc.trace.events) == before + 1
    assert soc.trace.events[-1].kind == "ERROR"


def test_society_run_terminates_and_reports():
    prog, soc = _team()
    soc.run(max_ticks=6)
    # La société expose des métriques agrégées cohérentes.
    m = soc.metrics
    assert "messages_in_flight" in m and m["messages_in_flight"] >= 0
    assert "shared_keys" in m
    # Les rendus texte ne lèvent pas.
    assert isinstance(soc.render_messages(), str)
    assert isinstance(soc.render_shared(), str)
    assert isinstance(soc.render_traces(), str)

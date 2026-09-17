"""Rendu HTML autonome d'une trace d'exécution.

`agentl run --html sortie.html` produit un journal visuel de tout ce que
l'agent a perçu, inféré, planifié et exécuté — chaque tick, chaque appel
d'outil avec ses paramètres, chaque décision de politique, chaque
décomposition bayésienne. Un seul fichier, sans dépendance ni ressource
externe : ouvrable hors ligne, joignable à un dossier d'audit.

    from agentl.trace_html import render
    Path("run.html").write_text(render(runtime, agent))
"""
from __future__ import annotations

import html
import re
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

if TYPE_CHECKING:                                    # pragma: no cover
    from .nodes import Agent
    from .runtime import Runtime, TraceEvent

# Chaque type d'événement appartient à un registre sémantique : c'est lui
# qui porte la couleur, donc le sens lu au premier coup d'œil.
GROUP: Dict[str, str] = {
    "OBSERVE": "perception", "BELIEF": "perception", "GOAL": "goal",
    "BAYES": "inference", "LLM": "reason",
    "PLANNER": "plan", "PLAN": "plan", "STEP": "plan",
    "TOOL": "action", "DELEGATE": "action", "MESSAGE": "action",
    "BLOCKED": "blocked", "ERROR": "blocked", "VERIFY_FAIL": "blocked",
    "APPROVAL": "approval", "ASK": "approval",
    "VERIFY_OK": "ok", "MEMORY": "memory", "SHARED": "memory",
    "EVENT": "signal", "RETRY": "signal", "INFO": "muted",
}
GLYPH: Dict[str, str] = {
    "OBSERVE": "◉", "BELIEF": "◆", "GOAL": "◎", "PLAN": "▶",
    "STEP": "·", "TOOL": "⚙", "BLOCKED": "⊘", "APPROVAL": "△",
    "VERIFY_OK": "✓", "VERIFY_FAIL": "✗", "LLM": "◈",
    "MEMORY": "▣", "EVENT": "⚡", "ASK": "?", "DELEGATE": "→",
    "ERROR": "‼", "INFO": "·", "RETRY": "↻", "BAYES": "∿",
    "PLANNER": "⌘", "MESSAGE": "✉", "SHARED": "⇄",
}
LABEL: Dict[str, str] = {
    "perception": "perception", "goal": "objectif", "inference": "inférence",
    "reason": "raisonnement", "plan": "planification", "action": "action",
    "blocked": "bloqué", "approval": "approbation", "ok": "vérifié",
    "memory": "mémoire", "signal": "signal", "muted": "info",
}
# Métriques mises en avant, dans un ordre qui raconte le run.
METRIC_ORDER = [
    ("ticks", "ticks"), ("tool_calls", "outils exécutés"),
    ("blocked", "actions bloquées"), ("approvals", "approbations"),
    ("llm_calls", "appels LLM"), ("inferences", "inférences"),
    ("plans_synthesized", "plans synthétisés"),
    ("domain_clamps", "sorties LLM écrêtées"),
    ("effect_drift", "dérives d'effet"), ("memory_writes", "écritures mémoire"),
    ("verify_pass", "vérifs OK"), ("verify_fail", "vérifs échouées"),
]
ALERT_METRICS = {"blocked", "domain_clamps", "effect_drift", "verify_fail",
                 "shared_conflicts"}

_CALL = re.compile(r"^([\w.]+)\((.*)\)$", re.S)


def _split_call(text: str) -> Optional[Tuple[str, List[Tuple[str, str]]]]:
    """« tool(host=PC-042, since=300) » → (« tool », [(host,PC-042), …])."""
    m = _CALL.match(text.strip())
    if not m:
        return None
    name, inside = m.group(1), m.group(2).strip()
    args: List[Tuple[str, str]] = []
    if inside:
        for part in _split_top(inside):
            key, sep, val = part.partition("=")
            args.append((key.strip(), val.strip()) if sep else ("", part.strip()))
    return name, args


def _split_top(s: str) -> List[str]:
    """Découpe sur les virgules de premier niveau (ignore {}, [], ())."""
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch in "{[(":
            depth += 1
        elif ch in "}])":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return out


def _e(s: object) -> str:
    return html.escape(str(s), quote=True)


def _event_html(ev: "TraceEvent") -> str:
    group = GROUP.get(ev.kind, "muted")
    glyph = GLYPH.get(ev.kind, "·")
    kind_label = LABEL.get(group, group)
    call = _split_call(ev.text) if ev.kind in ("TOOL", "DELEGATE") else None

    body = [f'<span class="ev-glyph" aria-hidden="true">{_e(glyph)}</span>',
            f'<span class="ev-kind">{_e(kind_label)}</span>']

    if call and call[1]:
        name, args = call
        body.append(f'<span class="ev-text"><span class="call-name">'
                    f'{_e(name)}</span></span>')
        params = "".join(
            (f'<span class="param"><span class="param-k">{_e(k)}</span>'
             f'<span class="param-v">{_e(v)}</span></span>') if k else
            f'<span class="param"><span class="param-v">{_e(v)}</span></span>'
            for k, v in args)
        body.append(f'<span class="params">{params}</span>')
    else:
        body.append(f'<span class="ev-text">{_e(ev.text)}</span>')

    if ev.detail:
        body.append(f'<span class="ev-detail">{_e(ev.detail)}</span>')

    stripe = group in ("blocked", "approval", "ok", "inference")
    cls = f'ev ev-{group}{" ev-stripe" if stripe else ""}'
    return (f'<div class="{cls}" data-group="{group}">'
            + "".join(body) + "</div>")


def _tick_groups(events: List["TraceEvent"]):
    groups, current = [], None
    for ev in events:
        if ev.kind == "TICK":
            current = {"tick": ev.tick, "events": []}
            groups.append(current)
        else:
            if current is None:
                current = {"tick": None, "events": []}
                groups.append(current)
            current["events"].append(ev)
    return [g for g in groups if g["events"]]


def _metric_tiles(metrics: Dict[str, int]) -> str:
    tiles = []
    for key, label in METRIC_ORDER:
        if key not in metrics:
            continue
        val = metrics[key]
        alert = " tile-alert" if key in ALERT_METRICS and val else ""
        tiles.append(
            f'<div class="tile{alert}"><div class="tile-val">{val}</div>'
            f'<div class="tile-label">{_e(label)}</div></div>')
    return "".join(tiles)


def _expr(node) -> str:
    """Rend une expression de garde AGENT-L en texte lisible."""
    if node is None:
        return ""
    cls = type(node).__name__
    if cls == "Literal":
        return str(getattr(node, "value", node))
    if cls == "PathExpr":
        return node.dotted
    if cls == "ListExpr":
        return "[" + ", ".join(_expr(i) for i in node.items) + "]"
    if cls == "BinOp":
        return f"{_expr(node.left)} {node.op} {_expr(node.right)}"
    if cls == "UnOp":
        return f"{node.op} {_expr(node.operand)}"
    if cls == "CallExpr":
        inside = ", ".join(_expr(a) for a in node.args)
        for k, v in getattr(node, "kwargs", {}).items():
            inside += f"{', ' if inside else ''}{k}={_expr(v)}"
        return f"{node.name}({inside})"
    return str(node)


_EFFECT = {
    "ALLOW": ("ok", "autorise"), "NEVER": ("blocked", "jamais"),
    "REQUIRE_APPROVAL": ("approval", "approbation"), "DENY": ("muted", "refuse"),
}


def _policy_html(agent: "Agent") -> str:
    rules = getattr(agent, "policies", None)
    if not rules:
        return ""
    default = getattr(agent, "policy_default", "ALLOW")
    dcls = "blocked" if default == "DENY" else "ok"
    head = (f'<div class="pol-default pol-{dcls}">par défaut : '
            f'<strong>{_e(default)}</strong> — '
            + ("rien n'est permis sans règle explicite" if default == "DENY"
               else "tout est permis sauf interdiction") + "</div>")
    rows = []
    for r in rules:
        group, label = _EFFECT.get(r.effect, ("muted", r.effect.lower()))
        kw = "SI" if r.effect == "ALLOW" else "QUAND"
        guard = _expr(r.guard) if r.guard is not None else ""
        cond = (f'<span class="pol-kw">{kw}</span>'
                f'<span class="pol-guard">{_e(guard)}</span>') if guard else \
               '<span class="pol-guard pol-uncond">sans condition</span>'
        rows.append(
            f'<div class="pol-rule pol-{group}">'
            f'<span class="pol-badge">{_e(label)}</span>'
            f'<span class="pol-target">{_e(r.target)}</span>'
            f'<span class="pol-cond">{cond}</span></div>')
    return (f'<section class="policy"><div class="pol-title">Politique '
            f'<span class="pol-sub">— le contrat que le runtime fait '
            f'respecter, indépendamment du LLM</span></div>'
            f'{head}<div class="pol-rules">{"".join(rows)}</div></section>')


def _agent_meta(agent: "Agent") -> str:
    if agent is None:
        return ""
    tools = ", ".join(t.name for t in agent.tools) or "—"
    goals = ", ".join(g.name for g in agent.goals) or "—"
    hyps = ", ".join(h.name for h in getattr(agent, "hypotheses", [])) or "—"
    rows = [("outils", tools), ("objectifs", goals), ("hypothèses", hyps)]
    return "".join(
        f'<div class="meta-row"><span class="meta-k">{_e(k)}</span>'
        f'<span class="meta-v">{_e(v)}</span></div>' for k, v in rows)


def _filter_chips() -> str:
    order = ["perception", "inference", "reason", "plan", "action",
             "approval", "blocked", "ok", "memory", "signal", "goal", "muted"]
    chips = []
    for g in order:
        chips.append(
            f'<button class="chip chip-{g}" data-toggle="{g}" '
            f'aria-pressed="true">{_e(LABEL.get(g, g))}</button>')
    return "".join(chips)


def render(runtime: "Runtime", agent: "Agent" = None,
           title: Optional[str] = None) -> str:
    agent = agent or getattr(runtime, "agent", None)
    name = getattr(agent, "name", "AGENT") if agent else "AGENT"
    desc = (getattr(agent, "description", "") or "") if agent else ""
    title = title or f"{name} — trace d'exécution"
    metrics = getattr(runtime, "metrics", {})
    groups = _tick_groups(runtime.trace.events)

    ticks_html = []
    for g in groups:
        tick = g["tick"]
        badge = f"tick {tick}" if tick is not None else "amorçage"
        rows = "".join(_event_html(ev) for ev in g["events"])
        ticks_html.append(
            f'<section class="tickcard">'
            f'<div class="tick-spine"><span class="tick-node">{_e(badge)}</span></div>'
            f'<div class="tick-body">{rows}</div></section>')

    return _PAGE.format(
        title=_e(title), name=_e(name), desc=_e(desc),
        meta=_agent_meta(agent), tiles=_metric_tiles(metrics),
        policy=_policy_html(agent) if agent else "",
        chips=_filter_chips(), ticks="".join(ticks_html),
        raw=_e("  ".join(f"{k}={v}" for k, v in metrics.items())),
    )


_PAGE = """<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{
  --paper:#e9ecf1; --surface:#f7f9fc; --surface-2:#eef1f6; --ink:#0e1420;
  --muted:#5b6675; --hair:#d3d9e2; --hair-strong:#b9c2ce;
  --perception:#4f647e; --inference:#7a54d8; --reason:#3f63dd; --plan:#4a5568;
  --action:#0d8f83; --blocked:#d83a3f; --approval:#c67a1e; --ok:#1f9152;
  --memory:#9a6f2e; --signal:#0d7d9e; --goal:#5a4b8a;
  --shadow:0 1px 2px rgba(14,20,32,.06),0 8px 24px rgba(14,20,32,.05);
  --mono:ui-monospace,"SF Mono","JetBrains Mono","Cascadia Code",Menlo,Consolas,monospace;
  --sans:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
}}
@media (prefers-color-scheme:dark) {{
  :root {{
    --paper:#0a0e15; --surface:#121926; --surface-2:#0f1622; --ink:#e4eaf3;
    --muted:#8b97a8; --hair:#1e2836; --hair-strong:#2c384a;
    --perception:#8ba3c2; --inference:#a488f0; --reason:#7d97f5; --plan:#9aa6b6;
    --action:#39c3b4; --blocked:#f2686c; --approval:#e6a34d; --ok:#48c07d;
    --memory:#d0a45c; --signal:#41b6d8; --goal:#a693e0;
    --shadow:0 1px 2px rgba(0,0,0,.4),0 12px 32px rgba(0,0,0,.35);
  }}
}}
:root[data-theme="light"] {{
  --paper:#e9ecf1; --surface:#f7f9fc; --surface-2:#eef1f6; --ink:#0e1420;
  --muted:#5b6675; --hair:#d3d9e2; --hair-strong:#b9c2ce;
  --perception:#4f647e; --inference:#7a54d8; --reason:#3f63dd; --plan:#4a5568;
  --action:#0d8f83; --blocked:#d83a3f; --approval:#c67a1e; --ok:#1f9152;
  --memory:#9a6f2e; --signal:#0d7d9e; --goal:#5a4b8a;
}}
:root[data-theme="dark"] {{
  --paper:#0a0e15; --surface:#121926; --surface-2:#0f1622; --ink:#e4eaf3;
  --muted:#8b97a8; --hair:#1e2836; --hair-strong:#2c384a;
  --perception:#8ba3c2; --inference:#a488f0; --reason:#7d97f5; --plan:#9aa6b6;
  --action:#39c3b4; --blocked:#f2686c; --approval:#e6a34d; --ok:#48c07d;
  --memory:#d0a45c; --signal:#41b6d8; --goal:#a693e0;
}}
* {{ box-sizing:border-box; }}
body {{
  margin:0; background:var(--paper); color:var(--ink); font-family:var(--sans);
  line-height:1.5; -webkit-font-smoothing:antialiased;
}}
.wrap {{ max-width:1080px; margin:0 auto; padding:32px 20px 96px; }}
header.masthead {{ margin-bottom:28px; }}
.eyebrow {{
  font-size:11px; letter-spacing:.16em; text-transform:uppercase;
  color:var(--muted); font-weight:600;
}}
h1 {{
  font-family:var(--mono); font-size:clamp(26px,4.5vw,40px); font-weight:600;
  margin:.28em 0 .1em; letter-spacing:-.01em; text-wrap:balance;
}}
.desc {{ color:var(--muted); max-width:64ch; margin:0 0 18px; }}
.meta {{
  display:flex; flex-wrap:wrap; gap:6px 24px; padding:14px 16px;
  background:var(--surface); border:1px solid var(--hair); border-radius:10px;
}}
.meta-row {{ display:flex; gap:10px; align-items:baseline; font-size:13px; min-width:0; }}
.meta-k {{
  font-size:10px; letter-spacing:.12em; text-transform:uppercase;
  color:var(--muted); font-weight:600; flex:none;
}}
.meta-v {{ font-family:var(--mono); font-size:12.5px; color:var(--ink); word-break:break-word; }}
.tiles {{
  display:grid; grid-template-columns:repeat(auto-fill,minmax(120px,1fr));
  gap:10px; margin:16px 0 8px;
}}
.tile {{
  background:var(--surface); border:1px solid var(--hair); border-radius:10px;
  padding:12px 14px; box-shadow:var(--shadow);
}}
.tile-val {{ font-family:var(--mono); font-size:24px; font-weight:600; font-variant-numeric:tabular-nums; }}
.tile-label {{ font-size:11px; color:var(--muted); margin-top:2px; }}
.tile-alert {{ border-color:color-mix(in srgb,var(--blocked) 55%,var(--hair)); }}
.tile-alert .tile-val {{ color:var(--blocked); }}
.policy {{
  margin:18px 0 4px; padding:16px 18px; background:var(--surface);
  border:1px solid var(--hair); border-radius:12px; box-shadow:var(--shadow);
}}
.pol-title {{ font-size:13px; font-weight:700; letter-spacing:.02em; }}
.pol-sub {{ font-weight:400; color:var(--muted); font-size:12.5px; }}
.pol-default {{
  font-size:12.5px; color:var(--muted); margin:8px 0 12px; padding:6px 10px;
  border-radius:7px; border-left:3px solid var(--gc,var(--muted));
  background:color-mix(in srgb,var(--gc,var(--muted)) 7%,transparent);
}}
.pol-default.pol-blocked {{ --gc:var(--blocked); }}
.pol-default.pol-ok {{ --gc:var(--ok); }}
.pol-default strong {{ font-family:var(--mono); color:var(--gc,var(--ink)); }}
.pol-rules {{ display:flex; flex-direction:column; gap:6px; }}
.pol-rule {{
  display:flex; flex-wrap:wrap; align-items:baseline; gap:10px;
  padding:7px 12px; border-radius:8px; --gc:var(--muted);
  background:color-mix(in srgb,var(--gc) 6%,transparent);
  border-left:3px solid var(--gc);
}}
.pol-ok {{ --gc:var(--ok); }} .pol-blocked {{ --gc:var(--blocked); }}
.pol-approval {{ --gc:var(--approval); }} .pol-muted {{ --gc:var(--muted); }}
.pol-badge {{
  font-size:9.5px; letter-spacing:.1em; text-transform:uppercase; font-weight:700;
  color:var(--gc); flex:none; min-width:88px;
}}
.pol-target {{ font-family:var(--mono); font-size:13px; font-weight:600; color:var(--ink); flex:none; }}
.pol-cond {{ font-family:var(--mono); font-size:12px; color:var(--muted); display:inline-flex; gap:7px; align-items:baseline; }}
.pol-kw {{ font-size:9.5px; letter-spacing:.1em; text-transform:uppercase; font-weight:700; color:var(--gc); }}
.pol-guard {{ color:var(--ink); }}
.pol-uncond {{ color:var(--muted); font-style:italic; }}
.toolbar {{
  position:sticky; top:0; z-index:5; display:flex; flex-wrap:wrap; gap:6px;
  align-items:center; padding:12px 0; margin:12px 0 8px;
  background:linear-gradient(var(--paper) 72%,transparent);
  backdrop-filter:blur(4px);
}}
.toolbar .tb-label {{
  font-size:10px; letter-spacing:.12em; text-transform:uppercase;
  color:var(--muted); font-weight:600; margin-right:4px;
}}
.chip {{
  font:inherit; font-size:12px; cursor:pointer; padding:4px 11px;
  border-radius:999px; border:1px solid var(--hair-strong);
  background:var(--surface); color:var(--muted); position:relative;
  transition:background .12s,color .12s,border-color .12s;
}}
.chip::before {{
  content:""; display:inline-block; width:7px; height:7px; border-radius:50%;
  margin-right:6px; vertical-align:middle; background:var(--gc,var(--muted));
}}
.chip[aria-pressed="true"] {{ color:var(--ink); border-color:var(--gc,var(--hair-strong)); }}
.chip[aria-pressed="false"] {{ opacity:.42; }}
.chip-perception{{--gc:var(--perception);}} .chip-inference{{--gc:var(--inference);}}
.chip-reason{{--gc:var(--reason);}} .chip-plan{{--gc:var(--plan);}}
.chip-action{{--gc:var(--action);}} .chip-approval{{--gc:var(--approval);}}
.chip-blocked{{--gc:var(--blocked);}} .chip-ok{{--gc:var(--ok);}}
.chip-memory{{--gc:var(--memory);}} .chip-signal{{--gc:var(--signal);}}
.chip-goal{{--gc:var(--goal);}} .chip-muted{{--gc:var(--muted);}}
.tickcard {{ display:grid; grid-template-columns:104px 1fr; gap:0; position:relative; }}
.tick-spine {{ position:relative; padding-top:14px; }}
.tick-spine::before {{
  content:""; position:absolute; left:14px; top:0; bottom:-4px; width:2px;
  background:var(--hair);
}}
.tickcard:last-child .tick-spine::before {{ bottom:auto; height:34px; }}
.tick-node {{
  position:relative; display:inline-block; font-family:var(--mono);
  font-size:11px; font-weight:600; color:var(--muted); background:var(--paper);
  border:2px solid var(--hair-strong); border-radius:999px; padding:3px 9px 3px 22px;
  letter-spacing:.02em;
}}
.tick-node::before {{
  content:""; position:absolute; left:7px; top:50%; transform:translateY(-50%);
  width:8px; height:8px; border-radius:50%; background:var(--reason);
}}
.tick-body {{
  padding:10px 0 22px; display:flex; flex-direction:column; gap:2px; min-width:0;
}}
.ev {{
  display:flex; flex-wrap:wrap; align-items:baseline; gap:8px;
  padding:6px 12px; border-radius:8px; min-width:0;
  --gc:var(--muted);
}}
.ev:hover {{ background:var(--surface-2); }}
.ev-perception{{--gc:var(--perception);}} .ev-inference{{--gc:var(--inference);}}
.ev-reason{{--gc:var(--reason);}} .ev-plan{{--gc:var(--plan);}}
.ev-action{{--gc:var(--action);}} .ev-approval{{--gc:var(--approval);}}
.ev-blocked{{--gc:var(--blocked);}} .ev-ok{{--gc:var(--ok);}}
.ev-memory{{--gc:var(--memory);}} .ev-signal{{--gc:var(--signal);}}
.ev-goal{{--gc:var(--goal);}} .ev-muted{{--gc:var(--muted);}}
.ev-stripe {{
  border-left:3px solid var(--gc); background:color-mix(in srgb,var(--gc) 7%,transparent);
  padding-left:12px;
}}
.ev-stripe:hover {{ background:color-mix(in srgb,var(--gc) 12%,transparent); }}
.ev-glyph {{ color:var(--gc); font-size:14px; flex:none; width:16px; text-align:center; }}
.ev-kind {{
  font-size:9.5px; letter-spacing:.1em; text-transform:uppercase; font-weight:700;
  color:var(--gc); flex:none; min-width:74px;
}}
.ev-text {{ font-family:var(--mono); font-size:13px; color:var(--ink); word-break:break-word; }}
.call-name {{ color:var(--gc); font-weight:600; }}
.params {{ display:inline-flex; flex-wrap:wrap; gap:5px; }}
.param {{
  display:inline-flex; align-items:stretch; border-radius:6px; overflow:hidden;
  border:1px solid var(--hair-strong); font-family:var(--mono); font-size:11.5px;
}}
.param-k {{ background:var(--surface-2); color:var(--muted); padding:1px 6px; }}
.param-v {{ padding:1px 7px; color:var(--ink); font-weight:600; background:var(--surface); }}
.ev-detail {{
  font-family:var(--mono); font-size:11.5px; color:var(--muted);
  flex:1 1 100%; padding-left:24px; white-space:pre-wrap; word-break:break-word;
}}
.ev[hidden] {{ display:none; }}
footer.raw {{
  margin-top:24px; padding:14px 16px; background:var(--surface);
  border:1px solid var(--hair); border-radius:10px; font-family:var(--mono);
  font-size:12px; color:var(--muted); overflow-x:auto; white-space:nowrap;
}}
.legend {{
  margin-top:10px; font-size:12px; color:var(--muted);
}}
@media (max-width:640px) {{
  .tickcard {{ grid-template-columns:64px 1fr; }}
  .tick-spine::before {{ left:9px; }}
  .tick-node {{ font-size:0; padding:0; width:20px; height:20px; }}
  .tick-node::before {{ left:50%; transform:translate(-50%,-50%); }}
  .ev-kind {{ min-width:0; }}
}}
</style>
</head>
<body>
<div class="wrap">
  <header class="masthead">
    <div class="eyebrow">AGENT-L · journal d'exécution</div>
    <h1>{name}</h1>
    <p class="desc">{desc}</p>
    <div class="meta">{meta}</div>
    <div class="tiles">{tiles}</div>
  </header>
  {policy}
  <div class="toolbar" role="group" aria-label="Filtrer par catégorie">
    <span class="tb-label">filtrer</span>{chips}
  </div>
  <main id="stream">{ticks}</main>
  <footer class="raw">{raw}</footer>
  <p class="legend">Chaque ligne est un événement réel du runtime. Les
  actions bloquées, approbations et vérifications portent un bandeau de
  couleur ; les paramètres d'outil sont ceux passés à l'hôte.</p>
</div>
<script>
(function() {{
  var off = Object.create(null);
  document.querySelectorAll(".chip").forEach(function(btn) {{
    btn.addEventListener("click", function() {{
      var g = btn.dataset.toggle, on = btn.getAttribute("aria-pressed") !== "true";
      btn.setAttribute("aria-pressed", on ? "true" : "false");
      off[g] = !on;
      document.querySelectorAll('.ev[data-group="' + g + '"]').forEach(function(el) {{
        el.hidden = !on;
      }});
    }});
  }});
}})();
</script>
</body>
</html>
"""

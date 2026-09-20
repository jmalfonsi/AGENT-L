r"""
agentl.viz — traduction VISUELLE d'un programme .agent en un graphe de flux
interactif, style N8N (noeuds deplacables, connexions beziers, lanes, panneau
de detail). [docstring en texte simple pour eviter les sequences d'echappement]

    python3 -m agentl.viz examples/soc_analyst.agent            # -> soc_analyst.html
    python3 -m agentl.viz examples/soc_analyst.agent out.html
    python3 -m agentl.viz examples/*.agent --dir build/

Le graphe rend explicite la chaîne cognitive d'AGENT-L :

    OBSERVE  ->  HYPOTHESIS  ->  GOAL  ->  DECIDE/EVENT  ->  PLAN  ->  TOOL
       (perception)  (inférence)          (déclenchement)  (stratégie) (action)
                         \___________ POLICY encadre les TOOL ___________/

Aucune dépendance externe : le HTML produit est autonome (SVG + JS inline).
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import fields, is_dataclass
from html import escape as _html_escape

from . import parser as P
from . import nodes as N


# --------------------------------------------------------------------------
# Extraction : parcours générique de l'AST d'expressions
# --------------------------------------------------------------------------

def _walk(node):
    """Émet récursivement tous les sous-nœuds de l'AST (dataclasses, listes, dicts)."""
    if node is None:
        return
    if is_dataclass(node):
        yield node
        for f in fields(node):
            yield from _walk(getattr(node, f.name))
    elif isinstance(node, (list, tuple)):
        for x in node:
            yield from _walk(x)
    elif isinstance(node, dict):
        for x in node.values():
            yield from _walk(x)


def _paths(expr):
    """Ensemble des chemins pointés (ex. 'wazuh.alert_count') d'une expression."""
    out = set()
    for nd in _walk(expr):
        if isinstance(nd, N.PathExpr):
            out.add(".".join(nd.parts))
    return out


def _calls(expr):
    """Noms des appels de fonction/outil rencontrés (ex. P, supported, query_wazuh)."""
    out = []
    for nd in _walk(expr):
        if isinstance(nd, N.CallExpr):
            out.append((nd.name, [".".join(a.parts) if isinstance(a, N.PathExpr)
                                   else getattr(a, "value", "…") for a in nd.args]))
    return out


def _root(path: str) -> str:
    return path.split(".")[0]


def _fmt_expr(expr) -> str:
    """Rendu texte compact d'une expression (pour l'affichage)."""
    if expr is None:
        return ""
    if isinstance(expr, N.BinOp):
        return f"{_fmt_expr(expr.left)} {expr.op} {_fmt_expr(expr.right)}"
    if isinstance(expr, N.UnOp):
        return f"{expr.op} {_fmt_expr(expr.operand)}" if hasattr(expr, "operand") else expr.op
    if isinstance(expr, N.PathExpr):
        return ".".join(expr.parts)
    if isinstance(expr, N.CallExpr):
        args = ", ".join(_fmt_expr(a) for a in expr.args)
        return f"{expr.name}({args})"
    if isinstance(expr, N.Literal):
        return str(expr.value)
    return str(expr)


# --------------------------------------------------------------------------
# Construction du modèle de graphe
# --------------------------------------------------------------------------

LANES = ["observe", "hypothesis", "goal", "trigger", "plan", "tool", "policy", "society"]
LANE_LABEL = {
    "observe": "PERCEPTION · OBSERVE",
    "hypothesis": "INFÉRENCE · HYPOTHESIS",
    "goal": "OBJECTIFS · GOAL",
    "trigger": "DÉCLENCHEMENT · DECIDE / EVENT",
    "plan": "STRATÉGIE · PLAN",
    "tool": "ACTION · TOOL",
    "policy": "GARDE-FOUS · POLICY",
    "society": "SOCIÉTÉ · MESSAGE / DELEGATE",
}

# Géométrie (calculée côté Python pour un éclatement net des lanes) -----------
LANE_W = 320       # largeur d'une colonne de lane
NODE_W = 232       # largeur d'un nœud
ROW_H = 158        # hauteur d'une rangée dans une lane
BAND_HEAD = 52     # bandeau titre d'un agent (multi-agents)
BAND_PAD = 40      # marge basse d'une bande d'agent
BAND_GAP = 48      # espace entre deux bandes d'agent


def _build_agent(agent, prefix, nodes, edges, reg):
    """Construit les nœuds/liens d'UN agent (sans coordonnées). Alimente `reg`
    avec les points d'entrée (inbox) et sorties (send) pour le câblage inter-agents."""
    nid = {}

    def add(kind, key, label, lane, detail):
        i = f"{prefix}{lane}:{key}"
        nid[(lane, key)] = i
        nodes.append({"id": i, "kind": kind, "label": label,
                      "lane": lane, "agent": agent.name, "detail": detail})
        return i

    def link(a, b, kind="", label=""):
        if a and b:
            edges.append({"from": a, "to": b, "kind": kind, "label": label})

    # -- OBSERVE ------------------------------------------------------------
    observed = {}
    for o in agent.observers:
        d = []
        if o.when:
            d.append("quand " + _fmt_expr(o.when))
        observed[o.path] = add("observe", o.path, o.path, "observe",
                               {"lines": d, "root": _root(o.path)})

    # -- HYPOTHESIS ---------------------------------------------------------
    hyps = {}
    for h in agent.hypotheses:
        ev_lines = []
        for e in h.evidence:
            g = f"[{e.group}] " if getattr(e, "group", None) else ""
            ev_lines.append(f"{g}{_fmt_expr(e.test)}  →  L={e.likelihood} / L̄={e.given_not}")
        detail = {
            "description": h.description or "",
            "prior": h.prior,
            "threshold": h.threshold,
            "explains": (h.explains_path or ""),
            "evidence": ev_lines,
        }
        hyps[h.name] = add("hypothesis", h.name, h.name, "hypothesis", detail)
        for e in h.evidence:
            for p in _paths(e.test):
                for op in observed:
                    if _root(op) == _root(p):
                        link(observed[op], hyps[h.name], "evidence")

    # -- GOAL ---------------------------------------------------------------
    goals = {}
    for g in agent.goals:
        cond = _fmt_expr(g.condition)
        detail = {
            "mode": g.mode,
            "condition": cond,
            "weight": g.weight,
            "targets": [_fmt_expr(t) for t in (g.targets or [])],
        }
        goals[g.name] = add("goal", g.name, g.name, "goal", detail)
        for p in _paths(g.condition):
            for op in observed:
                if _root(op) == _root(p):
                    link(observed[op], goals[g.name], "state")

    # -- TOOL ---------------------------------------------------------------
    tools = {}
    effect_by_root = {}
    for t in agent.tools:
        risk = t.risk if isinstance(t.risk, str) else (
            ", ".join(f"{k}={v}" for k, v in t.risk.items()) if isinstance(t.risk, dict) else "")
        eff = [f"{e.path} = {_fmt_expr(e.value)}" for e in (t.effects or [])]
        detail = {
            "description": t.description or "",
            "risk": risk,
            "cost": t.cost,
            "inputs": [f"{k}: {v}" for k, v in (t.inputs or {}).items()] if isinstance(t.inputs, dict)
                      else list(t.inputs or []),
            "requires": _fmt_expr(t.requires) if t.requires else "",
            "effects": eff,
            "side_effects": list(t.side_effects or []),
        }
        tools[t.name] = add("tool", t.name, t.name, "tool", detail)
        for e in (t.effects or []):
            effect_by_root.setdefault(_root(e.path), []).append(t.name)
            for g in agent.goals:
                if _root(e.path) in {_root(p) for p in _paths(g.condition)}:
                    link(tools[t.name], goals[g.name], "achieves", "EFFECT")
    for t in agent.tools:
        if not t.requires:
            continue
        for p in _paths(t.requires):
            for producer in effect_by_root.get(_root(p), []):
                if producer != t.name:
                    link(tools[producer], tools[t.name], "requires", "REQUIRES")

    # -- PLAN + STEPS (+ société : MESSAGE / DELEGATE dans les étapes) -------
    plans = {}
    first_plan = None
    msg_i = 0
    for pl in agent.plans:
        steps = []
        called, sends = set(), []
        for s in pl.steps:
            acts = []
            for b in s.body:
                if isinstance(b, N.CallStmt):
                    acts.append(f"↳ {b.call.name}()")
                    called.add(b.call.name)
                elif isinstance(b, N.ReasonStmt):
                    prod = ", ".join(b.produce.keys()) if isinstance(b.produce, dict) else ""
                    acts.append(f"🧠 {b.keyword} → {prod}")
                elif isinstance(b, N.SetStmt):
                    acts.append(f"= SET {getattr(b, 'path', getattr(b, 'target', ''))}")
                elif isinstance(b, N.AskStmt):
                    acts.append("🙋 ASK opérateur")
                elif isinstance(b, N.MessageStmt):
                    tgt = "broadcast" if b.broadcast else (b.to or "?")
                    acts.append(f"✉ SEND {b.name} → {tgt}")
                    sends.append(("message", b.name, b.to, b.broadcast))
                elif isinstance(b, N.DelegateStmt):
                    acts.append(f"⇒ DELEGATE → {b.agent}")
                    sends.append(("delegate", b.task, b.agent, False))
                else:
                    acts.append(type(b).__name__)
            steps.append({"name": s.name, "acts": acts})
        detail = {"when": _fmt_expr(pl.when) if pl.when else "", "steps": steps}
        pnode = add("plan", pl.name, pl.name, "plan", detail)
        plans[pl.name] = pnode
        if first_plan is None:
            first_plan = pnode
        for c in called:
            if c in tools:
                link(pnode, tools[c], "invoke")
        # nœuds de société émis par ce plan
        for kind, name, to, bc in sends:
            key = f"msg{msg_i}"; msg_i += 1
            if kind == "message":
                lbl = f"✉ {name}"
                mdetail = {"message": name, "to": ("∗ broadcast" if bc else to),
                           "from": agent.name}
            else:
                lbl = f"⇒ {name or 'tâche'}"
                mdetail = {"delegate": name, "to": to, "from": agent.name}
            mnode = add("message", key, lbl, "society", mdetail)
            link(pnode, mnode, "send")
            reg["sends"].append({"node": mnode, "to": to, "name": name,
                                 "broadcast": bc, "kind": kind})

    # -- DECIDE / EVENT (triggers) -----------------------------------------
    for i, rule in enumerate(getattr(agent.decide, "rules", []) if agent.decide else []):
        cond = _fmt_expr(rule.cond)
        target = None
        for th in rule.then:
            if isinstance(th, N.ThenPlan):
                target = th.plan
        key = f"decide{i}"
        label = f"SI {cond[:46]}…" if len(cond) > 46 else f"SI {cond}"
        node = add("decide", key, label, "trigger", {"condition": cond, "then": target})
        if target and target in plans:
            link(node, plans[target], "select")
        for nd in _walk(rule.cond):
            if isinstance(nd, N.PathExpr) and nd.parts and nd.parts[0] in hyps:
                link(hyps[nd.parts[0]], node, "supports")
            if isinstance(nd, N.CallExpr) and nd.args:
                a0 = nd.args[0]
                if isinstance(a0, N.PathExpr) and a0.parts and a0.parts[0] in hyps:
                    link(hyps[a0.parts[0]], node, "supports")

    for j, ev in enumerate(agent.events or []):
        target = None
        for b in ev.body:
            if isinstance(b, N.ThenPlan):
                target = b.plan
        key = f"event{j}"
        when = _fmt_expr(ev.when) if ev.when else ""
        node = add("event", key, f"⚡ {ev.source}", "trigger",
                   {"when": when, "then": target})
        if target and target in plans:
            link(node, plans[target], "select")

    # -- MESSAGE HANDLERS (inbox : point d'entrée d'un message reçu) --------
    for k, mh in enumerate(agent.messages or []):
        if type(mh).__name__ != "MessageHandler":
            continue
        target = None
        set_lines = []
        for b in mh.body:
            if isinstance(b, N.ThenPlan):
                target = b.plan
            elif isinstance(b, N.IfStmt):
                for st in (b.then or []):
                    if isinstance(st, N.SetStmt):
                        set_lines.append(getattr(st, "target", getattr(st, "path", "")))
            elif isinstance(b, N.SetStmt):
                set_lines.append(getattr(b, "target", getattr(b, "path", "")))
        when = _fmt_expr(mh.when) if mh.when else ""
        node = add("inbox", f"in{k}", f"✉ reçoit {mh.name}", "trigger",
                   {"message": mh.name, "when": when, "then": target,
                    "sets": [s for s in set_lines if s]})
        if target and target in plans:
            link(node, plans[target], "select")
        reg["inbox"][(agent.name, mh.name)] = node

    # -- POLICY -------------------------------------------------------------
    for i, pr in enumerate(agent.policies):
        guard = _fmt_expr(pr.guard) if pr.guard else ""
        key = f"pol{i}"
        label = f"{pr.effect} {pr.target or ''}".strip()
        node = add("policy", key, label, "policy",
                   {"effect": pr.effect, "target": pr.target, "guard": guard})
        if pr.target and pr.target in tools:
            link(node, tools[pr.target], pr.effect.lower())
        if pr.guard:
            for name, args in _calls(pr.guard):
                for a in args:
                    root = _root(a) if isinstance(a, str) else ""
                    if root in hyps:
                        link(hyps[root], node, "gates")

    reg["agents"].append({"name": agent.name,
                          "version": agent.version,
                          "description": agent.description or "",
                          "first_plan": first_plan})


def _layout(nodes, agents_meta):
    """Assigne x/y : colonnes = lanes, une bande horizontale par agent."""
    used_lanes = [l for l in LANES if any(n["lane"] == l for n in nodes)]
    lane_x = {l: i * LANE_W for i, l in enumerate(used_lanes)}
    bands = []
    top = 0
    order = [a["name"] for a in agents_meta]
    for aname in order:
        anodes = [n for n in nodes if n["agent"] == aname]
        rows = {}
        maxrow = 0
        multi = len(order) > 1
        head = BAND_HEAD if multi else 12
        for n in anodes:
            r = rows.get(n["lane"], 0)
            rows[n["lane"]] = r + 1
            n["x"] = lane_x[n["lane"]] + (LANE_W - NODE_W) / 2
            n["y"] = top + head + r * ROW_H
            maxrow = max(maxrow, r + 1)
        height = head + max(1, maxrow) * ROW_H + BAND_PAD
        meta = next(a for a in agents_meta if a["name"] == aname)
        bands.append({"name": aname, "version": meta.get("version"),
                      "top": top, "height": height})
        top += height + (BAND_GAP if multi else 0)
    width = len(used_lanes) * LANE_W
    lanes = [{"key": l, "label": LANE_LABEL[l], "x": lane_x[l], "w": LANE_W}
             for l in used_lanes]
    return lanes, bands, width, top


def build_program(program) -> dict:
    nodes, edges = [], []
    reg = {"inbox": {}, "sends": [], "agents": []}
    for k, agent in enumerate(program.agents):
        prefix = f"{agent.name}|" if len(program.agents) > 1 else ""
        _build_agent(agent, prefix, nodes, edges, reg)

    # câblage inter-agents : chaque SEND rejoint l'inbox correspondant
    for s in reg["sends"]:
        targets = []
        if s["broadcast"]:
            targets = [nid for (an, mn), nid in reg["inbox"].items() if mn == s["name"]]
        elif s["to"]:
            hit = reg["inbox"].get((s["to"], s["name"]))
            if hit:
                targets = [hit]
            elif s["kind"] == "delegate":
                a = next((a for a in reg["agents"] if a["name"] == s["to"]), None)
                if a and a["first_plan"]:
                    targets = [a["first_plan"]]
        for t in targets:
            edges.append({"from": s["node"], "to": t, "kind": "deliver", "label": "→"})

    lanes, bands, width, height = _layout(nodes, reg["agents"])
    a0 = program.agents[0]
    return {
        "name": a0.name if len(program.agents) == 1 else f"{len(program.agents)} agents",
        "version": a0.version,
        "description": (a0.description or "") if len(program.agents) == 1
                       else "Société d'agents AGENT-L — messages et délégations inter-agents.",
        "multi": len(program.agents) > 1,
        "lanes": lanes,
        "bands": bands,
        "width": width,
        "height": height,
        "row_h": ROW_H,
        "node_w": NODE_W,
        "nodes": nodes,
        "edges": edges,
    }


def build_graph(agent) -> dict:
    """Compat : construit le graphe d'un unique agent."""
    class _P:  # enveloppe minimale
        agents = [agent]
    return build_program(_P())




# --------------------------------------------------------------------------
# Rendu HTML autonome
# --------------------------------------------------------------------------

def render_html(graph: dict) -> str:
    data = json.dumps(graph, ensure_ascii=False, default=_fmt_expr)
    # Le blob JSON est injecté tel quel dans un <script>. Une valeur de trace
    # (description, version, effet d'outil…) contenant « </script> » pourrait
    # sinon fermer la balise et injecter du HTML arbitraire — XSS inacceptable
    # dans un rapport d'audit. On neutralise donc tout caractère capable de
    # rompre le contexte HTML sans casser la validité JS : « < » se relit
    # « < » côté navigateur, la donnée reste fidèle.
    for bad, safe in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"),
                      (" ", "\\u2028"), (" ", "\\u2029")):
        data = data.replace(bad, safe)
    # Le titre atterrit dans <title>…</title> : il doit être échappé HTML.
    # On l'insère avant les données pour qu'un « __TITLE__ » présent dans une
    # description ne soit pas confondu avec le marqueur de gabarit.
    title = _html_escape(str(graph.get("name", "AGENT")) + " · AGENT-L")
    return _TEMPLATE.replace("__TITLE__", title).replace("__DATA__", data)


_TEMPLATE = r"""<!doctype html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root{
    --bg:#0d1016; --panel:#161b22; --line:#39414d; --ink:#e6edf3; --muted:#8b949e;
    --observe:#3fb950; --hypothesis:#a371f7; --goal:#e3b341;
    --trigger:#58a6ff; --plan:#f0883e; --tool:#ec6a5e; --policy:#db61a2; --society:#2dd4bf;
  }
  *{box-sizing:border-box}
  html,body{margin:0;height:100%;background:var(--bg);color:var(--ink);
    font:13px/1.4 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
    overflow:hidden;user-select:none}
  #top{position:fixed;top:0;left:0;right:0;height:52px;z-index:20;
    display:flex;align-items:center;gap:16px;padding:0 18px;
    background:linear-gradient(180deg,#161b22,#0d1016);border-bottom:1px solid var(--line)}
  #top h1{font-size:15px;margin:0;font-weight:600}
  #top .ver{color:var(--muted);font-weight:400;font-size:12px}
  #top .desc{color:var(--muted);font-size:12px;flex:1;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  #legend{display:flex;gap:11px;flex-wrap:wrap}
  #legend span{display:flex;align-items:center;gap:5px;font-size:11px;color:var(--muted)}
  #legend i{width:10px;height:10px;border-radius:3px;display:inline-block}
  #stage{position:absolute;inset:52px 0 0 0;overflow:hidden;cursor:grab}
  #stage.pan{cursor:grabbing}
  #world{position:absolute;top:0;left:0;transform-origin:0 0}
  svg{position:absolute;top:0;left:0;overflow:visible;pointer-events:none;z-index:2}
  .lane-col{position:absolute;top:0;border-right:1px solid #1c2430;z-index:0;pointer-events:none}
  .lane-col.first{border-left:1px solid #1c2430}
  .lane-hd{position:absolute;top:0;height:34px;display:flex;align-items:center;
    padding:0 12px;font-size:10.5px;font-weight:700;letter-spacing:.09em;
    text-transform:uppercase;z-index:3;pointer-events:none;
    border-bottom:1px solid rgba(255,255,255,.06)}
  .band{position:absolute;left:0;z-index:1;pointer-events:none;
    border-top:1px dashed #263041}
  .band .bl{position:absolute;left:10px;top:8px;font-size:12px;font-weight:700;
    letter-spacing:.04em;color:#cfd8e3;background:rgba(13,16,22,.8);
    padding:3px 10px;border:1px solid var(--line);border-radius:20px}
  .node{position:absolute;background:var(--panel);
    border:1px solid var(--line);border-left-width:4px;border-radius:9px;
    box-shadow:0 6px 18px rgba(0,0,0,.45);cursor:grab;overflow:hidden;z-index:4}
  .node:hover{border-color:#6b7686;box-shadow:0 8px 26px rgba(0,0,0,.6)}
  .node .hd{padding:8px 10px;font-weight:600;font-size:12px;
    display:flex;align-items:center;gap:7px}
  .node .kind{font-size:9px;letter-spacing:.06em;text-transform:uppercase;
    color:var(--muted);margin-left:auto}
  .node .bd{padding:0 10px 9px;font-size:11px;color:var(--muted)}
  .node .bd .row{margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .node code{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:10.5px;color:#c9d3df}
  .dot{width:8px;height:8px;border-radius:50%;flex:none}
  .n-observe{border-left-color:var(--observe)} .n-observe .dot{background:var(--observe)}
  .n-hypothesis{border-left-color:var(--hypothesis)} .n-hypothesis .dot{background:var(--hypothesis)}
  .n-goal{border-left-color:var(--goal)} .n-goal .dot{background:var(--goal)}
  .n-decide,.n-event,.n-inbox{border-left-color:var(--trigger)}
  .n-decide .dot,.n-event .dot,.n-inbox .dot{background:var(--trigger)}
  .n-plan{border-left-color:var(--plan)} .n-plan .dot{background:var(--plan)}
  .n-tool{border-left-color:var(--tool)} .n-tool .dot{background:var(--tool)}
  .n-policy{border-left-color:var(--policy)} .n-policy .dot{background:var(--policy)}
  .n-message{border-left-color:var(--society)} .n-message .dot{background:var(--society)}
  .edge{fill:none;stroke:var(--line);stroke-width:1.7}
  .edge.evidence{stroke:var(--hypothesis)} .edge.gates,.edge.supports{stroke:var(--policy)}
  .edge.select,.edge.invoke{stroke:var(--plan)} .edge.achieves{stroke:var(--observe)}
  .edge.requires{stroke:#6e7681;stroke-dasharray:5 4}
  .edge.allow{stroke:var(--observe)} .edge.never,.edge.deny{stroke:var(--tool)}
  .edge.require_approval{stroke:var(--goal)}
  .edge.send,.edge.deliver{stroke:var(--society)}
  .edge.deliver{stroke-dasharray:2 5;stroke-width:2.2}
  .edge.hot{stroke-width:2.8;opacity:1}
  .edge.dim{opacity:.10}
  .elabel{fill:var(--muted);font-size:9px}
  #panel{position:fixed;top:62px;right:0;width:352px;max-height:calc(100% - 74px);
    background:var(--panel);border:1px solid var(--line);border-radius:10px;
    margin:0 10px;padding:14px 16px;overflow:auto;z-index:30;display:none;
    box-shadow:0 8px 30px rgba(0,0,0,.5)}
  #panel h2{margin:0 0 2px;font-size:15px}
  #panel .tag{font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
  #panel .sec{margin-top:12px}
  #panel .sec b{display:block;font-size:10px;text-transform:uppercase;
    letter-spacing:.05em;color:var(--muted);margin-bottom:4px}
  #panel code{display:block;background:#0d1117;border:1px solid var(--line);
    border-radius:5px;padding:5px 7px;margin:3px 0;font-family:ui-monospace,monospace;
    font-size:11px;color:#c9d3df;white-space:pre-wrap;word-break:break-word}
  #panel .x{position:absolute;top:10px;right:12px;cursor:pointer;color:var(--muted);
    font-size:18px;line-height:1}
  #hint{position:fixed;bottom:10px;left:14px;color:#4d5666;font-size:11px;z-index:10}
  #zoom{position:fixed;bottom:10px;right:14px;z-index:10;display:flex;gap:6px}
  #zoom button{background:var(--panel);border:1px solid var(--line);color:var(--ink);
    width:30px;height:30px;border-radius:7px;cursor:pointer;font-size:15px}
</style>
</head>
<body>
<div id="top"><h1></h1><div class="desc"></div><div id="legend"></div></div>
<div id="stage"><div id="world"><svg id="wires"></svg></div></div>
<div id="panel"><span class="x" onclick="closePanel()">×</span><div id="pbody"></div></div>
<div id="hint">Glissez un nœud (vertical, dans sa lane) · molette = zoom · fond = déplacer · clic = détail</div>
<div id="zoom"><button onclick="zoomBy(1.15)">+</button><button onclick="zoomBy(.87)">−</button><button onclick="fit()">⤢</button></div>
<script>
const G = __DATA__;
const RGB={observe:'63,185,80',hypothesis:'163,113,247',goal:'227,179,65',
  trigger:'88,166,255',plan:'240,136,62',tool:'236,106,94',policy:'219,97,162',society:'45,212,191'};
const LANE_COLOR={observe:'observe',hypothesis:'hypothesis',goal:'goal',
  trigger:'trigger',plan:'plan',tool:'tool',policy:'policy',society:'society'};
const NODE_W=G.node_w||232;
const world=document.getElementById('world'),svg=document.getElementById('wires');
const N={}; G.nodes.forEach(n=>N[n.id]=n);
document.querySelector('#top h1').innerHTML=esc(G.name)+' <span class="ver">v'+esc(G.version||'?')+'</span>';
document.querySelector('#top .desc').textContent=G.description||'';
[['observe','Observe'],['hypothesis','Hypothesis'],['goal','Goal'],
 ['trigger','Decide/Event/Inbox'],['plan','Plan'],['tool','Tool'],
 ['policy','Policy'],['society','Message/Delegate']]
 .forEach(([k,l])=>document.getElementById('legend').insertAdjacentHTML('beforeend',
   '<span><i style="background:var(--'+k+')"></i>'+l+'</span>'));

function esc(s){return (s==null?'':''+s).replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));}

// ---- colonnes de lanes (fond teinté + en-tête) ----
G.lanes.forEach((L,i)=>{
  const rgb=RGB[LANE_COLOR[L.key]]||'120,130,140';
  const col=document.createElement('div');
  col.className='lane-col'+(i===0?' first':'');
  col.style.left=L.x+'px'; col.style.width=L.w+'px'; col.style.height=G.height+'px';
  col.style.background='linear-gradient(180deg,rgba('+rgb+',.09),rgba('+rgb+',.02))';
  world.appendChild(col);
  const hd=document.createElement('div');
  hd.className='lane-hd'; hd.textContent=L.label;
  hd.style.left=L.x+'px'; hd.style.width=L.w+'px';
  hd.style.color='rgb('+rgb+')'; hd.style.background='rgba('+rgb+',.14)';
  world.appendChild(hd);
});
// ---- bandes d'agents (multi) ----
if(G.multi){G.bands.forEach(b=>{
  const el=document.createElement('div'); el.className='band';
  el.style.top=b.top+'px'; el.style.height=b.height+'px'; el.style.width=G.width+'px';
  el.innerHTML='<span class="bl">🤖 '+esc(b.name)+' <span style="color:#7d8590">v'+esc(b.version||'')+'</span></span>';
  world.appendChild(el);
});}

// ---- nœuds ----
G.nodes.forEach(n=>{
  const el=document.createElement('div');
  el.className='node n-'+n.kind; el.style.width=NODE_W+'px';
  el.style.left=n.x+'px'; el.style.top=n.y+'px';
  el.innerHTML='<div class="hd"><span class="dot"></span><span>'+esc(n.label)+
    '</span><span class="kind">'+n.kind+'</span></div><div class="bd">'+summary(n)+'</div>';
  el.addEventListener('mousedown',e=>startDrag(e,n,el));
  el.addEventListener('click',()=>{if(!moved)openPanel(n);});
  world.appendChild(el); n.el=el;
});
function summary(n){
  const d=n.detail||{},r=[];
  if(n.kind==='hypothesis'){r.push('prior '+d.prior+' · seuil '+d.threshold);r.push((d.evidence||[]).length+' témoin(s)');}
  else if(n.kind==='goal')r.push('<code>'+esc(d.condition)+'</code>');
  else if(n.kind==='tool'){r.push('risque '+esc(d.risk||'—')+(d.cost!=null?' · coût '+d.cost:''));
    if(d.effects&&d.effects.length)r.push('<code>'+esc(d.effects[0])+'</code>');}
  else if(n.kind==='plan')r.push((d.steps||[]).length+' étape(s)');
  else if(n.kind==='decide')r.push('<code>'+esc((d.condition||'').slice(0,60))+'</code>');
  else if(n.kind==='event')r.push(esc(d.when||'')||'déclencheur');
  else if(n.kind==='inbox')r.push('reçoit <code>'+esc(d.message)+'</code>');
  else if(n.kind==='message')r.push('vers <b>'+esc(d.to||'?')+'</b>');
  else if(n.kind==='policy')r.push('<code>'+esc(d.guard||'(inconditionnel)')+'</code>');
  else if(n.kind==='observe')r.push(esc((d.lines||[]).join(' · '))||'capteur');
  return r.map(x=>'<div class="row">'+x+'</div>').join('');
}

// ---- liens béziers ----
function aOut(n){return {x:n.x+NODE_W,y:n.y+28};}
function aIn(n){return {x:n.x,y:n.y+28};}
function drawEdges(){
  svg.innerHTML='';
  G.edges.forEach(e=>{
    const a=N[e.from],b=N[e.to]; if(!a||!b)return;
    let p1=aOut(a),p2=aIn(b);
    if(b.x<a.x){p1=aIn(a);p2=aOut(b);}
    const dx=Math.max(46,Math.abs(p2.x-p1.x)*0.5);
    const path=document.createElementNS('http://www.w3.org/2000/svg','path');
    path.setAttribute('d',`M${p1.x},${p1.y} C${p1.x+dx},${p1.y} ${p2.x-dx},${p2.y} ${p2.x},${p2.y}`);
    path.setAttribute('class','edge '+(e.kind||''));
    path.dataset.from=e.from; path.dataset.to=e.to; svg.appendChild(path);
    if(e.label){const t=document.createElementNS('http://www.w3.org/2000/svg','text');
      t.setAttribute('x',(p1.x+p2.x)/2);t.setAttribute('y',(p1.y+p2.y)/2-4);
      t.setAttribute('text-anchor','middle');t.setAttribute('class','elabel');
      t.textContent=e.label;svg.appendChild(t);}
  });
}
drawEdges();

// ---- drag ----
let moved=false,drag=null;
function startDrag(e,n,el){e.stopPropagation();moved=false;
  const sy=e.clientY,oy=n.y;   // x figé : le nœud reste dans sa lane
  drag=mv=>{const dy=(mv.clientY-sy)/scale;
    if(Math.abs(dy)>3)moved=true;
    n.y=oy+dy;el.style.top=n.y+'px';drawEdges();};
  document.addEventListener('mousemove',drag);
  document.addEventListener('mouseup',()=>{document.removeEventListener('mousemove',drag);drag=null;},{once:true});}

// ---- surbrillance des voisins ----
world.addEventListener('mouseover',e=>{const nd=e.target.closest('.node');if(!nd)return;
  const id=G.nodes.find(n=>n.el===nd)?.id;if(!id)return;
  svg.querySelectorAll('path').forEach(p=>{const on=p.dataset.from===id||p.dataset.to===id;
    p.classList.toggle('hot',on);p.classList.toggle('dim',!on);});});
world.addEventListener('mouseout',e=>{if(e.target.closest('.node'))
  svg.querySelectorAll('path').forEach(p=>p.classList.remove('hot','dim'));});

// ---- panneau détail ----
function openPanel(n){
  const d=n.detail||{},B=document.getElementById('pbody'),s=[];
  s.push('<div class="tag">'+n.kind+(G.multi&&n.agent?' · '+esc(n.agent):'')+'</div><h2>'+esc(n.label)+'</h2>');
  const sec=(t,body)=>{if(body)s.push('<div class="sec"><b>'+t+'</b>'+body+'</div>');};
  const code=x=>'<code>'+esc(x)+'</code>';
  if(d.description)sec('Description',esc(d.description));
  if(n.kind==='hypothesis'){sec('A priori / seuil','prior = '+d.prior+' · seuil = '+d.threshold+(d.explains?' · explique '+esc(d.explains):''));
    sec('Évidences',(d.evidence||[]).map(code).join(''));}
  else if(n.kind==='goal'){sec('Mode',esc(d.mode)+' · poids '+d.weight);sec('Condition',code(d.condition));
    if(d.targets&&d.targets.length)sec('Cibles',d.targets.map(code).join(''));}
  else if(n.kind==='tool'){sec('Risque / coût',esc(d.risk||'—')+(d.cost!=null?' · coût '+d.cost:''));
    if(d.inputs&&d.inputs.length)sec('Entrées',d.inputs.map(code).join(''));
    if(d.requires)sec('Requiert',code(d.requires));
    if(d.effects&&d.effects.length)sec('Effets',d.effects.map(code).join(''));
    if(d.side_effects&&d.side_effects.length)sec('Effets de bord',d.side_effects.map(esc).join(', '));}
  else if(n.kind==='plan'){sec('Déclenchement',d.when?code(d.when):'via DECIDE / EVENT / INBOX');
    (d.steps||[]).forEach(st=>sec('STEP '+esc(st.name),(st.acts||[]).map(code).join('')));}
  else if(n.kind==='decide'){sec('Condition',code(d.condition));sec('Alors',esc(d.then||''));}
  else if(n.kind==='event'){sec('Quand',code(d.when||'—'));sec('Alors',esc(d.then||''));}
  else if(n.kind==='inbox'){sec('Message reçu',code(d.message));if(d.when)sec('Garde',code(d.when));
    if(d.then)sec('Déclenche le plan',esc(d.then));if(d.sets&&d.sets.length)sec('Écrit',d.sets.map(code).join(''));}
  else if(n.kind==='message'){sec('Type',d.delegate?'DELEGATE':'MESSAGE');
    sec(d.delegate?'Tâche':'Message',code(d.delegate||d.message));
    sec('Destinataire',esc(d.to||'?'));sec('Émetteur',esc(d.from||''));}
  else if(n.kind==='policy'){sec('Effet',esc(d.effect)+' → '+esc(d.target||''));sec('Garde',code(d.guard||'(inconditionnel)'));}
  else if(n.kind==='observe')sec('Cadence / condition',(d.lines||[]).map(esc).join('<br>')||'chaque tick');
  B.innerHTML=s.join('');document.getElementById('panel').style.display='block';
}
function closePanel(){document.getElementById('panel').style.display='none';}

// ---- pan & zoom ----
let scale=1,tx=20,ty=20;
const stage=document.getElementById('stage');
function apply(){world.style.transform=`translate(${tx}px,${ty}px) scale(${scale})`;}
stage.addEventListener('mousedown',e=>{if(e.target.closest('.node'))return;
  stage.classList.add('pan');const sx=e.clientX-tx,sy=e.clientY-ty;
  const mv=m=>{tx=m.clientX-sx;ty=m.clientY-sy;apply();};
  document.addEventListener('mousemove',mv);
  document.addEventListener('mouseup',()=>{document.removeEventListener('mousemove',mv);stage.classList.remove('pan');},{once:true});});
stage.addEventListener('wheel',e=>{e.preventDefault();
  const r=stage.getBoundingClientRect(),mx=e.clientX-r.left,my=e.clientY-r.top;
  const f=e.deltaY<0?1.1:0.9,ns=Math.min(2.5,Math.max(0.2,scale*f));
  tx=mx-(mx-tx)*ns/scale;ty=my-(my-ty)*ns/scale;scale=ns;apply();},{passive:false});
function zoomBy(f){const r=stage.getBoundingClientRect(),mx=r.width/2,my=r.height/2,
  ns=Math.min(2.5,Math.max(0.2,scale*f));
  tx=mx-(mx-tx)*ns/scale;ty=my-(my-ty)*ns/scale;scale=ns;apply();}
function fit(){const r=stage.getBoundingClientRect();
  scale=Math.min(1,(r.width-40)/G.width,(r.height-40)/G.height);
  tx=20;ty=16;apply();}
fit();
</script>
</body>
</html>"""


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def visualize(path: str, out: str | None = None) -> str:
    prog = P.parse_file(path)
    graph = build_program(prog)          # gère 1 ou N agents (société)
    html = render_html(graph)
    if out is None:
        out = os.path.splitext(path)[0] + ".html"
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    return out


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 1
    outdir = None
    if "--dir" in argv:
        i = argv.index("--dir")
        outdir = argv[i + 1]
        del argv[i:i + 2]
        os.makedirs(outdir, exist_ok=True)
    single_out = None
    files = []
    for a in argv:
        if a.endswith(".agent"):
            files.append(a)
        else:
            single_out = a
    rc = 0
    for f in files:
        out = None
        if outdir:
            out = os.path.join(outdir, os.path.splitext(os.path.basename(f))[0] + ".html")
        elif single_out and len(files) == 1:
            out = single_out
        try:
            res = visualize(f, out)
        except OSError as exc:
            print(f"✗ {f} : fichier illisible ({exc})", file=sys.stderr)
            rc = 1
            continue
        except Exception as exc:                       # parse / rendu
            print(f"✗ {f} : {exc}", file=sys.stderr)
            rc = 1
            continue
        print(f"✓ {f}  ->  {res}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())

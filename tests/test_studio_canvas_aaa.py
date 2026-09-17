"""Test AAA du **suivi de caméra** du canevas (module M4).

Pendant une démonstration, la vue doit amener le nœud en action au centre
sans jamais toucher au niveau de zoom : un zoom qui change tout seul fait
perdre le fil de lecture du graphe. Trois exigences, chacune vérifiée en
exécutant le vrai `canvas.js` sous node avec un DOM minimal :

* recentrage sur le nœud tracé, à zoom rigoureusement constant ;
* zone morte : un nœud déjà au centre ne fait pas trembler la vue ;
* reprise en main : dès que l'utilisateur navigue, le suivi se met en veille.

    python -m pytest tests/test_studio_canvas_aaa.py -q
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

JS = ROOT / "agentl" / "studio" / "static" / "js"
AGENT = "examples/disk_sentinel.agent"
#: Nœud visé par la trace simulée, et un second dans une autre lane.
CIBLE = "tool:purge_old_files"
AUTRE = "observe:disk.usage_percent"

HARNESS = r"""// Vérifie le suivi de caméra : la vue se recentre sur le nœud tracé, et le
// niveau de zoom ne bouge jamais.
const graph = %(graph_json)s;

// --- DOM minimal ----------------------------------------------------------
const W = 900, H = 600;
class El {
  constructor(tag) {
    this.tagName = tag; this.children = []; this.attrs = {}; this.style = {};
    this.classList = {
      _s: new Set(),
      add: (...c) => c.forEach((x) => this.classList._s.add(x)),
      remove: (...c) => c.forEach((x) => this.classList._s.delete(x)),
      toggle: (c, on) => (on ? this.classList._s.add(c) : this.classList._s.delete(c)),
      contains: (c) => this.classList._s.has(c),
    };
    this._h = {};
  }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k] ?? null; }
  removeAttribute(k) { delete this.attrs[k]; }
  appendChild(c) { this.children.push(c); return c; }
  removeChild(c) { this.children = this.children.filter((x) => x !== c); }
  addEventListener(t, fn) { (this._h[t] = this._h[t] || []).push(fn); }
  removeEventListener(t, fn) { this._h[t] = (this._h[t] || []).filter((f) => f !== fn); }
  fire(t, ev) { for (const fn of this._h[t] || []) fn(ev); }
  getBoundingClientRect() { return { left: 0, top: 0, width: W, height: H }; }
  focus() {}
  closest() { return null; }
  set innerHTML(_) { this.children = []; }
  get innerHTML() { return ''; }
  set textContent(v) { this._t = v; }
  get textContent() { return this._t || ''; }
}
const doc = {
  createElement: (t) => new El(t),
  createElementNS: (_ns, t) => new El(t),
  getElementById: () => null,
  head: new El('head'),
  body: new El('body'),
  documentElement: new El('html'),
  addEventListener() {}, removeEventListener() {},
};
globalThis.document = doc;
globalThis.window = globalThis;
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.matchMedia = () => ({ matches: false, addEventListener() {}, addListener() {} });
globalThis.requestAnimationFrame = (fn) => setTimeout(() => fn(Date.now()), 0);
globalThis.cancelAnimationFrame = (id) => clearTimeout(id);
globalThis.ResizeObserver = class { observe() {} disconnect() {} };
globalThis.performance = { now: () => Date.now() };

const { bus } = await import('%(bus)s');
const { mountCanvas } = await import('%(canvas)s');

const host = new El('div');
mountCanvas(host);
bus.emit('graph', { graph });
await new Promise((r) => setTimeout(r, 30));

// Retrouve le <g> de transformation de vue pour lire tx/ty/scale.
function findView(el) {
  if (el.attrs && el.attrs.class === 'agl-view') return el;
  for (const c of el.children || []) { const f = findView(c); if (f) return f; }
  return null;
}
const view = findView(host);
const read = () => {
  const m = /translate\(([-\d.]+),([-\d.]+)\) scale\(([\d.]+)\)/.exec(view.attrs.transform || '');
  return m ? { tx: +m[1], ty: +m[2], scale: +m[3] } : null;
};

const before = read();
const cible = 'tool:purge_old_files';
bus.emit('run.started', { runId: 'r1' });
bus.emit('trace', { runId: 'r1', seq: 1, tick: 1, kind: 'TOOL',
                    text: 'purge_old_files()', nodeId: cible });
await new Promise((r) => setTimeout(r, 400));
const after = read();

// Position à l'écran du nœud visé après recentrage.
const n = graph.nodes.find((x) => x.id === cible);
const cx = (n.x + (n.w || 232) / 2) * after.scale + after.tx;
const cy = (n.y + (n.h || 76) / 2) * after.scale + after.ty;

const resultat = {
  zoomInchange: before.scale === after.scale,
  vueDeplacee: before.tx !== after.tx || before.ty !== after.ty,
  ecartCentreX: Math.abs(cx - W / 2),
  ecartCentreY: Math.abs(cy - H / 2),
};

// -- zone morte : un nœud déjà centré ne doit pas rebouger la vue ---------
bus.emit('trace', { runId: 'r1', seq: 2, tick: 1, kind: 'TOOL',
                    text: 'purge_old_files()', nodeId: cible });
await new Promise((r) => setTimeout(r, 400));
const stable = read();
resultat.zoneMorte = stable.tx === after.tx && stable.ty === after.ty;

// -- reprise en main : une molette suspend le suivi -----------------------
const stage = (function find(el) {
  if (el.attrs && String(el.attrs.class || '').includes('agl-stage')) return el;
  for (const c of el.children || []) { const f = find(c); if (f) return f; }
  return null;
})(host);
stage.fire('wheel', { preventDefault() {}, deltaY: -100, deltaMode: 0,
                      clientX: 10, clientY: 10 });
const apresMolette = read();
resultat.moletteZoome = apresMolette.scale > after.scale;
bus.emit('trace', { runId: 'r1', seq: 3, tick: 1, kind: 'OBSERVE',
                    text: 'disk.usage_percent = 40', nodeId: '%(autre)s' });
await new Promise((r) => setTimeout(r, 400));
const suspendu = read();
resultat.suiviSuspendu = suspendu.tx === apresMolette.tx && suspendu.ty === apresMolette.ty;

console.log(JSON.stringify(resultat));
process.exit(0);
"""


class TestCameraFollow(unittest.TestCase):
    """Le canevas est monté dans un DOM minimal, puis nourri de vraies traces."""

    @classmethod
    def setUpClass(cls):
        from agentl.parser import parse_file
        from agentl.viz import build_program

        graph = build_program(parse_file(str(ROOT / AGENT)))
        ids = {n["id"] for n in graph["nodes"]}
        assert CIBLE in ids and AUTRE in ids, "le programme d'essai a changé"

        node = shutil.which("node")
        if node is None:                       # pragma: no cover - env sans node
            raise unittest.SkipTest("node absent")
        script = HARNESS % {
            "graph_json": json.dumps(graph),
            "bus": (JS / "bus.js").as_uri(),
            "canvas": (JS / "canvas.js").as_uri(),
            "autre": AUTRE,
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "follow.mjs"
            path.write_text(script, encoding="utf-8")
            proc = subprocess.run([node, str(path)], capture_output=True,
                                  text=True, timeout=90)
        assert proc.returncode == 0, proc.stderr
        cls.r = json.loads(proc.stdout.strip().splitlines()[-1])

    def test_le_zoom_ne_change_jamais(self):
        self.assertTrue(self.r["zoomInchange"],
                        "le suivi a modifié le niveau de zoom")
        self.assertTrue(self.r["vueDeplacee"], "la vue n'a pas suivi le nœud")

    def test_le_noeud_en_action_arrive_au_centre(self):
        self.assertLess(self.r["ecartCentreX"], 12)
        self.assertLess(self.r["ecartCentreY"], 12)

    def test_zone_morte_pas_de_tremblement(self):
        """Recentrer un nœud déjà centré rendrait la démonstration illisible."""
        self.assertTrue(self.r["zoneMorte"])

    def test_le_suivi_se_suspend_des_que_l_utilisateur_navigue(self):
        self.assertTrue(self.r["moletteZoome"], "la molette doit zoomer")
        self.assertTrue(self.r["suiviSuspendu"],
                        "le suivi a repris la main sur l'utilisateur")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

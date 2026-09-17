"""Tests AAA du **direct** du studio : allure de démonstration, illumination
des nœuds, et diffusion des traces jusqu'au client.

Chaque test cible un défaut réel observé sur l'interface, pas une propriété
théorique :

* le bus jetait 41 traces sur 43 — il exigeait des `seq` consécutifs alors que
  ce compteur est partagé avec `tick`/`phase`/`state`, donc troué. Le canevas
  restait éteint et la chronologie affichait deux lignes ;
* un run allait trop vite pour être vu : rien ne permettait de le ralentir ;
* les croyances dérivées et les étapes de plan n'illuminaient aucun nœud.

    python -m pytest tests/test_studio_live_aaa.py -q
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentl.studio.session import StudioSession, _clamp_pace

# Programme d'essai : instantané et sans accès réseau. `disk_sentinel`
# conviendrait aussi mais son étape LLM part sur le réseau et expire.
AGENT = "examples/soc_analyst.agent"
HOST = "examples/soc_analyst.py"
BUS_JS = ROOT / "agentl" / "studio" / "static" / "js" / "bus.js"


def _run(session: StudioSession, **kwargs) -> list[dict]:
    """Exécute un run et retourne tous les messages émis."""
    q = session.subscribe()
    try:
        session.run(**kwargs)
        deadline = time.time() + 60
        out: list[dict] = []
        while time.time() < deadline:
            out.extend(StudioSession.drain(q))
            if any(m.get("type") == "run.finished" for m in out):
                return out
            time.sleep(0.01)
        raise AssertionError("run non terminé dans le délai imparti")
    finally:
        session.unsubscribe(q)


# ---- allure de démonstration ------------------------------------------------
class TestPace(unittest.TestCase):
    """Avant correction : aucun moyen de ralentir un run pour l'observer."""

    def test_clamp_refuse_valeurs_absurdes(self):
        for bad in ("vite", None, float("nan"), -3):
            self.assertEqual(_clamp_pace(bad), 0.0, f"valeur : {bad!r}")
        self.assertEqual(_clamp_pace(99), 5.0, "l'allure doit rester bornée")
        self.assertAlmostEqual(_clamp_pace("0.4"), 0.4)

    def test_le_mode_demo_ralentit_reellement(self):
        session = StudioSession(root=ROOT, file=AGENT)
        try:
            rapide = time.time()
            _run(session, ticks=1, pace=0.0)
            rapide = time.time() - rapide

            lent = time.time()
            messages = _run(session, ticks=1, pace=0.05)
            lent = time.time() - lent
        finally:
            session.close()
        self.assertGreater(lent, rapide + 0.4,
                           "l'allure de démonstration n'a pas ralenti le run")
        self.assertTrue(any(m["type"] == "trace" for m in messages))

    def test_stop_reste_immediat_sous_allure_lente(self):
        """Un pas de 3 s ne doit pas retarder l'arrêt de 3 s."""
        session = StudioSession(root=ROOT, file=AGENT)
        q = session.subscribe()
        try:
            session.run(ticks=20, pace=3.0)
            time.sleep(0.2)
            start = time.time()
            self.assertTrue(session.stop(timeout=2.0))
            self.assertLess(time.time() - start, 1.0)
        finally:
            session.unsubscribe(q)
            session.close()

    def test_allure_reglable_pendant_un_run(self):
        session = StudioSession(root=ROOT, file=AGENT)
        try:
            self.assertEqual(session.set_pace(0.5), 0.5)
            self.assertEqual(session.pace, 0.5)
            self.assertEqual(session.set_pace(-1), 0.0)
            self.assertEqual(session.snapshot()["pace"], 0.0)
        finally:
            session.close()


# ---- illumination des nœuds -------------------------------------------------
class TestNodeIllumination(unittest.TestCase):
    """Un canevas éteint est un canevas inutile : on mesure la couverture."""

    @classmethod
    def setUpClass(cls):
        session = StudioSession(root=ROOT, file=AGENT)
        try:
            cls.messages = _run(session, ticks=2)
            cls.ids = {n["id"] for n in session.graph["nodes"]}
        finally:
            session.close()
        cls.traces = [m for m in cls.messages if m["type"] == "trace"]

    def test_aucun_node_id_hors_du_graphe(self):
        """Un `nodeId` inconnu illumine un nœud inexistant : silencieux et faux."""
        emis = {m["nodeId"] for m in self.traces if m.get("nodeId")}
        self.assertTrue(emis, "aucune trace n'a été rattachée à un nœud")
        self.assertEqual(emis - self.ids, set())

    def test_la_plupart_des_traces_illuminent_quelque_chose(self):
        avec = [m for m in self.traces if m.get("nodeId")]
        ratio = len(avec) / max(len(self.traces), 1)
        self.assertGreater(ratio, 0.8,
                           f"seulement {len(avec)}/{len(self.traces)} traces "
                           "rattachées à un nœud")

    def test_les_lanes_de_la_chaine_cognitive_sont_couvertes(self):
        """Perception → inférence → but → plan → outil : tout doit s'allumer."""
        lanes = {m["nodeId"].split("|")[-1].split(":")[0]
                 for m in self.traces if m.get("nodeId")}
        for attendu in ("observe", "hypothesis", "goal", "plan", "tool"):
            self.assertIn(attendu, lanes)

    def test_les_etapes_heritent_du_plan_courant(self):
        """Sans héritage, le canevas s'éteint entre deux actions d'un plan."""
        steps = [m for m in self.traces if m["kind"] == "STEP"]
        self.assertTrue(steps, "le programme d'essai ne produit aucune étape")
        self.assertTrue(all(m.get("nodeId") for m in steps))


# ---- diffusion côté client (bus.js) ----------------------------------------
class TestBusDeliversEveryTrace(unittest.TestCase):
    """Régression : le bus n'avait diffusé que 2 traces sur 43.

    `seq` est monotone mais **partagé** par tous les messages d'un run : la
    suite vue par le journal est croissante et trouée. Un ordonnanceur qui
    attend `lastSeq + 1` bloque donc indéfiniment.
    """

    HARNESS = r"""
    let sock = null;
    globalThis.WebSocket = class {
      constructor(url) { this.url = url; sock = this; this._h = {};
        setTimeout(() => this.fire('open', {}), 0); }
      addEventListener(t, fn) { (this._h[t] = this._h[t] || []).push(fn); }
      removeEventListener(t, fn) { this._h[t] = (this._h[t] || []).filter(f => f !== fn); }
      fire(t, ev) { for (const fn of this._h[t] || []) fn(ev); }
      send() {} close() {}
    };
    globalThis.location = { protocol: 'http:', host: '127.0.0.1:8765' };
    globalThis.window = globalThis;
    const bus = await import('%(bus)s');
    let recues = 0;
    bus.bus.on('trace', () => { recues += 1; });
    bus.connect();
    await new Promise(r => setTimeout(r, 10));
    const msgs = %(msgs)s;
    for (const m of msgs) sock.fire('message', { data: JSON.stringify(m) });
    console.log(JSON.stringify({ recues }));
    // `connect()` arme un battement et une reconnexion : sans sortie
    // explicite, node resterait vivant et le test expirerait.
    process.exit(0);
    """

    def _node(self, messages: list[dict]) -> int:
        node = shutil.which("node")
        if node is None:                       # pragma: no cover - env sans node
            self.skipTest("node absent")
        script = self.HARNESS % {"bus": BUS_JS.as_uri(),
                                 "msgs": json.dumps(messages)}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "harness.mjs"
            path.write_text(script, encoding="utf-8")
            proc = subprocess.run([node, str(path)], capture_output=True,
                                  text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])["recues"]

    def test_les_seq_troues_sont_tous_diffuses(self):
        """Le cas réel : traces, phases et ticks partagent le compteur."""
        messages = [{"type": "run.started", "runId": "r1", "agent": "a",
                     "maxTicks": 2}]
        seq = 0
        for i in range(30):
            seq += 1
            messages.append({"type": "phase", "runId": "r1", "tick": 1,
                             "phase": "OBSERVE", "status": "enter", "seq": seq})
            seq += 1
            messages.append({"type": "trace", "runId": "r1", "seq": seq,
                             "tick": 1, "kind": "OBSERVE",
                             "text": f"x{i}", "detail": ""})
        self.assertEqual(self._node(messages), 30)

    def test_les_doublons_de_reconnexion_sont_ecartes(self):
        base = [{"type": "trace", "runId": "r1", "seq": s, "tick": 1,
                 "kind": "INFO", "text": f"e{s}", "detail": ""}
                for s in (1, 3, 5, 7)]
        self.assertEqual(self._node(base + base), 4)

    def test_une_trace_sans_seq_passe_quand_meme(self):
        messages = [{"type": "trace", "runId": "r1", "tick": 1, "kind": "INFO",
                     "text": "sans seq", "detail": ""}]
        self.assertEqual(self._node(messages), 1)


# ---- géométrie du canevas (graph.js) ---------------------------------------
class TestCanvasGeometry(unittest.TestCase):
    """Défauts constatés à l'usage : nœuds recouvrant l'intitulé de colonne, et
    nœuds déplaçables hors de leur lane — ce qui ment sur leur nature."""

    HARNESS = r"""
    globalThis.window = globalThis;
    globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
    const G = await import('%(graph)s');
    const m = G.normalizeGraph(%(graph_json)s);
    let dehors = 0;
    for (const n of m.nodes) {
      const lane = m.laneByKey.get(n.lane);
      if (!lane) continue;
      if (n.x < lane.x || n.x + n.w > lane.x + lane.w) dehors += 1;
    }
    const n = m.nodes[0];
    const lane = m.laneByKey.get(n.lane);
    const droite = G.clampToLane(m, n, lane.x + 5000, 400);
    const haut = G.clampToLane(m, n, n.x, -900);
    console.log(JSON.stringify({
      minY: Math.min(...m.nodes.map((x) => x.y)),
      headH: G.LANE_HEAD_H,
      dehors,
      droiteDansLaColonne: droite.x >= lane.x && droite.x + n.w <= lane.x + lane.w,
      hautSousEntete: haut.y >= G.LANE_HEAD_H,
      verticalLibre: droite.y === 400,
    }));
    process.exit(0);
    """

    @classmethod
    def setUpClass(cls):
        from agentl.parser import parse_file
        from agentl.viz import build_program
        cls.graph = build_program(parse_file(str(ROOT / AGENT)))

    def _probe(self) -> dict:
        node = shutil.which("node")
        if node is None:                       # pragma: no cover - env sans node
            self.skipTest("node absent")
        graph_js = ROOT / "agentl" / "studio" / "static" / "js" / "graph.js"
        script = self.HARNESS % {"graph": graph_js.as_uri(),
                                 "graph_json": json.dumps(self.graph)}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "geom.mjs"
            path.write_text(script, encoding="utf-8")
            proc = subprocess.run([node, str(path)], capture_output=True,
                                  text=True, timeout=60)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout.strip().splitlines()[-1])

    def test_aucun_noeud_ne_recouvre_l_intitule_de_colonne(self):
        """viz.py place sa première rangée à y=12 ; l'entête du canevas fait 30."""
        r = self._probe()
        self.assertGreaterEqual(r["minY"], r["headH"])

    def test_tous_les_noeuds_sont_dans_leur_colonne(self):
        self.assertEqual(self._probe()["dehors"], 0)

    def test_le_glissement_ne_sort_pas_de_la_colonne(self):
        r = self._probe()
        self.assertTrue(r["droiteDansLaColonne"], "un nœud a quitté sa lane")
        self.assertTrue(r["hautSousEntete"], "un nœud est passé sous l'entête")
        self.assertTrue(r["verticalLibre"], "le déplacement vertical doit rester libre")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

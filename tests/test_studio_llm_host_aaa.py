"""Tests AAA : mise en évidence des appels LLM, et déduction de l'hôte.

Deux besoins d'usage, deux invariants :

* **Oracle visible.** Un plan qui contient une étape `REASON` confie une
  décision à un modèle non déterministe. C'est le point du programme qu'un
  lecteur doit repérer sans ouvrir le nœud — et pendant l'appel, souvent long
  sur un modèle distant, l'interface doit montrer que l'agent attend. Le
  runtime ne journalise le LLM qu'*après* la réponse : la session encadre donc
  l'appel de deux traces (« appel en cours » / « appel terminé »).

* **Hôte déduit.** Le lien entre un `.agent` et son hôte n'est pas nominal
  mais contractuel : le programme déclare ce qu'il observe et appelle, l'hôte
  fournit les implémentations. La déduction lit ces déclarations **sans
  exécuter** le module — proposer un hôte ne doit jamais lancer du code
  arbitraire.

    python -m pytest tests/test_studio_llm_host_aaa.py -q
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

from agentl.studio.session import StudioSession, _declared_bindings

JS = ROOT / "agentl" / "studio" / "static" / "js"
AGENT = "examples/soc_analyst.agent"
HOST = "examples/soc_analyst.py"


class TestLLMCallMarkers(unittest.TestCase):
    """L'appel à l'oracle est encadré, donc observable pendant sa durée."""

    @classmethod
    def setUpClass(cls):
        session = StudioSession(root=ROOT, file=AGENT)
        q = session.subscribe()
        try:
            session.run(ticks=2)
            deadline = time.time() + 60
            out: list[dict] = []
            while time.time() < deadline:
                out.extend(StudioSession.drain(q))
                if any(m.get("type") == "run.finished" for m in out):
                    break
                time.sleep(0.01)
            cls.messages = out
            cls.graph = session.graph
        finally:
            session.unsubscribe(q)
            session.close()
        cls.llm = [m for m in cls.messages
                   if m["type"] == "trace" and m["kind"] == "LLM"]

    def test_l_appel_est_encadre_par_deux_traces(self):
        debuts = [m for m in self.llm if "en cours" in m["detail"]]
        fins = [m for m in self.llm if "terminé" in m["detail"]]
        self.assertTrue(debuts, "aucun début d'appel annoncé")
        self.assertEqual(len(debuts), len(fins),
                         "un appel commencé doit toujours être refermé, "
                         "sinon le nœud clignote indéfiniment")

    def test_le_debut_precede_la_fin(self):
        ordre = [("debut" if "en cours" in m["detail"] else
                  "fin" if "terminé" in m["detail"] else "autre")
                 for m in self.llm]
        self.assertEqual(ordre[0], "debut")

    def test_l_appel_est_rattache_au_plan_qui_le_produit(self):
        for m in self.llm:
            self.assertTrue(m.get("nodeId"), f"trace LLM sans nœud : {m['text']}")
            self.assertIn("plan:", m["nodeId"])

    def test_seuls_les_plans_avec_REASON_sont_marques(self):
        """La marque permanente doit distinguer, sinon elle n'informe pas."""
        plans = [n for n in self.graph["nodes"] if n["kind"] == "plan"]
        avec = [n for n in plans if any(
            "REASON" in a for st in n["detail"].get("steps", [])
            for a in st.get("acts", []))]
        self.assertTrue(avec, "le programme d'essai ne raisonne jamais")
        self.assertLess(len(avec), len(plans),
                        "il faut au moins un plan sans REASON pour discriminer")


class TestCanvasLLMHighlight(unittest.TestCase):
    """Le canevas réel, monté sous node : marque permanente et clignotement."""

    HARNESS = r"""
    const graph = %(graph_json)s;
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
      focus() {} closest() { return null; }
      set innerHTML(_) { this.children = []; }
      get innerHTML() { return ''; }
      set textContent(v) { this._t = v; }
      get textContent() { return this._t || ''; }
    }
    globalThis.document = {
      createElement: (t) => new El(t), createElementNS: (_n, t) => new El(t),
      getElementById: () => null, head: new El('head'), body: new El('body'),
      documentElement: new El('html'), addEventListener() {}, removeEventListener() {},
    };
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

    function classString(el) {
      return `${(el.attrs && el.attrs.class) || ''} ${[...(el.classList ? el.classList._s : [])].join(' ')}`;
    }
    function groups(el, out = []) {
      if (/\bagl-n\b/.test(classString(el))) out.push(el);
      for (const c of el.children || []) groups(c, out);
      return out;
    }
    const all = groups(host);
    const classesOf = (id) => {
      const g = all.find((x) => x.attrs && x.attrs['data-id'] === id);
      return g ? classString(g).split(/\s+/).filter(Boolean) : [];
    };

    const cible = '%(avec)s', temoin = '%(sans)s';
    const res = { marque: classesOf(cible).includes('is-llm'),
                  temoinSansMarque: !classesOf(temoin).includes('is-llm') };
    bus.emit('run.started', { runId: 'r1' });
    bus.emit('trace', { runId: 'r1', seq: 1, tick: 1, kind: 'LLM',
                        text: 'llm.reason', detail: 'appel en cours', nodeId: cible });
    await new Promise((r) => setTimeout(r, 60));
    res.clignotePendant = classesOf(cible).includes('is-calling');
    bus.emit('trace', { runId: 'r1', seq: 2, tick: 1, kind: 'LLM',
                        text: 'llm.reason', detail: 'appel terminé', nodeId: cible });
    await new Promise((r) => setTimeout(r, 60));
    res.eteintApres = !classesOf(cible).includes('is-calling');
    bus.emit('trace', { runId: 'r1', seq: 3, tick: 1, kind: 'LLM',
                        text: 'llm.reason', detail: 'appel en cours', nodeId: cible });
    await new Promise((r) => setTimeout(r, 40));
    bus.emit('run.finished', { runId: 'r1', status: 'stopped', metrics: {} });
    await new Promise((r) => setTimeout(r, 60));
    res.eteintSiArret = !classesOf(cible).includes('is-calling');
    console.log(JSON.stringify(res));
    process.exit(0);
    """

    @classmethod
    def setUpClass(cls):
        from agentl.parser import parse_file
        from agentl.viz import build_program

        graph = build_program(parse_file(str(ROOT / AGENT)))
        plans = [n for n in graph["nodes"] if n["kind"] == "plan"]
        avec = next(n for n in plans if any(
            "REASON" in a for st in n["detail"].get("steps", [])
            for a in st.get("acts", [])))
        sans = next(n for n in plans if n is not avec)

        node = shutil.which("node")
        if node is None:                       # pragma: no cover - env sans node
            raise unittest.SkipTest("node absent")
        script = cls.HARNESS % {
            "graph_json": json.dumps(graph),
            "bus": (JS / "bus.js").as_uri(),
            "canvas": (JS / "canvas.js").as_uri(),
            "avec": avec["id"], "sans": sans["id"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "llm.mjs"
            path.write_text(script, encoding="utf-8")
            proc = subprocess.run([node, str(path)], capture_output=True,
                                  text=True, timeout=90)
        assert proc.returncode == 0, proc.stderr
        cls.r = json.loads(proc.stdout.strip().splitlines()[-1])

    def test_marque_permanente_sur_le_plan_qui_raisonne(self):
        self.assertTrue(self.r["marque"])
        self.assertTrue(self.r["temoinSansMarque"],
                        "marquer tous les plans n'informerait de rien")

    def test_clignotement_pendant_l_appel(self):
        self.assertTrue(self.r["clignotePendant"])
        self.assertTrue(self.r["eteintApres"])

    def test_aucun_noeud_ne_reste_clignotant_apres_un_arret(self):
        self.assertTrue(self.r["eteintSiArret"])


class TestHostNamingNorm(unittest.TestCase):
    """Norme de nommage : `X.agent` est servi par `X.py`, et rien d'autre.

    Le nom **décide** de l'exécution ; la lecture des capteurs et des outils
    ne fait que **confirmer** que le fichier trouvé sert bien ce programme.
    """

    def test_l_hote_se_deduit_du_nom_du_programme(self):
        session = StudioSession(root=ROOT, file=AGENT)
        try:
            self.assertEqual(session.relpath(session.host_path_for()), HOST)
        finally:
            session.close()

    def test_la_lecture_des_capteurs_confirme_le_contrat(self):
        session = StudioSession(root=ROOT, file=AGENT)
        try:
            status = session.host_status()
        finally:
            session.close()
        self.assertTrue(status["exists"])
        self.assertTrue(status["confirmed"])
        self.assertEqual(status["sensors"], status["expectedSensors"])

    def test_les_liaisons_sont_lues_sans_executer_le_module(self):
        sensors, _ = _declared_bindings((ROOT / HOST).read_text(encoding="utf-8"))
        self.assertIn("wazuh.alert_count", sensors)

    def test_un_module_fautif_ne_leve_pas(self):
        self.assertEqual(_declared_bindings("def f(: pass"), (set(), set()))

    def test_le_module_n_est_jamais_importe_pour_verifier(self):
        """Un hôte est du code arbitraire : vérifier ne doit rien exécuter."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.agent").write_text(
                (ROOT / "examples" / "disk_sentinel.agent").read_text(
                    encoding="utf-8"), encoding="utf-8")
            (root / "a.py").write_text(
                "import pathlib\n"
                "pathlib.Path(__file__).with_name('EXECUTE').write_text('x')\n"
                "host.sensors['disk.usage_percent'] = lambda: 1\n",
                encoding="utf-8")
            session = StudioSession(root=root, file="a.agent")
            try:
                status = session.host_status()
            finally:
                session.close()
            self.assertTrue(status["exists"])
            self.assertTrue(status["confirmed"])
            self.assertFalse((root / "EXECUTE").exists(),
                             "le module hôte a été exécuté par la vérification")

    def test_un_hote_absent_interdit_l_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "orphelin.agent").write_text(
                (ROOT / "examples" / "disk_sentinel.agent").read_text(
                    encoding="utf-8"), encoding="utf-8")
            session = StudioSession(root=root, file="orphelin.agent")
            try:
                status = session.host_status()
                self.assertFalse(status["exists"])
                with self.assertRaises(Exception) as ctx:
                    session.run(ticks=1)
                self.assertIn("orphelin.py", str(ctx.exception))
            finally:
                session.close()

    def test_un_hote_present_mais_etranger_est_signale_sans_bloquer(self):
        """Enregistrement dynamique possible : on avertit, on n'interdit pas."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.agent").write_text(
                (ROOT / "examples" / "disk_sentinel.agent").read_text(
                    encoding="utf-8"), encoding="utf-8")
            (root / "a.py").write_text("host = None\n", encoding="utf-8")
            session = StudioSession(root=root, file="a.agent")
            try:
                status = session.host_status()
            finally:
                session.close()
            self.assertTrue(status["exists"])
            self.assertFalse(status["confirmed"])
            self.assertIn("aucun capteur", status["message"])

    def test_le_statut_est_publie_dans_la_session(self):
        session = StudioSession(root=ROOT, file=AGENT)
        try:
            snap = session.snapshot()
            self.assertEqual(snap["host"], HOST)
            self.assertTrue(snap["hostStatus"]["confirmed"])
            self.assertEqual(session.hello()["session"]["host"], HOST)
        finally:
            session.close()

    def test_la_norme_est_respectee_par_tous_les_exemples_avec_hote(self):
        """Chaque `X.agent` du dépôt a soit `X.py`, soit aucun hôte."""
        import ast

        def est_un_hote(path) -> bool:
            """Un hôte est ce que `agentl run` charge : un module exposant
            `build()`. Le reste — démonstrations, adaptateurs LLM, bancs de
            comparaison — n'est pas soumis à la norme de nommage.

            On le lit sur l'AST plutôt que par une liste de préfixes : celle-ci
            recassait à chaque script ajouté, et l'exemption finissait par ne
            plus décrire la règle. Lecture syntaxique, jamais un import :
            inspecter un exemple ne doit pas exécuter de code arbitraire.
            """
            try:
                arbre = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):
                return False
            return any(isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                       and n.name == "build" for n in arbre.body)

        examples = ROOT / "examples"
        orphelins = []
        for py in examples.glob("*.py"):
            if not est_un_hote(py):
                continue
            if not py.with_suffix(".agent").is_file():
                orphelins.append(py.name)
        self.assertEqual(orphelins, [],
                         f"hôtes sans programme du même nom : {orphelins}")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

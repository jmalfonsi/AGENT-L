"""Tests AAA du mode **société** du studio et de la CLI.

Avant correction, un programme multi-agents était refusé par le studio
(« le studio exécute un seul agent, pas une société ») et faisait échouer
silencieusement chaque capteur de la CLI (`AttributeError: 'dict' object has
no attribute 'read'`, une ligne d'erreur par capteur et par tick).

Ce qu'une société doit rendre visible, et que ces tests vérifient :

* chaque agent trace pour son propre compte, et la trace dit lequel ;
* les `nodeId` portent le préfixe d'agent de `viz.py`, donc chaque nœud
  s'illumine dans la bande du bon agent ;
* un message échangé illumine **les deux** extrémités — le nœud d'émission
  chez l'expéditeur, le nœud de réception chez le destinataire — sans quoi le
  câblage inter-agents resterait invisible, alors que c'est tout l'intérêt ;
* les deux conventions d'hôte des exemples sont acceptées.

    python -m pytest tests/test_studio_society_aaa.py -q
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentl.core import AgentLError
from agentl.host import Host
from agentl.llm import MockLLM
from agentl.studio.session import StudioSession

AGENT = "examples/soc_team.agent"
HOST = "examples/soc_team.py"


def _run(session: StudioSession, **kwargs) -> list[dict]:
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


class TestSocietyRun(unittest.TestCase):
    """Un vrai run à deux agents, messages compris."""

    @classmethod
    def setUpClass(cls):
        session = StudioSession(root=ROOT, file=AGENT)
        try:
            cls.messages = _run(session, ticks=3)
            cls.ids = {n["id"] for n in session.graph["nodes"]}
            cls.names = [a.name for a in session.program.agents]
        finally:
            session.close()
        cls.traces = [m for m in cls.messages if m["type"] == "trace"]

    def test_le_run_va_au_bout(self):
        fin = [m for m in self.messages if m["type"] == "run.finished"]
        self.assertEqual(fin[0]["status"], "done", fin[0].get("error"))

    def test_les_deux_agents_tracent(self):
        emetteurs = {m.get("agent") for m in self.traces}
        for name in self.names:
            self.assertIn(name, emetteurs, f"{name} n'a rien tracé")

    def test_les_node_id_restent_dans_le_graphe(self):
        emis = {m["nodeId"] for m in self.traces if m.get("nodeId")}
        self.assertTrue(emis)
        self.assertEqual(emis - self.ids, set())

    def test_les_node_id_sont_prefixes_par_agent(self):
        """En multi-agents, `viz.py` préfixe : sans cela, deux agents
        partageant un nom de plan illumineraient le même nœud."""
        emis = {m["nodeId"] for m in self.traces if m.get("nodeId")}
        self.assertTrue(all("|" in i for i in emis), sorted(emis)[:3])
        self.assertEqual({i.split("|")[0] for i in emis}, set(self.names))

    def test_un_message_illumine_ses_deux_extremites(self):
        envois = [m for m in self.traces
                  if m["kind"] == "MESSAGE" and "→" in m["text"]]
        receptions = [m for m in self.traces
                      if m["kind"] == "MESSAGE" and "←" in m["text"]]
        self.assertTrue(envois and receptions, "aucun message échangé")
        self.assertTrue(all(m.get("nodeId") for m in envois))
        self.assertTrue(all(m.get("nodeId") for m in receptions))
        # L'émission vit dans la lane `society`, la réception dans `trigger`
        # (c'est l'inbox) : les deux extrémités du câblage de viz.py.
        self.assertTrue(all("society:" in m["nodeId"] for m in envois))
        self.assertTrue(all("trigger:" in m["nodeId"] for m in receptions))
        # …et chez des agents différents.
        for envoi in envois:
            emetteur = envoi["nodeId"].split("|")[0]
            jumeaux = [r for r in receptions
                       if r["text"].split()[0] == envoi["text"].split()[0]]
            self.assertTrue(jumeaux)
            self.assertNotEqual(jumeaux[0]["nodeId"].split("|")[0], emetteur)

    def test_les_croyances_sont_attribuees_a_leur_agent(self):
        etats = [m for m in self.messages if m["type"] == "state"]
        self.assertTrue(etats)
        cles = {k for m in etats for k in m["beliefs"]}
        self.assertTrue(any(k.startswith(f"{n}.") for n in self.names
                            for k in cles), sorted(cles)[:4])

    def test_les_metriques_sont_celles_de_la_societe(self):
        fin = [m for m in self.messages if m["type"] == "run.finished"][0]
        self.assertGreaterEqual(fin["metrics"]["messages_sent"], 1)
        self.assertIn("shared_keys", fin["metrics"],
                      "métriques d'un agent isolé, pas d'une société")


class TestSocietyHostConventions(unittest.TestCase):
    """`build()` peut rendre un LLM par agent ou un LLM partagé."""

    def setUp(self):
        self.session = StudioSession(root=ROOT, file=AGENT)
        self.names = [a.name for a in self.session.program.agents]

    def tearDown(self):
        self.session.close()

    def test_llm_unique_partage_par_toute_la_societe(self):
        hosts = {n: Host() for n in self.names}
        llm = MockLLM()
        _, llms = self.session._society_hosts(hosts, llm)
        self.assertEqual(sorted(llms), sorted(self.names))
        self.assertTrue(all(v is llm for v in llms.values()))

    def test_llm_par_agent(self):
        hosts = {n: Host() for n in self.names}
        par_agent = {n: MockLLM() for n in self.names}
        _, llms = self.session._society_hosts(hosts, par_agent)
        self.assertEqual(sorted(llms), sorted(self.names))

    def test_un_hote_unique_est_partage(self):
        h = Host()
        hosts, _ = self.session._society_hosts(h, MockLLM())
        self.assertEqual(sorted(hosts), sorted(self.names))
        self.assertTrue(all(v is h for v in hosts.values()))

    def test_un_hote_pour_un_agent_inconnu_est_refuse(self):
        """Faute de frappe sur un nom d'agent : silencieuse et coûteuse."""
        hosts = {self.names[0]: Host(), "agent_fantome": Host()}
        with self.assertRaises(AgentLError) as ctx:
            self.session._society_hosts(hosts, None)
        self.assertIn("agent_fantome", str(ctx.exception))

    def test_un_faux_hote_est_refuse_avec_le_nom_de_l_agent(self):
        hosts = {self.names[0]: Host(), self.names[1]: {"pas": "un hôte"}}
        with self.assertRaises(AgentLError) as ctx:
            self.session._society_hosts(hosts, None)
        self.assertIn(self.names[1], str(ctx.exception))


class TestCliSociety(unittest.TestCase):
    """`agentl run` sur une société : plus d'AttributeError par capteur."""

    def test_run_multi_agents(self):
        import contextlib
        import io

        from agentl.cli import main

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["run", str(ROOT / AGENT), "--ticks", "3", "--quiet"])
        out = buf.getvalue()
        self.assertEqual(code, 0)
        self.assertNotIn("AttributeError", out)
        self.assertNotIn("'dict' object has no attribute", out)
        self.assertIn("MESSAGES", out)
        self.assertIn("mémoire partagée", out)
        self.assertIn("messages_sent=", out)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

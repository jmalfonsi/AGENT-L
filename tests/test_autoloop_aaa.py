"""Tests AAA de l'auto-amélioration (autoloop.py, CLI `agentl autoloop`).

Une boucle d'auto-amélioration est un outil dont la façon de mentir est
connue d'avance : elle finit par afficher 100 %. Les propriétés visées ici
portent donc d'abord sur ce qui pourrait la rendre **verte à tort**.

  * elle ne juge que des **invariants** — ce qui doit tenir quelles que soient
    les données. Une éventualité (`escalation.sent == yes`) dépend justement
    des données qu'on vient de changer : elle n'est jamais jugée ;
  * le **contexte de décision est gelé** dans l'étage « données », fermeture
    par les évidences d'hypothèse comprise — sans quoi la boucle imputerait au
    programme une faute commise par son propre générateur de cas ;
  * le **lot retenu** n'est jamais montré : ni exécuté pendant la boucle, ni
    cité dans une consigne de correction. Sinon il ne mesure plus rien ;
  * 100 % sur les cas vus et moins sur le lot retenu est un **échec nommé**
    (`overfit`, code de sortie 3), pas une réussite imparfaite ;
  * elle **s'arrête** : plafond, budget, absence de progrès ;
  * on ne monte pas son score en **supprimant l'épreuve** ;
  * et elle trouve ce qu'`agentl test` ne trouve pas — c'est sa seule raison
    d'exister.

    python3 -m pytest tests/test_autoloop_aaa.py -q
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentl.autoloop import (MISSING, Attempt, AutoloopReport, Case, CaseResult,
                             DryRunHost, Gate, autoloop, baseline_breaches,
                             build_prompt, compared_literals, decision_paths,
                             generate_cases, run_cases, run_gates,
                             unconditional_bans, variants, writer_for)
from agentl.core import Symbol
from agentl.parser import parse_source
from agentl.scenario import run_scenarios

EXAMPLES = ROOT / "examples"

#: Un agent qui purge un répertoire par lots. `dir.batch` est la seule donnée
#: qui n'entre dans **aucune** décision : c'est donc la seule que l'étage
#: « données » a le droit de faire varier — et c'est là qu'est le défaut, car
#: rien ne protège la soustraction contre un capteur muet.
BASE = """
AGENT purger {
    VERSION "1.0"
    OBSERVE { dir.files  dir.protected  dir.batch  purge.done  alarm.sent }
    BELIEF { purge.done = no CONFIDENCE 0.9 SOURCE prior }
    GOAL tenir { MAINTAIN purge.done == yes }
    TOOL purge {
        OUTPUT { ok: Symbol }
        RISK LOW
        REQUIRES dir.files > 10%(garde)s
        EFFECT { dir.files = dir.files - dir.batch  purge.done = yes ok = yes INTERNAL }
    }
    TOOL prevenir {
        OUTPUT { ok: Symbol }
        RISK LOW
        EFFECT { alarm.sent = yes ok = yes INTERNAL }
    }
    POLICY {
        DEFAULT DENY
        ALLOW purge
        ALLOW prevenir
        NEVER purge WHEN dir.protected == yes
    }
    PLAN alerter WHEN dir.protected == yes { STEP dire { prevenir() } }
    PLAN nettoyer WHEN dir.files > 10 { STEP go { purge() } }
    DECIDE {
        RULES {
            IF planner.exhausted THEN alerter
        }
    }
%(scenario)s
    LOOP UNTIL goal.satisfied MAX 3 {
        OBSERVE UPDATE_BELIEFS EVALUATE_GOALS SELECT_PLAN EXECUTE VERIFY
    }
}
"""

SCENARIO = """    SCENARIO on_ne_vide_jamais_tout {
        GIVEN { dir.files = 42  dir.protected = no  dir.batch = 10 }
        EXPECT { dir.files != 0 } WITHIN 3
    }
"""

FAUTIF = BASE % {"garde": "", "scenario": SCENARIO}
CORRIGE = BASE % {"garde": " AND dir.batch > 0", "scenario": SCENARIO}
SANS_SCENARIO = BASE % {"garde": " AND dir.batch > 0", "scenario": ""}


def _agent(source: str = FAUTIF):
    return parse_source(source).agents[0]


# --------------------------------------------------------------------------
# M1 — ce que la boucle a le droit de changer
# --------------------------------------------------------------------------
class ContexteDeDecision(unittest.TestCase):
    def test_un_chemin_qui_decide_est_gele(self):
        # ARRANGE
        agent = _agent()
        # ACT
        frozen = decision_paths(agent, extra=list(agent.scenarios[0].expect))
        # ASSERT
        self.assertIn("dir.protected", frozen)   # garde de POLICY
        self.assertIn("dir.files", frozen)       # REQUIRES, WHEN et l'attente
        self.assertNotIn("dir.batch", frozen)    # pure donnée

    def test_les_evidences_d_une_hypothese_citee_sont_gelees(self):
        # Sans la fermeture par les hypothèses, faire varier un compteur qui
        # n'apparaît dans aucune garde déplacerait quand même le postérieur,
        # franchirait le seuil d'un `ALLOW … IF P(…)`, et l'agent agirait — à
        # bon droit. La faute serait imputée au programme au lieu du
        # générateur de cas.
        # ARRANGE
        agent = parse_source((EXAMPLES / "soc_analyst.agent").read_text()).agents[0]
        hypothesis = next(h for h in agent.hypotheses
                          if h.name == "credential_attack")
        # ACT
        frozen = decision_paths(agent)
        # ASSERT
        from agentl.autoloop import _paths_in
        evidence = _paths_in(hypothesis.evidence)
        self.assertTrue(evidence)
        self.assertTrue(evidence <= frozen,
                        f"évidences laissées libres : {evidence - frozen}")

    def test_l_etage_donnees_ne_touche_qu_aux_donnees(self):
        # ARRANGE / ACT
        cases = generate_cases(_agent(), max_cases=200, holdout_ratio=0.0)
        # ASSERT
        touched = {path for case in cases if case.tier == "données"
                   for path, _ in case.changes}
        self.assertEqual(touched, {"dir.batch"})


# --------------------------------------------------------------------------
# M2 — les valeurs proposées
# --------------------------------------------------------------------------
class Valeurs(unittest.TestCase):
    def test_l_absence_est_toujours_proposee(self):
        # La panne la moins écrite dans un test et la plus fréquente en
        # production : le capteur n'a rien rendu.
        for value in (10, Symbol("yes"), "logs", 0.5):
            self.assertIn(MISSING, variants(value, []))

    def test_les_seuils_du_programme_sont_encadres(self):
        # ARRANGE
        agent = _agent()
        # ACT
        literals = compared_literals(agent, "dir.files")
        proposed = variants(42, literals)
        # ASSERT
        self.assertIn(10, literals)              # `dir.files > 10`
        for edge in (9, 10, 11):
            self.assertIn(edge, proposed)

    def test_un_symbole_non_booleen_ne_devient_pas_oui(self):
        # Proposer `yes` pour un niveau de menace ne teste rien : cela
        # fabrique une donnée qu'aucun capteur ne produirait.
        # ACT
        proposed = variants(Symbol("CRITICAL"), [Symbol("LOW")])
        # ASSERT
        names = {v.name for v in proposed if isinstance(v, Symbol)}
        self.assertIn("LOW", names)
        self.assertNotIn("yes", names)

    def test_un_booleen_bascule(self):
        # ACT
        proposed = variants(Symbol("yes"), [])
        # ASSERT
        self.assertIn("no", {v.name for v in proposed if isinstance(v, Symbol)})


# --------------------------------------------------------------------------
# M3 — ce que la boucle juge
# --------------------------------------------------------------------------
class Jugement(unittest.TestCase):
    def test_elle_trouve_ce_que_test_ne_trouve_pas(self):
        # La seule raison d'exister de la commande. `agentl test` est vert :
        # l'attente de l'auteur est satisfaite dans le monde de l'auteur.
        # ARRANGE
        agent = _agent(FAUTIF)
        self.assertTrue(run_scenarios(agent).passed)
        # ACT
        report = autoloop(FAUTIF, max_cases=200, holdout_ratio=0.0)
        # ASSERT
        self.assertTrue(report.gates_ok)
        failures = report.last.failures()
        self.assertEqual(len(failures), 1)
        self.assertEqual(failures[0].case.changes, (("dir.batch", MISSING),))
        self.assertIn("UNDEFINED", failures[0].error or " ".join(failures[0].broken))

    def test_la_correction_attendue_referme_le_cas(self):
        # ACT
        report = autoloop(CORRIGE, max_cases=200, holdout_ratio=0.0)
        # ASSERT
        self.assertTrue(report.ok, report.render())
        self.assertEqual(report.stopped_by, "réussite")

    def test_une_eventualite_n_est_jamais_jugee(self):
        # `dir.files != 0` est vraie au départ : c'est un invariant, jugé dans
        # l'étage « données ». Dans l'étage « contexte » on a changé la raison
        # même qui la rend vraie : plus rien de déclaré n'y est jugé.
        # ARRANGE
        agent = _agent(CORRIGE)
        scenario = agent.scenarios[0]
        case = Case(scenario.name, "essai", "contexte",
                    (("dir.files", 0),), False)
        # ACT
        result = run_cases(agent, [case])[0]
        # ASSERT
        self.assertTrue(result.passed, result.render())
        self.assertFalse([b for b in result.broken if "invariant" in b])

    def test_une_rupture_deja_presente_n_est_pas_imputee_au_cas(self):
        # Sans cette soustraction, tous les cas d'un scénario dont l'exécution
        # lève déjà une erreur seraient rouges pour une raison que la mutation
        # n'a pas causée, et le vrai signal se noierait.
        # ARRANGE
        agent = _agent(FAUTIF)
        scenario = agent.scenarios[0]
        # ACT
        already = baseline_breaches(agent, scenario)
        neutre = run_cases(agent, [Case(scenario.name, "neutre", "contexte",
                                        (("dir.protected", Symbol("yes")),))])[0]
        # ASSERT
        self.assertEqual(already, [])
        self.assertTrue(neutre.passed, neutre.render())

    def test_une_rupture_du_monde_declare_a_sa_propre_barriere(self):
        # Un scénario peut être vert — son EXPECT est satisfaite — alors que
        # l'exécution a levé une erreur en chemin. Aucune commande existante ne
        # le disait.
        # ARRANGE : le programme parcourt une collection que le GIVEN ne
        # fournit pas. L'attente de l'auteur reste satisfaite — `agentl test`
        # est vert — mais l'exécution a levé une erreur en chemin.
        source = CORRIGE.replace(
            "PLAN nettoyer WHEN dir.files > 10 { STEP go { purge() } }",
            "PLAN nettoyer WHEN dir.files > 10 { STEP go { purge() }\n"
            "        STEP tracer { FOREACH e IN dir.entries MAX 5 "
            "{ prevenir() } } }")
        program = parse_source(source)
        # TEST refuse maintenant les erreurs d'exécution même si EXPECT tient.
        self.assertFalse(run_scenarios(program.agents[0]).passed)
        # ACT
        gates = {g.name: g for g in run_gates(program)}
        # ASSERT
        self.assertFalse(gates["invariants (monde déclaré)"].passed)
        self.assertIn("U1", gates["invariants (monde déclaré)"].detail)


# --------------------------------------------------------------------------
# M4 — la POLICY, jugée sans redemander au runtime
# --------------------------------------------------------------------------
class Interdits(unittest.TestCase):
    def test_seules_les_regles_sans_garde_sont_retenues(self):
        # Une règle gardée dépend des données : la juger reviendrait à
        # redemander au runtime ce qu'il vient de décider.
        # ARRANGE / ACT
        forbidden, approval = unconditional_bans(_agent())
        # ASSERT
        self.assertNotIn("purge", forbidden)     # NEVER … WHEN : gardé
        self.assertEqual(approval, set())

    def test_sous_default_deny_un_outil_jamais_autorise_est_interdit(self):
        # ARRANGE
        source = FAUTIF.replace("        ALLOW prevenir\n", "")
        # ACT
        forbidden, _ = unconditional_bans(parse_source(source).agents[0])
        # ASSERT
        self.assertIn("prevenir", forbidden)


# --------------------------------------------------------------------------
# M5 — le lot retenu
# --------------------------------------------------------------------------
class LotRetenu(unittest.TestCase):
    def test_le_partage_est_stable_et_disjoint(self):
        # ARRANGE / ACT
        first = generate_cases(_agent(), seed=7, holdout_ratio=0.3)
        second = generate_cases(_agent(), seed=7, holdout_ratio=0.3)
        # ASSERT
        self.assertEqual([c.label for c in first], [c.label for c in second])
        seen = {c.label for c in first if not c.holdout}
        held = {c.label for c in first if c.holdout}
        self.assertTrue(held)
        self.assertEqual(seen & held, set())

    def test_aucun_cas_retenu_n_est_cite_dans_une_consigne(self):
        # S'il l'était, le rédacteur pourrait viser l'épreuve finale, et le lot
        # retenu ne mesurerait plus rien.
        # ARRANGE
        prompts = []

        def espion(prompt: str) -> str:
            prompts.append(prompt)
            return FAUTIF                        # obstiné : ne corrige rien

        # ACT : part retenue choisie sous le cas fautif, sans quoi la boucle
        # n'aurait rien à corriger et ne rédigerait aucune consigne.
        report = autoloop(FAUTIF, rewrite=espion, max_attempts=3, patience=9,
                          holdout_ratio=0.15, seed=3, max_cases=200)
        # ASSERT
        self.assertTrue(prompts)
        held = [c for c in generate_cases(_agent(), seed=3, holdout_ratio=0.15)
                if c.holdout]
        self.assertTrue(held)
        for prompt in prompts:
            for case in held:
                self.assertNotIn(case.label, prompt)

    def test_le_par_coeur_est_nomme_et_n_est_pas_une_reussite(self):
        # ARRANGE : tout est vert sur ce que la boucle a vu, une rupture sur ce
        # qu'elle n'a pas vu.
        attempt = Attempt(1, FAUTIF, [Gate("analyse", True)])
        case = Case("s", "retenu", "données", (("dir.batch", MISSING),), True)
        report = AutoloopReport(attempts=[attempt],
                                holdout=[CaseResult(case, False, ["U1 …"])])
        # ACT / ASSERT
        self.assertTrue(report.seen_ok)
        self.assertTrue(report.overfit)
        self.assertFalse(report.ok)
        self.assertIn("appris par cœur", report.render())


# --------------------------------------------------------------------------
# M6 — l'arrêt
# --------------------------------------------------------------------------
class Arret(unittest.TestCase):
    def test_sans_redacteur_elle_diagnostique_et_s_arrete(self):
        # ACT
        report = autoloop(FAUTIF, max_cases=200, holdout_ratio=0.0)
        # ASSERT
        self.assertEqual(len(report.attempts), 1)
        self.assertIn("rédacteur", report.stopped_by)

    def test_un_redacteur_qui_tourne_en_rond_est_arrete(self):
        # « boucler jusqu'à 100 % » n'est pas une condition d'arrêt.
        # ARRANGE
        calls = []

        def obstine(prompt: str) -> str:
            calls.append(prompt)
            return FAUTIF

        # ACT
        report = autoloop(FAUTIF, rewrite=obstine, max_attempts=50, patience=2,
                          max_cases=200, holdout_ratio=0.0)
        # ASSERT
        self.assertIn("aucun progrès", report.stopped_by)
        self.assertLessEqual(len(report.attempts), 4)

    def test_le_plafond_de_tentatives_tient(self):
        # ARRANGE : un rédacteur qui progresse d'un cheveu à chaque tour
        # échappe au détecteur d'immobilité ; il reste le plafond.
        def bavard(prompt: str) -> str:
            return FAUTIF.replace("VERSION \"1.0\"",
                                  f"VERSION \"1.{len(prompt) % 9}\"")

        # ACT
        report = autoloop(FAUTIF, rewrite=bavard, max_attempts=3, patience=99,
                          max_cases=200, holdout_ratio=0.0)
        # ASSERT
        self.assertEqual(len(report.attempts), 3)
        self.assertIn("plafond", report.stopped_by)

    def test_un_programme_illisible_est_renvoye_au_redacteur(self):
        # ARRANGE
        def repare(prompt: str) -> str:
            return f"```agent\n{CORRIGE}\n```"     # balises comprises

        # ACT
        report = autoloop("AGENT { ceci n'est pas un programme",
                          rewrite=repare, max_attempts=4, max_cases=200,
                          holdout_ratio=0.0)
        # ASSERT
        self.assertIsNotNone(report.attempts[0].parse_error)
        self.assertTrue(report.ok, report.render())

    def test_une_correction_convergente_est_reconnue(self):
        # ARRANGE
        def repare(prompt: str) -> str:
            self.assertIn("dir.batch", prompt)     # la consigne porte le défaut
            return CORRIGE

        # ACT
        report = autoloop(FAUTIF, rewrite=repare, max_attempts=5,
                          max_cases=200, holdout_ratio=0.0)
        # ASSERT
        self.assertEqual(report.stopped_by, "réussite")
        self.assertEqual(len(report.attempts), 2)
        self.assertEqual(report.source, CORRIGE.strip())


# --------------------------------------------------------------------------
# M7 — on ne monte pas son score en supprimant l'épreuve
# --------------------------------------------------------------------------
class Triche(unittest.TestCase):
    def test_supprimer_un_scenario_ne_fait_pas_monter_le_score(self):
        # Les cas sont engendrés une fois, depuis la première version
        # recevable : les réengendrer donnerait à la boucle le pouvoir de
        # choisir ses propres épreuves.
        # ARRANGE
        def tricheur(prompt: str) -> str:
            return SANS_SCENARIO

        # ACT
        report = autoloop(FAUTIF, rewrite=tricheur, max_attempts=3, patience=9,
                          max_cases=200, holdout_ratio=0.0)
        # ASSERT
        self.assertFalse(report.ok)
        disparus = [r for r in report.attempts[-1].results
                    if "disparu" in (r.error or "")]
        self.assertTrue(disparus)

    def test_la_consigne_le_dit_au_redacteur(self):
        # ACT
        prompt = build_prompt(FAUTIF, [], [], 2)
        # ASSERT
        self.assertIn("supprimer une épreuve", prompt.lower())


# --------------------------------------------------------------------------
# M8 — le second temps
# --------------------------------------------------------------------------
class SecondTemps(unittest.TestCase):
    class _Hote:
        def __init__(self):
            self.ecrit = []

        def read(self, path):
            return None

        def drain(self):
            return []

        def invoke(self, name, args):
            self.ecrit.append(name)
            return {}

        def approve(self, request):
            return True                          # complaisant : à ne pas suivre

        def ask(self, question, reason=""):
            return Symbol("oui")

        def emit(self, source, **payload):
            pass

    def test_un_outil_qui_ecrit_n_est_pas_execute(self):
        # ARRANGE
        agent = _agent(CORRIGE)
        agent.tool("prevenir").side_effects = ["envoie une alerte"]
        inner = self._Hote()
        guarded = DryRunHost(inner, agent)
        # ACT
        guarded.invoke("prevenir", {})
        guarded.invoke("purge", {})
        # ASSERT
        self.assertEqual(inner.ecrit, ["purge"])
        self.assertEqual(guarded.refused, ["prevenir"])
        self.assertEqual(guarded.calls, ["prevenir", "purge"])

    def test_l_approbation_n_est_jamais_accordee(self):
        # Un agent encore en correction ne doit pas obtenir un feu vert d'un
        # hôte complaisant.
        # ARRANGE / ACT
        guarded = DryRunHost(self._Hote(), _agent(CORRIGE))
        # ASSERT
        self.assertFalse(guarded.approve(None))


# --------------------------------------------------------------------------
# M9 — la ligne de commande
# --------------------------------------------------------------------------
class LigneDeCommande(unittest.TestCase):
    def test_trois_verdicts_distincts(self):
        # 0 tient · 1 ne tient pas · 3 appris par cœur. Confondre les deux
        # derniers ferait passer le plus dangereux pour le plus bénin.
        import io
        from contextlib import redirect_stderr, redirect_stdout

        from agentl.cli import main

        with tempfile() as (bon, mauvais):
            cas = (
                # Sans lot retenu : l'agent tient, l'agent ne tient pas.
                (bon, ["--holdout", "0"], 0),
                (mauvais, ["--holdout", "0"], 1),
                # Avec le lot retenu par défaut, le seul cas fautif y tombe :
                # la boucle est à 100 % sur ce qu'elle a vu et cède sur le
                # reste. C'est l'état que le code 3 nomme.
                (mauvais, ["--holdout", "0.3"], 3),
            )
            for path, options, expected in cas:
                buffer = io.StringIO()
                with redirect_stdout(buffer), redirect_stderr(io.StringIO()):
                    code = main(["autoloop", str(path), "--max-cases", "200",
                                 *options])
                self.assertEqual(code, expected, buffer.getvalue())

    def test_la_source_d_origine_n_est_jamais_ecrasee(self):
        import io
        from contextlib import redirect_stderr, redirect_stdout

        from agentl.cli import main

        with tempfile() as (bon, mauvais):
            avant = mauvais.read_text()
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                main(["autoloop", str(mauvais), "--holdout", "0"])
            self.assertEqual(mauvais.read_text(), avant)

    def test_un_modele_inconnu_est_refuse(self):
        from agentl.core import AgentLError

        with self.assertRaises(ValueError):
            writer_for("mistral-large")
        self.assertTrue(callable(writer_for("gemini-3.1-flash-lite")))
        self.assertIsNotNone(AgentLError)


import contextlib
import tempfile as _tempfile


@contextlib.contextmanager
def tempfile():
    """Écrit les deux programmes sur disque : la CLI lit un fichier."""
    with _tempfile.TemporaryDirectory() as directory:
        bon = Path(directory) / "bon.agent"
        mauvais = Path(directory) / "mauvais.agent"
        bon.write_text(CORRIGE, encoding="utf-8")
        mauvais.write_text(FAUTIF, encoding="utf-8")
        yield bon, mauvais


if __name__ == "__main__":
    unittest.main()

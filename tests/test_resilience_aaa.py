"""Résilience : ce que fait le runtime quand un étage tombe.

Ces trois défauts ont été trouvés par une épreuve de panne, pas par une
relecture : l'oracle coupé et les outils en 503 sur une tâche réelle. Le
runtime survivait déjà — c'était l'acquis — mais il rejouait sans jamais
réessayer, martelait un service mort 122 fois de suite, et laissait une
réponse d'oracle tronquée passer pour une réponse neutre.

Le critère n'est jamais « l'agent réussit » : un agent privé d'oracle doit
échouer. C'est **comment** il échoue qui se teste ici.
"""
from __future__ import annotations

import sys
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentl import Host, MockLLM, Runtime, parse_source
from agentl import trivalent
from agentl.state import Evaluator, State
from agentl.analyzer import Analyzer
from agentl.llm import (call_with_retry, is_transient, missing_from,
                        retry_after)


def http_error(code: int, headers=None) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://exemple.test", code, "boom",
                                  headers or {}, None)


# ------------------------------------------------- 1. reprise sur transitoire
class LaRepriseDistingueLaPanneDeLErreurDeContrat(unittest.TestCase):
    def test_les_pannes_serveur_et_de_quota_sont_transitoires(self):
        for code in (408, 429, 500, 502, 503, 504):
            self.assertTrue(is_transient(http_error(code)), code)

    def test_une_erreur_de_contrat_ne_l_est_pas(self):
        """Rejouer un 401 ne le transformera jamais en 200."""
        for code in (400, 401, 403, 404, 422):
            self.assertFalse(is_transient(http_error(code)), code)

    def test_le_reseau_coupe_est_transitoire(self):
        self.assertTrue(is_transient(urllib.error.URLError("dns")))
        self.assertTrue(is_transient(TimeoutError("délai dépassé")))
        self.assertTrue(is_transient(ConnectionError("connexion perdue")))

    def test_un_bug_du_programme_n_est_pas_transitoire(self):
        self.assertFalse(is_transient(ValueError("schéma illisible")))

    def test_un_second_essai_recolte_la_reponse(self):
        essais = []
        dodos = []

        def flaky():
            essais.append(1)
            if len(essais) < 2:
                raise http_error(503)
            return "ok"

        out = call_with_retry(flaky, attempts=3, sleep=dodos.append)
        self.assertEqual(out, "ok")
        self.assertEqual(len(essais), 2)
        self.assertEqual(len(dodos), 1)

    def test_une_erreur_de_contrat_ne_dort_jamais(self):
        essais, dodos = [], []

        def refuse():
            essais.append(1)
            raise http_error(401)

        with self.assertRaises(urllib.error.HTTPError):
            call_with_retry(refuse, attempts=5, sleep=dodos.append)
        self.assertEqual(len(essais), 1, "un 401 rejoué brûle du quota pour rien")
        self.assertEqual(dodos, [])

    def test_le_nombre_d_essais_est_borne(self):
        essais, dodos = [], []

        def toujours_casse():
            essais.append(1)
            raise http_error(503)

        with self.assertRaises(urllib.error.HTTPError):
            call_with_retry(toujours_casse, attempts=3, sleep=dodos.append)
        self.assertEqual(len(essais), 3)

    def test_le_budget_de_temps_prime_sur_le_nombre_d_essais(self):
        """Un agent régulé ne peut pas attendre indéfiniment."""
        dodos = []

        def toujours_casse():
            raise http_error(503)

        with self.assertRaises(urllib.error.HTTPError):
            call_with_retry(toujours_casse, attempts=99, base_delay=10.0,
                            budget=1.0, sleep=dodos.append)
        self.assertEqual(dodos, [], "aucun sommeil ne doit dépasser le budget")

    def test_le_delai_demande_par_le_fournisseur_est_respecte(self):
        self.assertEqual(retry_after(http_error(429, {"Retry-After": "7"})), 7.0)

    def test_le_delai_annonce_dans_le_corps_est_lu_aussi(self):
        """Gemini le met dans le corps JSON, pas dans un en-tête."""
        self.assertEqual(retry_after(RuntimeError("quota: retryDelay: 12s")), 12.0)
        self.assertEqual(retry_after(RuntimeError("please retry in 3.5s")), 3.5)

    def test_sans_indication_il_n_y_a_pas_de_delai_devine(self):
        self.assertIsNone(retry_after(http_error(503)))

    def test_le_recul_croit_et_reste_plafonne(self):
        dodos = []

        def toujours_casse():
            raise http_error(503)

        with self.assertRaises(urllib.error.HTTPError):
            call_with_retry(toujours_casse, attempts=5, base_delay=1.0,
                            max_delay=4.0, budget=10_000, sleep=dodos.append)
        self.assertEqual(len(dodos), 4)
        self.assertLess(dodos[0], dodos[-1], "le recul doit croître")
        self.assertTrue(all(d <= 4.0 for d in dodos), "plafond non respecté")


# ------------------------------------------------------------ 2. disjoncteur
PROG_OUTIL = """
AGENT sonde {
  VERSION "1.8"
  GOAL fini { MAINTAIN fait == yes }
  BELIEF { fait = no CONFIDENCE 1.00 SOURCE prior }
  TOOL ping {
    OUTPUT { pong: Symbol }
    RISK LOW
    EFFECT { fait = yes }
    COST 1
  }
  POLICY { DEFAULT ALLOW }
  PLAN battre WHEN fait != yes {
    STEP frappe { ping() }
  }
  LOOP UNTIL goal.satisfied MAX 12 {
    OBSERVE
    UPDATE_BELIEFS
    EVALUATE_GOALS
    SELECT_PLAN
    EXECUTE
    VERIFY
  }
}
"""


def sonde(defaillant: bool, echecs_puis_ok: int = 0):
    """Hôte dont l'outil échoue `echecs_puis_ok` fois avant de guérir."""
    host = Host()
    appels = {"n": 0}

    @host.tool("ping")
    def ping():
        appels["n"] += 1
        if defaillant and appels["n"] <= echecs_puis_ok:
            raise RuntimeError("service indisponible (503)")
        if defaillant and echecs_puis_ok == 0:
            raise RuntimeError("service indisponible (503)")
        return {"pong": "ok"}

    return host, appels


class LeDisjoncteurCesseDeMartelerUnServiceMort(unittest.TestCase):
    def agent(self):
        return parse_source(PROG_OUTIL).agents[0]

    def test_sans_disjoncteur_le_martelage_continue(self):
        """Le comportement historique reste accessible : seuil à zéro."""
        host, appels = sonde(defaillant=True)
        rt = Runtime(self.agent(), host, MockLLM(), tool_breaker=0)
        rt.run(max_ticks=12)
        self.assertGreaterEqual(appels["n"], 12)
        self.assertEqual(rt.metrics["circuit_open"], 0)

    def test_apres_le_seuil_l_outil_n_est_plus_appele(self):
        host, appels = sonde(defaillant=True)
        rt = Runtime(self.agent(), host, MockLLM(),
                     tool_breaker=3, tool_cooldown=100)
        rt.run(max_ticks=12)
        self.assertEqual(appels["n"], 3, "l'hôte ne doit plus être sollicité")
        self.assertGreater(rt.metrics["circuit_open"], 0)
        self.assertEqual(rt.metrics["tool_failures"], 3)

    def test_l_indisponibilite_devient_un_fait_lisible(self):
        """L'hôte fournit des FAITS : « cet outil est mort » en est un."""
        host, _ = sonde(defaillant=True)
        rt = Runtime(self.agent(), host, MockLLM(), tool_breaker=2)
        rt.run(max_ticks=6)
        self.assertIs(rt.state.get("tools.ping.available"), False)
        self.assertGreaterEqual(rt.state.get("tools.ping.failures"), 2)

    def test_un_succes_referme_le_disjoncteur(self):
        host, _ = sonde(defaillant=True, echecs_puis_ok=2)
        rt = Runtime(self.agent(), host, MockLLM(), tool_breaker=5)
        rt.run(max_ticks=8)
        self.assertIs(rt.state.get("tools.ping.available"), True)
        self.assertEqual(rt.state.get("tools.ping.failures"), 0)

    def test_la_demi_ouverture_laisse_repasser_un_sondage(self):
        """Un service revenu à la vie ne doit pas rester coupé jusqu'au bout."""
        host, appels = sonde(defaillant=True, echecs_puis_ok=3)
        rt = Runtime(self.agent(), host, MockLLM(),
                     tool_breaker=3, tool_cooldown=2)
        rt.run(max_ticks=12)
        self.assertGreater(appels["n"], 3, "le sondage de reprise n'a pas eu lieu")
        self.assertIs(rt.state.get("tools.ping.available"), True)

    def test_le_refus_du_disjoncteur_n_est_pas_un_refus_de_politique(self):
        """`blocked` compte les interdits ; les confondre fausse l'audit."""
        host, _ = sonde(defaillant=True)
        rt = Runtime(self.agent(), host, MockLLM(),
                     tool_breaker=2, tool_cooldown=100)
        rt.run(max_ticks=10)
        self.assertEqual(rt.metrics["blocked"], 0)
        self.assertGreater(rt.metrics["circuit_open"], 0)

    def test_la_trace_nomme_le_disjoncteur(self):
        host, _ = sonde(defaillant=True)
        rt = Runtime(self.agent(), host, MockLLM(),
                     tool_breaker=2, tool_cooldown=100)
        rt.run(max_ticks=8)
        textes = [f"{e.text} {e.detail}" for e in rt.trace.events]
        self.assertTrue(any("disjoncteur ouvert" in t for t in textes))


# --------------------------------------------------------- 3. oracle dégradé
PROG_REASON = """
AGENT juge {
  VERSION "1.8"
  GOAL fini { MAINTAIN tranche == yes }
  BELIEF {
    tranche = no CONFIDENCE 1.00 SOURCE prior
    verdict = unknown CONFIDENCE 0.10 SOURCE prior
    score = 0 CONFIDENCE 0.10 SOURCE prior
  }
  TOOL noter {
    INPUT { verdict: Symbol BIND verdict }
    OUTPUT { ok: Symbol }
    RISK LOW
    EFFECT { tranche = yes }
    COST 1
  }
  POLICY { DEFAULT ALLOW }
  PLAN juger WHEN tranche != yes {
    STEP peser {
      REASON {
        TASK "classer la demande"
        PRODUCE { verdict: Symbol, score: Number }
      }
    }
    STEP acter { noter(verdict: verdict) }
  }
  LOOP UNTIL goal.satisfied MAX 4 {
    OBSERVE
    UPDATE_BELIEFS
    EVALUATE_GOALS
    SELECT_PLAN
    EXECUTE
    VERIFY
  }
}
"""


class OracleMuet(MockLLM):
    """Oracle qui lève — panne réseau, clé invalide, service coupé."""

    def reason(self, task, context, produce):
        raise RuntimeError("oracle injoignable")


class OraclePartiel(MockLLM):
    """Oracle tronqué : le JSON s'arrête au milieu, un champ manque."""

    def reason(self, task, context, produce):
        from agentl.llm import _coerce
        payload = {"verdict": "accepte"}
        self.last_reason_missing = missing_from(payload, produce)
        return _coerce(payload, produce)


class OracleComplet(MockLLM):
    def reason(self, task, context, produce):
        from agentl.llm import _coerce
        payload = {"verdict": "accepte", "score": 0.9}
        self.last_reason_missing = missing_from(payload, produce)
        return _coerce(payload, produce)


class UneReponseTronqueeNeDoitPasPasserPourUneReponseNeutre(unittest.TestCase):
    def agent(self):
        return parse_source(PROG_REASON).agents[0]

    def run_with(self, oracle):
        host = Host()

        @host.tool("noter")
        def noter(verdict=None):
            return {"ok": "yes"}

        rt = Runtime(self.agent(), host, oracle)
        rt.run(max_ticks=4)
        return rt

    def test_un_oracle_muet_est_signale(self):
        rt = self.run_with(OracleMuet())
        self.assertIs(rt.state.get("reason.degraded"), True)
        self.assertGreater(rt.metrics["reason_degraded"], 0)

    def test_un_oracle_muet_laisse_une_ligne_dans_la_trace(self):
        rt = self.run_with(OracleMuet())
        textes = [e.text for e in rt.trace.events]
        self.assertTrue(any("oracle muet" in t for t in textes), textes)

    def test_un_oracle_muet_n_est_pas_une_erreur_d_execution(self):
        """`ERROR` veut dire « le runtime a fauté ». Un oracle qui se tait,
        non — et `autoloop` juge son invariant U1 sur ce type : les confondre
        faisait tomber la porte sur tout monde dérivé sans script."""
        rt = self.run_with(OraclePartiel())
        muets = [e for e in rt.trace.events
                 if e.kind == "ERROR" and "oracle muet" in e.text]
        self.assertEqual(muets, [])

    def test_un_oracle_complet_n_est_pas_degrade(self):
        rt = self.run_with(OracleComplet())
        self.assertIs(rt.state.get("reason.degraded"), False)
        self.assertEqual(rt.state.get("reason.missing"), 0)
        self.assertEqual(rt.metrics["reason_degraded"], 0)

    def test_une_reponse_partielle_se_compte_sans_crier_a_la_panne(self):
        """Un champ manquant n'est pas une panne d'oracle, mais ça se voit."""
        rt = self.run_with(OraclePartiel())
        self.assertIs(rt.state.get("reason.degraded"), False)
        self.assertEqual(rt.state.get("reason.missing"), 1)

    def test_le_verdict_ne_traine_pas_d_un_appel_a_l_autre(self):
        """Sans effacement, un REASON réussi héritait du verdict du précédent."""
        oracle = OracleComplet()
        rt = self.run_with(oracle)
        self.assertIs(rt.state.get("reason.degraded"), False)

    def test_missing_from_compte_les_absents(self):
        self.assertEqual(missing_from({"a": 1}, {"a": "Number", "b": "Symbol"}),
                         ["b"])
        self.assertEqual(missing_from(None, {"a": "Number"}), ["a"])
        self.assertEqual(missing_from({"a": 1}, {"a": "Number"}), [])


# ------------------------------------------------------- 4. garde-fou statique
class UnSchemaTropLongEstSignaleALaCompilation(unittest.TestCase):
    def programme(self, champs: int) -> str:
        produce = " ".join(f"c{i}: Symbol" for i in range(champs))
        return f"""
AGENT large {{
  VERSION "1.8"
  GOAL fini {{ MAINTAIN fait == yes }}
  BELIEF {{ fait = no CONFIDENCE 1.00 SOURCE prior }}
  POLICY {{ DEFAULT ALLOW }}
  PLAN p WHEN fait != yes {{
    STEP s {{
      REASON {{
        TASK "classer"
        PRODUCE {{ {produce} }}
      }}
    }}
  }}
  LOOP UNTIL goal.satisfied MAX 2 {{
    OBSERVE
    UPDATE_BELIEFS
    EVALUATE_GOALS
    SELECT_PLAN
    EXECUTE
    VERIFY
  }}
}}
"""

    def codes(self, champs: int):
        agent = parse_source(self.programme(champs)).agents[0]
        return [d.code for d in Analyzer(agent).run()]

    def test_huit_champs_passent(self):
        self.assertNotIn("W133", self.codes(8))

    def test_au_dela_du_seuil_l_avertissement_tombe(self):
        self.assertIn("W133", self.codes(12))

    def test_ce_n_est_qu_un_avertissement(self):
        agent = parse_source(self.programme(12)).agents[0]
        erreurs = [d for d in Analyzer(agent).run() if d.severity == "error"]
        self.assertEqual(erreurs, [], "W133 ne doit jamais bloquer une compilation")


# ------------------------------- 5. un fait booléen se compare comme un symbole
class UnFaitBooleenSeCompareCommeUnSymbole(unittest.TestCase):
    """Le pire mode de défaillance du langage : une garde qui ne mord pas.

    Le runtime pose `tools.<nom>.available` en `bool`, un `.agent` n'écrit que
    des symboles, et l'égalité comparait les deux par leur texte. `str(False)`
    valant `"False"`, l'idiome évident était faux **alors que l'outil était
    bien indisponible** — sans erreur, sans avertissement, sans trace.
    """

    def garde(self, expr: str, **faits):
        etat = State()
        for chemin, valeur in faits.items():
            etat.set_world(chemin.replace("__", "."), valeur)
        agent = parse_source(
            'AGENT t { VERSION "1.8" POLICY { NEVER x WHEN ' + expr + " } }"
        ).agents[0]
        return trivalent.evaluate(Evaluator(etat), agent.policies[0].guard)

    def test_l_interdit_mord_sur_un_outil_indisponible(self):
        for negatif in ("false", "no"):
            self.assertIs(
                self.garde(f"tools.envoi.available == {negatif}",
                           tools__envoi__available=False),
                True, negatif)

    def test_il_ne_mord_pas_sur_un_outil_disponible(self):
        for negatif in ("false", "no"):
            self.assertIs(
                self.garde(f"tools.envoi.available == {negatif}",
                           tools__envoi__available=True),
                False, negatif)

    def test_les_deux_vocabulaires_du_vrai(self):
        for positif in ("true", "yes"):
            self.assertIs(
                self.garde(f"reason.degraded == {positif}",
                           reason__degraded=True),
                True, positif)

    def test_le_capteur_muet_beneficie_du_meme_pont(self):
        """Le défaut touchait `sensors.*.available` depuis la v1.6."""
        self.assertIs(
            self.garde("sensors.src.read_ok.available == false",
                       sensors__src__read_ok__available=False),
            True)

    def test_un_symbole_hors_vocabulaire_n_est_egal_a_aucun_booleen(self):
        self.assertIs(
            self.garde("tools.envoi.available == pending",
                       tools__envoi__available=False),
            False)

    def test_un_entier_ne_devient_pas_un_booleen_au_passage(self):
        """`bool` hérite de `int` : le pont doit rester un test de type strict."""
        self.assertIs(
            self.garde("compteur.valeur == true", compteur__valeur=1),
            False)

    def test_un_chemin_jamais_pose_reste_indetermine(self):
        """Indéterminé n'est pas faux : sous un NEVER, il s'applique."""
        self.assertIs(self.garde("tools.absent.available == false"),
                      trivalent.UNKNOWN)


# ------------------------- 6. un DEFAULT doit déclencher l'interdit qu'il garde
class UnDefautDoitDeclencherLInterditQuIlGarde(unittest.TestCase):
    """W134 — toute panne d'oracle aboutit au `DEFAULT`.

    Troncature, JSON invalide, clé révoquée : le même chemin. Le défaut n'est
    donc pas une commodité de rédaction, c'est le comportement de l'agent en
    panne — et le principe « DEFAULT inoffensif » était énoncé dans le SKILL
    sans que rien ne le vérifie.
    """

    def programme(self, defaut: str, garde: str = "decision != approved") -> str:
        return f"""
AGENT verdict {{
  VERSION "1.8"
  GOAL fini {{ MAINTAIN travail.fait == yes }}
  OBSERVE {{ demande.message travail.fait }}
  TOOL appliquer {{
    OUTPUT {{ applique: Symbol }}
    RISK LOW
    EFFECT {{ travail.fait = yes }}
    COST 1
  }}
  POLICY {{
    DEFAULT ALLOW
    NEVER appliquer WHEN {garde}
  }}
  PLAN juger {{
    STEP classer {{
      REASON {{
        TASK "Trancher."
        USING {{ demande.message }}
        PRODUCE {{ decision: Symbol IN [approved, refused, unknown] DEFAULT {defaut} }}
      }}
    }}
    STEP agir {{ appliquer() }}
  }}
  LOOP UNTIL goal.satisfied MAX 2 {{
    OBSERVE
    UPDATE_BELIEFS
    EVALUATE_GOALS
    SELECT_PLAN
    EXECUTE
    VERIFY
  }}
}}
"""

    def codes(self, defaut: str, garde: str = "decision != approved"):
        agent = parse_source(self.programme(defaut, garde)).agents[0]
        return [d.code for d in Analyzer(agent).run()]

    def test_un_defaut_que_l_interdit_rejette_est_silencieux(self):
        self.assertNotIn("W134", self.codes("unknown"))

    def test_un_defaut_permissif_est_signale(self):
        """`DEFAULT approved` : l'oracle muet **autorise** l'action."""
        self.assertIn("W134", self.codes("approved"))

    def test_le_controle_se_tait_sur_une_garde_indeterminee(self):
        """Une garde restée indéterminée s'applique déjà (§7.1) : rien à dire.

        `faux OU indéterminé` vaut indéterminé — l'autre branche peut encore
        fermer l'action, et un avertissement ici serait du bruit.
        """
        self.assertNotIn(
            "W134",
            self.codes("approved", "decision != approved OR demande.urgent == yes"))

    def test_une_conjonction_ne_sauve_pas_un_defaut_permissif(self):
        """`faux ET indéterminé` vaut **faux** : l'interdit est bien désarmé.

        Ajouter une condition à côté d'un défaut permissif ne rattrape rien —
        c'est même la forme qui trompe le plus, parce qu'elle a l'air gardée.
        """
        self.assertIn(
            "W134",
            self.codes("approved", "decision != approved AND demande.urgent == yes"))

    def test_ce_n_est_qu_un_avertissement(self):
        agent = parse_source(self.programme("approved")).agents[0]
        erreurs = [d for d in Analyzer(agent).run() if d.severity == "error"]
        self.assertEqual(erreurs, [])

    def test_l_exemple_canonique_du_skill_reste_vert(self):
        chemin = ROOT / "SKILLS/agentl-author/references/generated/canonical.agent"
        agent = parse_source(chemin.read_text()).agents[0]
        self.assertNotIn("W134", [d.code for d in Analyzer(agent).run()])


if __name__ == "__main__":
    unittest.main()

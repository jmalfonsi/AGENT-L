"""Régressions de la composition VALMONT, entièrement hors ligne."""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "examples"))

from agentl import Symbol
import valmont_supervision
from valmont_sim import SimClient, SimUnavailable


class _State:
    def __init__(self, **values):
        self._values = values

    def get(self, path):
        return self._values[path]


class TestProductionConfidenceContract(unittest.TestCase):
    def test_relaunch_confidence_is_complement_of_utility_bottleneck_risk(self):
        runtime = SimpleNamespace(state=_State(
            **{"optim.conseil": Symbol("relancer"), "goulot": Symbol("aucun")}
        ))

        with patch.object(valmont_supervision, "_sub", return_value=(runtime, 0.001)):
            result = valmont_supervision._run_optimiseur({}, {})

        self.assertEqual(result["conseil_production"], Symbol("relancer"))
        self.assertEqual(result["confiance_production"], 0.999)


class _ChatEnTimeout:
    def chat(self, message):
        raise SimUnavailable("TimeoutError sur /chat : timed out")


class TestRoundClosure(unittest.TestCase):
    def test_chat_timeout_covers_the_server_llm_budget(self):
        client = SimClient(timeout=8.0, chat_timeout=35.0)

        with patch.object(client, "_request", return_value={"reply": "ok"}) as request:
            self.assertEqual(client.chat("statut"), "ok")

        request.assert_called_once_with(
            "/chat", payload={"message": "statut"}, timeout=35.0
        )

    def test_chat_timeout_does_not_prevent_local_closure(self):
        with patch.object(valmont_supervision, "SimClient", return_value=_ChatEnTimeout()):
            host, _ = valmont_supervision.build()

        result = host.tools["cloturer_ronde"](
            Symbol("surveillance"), Symbol("relancer")
        )

        self.assertEqual(result["cloturee"], Symbol("yes"))
        self.assertEqual(result["notification"], Symbol("incertaine"))
        self.assertEqual(host.sensors["ronde.status"](), Symbol("terminee"))


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# v1.9 — LES CONSIGNES DU RESPONSABLE DE SITE.
#
# Le responsable gagne un droit de PROPOSITION sur le catalogue complet du
# simulateur. Ce qui le borne, ce sont les quatre contrôles que l'agent conduit
# lui-même. Ces tests portent sur leurs ENTRÉES — les faits que l'hôte projette
# —, parce que c'est là que se joue la sûreté : une cible non attestée n'a pas
# de jeton, et sans jeton aucune commande ne peut avoir d'effet.
#
# Le client HTTP est le VRAI `SimClient` : seul le transport est remplacé. La
# conformité au schéma, la déduplication des alarmes et la résolution des
# cibles sont donc exercées telles qu'elles tournent en production.
# ---------------------------------------------------------------------------

_CATALOGUE = {
    "commands": [
        {"name": "alarm.ack", "role": "operator",
         "schema": {"type": "object", "properties": {"id": {"type": "string", "minLength": 1}},
                    "required": [], "additionalProperties": False}},
        {"name": "maintenance.repair", "role": "operator",
         "schema": {"type": "object", "properties": {"faultId": {"type": "string", "minLength": 1}},
                    "required": ["faultId"], "additionalProperties": False}},
        {"name": "genset.refuel", "role": "operator",
         "schema": {"type": "object", "properties": {"liters": {"type": "number", "minimum": 1, "maximum": 3000}},
                    "required": [], "additionalProperties": False}},
        {"name": "asset.stop", "role": "operator",
         "schema": {"type": "object", "properties": {"id": {"type": "string", "minLength": 1}},
                    "required": ["id"], "additionalProperties": False}},
        {"name": "sim.injectFault", "role": "operator",
         "schema": {"type": "object", "properties": {"type": {"type": "string", "minLength": 1}},
                    "required": ["type"], "additionalProperties": False}},
        {"name": "production.rate", "role": "operator",
         "schema": {"type": "object",
                    "properties": {"line": {"type": "string", "enum": ["ALL", "L1", "L3"]},
                                   "rate_pct": {"type": "number", "minimum": 0, "maximum": 100}},
                    "required": [], "additionalProperties": False}},
        {"name": "loadshed.set", "role": "operator",
         "schema": {"type": "object",
                    "properties": {"level": {"type": "integer", "minimum": 0, "maximum": 3}},
                    "required": ["level"], "additionalProperties": False}},
    ],
    "setpoints": [{"name": "AIR.PRESSION", "value": 7}],
}

_ALARMES = [
    {"id": "A-000065", "severity": "critical", "domain": "EAU", "source": "STEP",
     "message": "Rejet non conforme", "state": "ACTIVE", "ack": False,
     "faultId": None, "at": 300, "lastAt": 300},
    {"id": "A-000097", "severity": "minor", "domain": "GAZ", "source": "CH2",
     "message": "Rendement degrade", "state": "ACKED", "ack": True,
     "faultId": "F-00042", "at": 200, "lastAt": 200},
]


class _ClientDeBanc(SimClient):
    """Vrai `SimClient`, transport remplacé. Rien d'autre n'est simulé."""

    def __init__(self, commandes_du_responsable=None):
        super().__init__(dry_run=True)
        self.agent_events = []
        self.commandes_du_responsable = commandes_du_responsable or []
        self.envoyees = []
        self.messages = []
        self.production_state = {
            "lines": {
                "L1": {"state": "RUN", "rate_pct": 100.0, "actual_pct": 80.0},
                "L2": {"state": "RUN", "rate_pct": 100.0, "actual_pct": 80.0},
                "L3": {"state": "RUN", "rate_pct": 50.0, "actual_pct": 50.0},
                "L4": {"state": "RUN", "rate_pct": 100.0, "actual_pct": 50.0},
            },
            "avgRate_pct": 65.0,
        }

    def _post_agent_events(self, events):
        """Capture le journal dans le banc : aucune socket pendant les tests."""
        self.agent_events.extend(events)

    def _request(self, path, payload=None, headers=None, timeout=None):
        if path.startswith("/summary"):
            return {"alarms": {"unacknowledged": 1, "critical": 1},
                    "production": {"rate_pct": 76.8}}
        if path.startswith("/alarms"):
            return {"alarms": list(_ALARMES)}
        if path.startswith("/faults"):
            return {"faults": [{"id": "F-00042", "status": "ACTIVE"},
                               {"id": "F-00099", "status": "REPAIRING"}]}
        if path.startswith("/production"):
            return self.production_state
        if path.startswith("/water"):
            return {"wwtp": {"compliant": False}}
        if path.startswith("/assets"):
            return {"assets": [{"id": "CH2"}, {"id": "GEN1"}]}
        if path.startswith("/commands"):
            if payload is not None:
                self.envoyees.append(payload)
                return {"ok": True}
            return _CATALOGUE
        if path.startswith("/chat"):
            self.messages.append(payload["message"])
            return {"reply": "Voici la conduite.",
                    "commands": list(self.commandes_du_responsable)}
        raise SimUnavailable(f"chemin non prevu au banc : {path}")

class _ClientCadence(_ClientDeBanc):
    """L2 en SETUP, avec commande réellement simulée par le transport."""

    def __init__(self, consigne=100.0, applique=True, recu_ok=True):
        super().__init__([])
        self.dry_run = False
        self.applique = applique
        self.recu_ok = recu_ok
        self.production_state = {
            "lines": {
                "L1": {"state": "RUN", "rate_pct": consigne, "actual_pct": 100.0},
                "L2": {"state": "SETUP", "rate_pct": consigne, "actual_pct": 0.0},
                "L3": {"state": "RUN", "rate_pct": consigne, "actual_pct": 100.0},
                "L4": {"state": "RUN", "rate_pct": consigne, "actual_pct": 100.0},
            },
            "avgRate_pct": 75.0,
        }

    def _request(self, path, payload=None, headers=None, timeout=None):
        if path.startswith("/summary"):
            return {"alarms": {"unacknowledged": 0, "critical": 0},
                    "production": {"rate_pct": 75.0}}
        if path.startswith("/commands") and payload is not None:
            self.envoyees.append(payload)
            if not self.recu_ok:
                return {"ok": False, "error": "refus de banc"}
            if self.applique:
                cible = float(payload["args"]["rate_pct"])
                identifiant = payload["args"]["line"]
                lignes = (self.production_state["lines"].values()
                          if identifiant == "ALL"
                          else [self.production_state["lines"][identifiant]])
                for ligne in lignes:
                    ligne["rate_pct"] = cible
            return {"ok": True, "lines": [
                {"id": identifiant, "rate_pct": ligne["rate_pct"]}
                for identifiant, ligne in self.production_state["lines"].items()]}
        return super()._request(path, payload=payload, headers=headers,
                                timeout=timeout)


def _monter(client):
    with patch.object(valmont_supervision, "SimClient", return_value=client):
        host, _llm = valmont_supervision.build()
    return host


def _banc(commandes, tmp_registre=None):
    """Monte l'hôte du superviseur sur le client de banc."""
    return _monter(_ClientDeBanc(commandes))


def _consignes(host, arriere=1):
    host.tools["demander_conduite_alarmes"](arriere=arriere)
    return {str(p["commande"]): p for p in host.sensors["propositions"]()}



class _ClientAlarmesEnCascade(_ClientDeBanc):
    """Banc où acquitter une occurrence révèle la précédente de la condition."""

    def __init__(self, alarmes):
        super().__init__([])
        self.dry_run = False
        self.alarmes = [dict(a) for a in alarmes]

    def _request(self, path, payload=None, headers=None, timeout=None):
        if path.startswith("/summary"):
            actives = [a for a in self.alarmes
                       if a["state"] in ("ACTIVE", "ACKED")]
            return {
                "alarms": {
                    "unacknowledged": sum(
                        1 for a in self.alarmes
                        if not a["ack"] and a["state"] != "CLEARED"),
                    "critical": sum(
                        1 for a in actives if a["severity"] == "critical"),
                },
                "production": {"rate_pct": 92.0},
            }
        if path.startswith("/alarms"):
            etat = path.split("state=", 1)[1].split("&", 1)[0]
            rows = sorted(self.alarmes,
                          key=lambda a: a.get("lastAt", 0), reverse=True)
            if etat == "active":
                rows = [a for a in rows
                        if a["state"] in ("ACTIVE", "ACKED")]
            elif etat == "unacknowledged":
                rows = [a for a in rows
                        if not a["ack"] and a["state"] != "CLEARED"]
            return {"count": len(rows), "alarms": [dict(a) for a in rows]}
        if path.startswith("/faults"):
            return {"faults": []}
        if path.startswith("/water"):
            return {"wwtp": {"compliant": True}}
        if path.startswith("/commands") and payload is not None:
            self.envoyees.append(payload)
            if payload["command"] == "alarm.ack":
                cible = str(payload["args"]["id"])
                alarme = next(a for a in self.alarmes if a["id"] == cible)
                alarme["ack"] = True
                alarme["state"] = (
                    "CLEARED" if alarme["state"] == "CLEARED_UNACK"
                    else "ACKED")
                self._cache.clear()
                return {"ok": True, "acknowledged": [cible]}
        return super()._request(path, payload=payload, headers=headers,
                                timeout=timeout)


class TestTraitementDesOccurrencesDAlarme(unittest.TestCase):
    @staticmethod
    def _executer(client):
        def alarmiste(_payload, sink):
            resultat = {
                "verdict_alarme": Symbol("acquittement"),
                "classe_alarme": Symbol("information"),
                "confiance_alarme": 0.20,
            }
            sink.update(resultat)
            return resultat

        def optimiseur(_payload, sink):
            resultat = {
                "conseil_production": Symbol("maintenir"),
                "goulot_production": Symbol("aucun"),
                "confiance_production": 0.30,
            }
            sink.update(resultat)
            return resultat

        correctifs = (
            patch.object(valmont_supervision, "SimClient", return_value=client),
            patch.object(valmont_supervision, "_run_alarmiste", alarmiste),
            patch.object(valmont_supervision, "_run_optimiseur", optimiseur),
            patch.object(valmont_supervision, "_lire_registre", return_value=[]),
        )
        with correctifs[0], correctifs[1], correctifs[2], correctifs[3]:
            host, llm = valmont_supervision.build()
            agent = valmont_supervision.parse_file(
                str(ROOT / "examples" / "valmont_supervision.agent")).agents[0]
            valmont_supervision.Runtime(agent, host, llm).run()

    @staticmethod
    def _acquittements(client):
        return [p["args"]["id"] for p in client.envoyees
                if p["command"] == "alarm.ack"]

    def test_une_critique_revenue_a_la_normale_est_acquittee(self):
        client = _ClientAlarmesEnCascade([{
            "id": "A-000146", "severity": "critical", "domain": "EAU",
            "source": "STEP", "message": "Rejet non conforme", "detail": "",
            "state": "CLEARED_UNACK", "ack": False, "faultId": None,
            "at": 20, "lastAt": 20,
        }])

        self._executer(client)

        self.assertEqual(self._acquittements(client), ["A-000146"])

    def test_une_occurrence_revelee_apres_acquittement_est_traitee(self):
        client = _ClientAlarmesEnCascade([
            {"id": "A-000138", "severity": "minor", "domain": "ELEC",
             "source": "HTA", "message": "THD elevee", "detail": "",
             "state": "CLEARED_UNACK", "ack": False, "faultId": None,
             "at": 10, "lastAt": 10},
            {"id": "A-000139", "severity": "minor", "domain": "ELEC",
             "source": "HTA", "message": "THD elevee", "detail": "",
             "state": "CLEARED_UNACK", "ack": False, "faultId": None,
             "at": 20, "lastAt": 20},
        ])

        self._executer(client)

        self.assertCountEqual(self._acquittements(client),
                              ["A-000138", "A-000139"])

    def test_le_replay_des_25_occurrences_du_run_vide_tout_l_arriere(self):
        series = [
            ("STEP", "Rejet non conforme", "critical", 3),
            ("HTA", "THD elevee", "minor", 14),
            ("HTA", "Puissance proche limite", "minor", 6),
            ("BESS", "Charge basse", "minor", 1),
            ("Q24", "Depart proche calibre", "minor", 1),
        ]
        alarmes = []
        index = 100
        for source, message, severite, nombre in series:
            for _ in range(nombre):
                index += 1
                alarmes.append({
                    "id": f"A-{index:06d}", "severity": severite,
                    "domain": "EAU" if source == "STEP" else "ELEC",
                    "source": source, "message": message, "detail": "",
                    "state": "CLEARED_UNACK", "ack": False, "faultId": None,
                    "at": index, "lastAt": index,
                })
        client = _ClientAlarmesEnCascade(alarmes)

        self._executer(client)

        self.assertEqual(len(self._acquittements(client)), 25)
        self.assertTrue(all(a["state"] == "CLEARED" for a in client.alarmes))


class TestCadenceCommandeEtEffet(unittest.TestCase):
    """La consigne acceptee et son effet physique ne sont plus confondus."""

    def test_une_consigne_nominale_deja_posee_est_percue_sans_commande(self):
        client = _ClientCadence(consigne=100.0)
        host = _monter(client)

        self.assertEqual(host.sensors["site.consigne_l1_pct"](), 100.0)
        self.assertEqual(host.sensors["site.consigne_l4_pct"](), 100.0)
        self.assertEqual(host.sensors["site.etat_l2"](), Symbol("SETUP"))

        resultat = host.tools["constater_consigne_nominale"](
            cadence=75, etat_l1=Symbol("RUN"), etat_l2=Symbol("SETUP"),
            etat_l3=Symbol("RUN"), etat_l4=Symbol("RUN"))
        self.assertEqual(str(resultat["deja_posee"]), "yes")
        self.assertEqual(client.envoyees, [])
        self.assertEqual(host.sensors["site.commande_envoyee"](), Symbol("no"))
        self.assertEqual(host.sensors["ronde.volet_cadence"](), Symbol("traite"))

    def test_un_recu_ok_sans_postcondition_n_est_pas_declare_applique(self):
        client = _ClientCadence(consigne=60.0, applique=False)
        host = _monter(client)

        with self.assertRaisesRegex(RuntimeError, "non confirmee"):
            host.tools["relancer_cadence"](
                cible=100, goulot=Symbol("aucun"))
        self.assertEqual(len(client.envoyees), 1)
        self.assertEqual(host.sensors["site.commande_envoyee"](), Symbol("no"))

    def test_la_postcondition_confirmee_marque_la_commande_appliquee(self):
        client = _ClientCadence(consigne=60.0, applique=True)
        host = _monter(client)

        resultat = host.tools["relancer_cadence"](
            cible=100, goulot=Symbol("aucun"))
        self.assertEqual(str(resultat["appliquee"]), "yes")
        self.assertEqual(len(client.envoyees), 1)
        self.assertTrue(all(ligne["rate_pct"] == 100
                            for ligne in client.production_state["lines"].values()))
        self.assertEqual(host.sensors["site.commande_envoyee"](), Symbol("yes"))
        self.assertIn("line=L2 rate_pct=60.0", client.rollback_log[0]["rollback"])


class TestConformiteConsentementEtEffet(unittest.TestCase):
    """Le bridage applique exactement la cible soumise au responsable."""

    def test_la_question_nomme_la_cible_et_la_restitue(self):
        client = _ClientCadence(consigne=100.0)
        host = _monter(client)

        resultat = host.tools["consulter_sur_conformite"](charge=100, cible=50)

        self.assertEqual(resultat["cible_soumise"], 50.0)
        self.assertIn("50 %", client.messages[-1])

    def test_une_cible_differente_de_celle_soumise_est_refusee(self):
        client = _ClientCadence(consigne=100.0)
        host = _monter(client)
        host.tools["consulter_sur_conformite"](charge=100, cible=50)

        with self.assertRaisesRegex(RuntimeError, "cible.*approuvee"):
            host.tools["brider_pour_conformite"](cible=70, charge=100)
        self.assertEqual(client.envoyees, [])

    def test_un_recu_refuse_ne_marque_pas_le_bridage_applique(self):
        client = _ClientCadence(consigne=100.0, recu_ok=False)
        host = _monter(client)
        host.tools["consulter_sur_conformite"](charge=100, cible=50)

        with self.assertRaisesRegex(RuntimeError, "refusee"):
            host.tools["brider_pour_conformite"](cible=50, charge=100)
        self.assertEqual(host.sensors["site.commande_envoyee"](), Symbol("no"))

    def test_un_recu_ok_sans_postcondition_l3_n_est_pas_applique(self):
        client = _ClientCadence(consigne=100.0, applique=False)
        host = _monter(client)
        host.tools["consulter_sur_conformite"](charge=100, cible=50)

        with self.assertRaisesRegex(RuntimeError, "non confirmee"):
            host.tools["brider_pour_conformite"](cible=50, charge=100)
        self.assertEqual(host.sensors["site.commande_envoyee"](), Symbol("no"))

    def test_seule_l3_est_bridee_apres_confirmation(self):
        client = _ClientCadence(consigne=100.0, applique=True)
        host = _monter(client)
        host.tools["consulter_sur_conformite"](charge=100, cible=50)

        resultat = host.tools["brider_pour_conformite"](cible=50, charge=100)

        self.assertEqual(resultat["appliquee"], Symbol("yes"))
        self.assertEqual(client.production_state["lines"]["L3"]["rate_pct"], 50)
        self.assertEqual(client.production_state["lines"]["L1"]["rate_pct"], 100)
        self.assertEqual(host.sensors["site.commande_envoyee"](), Symbol("yes"))



class TestControleDeCoherence(unittest.TestCase):
    """CONTRÔLE 1 — une cible que l'agent n'a pas observée n'a pas de jeton."""

    def test_acquitter_tout_n_est_pas_attestable(self):
        # « acquitte tout » est valide au schéma et ne nomme AUCUNE entité :
        # c'est exactement la commande en bloc que la v1.9 remplace.
        vues = _consignes(_banc([{"command": "alarm.ack", "args": {"id": "*"}}]))

        vue = vues["alarm_ack"]
        self.assertEqual(str(vue["cible_attestee"]), "no")
        self.assertEqual(vue["jeton"], "aucun")

    def test_alarme_nommee_et_observee_est_attestee_avec_son_jeton(self):
        vues = _consignes(_banc([{"command": "alarm.ack",
                                  "args": {"id": "A-000065"}}]))

        vue = vues["alarm_ack"]
        self.assertEqual(str(vue["cible_attestee"]), "yes")
        self.assertNotEqual(vue["jeton"], "aucun")
        # Les faits qui nourriront le contrôle d'opportunité, pas un jugement.
        self.assertEqual(str(vue["cible_severite"]), "critical")
        self.assertEqual(str(vue["sans_objet"]), "no")

    def test_alarme_inventee_par_le_modele_n_est_pas_attestee(self):
        vues = _consignes(_banc([{"command": "alarm.ack",
                                  "args": {"id": "A-999999"}}]))

        self.assertEqual(str(vues["alarm_ack"]["cible_attestee"]), "no")
        self.assertEqual(vues["alarm_ack"]["jeton"], "aucun")

    def test_alarme_deja_acquittee_est_sans_objet(self):
        vues = _consignes(_banc([{"command": "alarm.ack",
                                  "args": {"id": "A-000097"}}]))

        self.assertEqual(str(vues["alarm_ack"]["sans_objet"]), "yes")

    def test_defaut_deja_pris_en_charge_n_est_pas_a_reparer(self):
        # F-00099 est `REPAIRING` : une équipe y est déjà. F-00042 est `ACTIVE`.
        vues = _consignes(_banc([
            {"command": "maintenance.repair", "args": {"faultId": "F-00099"}}]))
        self.assertEqual(str(vues["maintenance_repair"]["cible_attestee"]), "no")

        vues = _consignes(_banc([
            {"command": "maintenance.repair", "args": {"faultId": "F-00042"}}]))
        self.assertEqual(str(vues["maintenance_repair"]["cible_attestee"]), "yes")

    def test_ravitaillement_sans_identifiant_est_atteste_par_son_schema(self):
        host = _banc([{"command": "genset.refuel", "args": {"liters": 2000}}])
        vues = _consignes(host)

        vue = vues["genset_refuel"]
        self.assertEqual(str(vue["cible_genre"]), "sans_objet")
        self.assertEqual(str(vue["cible_attestee"]), "yes")
        self.assertNotEqual(vue["jeton"], "aucun")

        resultat = host.tools["executer_consigne"](
            rang=0,
            commande=vue["commande"],
            cible=vue["cible"],
            jeton=vue["jeton"],
            implication=Symbol("reappro_consommable"),
        )
        self.assertEqual(str(resultat["appliquee"]), "yes")
        self.assertEqual(host.sensors["site.commande_envoyee"](), Symbol("yes"))

    def test_argument_hors_des_bornes_publiees_est_rejete_par_le_schema(self):
        vues = _consignes(_banc([{"command": "loadshed.set",
                                  "args": {"level": 9}}]))

        self.assertEqual(str(vues["loadshed_set"]["args_valides"]), "no")

    def test_commande_absente_du_catalogue_est_nommee_comme_telle(self):
        vues = _consignes(_banc([{"command": "usine.autodestruction",
                                  "args": {}}]))

        # Le nom inventé ne devient JAMAIS un symbole du domaine : il est
        # écrasé sur `hors_catalogue`, que la POLICY refuse.
        self.assertIn("hors_catalogue", vues)
        self.assertEqual(str(vues["hors_catalogue"]["args_valides"]), "no")

    def test_hausse_de_cadence_est_un_fait_calcule_sur_la_ligne_visee(self):
        vues = _consignes(_banc([{"command": "production.rate",
                                  "args": {"line": "L3", "rate_pct": 90}}]))
        self.assertEqual(str(vues["production_rate"]["hausse_de_cadence"]), "yes")

        vues = _consignes(_banc([{"command": "production.rate",
                                  "args": {"line": "L3", "rate_pct": 20}}]))
        self.assertEqual(str(vues["production_rate"]["hausse_de_cadence"]), "no")


class TestJetonLieALaCible(unittest.TestCase):
    """Un jeton atteste UNE entité : c'est ce qui remplace le compteur global."""

    def test_un_jeton_emis_pour_une_alarme_ne_vaut_pas_pour_une_autre(self):
        host = _banc([])
        inventaire = {str(a["id"]): a for a in host.sensors["alarmes_actives"]()}
        jeton_de_la_critique = inventaire["A-000065"]["jeton"]

        with self.assertRaisesRegex(RuntimeError, "atteste"):
            host.tools["acquitter_une_alarme"](alarme="A-000097",
                                               jeton=jeton_de_la_critique)

    def test_un_jeton_ne_sert_qu_une_fois(self):
        host = _banc([])
        inventaire = {str(a["id"]): a for a in host.sensors["alarmes_actives"]()}
        jeton = inventaire["A-000065"]["jeton"]

        host.tools["escalader_une_alarme"](alarme="A-000065", equipement="STEP",
                                           motif="critique_sans_defaut_equipement",
                                           jeton=jeton)
        # La deuxième passe retrouve le travail de la première : elle SAUTE,
        # elle ne lève pas — sans quoi le disjoncteur s'ouvrirait pour rien.
        again = host.tools["escalader_une_alarme"](
            alarme="A-000065", equipement="STEP",
            motif="critique_sans_defaut_equipement", jeton=jeton)
        self.assertEqual(str(again["escaladee"]), "yes")

    def test_un_jeton_invente_ne_produit_aucun_effet(self):
        host = _banc([])

        with self.assertRaisesRegex(RuntimeError, "inconnu"):
            host.tools["acquitter_une_alarme"](alarme="A-000065",
                                               jeton="cible-inventee")


class TestInventaireComplet(unittest.TestCase):
    """PROPRIÉTÉ N°2 — aucune alarme ouverte ne reste sans disposition."""

    def test_le_compteur_ne_retombe_a_zero_que_lorsque_tout_est_dispose(self):
        host = _banc([])
        self.assertEqual(host.sensors["alarmes.non_traitees"](), 2)

        inventaire = {str(a["id"]): a for a in host.sensors["alarmes_actives"]()}
        host.tools["escalader_une_alarme"](
            alarme="A-000065", equipement="STEP",
            motif="critique_sans_defaut_equipement",
            jeton=inventaire["A-000065"]["jeton"])
        self.assertEqual(host.sensors["alarmes.non_traitees"](), 1)

        inventaire = {str(a["id"]): a for a in host.sensors["alarmes_actives"]()}
        host.tools["differer_une_alarme"](
            alarme="A-000097", motif=Symbol("deja_acquittee_cause_persistante"),
            jeton=inventaire["A-000097"]["jeton"])
        self.assertEqual(host.sensors["alarmes.non_traitees"](), 0)


class TestRegistreArme(unittest.TestCase):
    """CONTRÔLE 3 — armer n'est ni exécuter ni refuser, et ça survit."""

    def setUp(self):
        self._dir = Path(__file__).resolve().parent / "_registre_de_banc"
        patcher = patch.object(valmont_supervision, "ETAT_DIR", self._dir)
        patcher.start(); self.addCleanup(patcher.stop)
        patcher = patch.object(valmont_supervision, "REGISTRE_ARME",
                               self._dir / "commandes_armees.json")
        patcher.start(); self.addCleanup(patcher.stop)
        self.addCleanup(self._nettoyer)
        self._nettoyer()

    def _nettoyer(self):
        fichier = self._dir / "commandes_armees.json"
        if fichier.exists():
            fichier.unlink()
        if self._dir.exists():
            self._dir.rmdir()

    def test_une_consigne_armee_survit_a_la_ronde_et_revient_par_le_meme_chemin(self):
        commande = {"command": "alarm.ack", "args": {"id": "A-000065"}}
        host = _banc([commande])
        _consignes(host)

        host.tools["programmer_consigne"](rang=0, commande=Symbol("alarm_ack"),
                                          cible="A-000065",
                                          garde=Symbol("plus_aucune_alarme_critique"))
        self.assertEqual(len(valmont_supervision._lire_registre()), 1)

        # Ronde suivante : la consigne rentre par la MÊME porte, avec sa garde,
        # et repasse donc par les quatre contrôles au lieu d'être crue.
        suivante = _banc([])
        suivante.tools["reprendre_les_consignes_armees"]()
        reprises = suivante.sensors["propositions"]()

        self.assertEqual(len(reprises), 1)
        self.assertEqual(str(reprises[0]["origine"]), "armee")
        self.assertEqual(str(reprises[0]["garde"]), "plus_aucune_alarme_critique")
        self.assertEqual(str(reprises[0]["cible_attestee"]), "yes")

    def test_armer_deux_fois_la_meme_consigne_ne_la_dedouble_pas(self):
        commande = {"command": "alarm.ack", "args": {"id": "A-000065"}}
        for _ in range(3):
            host = _banc([commande])
            _consignes(host)
            host.tools["programmer_consigne"](
                rang=0, commande=Symbol("alarm_ack"), cible="A-000065",
                garde=Symbol("plus_aucune_alarme_critique"))

        self.assertEqual(len(valmont_supervision._lire_registre()), 1)

    def test_une_consigne_oubliee_depuis_huit_heures_se_declare_perimee(self):
        valmont_supervision._ecrire_registre([{
            "commande": "alarm.ack", "args": {"id": "A-000065"},
            "garde": "plus_aucune_alarme_critique",
            "arme_le": time.time() - valmont_supervision.PEREMPTION_ARMEE_S - 60}])

        host = _banc([])
        host.tools["reprendre_les_consignes_armees"]()

        self.assertEqual(str(host.sensors["propositions"]()[0]["perimee"]), "yes")

    def test_executer_une_consigne_armee_la_retire_du_registre(self):
        valmont_supervision._ecrire_registre([{
            "commande": "alarm.ack", "args": {"id": "A-000065"},
            "garde": "plus_aucune_alarme_critique", "arme_le": time.time()}])

        host = _banc([])
        host.tools["reprendre_les_consignes_armees"]()
        vue = host.sensors["propositions"]()[0]
        host.tools["executer_consigne"](rang=0, commande=vue["commande"],
                                        cible=vue["cible"], jeton=vue["jeton"],
                                        implication=Symbol("marque_vue_sans_regard"))

        self.assertEqual(valmont_supervision._lire_registre(), [])


# ---------------------------------------------------------------------------
# PERCEPTION DU JOURNAL D'ALARMES — la fenêtre qui cachait une alarme vivante.
#
# `/alarms` découpe la liste APRÈS filtrage mais AVANT tout tri de notre côté,
# et son ordre est chronologique inverse. Une alarme majeure toujours active
# mais ancienne sortait donc de la fenêtre, chassée par des dizaines de
# clôturées plus récentes : l'agent inventoriait zéro alarme pendant que le
# résumé en comptait une, et ne demandait jamais quoi en faire.
#
# Trier après coup ne corrige rien — le tri s'applique à ce qui a survécu à la
# découpe. C'est le FILTRE qui doit être posé côté serveur.
# ---------------------------------------------------------------------------

class _ServeurAJournalLong(SimClient):
    """Reproduit la découpe du vrai `/alarms` : filtre, puis `slice(0, limit)`."""

    #: Une majeure vivante, noyée sous cent clôturées plus récentes.
    VIVANTE = {"id": "A-000023", "severity": "major", "domain": "ELEC",
               "source": "GEN1", "message": "Niveau de carburant bas",
               "detail": "17 % (mini 25 %)", "state": "ACKED", "ack": True,
               "faultId": None, "at": 10, "lastAt": 10}

    def __init__(self, limite_serveur=500):
        super().__init__(dry_run=True)
        self.limite_serveur = limite_serveur
        recentes = [{"id": f"A-{100 + i:06d}", "severity": "minor",
                     "domain": "EAU", "source": f"P{i}", "message": f"m{i}",
                     "detail": "", "state": "CLEARED", "ack": True,
                     "faultId": None, "at": 1000 + i, "lastAt": 1000 + i}
                    for i in range(100)]
        self.journal = recentes + [dict(self.VIVANTE)]

    def _request(self, path, payload=None, headers=None, timeout=None):
        if not path.startswith("/alarms"):
            raise SimUnavailable(path)
        etat = path.split("state=")[1].split("&")[0]
        limite = min(self.limite_serveur, int(path.split("limit=")[1]))
        rows = self.journal
        if etat == "active":
            rows = [a for a in rows if a["state"] in ("ACTIVE", "ACKED")]
        elif etat == "unacknowledged":
            rows = [a for a in rows if not a["ack"] and a["state"] != "CLEARED"]
        return {"count": len(rows), "alarms": rows[:limite]}


class TestFenetreDuJournalDAlarmes(unittest.TestCase):
    def test_une_alarme_vivante_mais_ancienne_reste_percue(self):
        vues = _ServeurAJournalLong().open_alarms()

        self.assertEqual([a["id"] for a in vues], ["A-000023"])

    def test_une_page_tronquee_est_une_lecture_ratee_pas_une_liste_courte(self):
        # « Indéterminé n'est pas zéro » : une fenêtre qui cache des lignes ne
        # doit pas se lire « il y a moins d'alarmes ». Une lecture partielle
        # est pire qu'une lecture ratée — elle a l'air d'avoir réussi.
        #
        # Il faut pour cela que la page FILTRÉE déborde, ce qui n'arrive plus
        # avec les vraies données — et c'est précisément l'effet du correctif :
        # filtrer côté serveur rend la troncature improbable au lieu de la
        # rendre invisible. Le garde-fou reste, parce que « improbable » n'est
        # pas « impossible » : le simulateur garde jusqu'à 800 alarmes.
        serveur = _ServeurAJournalLong(limite_serveur=3)
        for i in range(10):
            serveur.journal.append({"id": f"A-{900 + i:06d}", "severity": "major",
                                    "domain": "AIR", "source": f"C{i}",
                                    "message": "m", "detail": "",
                                    "state": "ACTIVE", "ack": True,
                                    "faultId": None, "at": i, "lastAt": i})

        with self.assertRaisesRegex(SimUnavailable, "tronque"):
            serveur.open_alarms()

    def test_le_filtre_est_pose_cote_serveur_donc_avant_la_decoupe(self):
        serveur = _ServeurAJournalLong()
        serveur.open_alarms()

        # Aucun appel ne demande `state=all` : ce serait rapatrier cent lignes
        # clôturées pour en trier une, et rouvrir la fenêtre qu'on vient de
        # fermer.
        self.assertTrue(all("state=all" not in chemin for chemin in serveur._cache))

    def test_une_critique_revenue_a_la_normale_n_est_plus_une_critique_active(self):
        serveur = _ServeurAJournalLong()
        serveur.journal.append({
            "id": "A-000146", "severity": "critical", "domain": "EAU",
            "source": "STEP", "message": "Rejet non conforme",
            "detail": "revenu a la normale", "state": "CLEARED_UNACK",
            "ack": False, "faultId": None, "at": 2000, "lastAt": 2000,
        })

        # Elle reste ouverte afin que la ronde puisse l'acquitter, mais elle
        # ne doit plus alimenter le fait « critique active sans défaut ».
        self.assertIn("A-000146", [a["id"] for a in serveur.open_alarms()])
        self.assertEqual(serveur.critical_alarms(), [])
        self.assertEqual(serveur.critical_without_fault(), [])

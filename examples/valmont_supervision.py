"""Hôte du SUPERVISEUR VALMONT — hiérarchie à deux étages sur ENTERPRISE-SIM.

Les deux sous-agents ne sont pas des fonctions : ce sont des **runtimes
AGENT-L complets** exécutant `valmont_alarmes.agent` et
`valmont_production.agent`, chacun avec sa perception, son inférence calibrée
et sa politique. Le superviseur ne voit que les champs bornés par les
contrats `EXPECT` — jamais leur état interne.

Chaque spécialiste porte son propre hôte, sous son propre nom (norme
X.agent ↔ X.py). Ce fichier les **importe** : les deux étages ne peuvent pas
diverger, et chaque `.agent` reste exécutable et vérifiable seul.

    # ronde nominale, sans rien envoyer à l'usine (défaut) :
    python3 -m agentl run examples/valmont_supervision.agent \
        --html /tmp/valmont.html --record /tmp/valmont.json

    # ronde réelle : les commandes partent vraiment vers le simulateur
    VALMONT_DRY_RUN=0 VALMONT_APPROVAL=oui \
        python3 -m agentl run examples/valmont_supervision.agent

    # contre-factuel « usine injoignable » :
    VALMONT_SIM_URL=http://127.0.0.1:1 \
        python3 -m agentl run examples/valmont_supervision.agent

Variables : VALMONT_SIM_URL, VALMONT_TOKEN, VALMONT_DRY_RUN (défaut 1),
VALMONT_APPROVAL (défaut : refus), VALMONT_LLM_EXTERNE (défaut : non),
VALMONT_MOCK_LLM.
"""
from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from agentl import Host, MockLLM, Runtime, Symbol, parse_file

import valmont_alarmes as alarmes_host
import valmont_production as production_host
from valmont_sim import (SimClient, SimUnavailable,
                         external_llm_consent, gemini_key)

HERE = Path(__file__).resolve().parent
PERCEPTION_TTL_S = 1.5
TOKEN_TTL_S = 60.0

#: Registre des commandes ARMÉES — celles dont l'agent a jugé qu'elles ne
#: devaient pas partir maintenant, mais partir quand un paramètre aura évolué.
#: Rien dans le langage ne persiste la mémoire (cf. `Runtime.seed_memory`) :
#: c'est l'hôte qui décide où elle vit. Elle revient donc à l'agent par la
#: PERCEPTION et non par `LONG_TERM`, ce qui est plus sûr : une commande armée
#: repasse par les quatre contrôles à chaque ronde au lieu d'être crue sur
#: parole parce qu'un tour précédent l'avait acceptée.
ETAT_DIR = Path(os.environ.get("VALMONT_ETAT",
                               str(Path.home() / ".agentl" / "valmont")))
REGISTRE_ARME = ETAT_DIR / "commandes_armees.json"
#: Une consigne qu'on n'a pas pu passer en huit heures n'est plus la consigne
#: d'un responsable : c'est un ordre oublié. Elle se périme.
PEREMPTION_ARMEE_S = 8 * 3600.0

#: Clés d'argument qui NOMMENT une entité, par ordre de préséance. Table de
#: RÉSOLUTION — « où va-t-on chercher cet identifiant » —, pas de jugement —
#: « faut-il agir dessus ». Le second point appartient au `.agent`.
_CLES_CIBLE = ("faultId", "id", "line", "name", "movementId", "department")


def _genre_de_cible(commande: str) -> str:
    """Collection perçue dans laquelle l'identifiant doit se retrouver.

    Mécanique : le préfixe de la commande dit dans quel registre du simulateur
    vit son identifiant. Aucune opportunité n'est jugée ici — `sans_objet`
    signale seulement qu'il n'y a rien à retrouver, et `schema` que
    l'énumération publiée par le simulateur fait déjà foi.
    """
    if commande.startswith("alarm."):
        return "alarme"
    if commande.startswith("maintenance."):
        return "defaut"
    if commande.startswith("asset."):
        return "equipement"
    # BOUNDARY-OK: table de RESOLUTION d'identifiant — « dans quel registre du
    # simulateur vit cet id » —, pas d'opportunite. Rien n'est ecarte : les
    # commandes non listees rendent `schema`, et le `.agent` decide de la suite.
    if commande == "production.rate":
        return "ligne"
    return "schema"


# --------------------------------------------------------------- sous-agents
def _sub(name: str, key_path: str, hypothesis: str, host_module):
    """Exécute un agent AGENT-L imbriqué et rend SON verdict borné."""
    sub, sub_llm = host_module.build()
    agent = parse_file(str(HERE / f"{name}.agent")).agents[0]
    runtime = Runtime(agent, sub, sub_llm).run()

    inference = runtime.inferences.get(hypothesis)
    posterior = round(inference.posterior, 3) if inference else 0.0
    return runtime, posterior


def _run_alarmiste(payload: dict, sink: dict) -> dict:
    runtime, posterior = _sub("valmont_alarmes", "diagnostic.verdict",
                              "incident_majeur", alarmes_host)
    verdict = runtime.state.get("diagnostic.verdict")
    classe = runtime.state.get("classe")
    print(f"    ↳ [VALMONT_ALARMES] verdict={verdict} classe={classe} "
          f"P(incident_majeur)={posterior}")
    # On ne remonte QUE le contrat EXPECT — jamais l'état interne du
    # spécialiste. `sink` est ce que les capteurs du superviseur reliront :
    # consigner un retour n'est pas le juger.
    result = {"verdict_alarme": Symbol(str(verdict)),
              "classe_alarme": Symbol(str(classe)),
              "confiance_alarme": posterior}
    sink.update(result)
    return result


def _production_confidence(conseil, bottleneck_posterior: float) -> float:
    """Convertit le risque de bridage en confiance dans le conseil rendu.

    Le postérieur calculé par VALMONT_PRODUCTION est P(bridage_utilites). Il
    porte directement la confiance d'un conseil ``brider`` ; pour ``relancer``,
    la confiance pertinente est son complément. Sans cette conversion, les
    deux conseils mutuellement exclusifs ne peuvent franchir le même seuil.
    """
    # BOUNDARY-OK: arithmetique du contrat EXPECT — le posterieur porte le
    # risque de bridage, sa confiance pour `relancer` en est le complement. Le
    # SEUIL qui en tire une conduite est dans la POLICY, pas ici.
    if str(conseil) == "relancer":
        return round(1.0 - bottleneck_posterior, 3)
    return bottleneck_posterior

def _faits_cadence(production: dict) -> dict:
    """Extrait les consignes et etats bruts, distincts de la cadence reelle.

    ``avgRate_pct`` mesure l'effet physique. ``rate_pct`` porte la consigne de
    chaque ligne. Les confondre faisait renvoyer ``ALL=100`` pendant le SETUP
    d'une ligne dont la consigne etait deja a 100 %.
    """
    lignes = production.get("lines") if isinstance(production, dict) else None
    if not isinstance(lignes, dict) or not lignes:
        raise SimUnavailable("reponse /production sans lignes")
    try:
        consignes = {
            identifiant: float(lignes[identifiant]["rate_pct"])
            for identifiant in ("L1", "L2", "L3", "L4")
        }
        etats = {
            identifiant: str(lignes[identifiant].get("state") or "INCONNU")
            for identifiant in ("L1", "L2", "L3", "L4")
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise SimUnavailable("reponse /production incomplete ou invalide") from exc
    return {
        "consignes": consignes,
        "etats": etats,
        "lignes": lignes,
    }



def _run_optimiseur(payload: dict, sink: dict) -> dict:
    runtime, posterior = _sub("valmont_production", "optim.conseil",
                              "bridage_utilites", production_host)
    conseil = runtime.state.get("optim.conseil")
    goulot = runtime.state.get("goulot")
    confidence = _production_confidence(conseil, posterior)
    print(f"    ↳ [VALMONT_PRODUCTION] conseil={conseil} goulot={goulot} "
          f"P(bridage_utilites)={posterior} confiance={confidence}")
    result = {"conseil_production": Symbol(str(conseil)),
              "goulot_production": Symbol(str(goulot)),
              "confiance_production": confidence}
    sink.update(result)
    return result


# ------------------------------------------- registre des commandes armées
def _lire_registre() -> list:
    """Lecture défensive : un registre illisible vaut registre vide.

    Repartir sans les consignes armées est un défaut ; refuser de démarrer
    parce qu'un fichier est abîmé en serait un pire — c'est le raisonnement
    déjà tenu par `Runtime._load_drift`, et il vaut ici pour les mêmes raisons.
    """
    try:
        rows = json.loads(REGISTRE_ARME.read_text(encoding="utf-8"))
    except Exception:                                  # noqa: BLE001
        return []
    return [r for r in rows if isinstance(r, dict) and r.get("commande")]


def _ecrire_registre(rows: list) -> None:
    ETAT_DIR.mkdir(parents=True, exist_ok=True)
    REGISTRE_ARME.write_text(json.dumps(rows, ensure_ascii=False, indent=1),
                             encoding="utf-8")


# ------------------------------------------------------------------- l'hôte
def build():
    client = SimClient()
    state = {"volet_alarmes": Symbol("attente"), "volet_cadence": Symbol("attente"),
             "status": Symbol("ouverte"), "commande_envoyee": Symbol("no"),
             "approbation_refusee": Symbol("no"),
             "conformite_consultee": Symbol("no"),
             "cible_conformite_demandee": 0.0,
             # Étapes du nouveau volet alarmes : reprendre les consignes
             # armées, demander la conduite, l'examiner, puis traiter au cas
             # par cas ce que le responsable n'a pas couvert.
             "consignes_reprises": Symbol("no"),
             "consigne_demandee": Symbol("no"),
             "inventaire_fait": Symbol("no")}
    view = {"at": 0.0, "data": None}
    #: Ce que les spécialistes ont rendu. `indetermine` tant qu'ils se taisent
    #: — un sous-agent qui n'a pas parlé n'est pas un sous-agent rassurant.
    verdicts: dict = {"verdict_alarme": Symbol("indetermine"),
                      "classe_alarme": Symbol("indetermine"),
                      "confiance_alarme": 0.0,
                      "conseil_production": Symbol("indetermine"),
                      "goulot_production": Symbol("indetermine"),
                      "confiance_production": 0.0}

    def perceive(force: bool = False) -> dict:
        now = time.monotonic()
        if not force and view["data"] is not None and now - view["at"] < PERCEPTION_TTL_S:
            return view["data"]
        if force:
            client._cache.clear()
        try:
            summary = client.summary()
            production = _faits_cadence(client.production())
            alarms = summary.get("alarms", {})
            data = {"read_ok": "yes",
                    # En ATTENTE d'intervention, pas « ouverts » : un défaut
                    # que les équipes du site traitent déjà n'en appelle pas
                    # une seconde.
                    "defauts": client.faults_awaiting_repair(),
                    "non_acquittees": int(alarms.get("unacknowledged", 0)),
                    "critiques": int(alarms.get("critical", 0)),
                    "cadence": float(summary.get("production", {}).get("rate_pct", 0)),
                    "consigne_l1": production["consignes"]["L1"],
                    "consigne_l2": production["consignes"]["L2"],
                    "consigne_l3": production["consignes"]["L3"],
                    "consigne_l4": production["consignes"]["L4"],
                    "etat_l1": production["etats"]["L1"],
                    "etat_l2": production["etats"]["L2"],
                    "etat_l3": production["etats"]["L3"],
                    "etat_l4": production["etats"]["L4"],
                    # CONFORMITE DU REJET ET CHARGE DE LA LIGNE QUI LE PRODUIT.
                    #
                    # Deux FAITS, pas un raisonnement : le simulateur dit
                    # lui-même si le rejet est conforme (`wwtp.compliant`), et
                    # la cadence de la ligne 3 se lit telle quelle. Le lien
                    # entre les deux — le traitement de surface rejette
                    # l'effluent chaud, donc sa charge pilote la temperature —
                    # est une connaissance metier : elle se declare dans le
                    # `.agent`, ou elle est auditable, jamais ici.
                    "rejet_conforme": "yes" if client.discharge_compliant() else "no",
                    "charge_traitement": client.surface_line_rate()}
        except SimUnavailable as exc:
            print(f"    ⚠  ENTERPRISE-SIM injoignable : {exc}")
            # « Indetermine n'est pas zero » : une lecture ratee ne doit pas
            # se lire « rejet conforme ». On rend l'inconnu explicite.
            data = {"read_ok": "no", "defauts": 0, "non_acquittees": 0,
                    "critiques": 0, "cadence": 0.0,
                    "consigne_l1": 0.0, "consigne_l2": 0.0,
                    "consigne_l3": 0.0, "consigne_l4": 0.0,
                    "etat_l1": "INCONNU", "etat_l2": "INCONNU",
                    "etat_l3": "INCONNU", "etat_l4": "INCONNU",
                    "rejet_conforme": "inconnu", "charge_traitement": 0.0}
        view["at"], view["data"] = now, data
        return data

    # ------------------------------------------------- jetons liés à UNE cible
    #
    # `site.jeton_action` atteste un COMPTEUR (« il y a n alarmes »). Il ne
    # convient plus dès qu'on traite les alarmes une par une : un compteur ne
    # dit pas QUELLE alarme, et c'est exactement le défaut que W122 signale —
    # une action ciblée autorisée par une preuve globale. Chaque élément
    # inventorié porte donc SON jeton, lié au couple (genre, identifiant).
    cibles: dict = {}

    def emettre(genre: str, cible: str) -> str:
        maintenant = time.time()
        # BOUNDARY-OK: peremption de jetons de preuve emis par l'hote, pas un
        # filtrage metier. Retirer cette purge ne rendrait visible aucune donnee
        # de plus a l'agent : elle rendrait utilisables des preuves perimees.
        for vieux in [k for k, (_, _, ts) in cibles.items()
                      if maintenant - ts > TOKEN_TTL_S]:
            cibles.pop(vieux, None)
        jeton = f"cible-{uuid.uuid4().hex[:12]}"
        cibles[jeton] = (genre, str(cible), maintenant)
        return jeton

    def consommer_cible(jeton: str, genre: str, cible: str) -> None:
        """Fraîcheur, usage unique, et IDENTITÉ de la cible attestée.

        La différence avec `consume` : on ne vérifie pas qu'un compteur est
        resté positif, on vérifie que le jeton atteste **cette entité-là**.
        Un jeton émis pour A-000065 ne peut pas servir à acquitter A-000101.
        """
        record = cibles.pop(str(jeton), None)          # usage unique
        if record is None:
            raise RuntimeError(f"jeton de cible inconnu ou deja consomme : {jeton}")
        vu_genre, vu_cible, emis_le = record
        if time.time() - emis_le > TOKEN_TTL_S:
            raise RuntimeError("jeton de cible perime : la perception date de trop longtemps")
        if vu_genre != genre or vu_cible != str(cible):
            raise RuntimeError(f"le jeton atteste {vu_genre} « {vu_cible} », "
                               f"pas {genre} « {cible} »")
        # BOUNDARY-OK: revalidation d'identite juste avant l'effet. Ces levees
        # ne peuvent qu'EMPECHER un effet, jamais en autoriser un : la POLICY
        # du .agent a deja tranche.
        if perceive(force=True)["read_ok"] != "yes":
            raise RuntimeError("usine injoignable a l'instant d'agir")

    # --------------------------------------------------- inventaire d'alarmes
    #: Ce que la ronde a décidé pour chaque alarme, par identifiant. Une
    #: alarme sans entrée ici n'est PAS traitée — et la clôture le vérifie.
    dispositions: dict = {}
    inventaire = {"at": 0.0, "rows": None}

    def inventorier(force: bool = False) -> list:
        maintenant = time.monotonic()
        if not force and inventaire["rows"] is not None \
                and maintenant - inventaire["at"] < PERCEPTION_TTL_S:
            return inventaire["rows"]
        try:
            ouvertes = client.open_alarms()
            actifs = set(client.fault_ids("ACTIVE"))
        except SimUnavailable:
            # « Indeterminé n'est pas vide » : une lecture ratée ne doit pas
            # se lire « aucune alarme à traiter ». On rend une liste vide MAIS
            # `site.read_ok` vaut `no`, et la POLICY interdit alors de clore.
            ouvertes, actifs = [], set()
        rows = []
        for row in ouvertes:
            identifiant = str(row.get("id"))
            defaut = str(row.get("faultId") or "") or "aucun"
            rows.append({
                "id": identifiant,
                "severite": Symbol(str(row.get("severity") or "inconnue")),
                "etat": Symbol(str(row.get("state") or "inconnu")),
                "acquittee": Symbol("yes" if row.get("ack") else "no"),
                "defaut": Symbol(defaut),
                "defaut_actif": Symbol("yes" if defaut in actifs else "no"),
                "source": Symbol(str(row.get("source") or "inconnu")),
                "domaine": Symbol(str(row.get("domain") or "inconnu")),
                "repetitions": int(row.get("repetitions") or 1),
                "traitee": Symbol("yes" if identifiant in dispositions else "no"),
                "jeton": emettre("alarme", identifiant),
            })
        inventaire["at"], inventaire["rows"] = maintenant, rows
        return rows

    def disposer(alarme, disposition: str) -> None:
        """Inscrit la disposition d'une alarme, et périme l'inventaire.

        POINT D'ÉCRITURE UNIQUE, et c'est la raison d'être de cette fonction :
        l'inventaire est mis en cache le temps d'un tick, si bien qu'écrire la
        disposition sans périmer le cache laissait `alarmes.non_traitees`
        annoncer du travail déjà fait — un compteur que la POLICY lit pour
        décider de clore. Mesuré en test, pas supposé.
        """
        dispositions[str(alarme)] = disposition
        inventaire["rows"] = None

    def non_traitees() -> int:
        # BOUNDARY-OK: comptage du registre de dispositions, qui vit dans l'hote
        # parce que ce sont ses outils qui l'ecrivent. Aucune alarme n'est
        # ecartee — `inventorier()` les rend TOUTES, avec leur `traitee`, et le
        # `FOREACH` trie. Ce que ce compteur interdit est ecrit dans la POLICY.
        return sum(1 for r in inventorier() if str(r["traitee"]) == "no")

    # ------------------------------------------ projection d'une proposition
    #: Arguments bruts des consignes reçues, par rang. Ils ne sont JAMAIS
    #: exposés à l'agent : il ne voit que des scalaires bornés, et c'est le
    #: rang — un entier qu'il a lui-même parcouru — qui les rappelle.
    propositions: list = []
    args_bruts: dict = {}

    def _projeter(rang: int, nom: str, args: dict, origine: str,
                  garde: str = "aucune", arme_le: float = 0.0) -> dict:
        spec = client.command_spec(nom)
        conforme, motif = ((False, "commande hors catalogue") if spec is None
                           else client.check_against_schema(nom, args))
        # BOUNDARY-OK: reperage de l'argument qui NOMME une entite, par ordre
        # de preseance fixe. Rien n'est juge : les autres arguments partent tels
        # quels vers le simulateur, et `check_against_schema` les valide tous.
        cle = next((k for k in _CLES_CIBLE if k in args), None)
        cible = str(args.get(cle)) if cle else "aucune"
        genre = _genre_de_cible(nom) if cle else "sans_objet"

        severite, defaut, defaut_actif = "sans_objet", "sans_objet", "sans_objet"
        sans_objet, hausse = "inconnu", "sans_objet"
        atteste = False
        # BOUNDARY-OK: RESOLUTION de la cible dans le monde percu — « cet
        # identifiant designe-t-il une entite que j'ai vue ce tick ». Chaque
        # branche interroge le registre ou l'identifiant vit ; aucune n'ecarte
        # une consigne. Le fait produit (`cible_attestee`) est rendu tel quel au
        # `.agent`, qui seul en tire un refus, un armement ou une execution.
        try:
            if genre == "alarme":
                # `*` est schema-valide et ne nomme AUCUNE entité observée :
                # « acquitte tout » n'est donc pas attestable, et c'est voulu.
                # C'est précisément la commande en bloc que le traitement
                # alarme par alarme remplace.
                ligne = client.alarm_by_id(cible) if cible != "*" else None
                atteste = ligne is not None
                if ligne is not None:
                    severite = str(ligne.get("severity") or "inconnue")
                    defaut = str(ligne.get("faultId") or "") or "aucun"
                    defaut_actif = "yes" if defaut in set(client.fault_ids("ACTIVE")) else "no"
                    sans_objet = "yes" if ligne.get("ack") else "no"
            elif genre == "defaut":
                atteste = cible in set(client.fault_ids("ACTIVE"))
                # « Sans objet » veut dire « la cible existe et l'acte n'a plus
                # rien à y faire ». Sur un défaut qu'on ne retrouve pas, ce
                # n'est pas ce qu'on sait : on sait qu'on ne le retrouve pas.
                # Rendre `yes` ici faisait dire au refus « c'est déjà fait »
                # là où la vérité était « je ne reconnais pas cette cible ».
                sans_objet = "no" if atteste else "inconnu"
            elif genre == "equipement":
                atteste = cible in set(client.asset_ids())
            elif genre == "ligne":
                # `ALL` reste attestable : c'est une cible que le simulateur
                # publie dans son énumération et que l'agent commande déjà.
                lignes = set(client.production_line_ids())
                atteste = cible in lignes or cible == "ALL"
                taux = args.get("rate_pct")
                if taux is not None and cible in lignes:
                    actuelle = client.production().get("lines", {}).get(cible, {})
                    hausse = "yes" if float(taux) > float(actuelle.get("actual_pct") or 0) else "no"
                elif taux is not None:
                    hausse = "yes" if float(taux) > float(
                        client.production().get("avgRate_pct") or 0) else "no"
            else:
                # Ni identifiant à retrouver, ni registre où le chercher :
                # l'énumération publiée par le simulateur fait foi.
                atteste = conforme
        except SimUnavailable:
            atteste = False                       # ne pas attester à l'aveugle

        return {
            "index": rang,
            "commande": Symbol(nom.replace(".", "_") if spec is not None
                               else "hors_catalogue"),
            "role": Symbol(str((spec or {}).get("role") or "inconnu")),
            "origine": Symbol(origine),
            "cible": cible,
            "cible_genre": Symbol(genre),
            "cible_attestee": Symbol("yes" if atteste else "no"),
            "cible_severite": Symbol(severite),
            "cible_defaut": Symbol(defaut),
            "cible_defaut_actif": Symbol(defaut_actif),
            "args_valides": Symbol("yes" if conforme else "no"),
            "args_motif": motif,
            "sans_objet": Symbol(sans_objet),
            "hausse_de_cadence": Symbol(hausse),
            "garde": Symbol(garde),
            "perimee": Symbol("yes" if arme_le
                              and time.time() - arme_le > PEREMPTION_ARMEE_S
                              else "no"),
            "jeton": emettre(genre, cible) if atteste else "aucun",
        }

    def _enregistrer(nom: str, args: dict, origine: str, garde: str = "aucune",
                     arme_le: float = 0.0) -> dict:
        rang = len(propositions)
        args_bruts[rang] = {"nom": nom, "args": dict(args), "origine": origine,
                            "garde": garde, "arme_le": arme_le}
        vue = _projeter(rang, nom, args, origine, garde, arme_le)
        propositions.append(vue)
        return vue

    host = Host()
    trace_sink = getattr(client, "trace_sink", None)
    if trace_sink is not None:
        host.trace_sink = trace_sink("VALMONT_SUPERVISION")
    host.sensors.update({
        "site.read_ok":                lambda: Symbol(perceive()["read_ok"]),
        "site.defauts_ouverts":        lambda: perceive()["defauts"],
        "site.alarmes_non_acquittees": lambda: perceive()["non_acquittees"],
        "site.alarmes_critiques":      lambda: perceive()["critiques"],
        "site.cadence_pct":            lambda: perceive()["cadence"],
        "site.consigne_l1_pct":        lambda: perceive()["consigne_l1"],
        "site.consigne_l2_pct":        lambda: perceive()["consigne_l2"],
        "site.consigne_l3_pct":        lambda: perceive()["consigne_l3"],
        "site.consigne_l4_pct":        lambda: perceive()["consigne_l4"],
        "site.etat_l1":                lambda: Symbol(perceive()["etat_l1"]),
        "site.etat_l2":                lambda: Symbol(perceive()["etat_l2"]),
        "site.etat_l3":                lambda: Symbol(perceive()["etat_l3"]),
        "site.etat_l4":                lambda: Symbol(perceive()["etat_l4"]),
        # La POLICY compare elle-meme chaque consigne a la valeur nominale.
        "site.commande_envoyee":       lambda: state["commande_envoyee"],
        "approbation.refusee":         lambda: state["approbation_refusee"],
        "site.rejet_conforme":         lambda: Symbol(perceive()["rejet_conforme"]),
        "site.charge_traitement_pct":  lambda: perceive()["charge_traitement"],
        "conformite.consultee":        lambda: state["conformite_consultee"],
        "conformite.cible_demandee_pct":
                                            lambda: state["cible_conformite_demandee"],
        "ronde.volet_alarmes":         lambda: state["volet_alarmes"],
        "ronde.volet_cadence":         lambda: state["volet_cadence"],
        "ronde.status":                lambda: state["status"],
        # -- inventaire d'alarmes et consignes du responsable ---------------
        # Deux COLLECTIONS, seule construction du langage qui relie le monde
        # en listes à un état scalaire (`FOREACH`). Chaque élément porte ses
        # propres faits et son propre jeton ; aucun texte du monde n'y entre.
        "alarmes_actives":             lambda: inventorier(),
        "alarmes.non_traitees":        lambda: non_traitees(),
        "propositions":                lambda: list(propositions),
        "consignes.reprises":          lambda: state["consignes_reprises"],
        "consigne.demandee":           lambda: state["consigne_demandee"],
        "inventaire.fait":             lambda: state["inventaire_fait"],
        # Ce que les spécialistes ont rendu, relu à chaque tick.
        "verdict_alarme":              lambda: verdicts["verdict_alarme"],
        "classe_alarme":               lambda: verdicts["classe_alarme"],
        "confiance_alarme":            lambda: verdicts["confiance_alarme"],
        "conseil_production":          lambda: verdicts["conseil_production"],
        "goulot_production":           lambda: verdicts["goulot_production"],
        "confiance_production":        lambda: verdicts["confiance_production"],
    })

    # ----------------------------------------------------------- commandes
    def _recette_cadence(lignes: dict) -> str:
        return "; ".join(
            f"production.rate line={identifiant} "
            f"rate_pct={round(float(ligne['rate_pct']), 2)}"
            for identifiant, ligne in lignes.items()
        )

    def _cadence(cible, goulot, sens):
        cible = float(cible)
        avant = _faits_cadence(client.production())
        resultat = client.command(
            "production.rate", {"line": "ALL", "rate_pct": cible},
            justification=f"Cadence {sens} sur conseil du sous-agent "
                          f"production (goulot: {goulot})",
            idem_key=f"valmont-rate-{sens}-{int(time.time())}",
            rollback=_recette_cadence(avant["lignes"]))
        if not resultat.get("ok"):
            raise RuntimeError(
                f"production.rate refusee : {resultat.get('error', resultat)}")

        # Le recu confirme l'acceptation ; la relecture confirme la
        # postcondition utile. On ne demande PAS ``actual_pct == cible`` : une
        # ligne en SETUP doit finir sa sequence et sa rampe normalement.
        if not client.dry_run:
            apres = _faits_cadence(client.production())
            # BOUNDARY-OK: verification de postcondition apres l'effet ; ce
            # filtre ne peut qu'invalider un recu, jamais autoriser une action.
            # La decision d'envoyer la commande a deja ete prise par la POLICY.
            ecarts = {
                identifiant: ligne.get("rate_pct")
                for identifiant, ligne in apres["lignes"].items()
                if abs(float(ligne.get("rate_pct", -1)) - cible) > 1e-6
            }
            if ecarts:
                raise RuntimeError(
                    f"consigne production.rate non confirmee : {ecarts}")

        view["data"] = None
        state["volet_cadence"] = Symbol("traite")
        state["commande_envoyee"] = Symbol("yes")
        return {"appliquee": Symbol("yes")}

    def constater_consigne_nominale(cadence, etat_l1, etat_l2, etat_l3,
                                    etat_l4):
        """Clot sans effet : la commande est deja posee sur chaque ligne."""
        state["volet_cadence"] = Symbol("traite")
        print(f"    · relance deja commandee sur L1-L4 ; "
              f"cadence reelle {float(cadence):.1f} % ; "
              f"etats={etat_l1}/{etat_l2}/{etat_l3}/{etat_l4}")
        return {"deja_posee": Symbol("yes")}


    def brider_pour_conformite(cible, charge):
        """Bride LA SEULE ligne en cause, pas tout le site.

        `_cadence` commande `line=ALL` : c'est ce qu'il faut quand le goulot
        est une utilité commune. Ici la cause est locale — le traitement de
        surface rejette l'effluent chaud — et brider la fonderie ou
        l'assemblage coûterait du tonnage sans rien changer au rejet.
        """
        cible = float(cible)
        cible_demandee = float(state["cible_conformite_demandee"])
        if abs(cible_demandee - cible) > 1e-6:
            raise RuntimeError(
                f"cible {cible:g} non approuvee : cible soumise "
                f"{cible_demandee:g}")
        ligne = client.LIGNE_TRAITEMENT_SURFACE
        avant = _faits_cadence(client.production())
        resultat = client.command(
            "production.rate", {"line": ligne, "rate_pct": cible},
            justification="Rejet STEP hors limites : bridage de la ligne de "
                          f"traitement de surface a {cible:g} %, cible exacte "
                          "approuvee par le responsable de site",
            idem_key=f"valmont-rate-conformite-{int(time.time())}",
            rollback=f"production.rate line={ligne} "
                     f"rate_pct={avant['consignes'][ligne]:g}")
        if not resultat.get("ok"):
            raise RuntimeError(
                f"production.rate L3 refusee : "
                f"{resultat.get('error', resultat)}")

        if not client.dry_run:
            apres = _faits_cadence(client.production())
            # BOUNDARY-OK: verification de postcondition apres l'effet ; elle
            # ne peut qu'invalider le recu d'une action deja autorisee.
            l3_confirmee = abs(apres["consignes"][ligne] - cible) <= 1e-6
            autres_avant = (
                avant["consignes"]["L1"], avant["consignes"]["L2"],
                avant["consignes"]["L4"])
            autres_apres = (
                apres["consignes"]["L1"], apres["consignes"]["L2"],
                apres["consignes"]["L4"])
            autres_inchangees = autres_apres == autres_avant
            if not l3_confirmee or not autres_inchangees:
                raise RuntimeError(
                    "consigne production.rate L3 non confirmee : "
                    f"L3={apres['consignes'][ligne]}, "
                    f"autres_avant={autres_avant}, autres_apres={autres_apres}")

        view["data"] = None
        state["volet_cadence"] = Symbol("traite")
        state["commande_envoyee"] = Symbol("yes")
        print(f"    🌡  {ligne} bridee a {cible:.0f} % "
              f"(etait a {float(charge):.0f} %) — conformite du rejet")
        return {"appliquee": Symbol("yes")}

    def signaler_rejet_non_conforme(charge):
        """Sortie quand le bridage de conformité est refusé ou impossible."""
        client.chat("Ici l'agent de supervision automatique du site. Le rejet "
                    "de la station d'effluents est hors limites et la seule "
                    "action de mon catalogue qui agirait dessus — brider la "
                    f"ligne {client.LIGNE_TRAITEMENT_SURFACE} de traitement de "
                    f"surface, actuellement a {float(charge):.0f} % — n'a pas "
                    "recu votre accord. La cadence reste en l'etat et aucune "
                    "commande n'a ete passee sur l'installation.")
        state["volet_cadence"] = Symbol("traite")
        return {"signale": Symbol("yes")}

    def consulter_sur_conformite(charge, cible):
        """Question portant sur LE bridage, pas sur le conseil de production.

        Réutiliser `avis_responsable` serait une faute de raisonnement : dans
        le run du 2026-08-13 le responsable a refusé une RELANCE en invoquant
        justement l'alarme environnementale. Prendre ce refus pour un refus
        de brider inverserait son sens.
        """
        cible = float(cible)
        state["conformite_consultee"] = Symbol("yes")
        state["cible_conformite_demandee"] = cible
        reponse = client.chat(
            "Ici l'agent de supervision automatique du site. Le rejet de la "
            "station d'effluents est hors limites. Donnez-vous votre accord "
            f"pour brider la ligne {client.LIGNE_TRAITEMENT_SURFACE} de "
            f"traitement de surface, actuellement a {float(charge):.0f} %, "
            f"a la cible exacte de {cible:.0f} %, afin de revenir dans les "
            "seuils ? Toute autre cible vaut refus de cette proposition. "
            "Repondez par oui ou par non.")
        return {"reponse": reponse, "cible_soumise": cible}

    def activer_chasse_fuites(goulot):
        client.command("ecm.enable", {"id": "ECM_AIR_LEAKS"},
                       justification=f"Campagne de chasse aux fuites d'air sur "
                                     f"conseil du sous-agent production ({goulot})",
                       idem_key=f"valmont-ecm-leaks-{int(time.time())}",
                       rollback="ecm.disable id=ECM_AIR_LEAKS")
        state["volet_cadence"] = Symbol("traite")
        state["commande_envoyee"] = Symbol("yes")
        return {"activee": Symbol("yes")}

    def maintenir_cadence(conseil):
        state["volet_cadence"] = Symbol("traite")
        print(f"    · volet cadence clos sans action (conseil={conseil})")
        return {"maintenue": Symbol("yes")}

    def renoncer_a_la_cadence(conseil):
        state["volet_cadence"] = Symbol("traite")
        print(f"    · cadence inchangee : le responsable de site n'a pas donne "
              f"son accord pour « {conseil} »")
        return {"renonce": Symbol("yes")}

    # ------------------------------------------------------------ dialogue
    # L'hôte COMPOSE le texte autour d'un gabarit figé ; les seules parties
    # variables sont des symboles bornés venus de domaines clos. Aucun texte
    # lu dans le monde ne rejoint la sortie (principe 9).
    def consulter_responsable(conseil, motif):
        message = (
            "Ici l'agent de supervision automatique du site. "
            f"Diagnostic alarmes : {motif}. "
            f"Action envisagee sur la cadence de production : {conseil}. "
            "Donnez-vous votre accord explicite ? Repondez par oui ou par non.")
        reponse = client.chat(message)
        print(f"    💬 responsable de site → {reponse[:220]}")
        return {"reponse": reponse}

    def informer_responsable(sujet):
        client.chat("Ici l'agent de supervision automatique du site. "
                    f"Information : {sujet}. "
                    "Aucune commande n'a ete passee sur l'installation.")
        return {"transmis": Symbol("yes")}

    # ------------------------------------------- consignes du responsable
    #
    # Le responsable de site gagne ici un DROIT DE PROPOSITION. Il ne gagne
    # pas un droit d'ordre : ses commandes reviennent en propositions
    # projetées en scalaires bornés, et c'est la POLICY du `.agent` qui décide
    # laquelle part, laquelle attend et laquelle est refusée.

    def reprendre_les_consignes_armees():
        """Remet en examen les consignes qu'une ronde précédente a armées.

        Elles rentrent par la MÊME porte que les consignes fraîches : même
        projection, mêmes quatre contrôles. Une consigne armée hier n'est donc
        jamais crue sur parole — le monde a pu changer contre elle.
        """
        registre = _lire_registre()
        for ligne in registre:
            _enregistrer(str(ligne.get("commande")), dict(ligne.get("args") or {}),
                         "armee", str(ligne.get("garde") or "aucune"),
                         float(ligne.get("arme_le") or 0.0))
        state["consignes_reprises"] = Symbol("yes")
        if registre:
            print(f"    ⏱  {len(registre)} consigne(s) armee(s) remise(s) en examen")
        return {"reprises": len(registre)}

    def demander_conduite_alarmes(arriere):
        """Demande au responsable de site COMMENT traiter les alarmes ouvertes.

        Ce qui part est un inventaire d'IDENTIFIANTS produits par le
        simulateur (`A-000065`, `STEP`, `critical`) et des compteurs. Aucun
        libellé, aucun détail, aucun texte lu dans le monde ne rejoint la
        sortie : le responsable a la supervision sous les yeux.

        Ce qui revient est un couple `{reply, commands}`. Le simulateur tourne
        avec `chat.allowCommands = false` : il n'exécute rien, il rend les
        commandes telles quelles. Elles n'entrent dans l'agent que projetées
        en scalaires bornés.
        """
        rows = inventorier(force=True)
        liste = ", ".join(f"{r['id']} ({r['severite']} sur {r['source']})"
                          for r in rows[:12])
        # BOUNDARY-OK: mise en forme d'un message, pas un tri par importance.
        # La troncature ne peut pas faire disparaitre une critique — `open_alarms`
        # rend les plus graves d'abord (fait publie par le simulateur) — et le
        # compte TOTAL est rappele en clair juste avant. Meme raisonnement que
        # `SimClient.active_alarm_text`, et pour la meme raison.
        if len(rows) > 12:
            liste += f", et {len(rows) - 12} autre(s)"
        out = client.chat_full(
            "Ici l'agent de supervision automatique du site. "
            f"{len(rows)} alarme(s) ouverte(s), {int(arriere)} en attente "
            f"d'acquittement. Inventaire : {liste or 'aucune'}. "
            "Comment dois-je les traiter ? Repondez en emettant les commandes "
            "exactes a passer, avec les identifiants ci-dessus. Je verifierai "
            "chacune avant de l'executer et je vous dirai ce que je refuse.")
        for item in out["commands"][:8]:
            _enregistrer(item["command"], item["args"], "responsable")
        state["consigne_demandee"] = Symbol("yes")
        print(f"    💬 responsable de site → {out['reply'][:200]}")
        print(f"    📋 {len(out['commands'])} consigne(s) recue(s), "
              f"{len(propositions)} a examiner")
        return {"reponse": out["reply"], "consignes": len(out["commands"])}

    #: Recettes de retour arrière, par commande. Table de RECETTE — « par quoi
    #: défait-on ceci » —, journalisée avant l'effet ; elle ne décide rien.
    _INVERSE = {"asset.start": "asset.stop", "asset.stop": "asset.start",
                "breaker.open": "breaker.close", "breaker.close": "breaker.open",
                "ecm.enable": "ecm.disable", "ecm.disable": "ecm.enable",
                "sim.pause": "sim.resume", "sim.resume": "sim.pause",
                "personnel.recruit": "personnel.recruit.cancel",
                "personnel.dismiss": "personnel.dismiss.cancel"}

    def _recette(nom: str, args: dict) -> str:
        # BOUNDARY-OK: table de RECETTE — « par quoi defait-on ceci » —,
        # journalisee avant l'effet. Elle ne decide ni si l'action a lieu ni
        # laquelle : elle decrit comment revenir en arriere si elle a eu lieu.
        if nom == "production.rate":
            ligne = str(args.get("line") or "ALL")
            try:
                actuelle = (client.production().get("lines", {})
                            .get(ligne, {}).get("actual_pct"))
            except SimUnavailable:
                actuelle = None
            return (f"production.rate line={ligne} rate_pct={round(float(actuelle), 2)}"
                    if actuelle is not None else "aucune : cadence precedente non lue")
        # BOUNDARY-OK: suite de la meme table de RECETTE — la valeur precedente
        # de la consigne se relit dans le catalogue publie par le simulateur.
        if nom == "setpoint.set":
            for consigne in client.catalog().get("setpoints", []):
                if consigne.get("name") == args.get("name"):
                    return f"setpoint.set name={consigne['name']} value={consigne.get('value')}"
            return "aucune : valeur precedente non lue"
        if nom in _INVERSE:
            cible = args.get("id")
            return f"{_INVERSE[nom]}" + (f" id={cible}" if cible else "")
        return f"aucune recette connue pour {nom} : effet a considerer comme irreversible"

    def _dedup_registre(rows: list, nom: str, args: dict) -> list:
        """Registre sans doublon : l'identité d'une consigne est (commande, args).

        Le registre armé est relu à chaque ronde ; sans cette déduplication
        explicite, une consigne réarmée trois rondes de suite y figurerait
        trois fois et serait examinée trois fois. C'est le curseur de ce
        journal-là : il n'a pas de position, il a une identité.
        """
        return [r for r in rows
                if not (r.get("commande") == nom and r.get("args") == args)]

    def _oublier(rang: int) -> None:
        """Retire du registre la consigne armée qu'on vient de trancher."""
        trace = args_bruts.get(rang) or {}
        # BOUNDARY-OK: tenue du registre PROPRE a l'hote — une consigne tranchee
        # n'a plus a etre reproposee. Ne concerne que les consignes armees, dont
        # l'hote est l'auteur ; aucune consigne du responsable n'est ecartee.
        if str(trace.get("origine")) != "armee":
            return
        _ecrire_registre(_dedup_registre(_lire_registre(),
                                         trace.get("nom"), trace.get("args")))

    def executer_consigne(rang, commande, cible, jeton, implication):
        """Passe la commande du responsable, une fois les quatre contrôles tenus."""
        trace = args_bruts.get(int(rang))
        if trace is None:
            raise RuntimeError(f"consigne inconnue au rang {rang}")
        vue = propositions[int(rang)]
        consommer_cible(jeton, str(vue["cible_genre"]), str(cible))
        client.command(
            trace["nom"], trace["args"],
            justification=f"Consigne du responsable de site, verifiee par "
                          f"VALMONT_SUPERVISION (implication: {implication})",
            idem_key=f"valmont-consigne-{rang}-{int(time.time())}",
            rollback=_recette(trace["nom"], trace["args"]))
        # BOUNDARY-OK: inscription au registre de dispositions. Une consigne
        # qui a porte sur une alarme la marque traitee ; c'est de la
        # comptabilite de ronde, et c'est la POLICY qui dit ce que le compteur
        # resultant autorise (`NEVER cloturer_ronde WHEN non_traitees > 0`).
        if str(vue["cible_genre"]) == "alarme":
            disposer(cible, "consigne_du_responsable")
        state["commande_envoyee"] = Symbol("yes")
        _oublier(int(rang))
        inventorier(force=True)
        print(f"    ✅ consigne appliquee : {commande} sur {cible} "
              f"({implication})")
        return {"appliquee": Symbol("yes")}

    def programmer_consigne(rang, commande, cible, garde):
        """Arme la consigne au lieu de l'envoyer : le monde n'est pas prêt.

        Ce n'est ni un refus ni un oubli. La consigne est écrite dans un
        registre qui survit à la ronde, avec la GARDE que l'agent a nommée ;
        chaque ronde suivante la reprend et la repasse par les mêmes contrôles.
        """
        trace = args_bruts.get(int(rang))
        if trace is None:
            raise RuntimeError(f"consigne inconnue au rang {rang}")
        registre = _dedup_registre(_lire_registre(), trace["nom"], trace["args"])
        registre.append({"commande": trace["nom"], "args": trace["args"],
                         "garde": str(garde),
                         "arme_le": float(trace.get("arme_le") or time.time())})
        _ecrire_registre(registre)
        # BOUNDARY-OK: meme registre de dispositions — une consigne armee sur
        # une alarme vaut disposition, sans quoi la ronde ne pourrait pas se
        # clore alors qu'un rendez-vous a bien ete pris pour cette alarme.
        if str(propositions[int(rang)]["cible_genre"]) == "alarme":
            disposer(cible, "consigne_programmee")
        print(f"    ⏱  consigne armee : {commande} sur {cible} — "
              f"partira quand « {garde} »")
        return {"programmee": Symbol("yes")}

    def refuser_consigne(rang, commande, motif):
        """Refuse la consigne et le dit au responsable, avec son motif.

        Le motif est un symbole d'un domaine clos décidé par le `.agent`, et
        la cible n'est rappelée que si l'agent l'a lui-même observée : une
        cible non attestée est du texte, et le texte ne repart pas.
        """
        vue = propositions[int(rang)]
        # BOUNDARY-OK: ASSAINISSEMENT DE SORTIE, seul geste que le `.agent` ne
        # peut pas faire — il ne manipule pas de chaines. Une cible non attestee
        # est du texte produit par un modele ; le renvoyer au chat en ferait une
        # boucle texte→texte (principe 9). Refuser reste decide par la POLICY.
        cible = (f"« {vue['cible']} »" if str(vue["cible_attestee"]) == "yes"
                 else "une cible que je n'ai pas reconnue")
        client.chat("Ici l'agent de supervision automatique du site. "
                    f"Je n'execute pas la consigne {commande} portant sur "
                    f"{cible} : {motif}. Aucune commande n'a ete passee sur "
                    "l'installation pour cette consigne.")
        # BOUNDARY-OK: registre de dispositions, cf. `executer_consigne`. Un
        # refus motive EST une disposition : l'alarme a ete regardee et tranchee.
        if str(vue["cible_genre"]) == "alarme" and str(vue["cible_attestee"]) == "yes":
            disposer(vue["cible"], f"consigne_refusee:{motif}")
        _oublier(int(rang))
        print(f"    ⛔ consigne refusee : {commande} — {motif}")
        return {"refusee": Symbol("yes")}

    # --------------------------------------- traitement alarme par alarme
    #
    # Ce que le responsable n'a pas couvert reste à traiter : « toutes les
    # alarmes actives » veut dire toutes, pas celles dont on a parlé.

    def _deja_dispose(alarme, geste: str) -> bool:
        """La ronde a-t-elle déjà tranché pour cette alarme ?

        LA COUTURE ENTRE UNE PERCEPTION EN CACHE ET UN REGISTRE QUI BOUGE.
        Le `FOREACH` lie sa source à l'instantané du monde pris à l'OBSERVE du
        tick ; la deuxième passe (`solder_l_inventaire`) peut donc retrouver
        `traitee = no` sur une alarme que la première vient de traiter. Sans
        cette garde, elle représentait un jeton déjà consommé : l'outil levait,
        le disjoncteur comptait un échec, et l'escalade partait pour rien —
        mesuré sur une ronde réelle.

        Sauter n'est pas agir : cette fonction ne peut qu'EMPÊCHER un effet, et
        elle ne peut pas écraser une disposition déjà prise par une autre.
        """
        # BOUNDARY-OK: idempotence de la ronde sur son propre registre de
        # dispositions, qui vit dans l'hote parce que ses outils l'ecrivent.
        # Aucune alarme n'est ecartee de l'inventaire rendu a l'agent.
        if str(alarme) not in dispositions:
            return False
        print(f"    · {alarme} deja traitee ({dispositions[str(alarme)]}) — "
              f"{geste} sans objet")
        return True

    def reparer_le_defaut(alarme, defaut, jeton):
        if _deja_dispose(alarme, "intervention"):
            return {"lancee": Symbol("yes")}
        consommer_cible(jeton, "alarme", alarme)
        client.command("maintenance.repair", {"faultId": str(defaut)},
                       justification=f"Defaut {defaut} en attente derriere "
                                     f"l'alarme {alarme} : intervention decidee "
                                     f"par VALMONT_SUPERVISION",
                       idem_key=f"valmont-repair-{defaut}-{int(time.time())}",
                       rollback="aucune : une intervention lancee suit son MTTR")
        disposer(alarme, "intervention")
        state["commande_envoyee"] = Symbol("yes")
        inventorier(force=True)
        print(f"    🔧 {alarme} → intervention sur le defaut {defaut}")
        return {"lancee": Symbol("yes")}

    def acquitter_une_alarme(alarme, jeton):
        """Acquitte UNE alarme nommée — jamais `*`.

        L'ancien volet acquittait l'arriéré en bloc (`alarm.ack *`). Un
        acquittement en bloc marque « vue » une alarme que personne n'a vue,
        y compris celles qu'on n'a pas inventoriées. Nommer la cible rend
        l'acte attestable, donc vérifiable.
        """
        if _deja_dispose(alarme, "acquittement"):
            return {"acquittee": Symbol("yes")}
        consommer_cible(jeton, "alarme", alarme)
        client.command("alarm.ack", {"id": str(alarme)},
                       justification=f"Alarme {alarme} acquittee apres controle "
                                     f"de sa cause par VALMONT_SUPERVISION",
                       idem_key=f"valmont-ack-{alarme}-{int(time.time())}",
                       rollback="alarm.ack est irreversible ; une alarme dont "
                                "la cause persiste se relevera d'elle-meme")
        disposer(alarme, "acquittee")
        state["commande_envoyee"] = Symbol("yes")
        inventorier(force=True)
        print(f"    🔔 {alarme} acquittee")
        return {"acquittee": Symbol("yes")}

    #: Ce que le motif d'escalade dit au responsable, en clair. Table de
    #: RÉDACTION, pas de décision : le `.agent` a déjà choisi la route, ces
    #: phrases n'en changent aucune. Un motif inattendu ne fait pas taire
    #: l'escalade — il part avec la formulation neutre.
    _PHRASES_ESCALADE = {
        "critique_sans_defaut_equipement":
            "elle est critique et aucun defaut equipement ne la porte : "
            "aucune equipe n'est depechee automatiquement",
        "deja_acquittee_toujours_presente":
            "elle a deja ete acquittee et elle est toujours la : sa cause "
            "persiste, et aucun defaut equipement ne la porte",
    }

    def escalader_une_alarme(alarme, equipement, motif, jeton):
        """Remonte l'alarme NOMMÉMENT, avec la raison de déranger.

        Écrit au responsable — c'est la différence avec `differer_une_alarme`,
        qui ne fait que classer. Une alarme qu'aucune commande ne traite doit
        sortir de la trace locale et arriver chez quelqu'un.
        """
        if _deja_dispose(alarme, "escalade"):
            return {"escaladee": Symbol("yes")}
        consommer_cible(jeton, "alarme", alarme)
        # BOUNDARY-OK: mise en forme d'un symbole DEJA choisi par le .agent.
        # Le defaut rend une phrase neutre : aucune escalade ne peut etre
        # perdue parce que sa redaction manque.
        raison = _PHRASES_ESCALADE.get(str(motif),
                                       "aucune commande de mon catalogue n'y remedie")
        client.chat("Ici l'agent de supervision automatique du site. "
                    f"Alarme {alarme} sur {equipement} : {raison}. "
                    "Je vous la remonte nommement pour que vous me disiez "
                    "comment la traiter. Aucune commande n'a ete passee sur "
                    "l'installation.")
        disposer(alarme, f"escaladee:{motif}")
        print(f"    🚨 {alarme} sur {equipement} remontee au responsable — {motif}")
        return {"escaladee": Symbol("yes")}

    def differer_une_alarme(alarme, motif, jeton):
        """Disposition explicite « je n'agis pas, et voici pourquoi ».

        C'est une décision, pas un oubli : l'alarme est comptée traitée, le
        motif est un symbole borné, et la trace le porte. Sans cette route,
        une alarme qu'aucune commande ne concerne bloquerait la clôture.
        """
        if _deja_dispose(alarme, "report"):
            return {"differee": Symbol("yes")}
        consommer_cible(jeton, "alarme", alarme)
        disposer(alarme, f"differee:{motif}")
        print(f"    ⏸  {alarme} laissee en l'etat — {motif}")
        return {"differee": Symbol("yes")}

    def cloturer_l_inventaire(restantes):
        state["inventaire_fait"] = Symbol("yes")
        state["volet_alarmes"] = Symbol("traite")
        print(f"    · inventaire d'alarmes clos ({int(restantes)} non traitee(s))")
        return {"close": Symbol("yes")}

    def cloturer_ronde(verdict, conseil):
        notification = Symbol("envoyee")
        try:
            client.chat("Ici l'agent de supervision automatique du site. "
                        f"Ronde terminee — volet alarmes : {verdict}, "
                        f"volet production : {conseil}.")
        except SimUnavailable as exc:
            # La notification consultative ne bloque jamais la cloture locale.
            notification = Symbol("incertaine")
            print(f"    ⚠  synthese de cloture non confirmee : {exc}")
        state["status"] = Symbol("terminee")
        print(f"    ✅ ronde close (alarmes={verdict}, production={conseil}, "
              f"notification={notification})")
        return {"cloturee": Symbol("yes"), "notification": notification}

    host.tools.update({
        "brider_cadence": lambda cible, goulot: _cadence(cible, goulot, "bridee"),
        "relancer_cadence": lambda cible, goulot: _cadence(cible, goulot, "relancee"),
        "constater_consigne_nominale": constater_consigne_nominale,
        "activer_chasse_fuites": activer_chasse_fuites,
        "brider_pour_conformite": brider_pour_conformite,
        "signaler_rejet_non_conforme": signaler_rejet_non_conforme,
        "consulter_sur_conformite": consulter_sur_conformite,
        "maintenir_cadence": maintenir_cadence,
        "renoncer_a_la_cadence": renoncer_a_la_cadence,
        "consulter_responsable": consulter_responsable,
        "informer_responsable": informer_responsable,
        "cloturer_ronde": cloturer_ronde,
        # -- conduite demandee au responsable, puis verifiee par l'agent ----
        "reprendre_les_consignes_armees": reprendre_les_consignes_armees,
        "demander_conduite_alarmes": demander_conduite_alarmes,
        "executer_consigne": executer_consigne,
        "programmer_consigne": programmer_consigne,
        "refuser_consigne": refuser_consigne,
        # -- traitement alarme par alarme ----------------------------------
        "reparer_le_defaut": reparer_le_defaut,
        "acquitter_une_alarme": acquitter_une_alarme,
        "escalader_une_alarme": escalader_une_alarme,
        "differer_une_alarme": differer_une_alarme,
        "cloturer_l_inventaire": cloturer_l_inventaire,
    })

    # LE point de la composition : les sous-agents sont des runtimes imbriqués.
    host.subagents["alarmiste"] = lambda payload: _run_alarmiste(payload, verdicts)
    host.subagents["optimiseur"] = lambda payload: _run_optimiseur(payload, verdicts)

    # Approbateur : l'absence de réponse vaut REFUS (fail-closed).
    def approver(request):
        accord = os.environ.get("VALMONT_APPROVAL", "").strip().lower() in (
            "oui", "o", "yes", "y", "1")
        print(f"\n  🖐  APPROBATION demandee : {request.render()}"
              f"  → {'accordee' if accord else 'REFUSEE (defaut)'}\n")
        # Consigner le refus : c'est un fait que l'agent doit pouvoir relire,
        # sans quoi il re-sollicite l'humain à chaque tick. Consigner n'est
        # pas décider — ce que le refus interdit est écrit dans la POLICY.
        if not accord:
            state["approbation_refusee"] = Symbol("yes")
        return accord

    host.approver = approver

    return host, _make_llm()


def _make_llm():
    fallback = MockLLM({"Determiner": {"avis_responsable": "refus"}})
    if os.environ.get("VALMONT_MOCK_LLM", "").strip() not in ("", "0"):
        return fallback
    if not external_llm_consent():
        print("    · oracle externe non autorise (VALMONT_LLM_EXTERNE) "
              "— repli deterministe local, aucune donnee ne quitte le site")
        return fallback
    try:
        from gemini_llm import GeminiLLM
        key = gemini_key()
        if key:
            return GeminiLLM(os.environ.get("VALMONT_MODEL",
                                            "gemini-3.1-flash-lite"), key)
    except Exception as exc:                       # pragma: no cover
        print(f"    ⚠  oracle Gemini indisponible ({exc}) — repli deterministe")
    return fallback

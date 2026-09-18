"""Hôte local de simulation pour la surveillance d'une cave à vins.

Branchements à remplacer en production :
[B1] automate de capteurs et GTB/GTC ; [B2] WMS/ERP et contrôle d'accès ;
[B3] centrale CVC et secours ; [B4] éclairage, sirène et verrouillage ;
[B5] notifications et centre de télésurveillance ; [B6] journal append-only.

Ce module ne réalise aucune E/S réseau, fichier, SMS ou action physique. Les
outils mutent seulement un état mémoire et gardent une trace structurée.
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[5]))

from agentl import AnthropicLLM, Host, MockLLM, Symbol


def build(fixture_overrides=None):
    host = Host()
    mode = os.environ.get("AGENTL_CAVE_MODE", "nominal")
    fixtures = {
        "nominal": {
            "read_ok": "yes", "event_id": "evt-live-001", "zone": "ZONE_A",
            "kind": "periodic", "temperature": 12.0, "drift": 0.1,
            "humidity": 70.0, "lux": 0.0, "vibration": 0.003,
            "vibration_duration": 0.0, "signals": 0, "presence": "no",
            "forced_entry": "no", "tampered": "no", "access_authorized": "yes",
        },
        "minor_hot": {
            "read_ok": "yes", "event_id": "evt-live-002", "zone": "ZONE_A",
            "kind": "periodic", "temperature": 13.6, "drift": 0.6,
            "humidity": 70.0, "lux": 0.0, "vibration": 0.003,
            "vibration_duration": 0.0, "signals": 0, "presence": "no",
            "forced_entry": "no", "tampered": "no", "access_authorized": "yes",
        },
        "critical_hot": {
            "read_ok": "yes", "event_id": "evt-live-003", "zone": "ZONE_B",
            "kind": "periodic", "temperature": 15.2, "drift": 1.4,
            "humidity": 70.0, "lux": 0.0, "vibration": 0.003,
            "vibration_duration": 0.0, "signals": 0, "presence": "no",
            "forced_entry": "no", "tampered": "no", "access_authorized": "yes",
        },
        "intrusion": {
            "read_ok": "yes", "event_id": "evt-live-004", "zone": "ZONE_C",
            "kind": "security", "temperature": 12.0, "drift": 0.0,
            "humidity": 70.0, "lux": 120.0, "vibration": 0.08,
            "vibration_duration": 15.0, "signals": 3, "presence": "yes",
            "forced_entry": "yes", "tampered": "no", "access_authorized": "no",
        },
        "sabotage": {
            "read_ok": "yes", "event_id": "evt-live-005", "zone": "ZONE_D",
            "kind": "security", "temperature": 12.0, "drift": 0.0,
            "humidity": 70.0, "lux": 0.0, "vibration": 0.003,
            "vibration_duration": 0.0, "signals": 1, "presence": "no",
            "forced_entry": "no", "tampered": "yes", "access_authorized": "unknown",
        },
        "ambiguous_security": {
            "read_ok": "yes", "event_id": "evt-live-007", "zone": "ZONE_C",
            "kind": "security", "temperature": 12.0, "drift": 0.0,
            "humidity": 70.0, "lux": 18.0, "vibration": 0.012,
            "vibration_duration": 2.0, "signals": 1, "presence": "yes",
            "forced_entry": "no", "tampered": "no", "access_authorized": "unknown",
        },
        "source_failure": {
            "read_ok": "no", "event_id": "evt-live-006", "zone": "ZONE_A",
            "kind": "failure", "temperature": 99.0, "drift": 99.0,
            "humidity": 0.0, "lux": 0.0, "vibration": 1.0,
            "vibration_duration": 60.0, "signals": 3, "presence": "yes",
            "forced_entry": "yes", "tampered": "yes", "access_authorized": "unknown",
        },
    }
    fixture = dict(fixtures.get(mode, fixtures["nominal"]))
    if fixture_overrides:
        # Couture de test et d'intégration : les valeurs restent brutes et
        # traversent exactement la même validation que les capteurs réels.
        # Aucun override n'est lu depuis une variable d'environnement.
        fixture.update(dict(fixture_overrides))
    fixture["evidence_token"] = f"proof-{fixture['zone']}-{fixture['event_id']}"

    def finite_number(name, minimum=None, maximum=None):
        value = fixture.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        try:
            number = float(value)
        except (OverflowError, ValueError):
            return False
        return (math.isfinite(number)
                and (minimum is None or number >= minimum)
                and (maximum is None or number <= maximum))

    def bounded_text(name, maximum=256):
        value = fixture.get(name)
        # BOUNDARY-OK: validation défensive du schéma du connecteur — un
        # identifiant non vide et de longueur bornée ; aucun choix de plan.
        return isinstance(value, str) and 0 < len(value) <= maximum

    def closed_symbol(name, values):
        return str(fixture.get(name, "")).lower() in values

    # La santé de la source n'est pas une affirmation aveugle du connecteur :
    # elle inclut le contrat minimal des données qui pilotent des actions.
    # Sans cela, ``+Inf`` déclenchait le secours, ``-Inf`` aussi et ``True``
    # était pris pour 1 °C, donc pour un froid critique.
    # BOUNDARY-OK: validation défensive du schéma du connecteur ; ces bornes
    # physiques rejettent les données impossibles mais ne choisissent aucun plan.
    data_valid = all((
        bounded_text("event_id"),
        bounded_text("zone"),
        finite_number("temperature", -50, 100),
        finite_number("drift", -100, 100),
        finite_number("humidity", 0, 100),
        finite_number("lux", 0, 10_000_000),
        finite_number("vibration", 0, 100),
        finite_number("vibration_duration", 0, 86_400),
        isinstance(fixture.get("signals"), int)
        and not isinstance(fixture.get("signals"), bool)
        and 0 <= fixture["signals"] <= 1_000,
        closed_symbol("presence", {"yes", "no", "unknown"}),
        closed_symbol("forced_entry", {"yes", "no", "unknown"}),
        closed_symbol("tampered", {"yes", "no", "unknown"}),
        closed_symbol("access_authorized", {"yes", "no", "unknown"}),
    ))
    # BOUNDARY-OK: agrégation de santé technique et de conformité du schéma ;
    # le `.agent` décide seul de la conduite à tenir quand le fait vaut no.
    effective_read_ok = "yes" if fixture.get("read_ok") == "yes" and data_valid else "no"

    def rendered_number(name, digits=1):
        value = fixture.get(name)
        return f"{float(value):.{digits}f}" if finite_number(name) else "INVALIDE"

    # Resumes volontairement desidentifies : aucun identifiant, zone ou jeton
    # de preuve ne rejoint le contexte du fournisseur de modele.
    environment_history_summary = (
        "Profil agrege sur douze mois; consigne historique 12 C et 70 pourcent; "
        f"derive recente {rendered_number('drift')} C par heure."
    )
    environment_weather_summary = (
        "Prevision locale agregee sur six heures: variation exterieure faible, "
        "aucun episode meteorologique violent annonce."
    )
    environment_multisensor_summary = (
        f"Mesures anonymisees: temperature {rendered_number('temperature')} C; "
        f"humidite {rendered_number('humidity')} pourcent; "
        f"lumiere {rendered_number('lux')} lux; "
        f"vibration {rendered_number('vibration', 3)} g pendant "
        f"{rendered_number('vibration_duration')} secondes."
    )
    security_multimodal_summary = {
        "nominal": "Aucun mouvement; obscurite stable; aucune vibration anormale.",
        "minor_hot": "Aucun mouvement; obscurite stable; aucune vibration anormale.",
        "critical_hot": "Aucun mouvement; obscurite stable; alarme climatique uniquement.",
        "intrusion": "Presence non reconnue, forte lumiere et vibration coherente avec une ouverture forcee.",
        "sabotage": "Integrite capteur rompue; donnees multimodales incompletes.",
        "ambiguous_security": "Silhouette partielle, bref eclairage et faible vibration; aucune ouverture forcee confirmee.",
        "source_failure": "Flux capteurs indisponible; contenu non exploitable.",
    }.get(mode, "Resume multimodal indisponible.")
    security_access_summary = {
        "intrusion": "Aucun acces autorise dans la fenetre temporelle agregee.",
        "ambiguous_security": "Journal agrege incomplet; aucune correspondance certaine.",
        "sabotage": "Etat du controle acces inconnu.",
    }.get(mode, "Acces planifie present ou absence de signal de securite.")

    state = {
        "cycle_done": Symbol("no"),
        "regulation_adjusted": Symbol("no"),
        "backup_activated": Symbol("no"),
        "notification_sent": Symbol("no"),
        "deterrence_activated": Symbol("no"),
        "lock_commanded": Symbol("no"),
        "security_center_alerted": Symbol("no"),
        "public_authorities_alerted": Symbol("no"),
        "neutralization_activated": Symbol("no"),
        "life_safety_cut": Symbol("no"),
        "audit_recorded": Symbol("no"),
        "global_status": Symbol("unknown"),
        "journal": [],
    }

    host.sensors.update({
        "source.read_ok": lambda: Symbol(effective_read_ok),
        "cycle.done": lambda: state["cycle_done"],
        "event.id": lambda: fixture["event_id"],
        "event.zone": lambda: fixture["zone"],
        "event.evidence_token": lambda: fixture["evidence_token"],
        "environment.temperature_c": lambda: fixture["temperature"],
        "environment.history_summary": lambda: environment_history_summary,
        "environment.weather_summary": lambda: environment_weather_summary,
        "environment.multisensor_summary": lambda: environment_multisensor_summary,
        "environment.humidity_percent": lambda: fixture["humidity"],
        "environment.lux": lambda: fixture["lux"],
        "environment.vibration_rms_g": lambda: fixture["vibration"],
        "environment.vibration_duration_s": lambda: fixture["vibration_duration"],
        "security.signal_count": lambda: fixture["signals"],
        "security.multimodal_summary": lambda: security_multimodal_summary,
        "security.access_summary": lambda: security_access_summary,
        "security.summary_trusted": lambda: Symbol(
            os.environ.get("AGENTL_CAVE_SECURITY_SUMMARY_TRUSTED", "yes")
        ),
        "consent.llm_export": lambda: Symbol(
            os.environ.get("AGENTL_CAVE_LLM_CONSENT", "no")
        ),
        "security.presence_unidentified": lambda: Symbol(fixture["presence"]),
        "security.forced_entry": lambda: Symbol(fixture["forced_entry"]),
        "security.sensor_tampered": lambda: Symbol(fixture["tampered"]),
        "operator.confirmed": lambda: Symbol(os.environ.get("AGENTL_CAVE_HUMAN_CONFIRMED", "no")),
        "regulation.adjusted": lambda: state["regulation_adjusted"],
        "backup.activated": lambda: state["backup_activated"],
        "notification.sent": lambda: state["notification_sent"],
        "deterrence.activated": lambda: state["deterrence_activated"],
        "lock.commanded": lambda: state["lock_commanded"],
        "security_center.alerted": lambda: state["security_center_alerted"],
        "public_authorities.alerted": lambda: state["public_authorities_alerted"],
        "neutralization.activated": lambda: state["neutralization_activated"],
        "life_safety.cut": lambda: state["life_safety_cut"],
        "audit.recorded": lambda: state["audit_recorded"],
        "status.global": lambda: state["global_status"],
    })

    def _attest(zone, evidence_token):
        # BOUNDARY-OK: revalidation d'identité et de fraîcheur, aucun choix métier.
        if str(zone) != fixture["zone"] or str(evidence_token) != fixture["evidence_token"]:
            raise ValueError("attestation de zone invalide ou périmée")

    def adjust_regulation(zone, temperature_delta_c, humidity_delta_percent,
                          evidence_token):
        _attest(zone, evidence_token)
        # BOUNDARY-OK: bornes physiques de défense en profondeur, reflétant le
        # maximum absolu autorisé par l'équipement et non un choix de scénario.
        if abs(float(temperature_delta_c)) > 1.0 or abs(float(humidity_delta_percent)) > 5.0:
            raise ValueError("ordre de régulation hors bornes physiques")
        state["regulation_adjusted"] = Symbol("yes")
        state["journal"].append({"tool": "adjust_regulation", "zone": str(zone)})
        return {"accepted": Symbol("yes")}

    def activate_backup(zone, evidence_token):
        _attest(zone, evidence_token)
        state["backup_activated"] = Symbol("yes")
        state["journal"].append({"tool": "activate_backup", "zone": str(zone)})
        return {"activated": Symbol("yes")}

    def notify_regisseur(event_id, zone, level, evidence_token):
        _attest(zone, evidence_token)
        state["notification_sent"] = Symbol("yes")
        state["journal"].append({"tool": "notify_regisseur", "event": str(event_id),
                                 "zone": str(zone), "level": float(level)})
        return {"delivered": Symbol("yes")}

    def activate_deterrence(zone, evidence_token):
        _attest(zone, evidence_token)
        state["deterrence_activated"] = Symbol("yes")
        state["journal"].append({"tool": "activate_deterrence", "zone": str(zone)})
        return {"activated": Symbol("yes")}

    def command_internal_lock(zone, evidence_token):
        _attest(zone, evidence_token)
        state["lock_commanded"] = Symbol("yes")
        state["journal"].append({"tool": "command_internal_lock", "zone": str(zone)})
        return {"commanded": Symbol("yes")}

    def alert_security_center(event_id, zone, evidence_token):
        _attest(zone, evidence_token)
        state["security_center_alerted"] = Symbol("yes")
        state["journal"].append({"tool": "alert_security_center", "event": str(event_id),
                                 "zone": str(zone)})
        return {"delivered": Symbol("yes")}

    def alert_public_authorities(event_id, zone, human_confirmation, evidence_token):
        _attest(zone, evidence_token)
        # BOUNDARY-OK: seconde vérification fail-closed de la confirmation humaine.
        if str(human_confirmation) != "yes":
            raise PermissionError("confirmation humaine absente")
        # BOUNDARY-OK: opt-in explicite de branchement ; la simulation reste sans réseau.
        if os.environ.get("AGENTL_CAVE_ALLOW_PUBLIC_ALERT") != "yes":
            raise PermissionError("passerelle publique désactivée en simulation")
        state["journal"].append({"rollback": "annuler la demande avant émission externe",
                                 "event": str(event_id)})
        state["public_authorities_alerted"] = Symbol("yes")
        return {"delivered": Symbol("yes")}

    def activate_physical_neutralization():
        raise PermissionError("capacité dangereuse absente de l'hôte de simulation")

    def cut_fire_and_ventilation():
        raise PermissionError("équipements vitaux protégés par construction")

    def _record(status):
        state["audit_recorded"] = Symbol("yes")
        state["global_status"] = Symbol(status)
        state["cycle_done"] = Symbol("yes")
        state["journal"].append({"tool": "append_audit", "event": fixture["event_id"], "status": status})
        return {"recorded": Symbol("yes")}

    def record_conforme():
        return _record("conforme")

    def record_alerte():
        return _record("alerte")

    def record_deteriore():
        return _record("deteriore")

    def record_degraded():
        return _record("indetermine")

    host.tools.update({
        "adjust_regulation": adjust_regulation,
        "activate_backup": activate_backup,
        "notify_regisseur": notify_regisseur,
        "activate_deterrence": activate_deterrence,
        "command_internal_lock": command_internal_lock,
        "alert_security_center": alert_security_center,
        "alert_public_authorities": alert_public_authorities,
        "activate_physical_neutralization": activate_physical_neutralization,
        "cut_fire_and_ventilation": cut_fire_and_ventilation,
        "record_conforme": record_conforme,
        "record_alerte": record_alerte,
        "record_deteriore": record_deteriore,
        "record_degraded": record_degraded,
    })

    # BOUNDARY-OK: opt-in opérateur explicite ; absence de variable = refus.
    host.approver = lambda _request: os.environ.get("AGENTL_CAVE_APPROVE_PUBLIC") == "yes"

    consent = os.environ.get("AGENTL_CAVE_LLM_CONSENT", "no")
    provider_name = os.environ.get("AGENTL_CAVE_LLM_PROVIDER", "gemini").lower()
    llm = MockLLM({})

    # BOUNDARY-OK: choix de connecteur technique, jamais choix de plan ou action.
    # Le consentement doit etre explicite; une cle absente conserve oracle muet.
    if consent == "yes" and provider_name == "gemini":
        api_key = os.environ.get("GEMINI_API_KEY", "")
        if api_key:
            from examples.gemini_llm import GeminiLLM
            llm = GeminiLLM(
                model=os.environ.get("AGENTL_CAVE_LLM_MODEL",
                                     "gemini-3.1-flash-lite"),
                api_key=api_key,
            )
    elif consent == "yes" and provider_name == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if api_key:
            llm = AnthropicLLM(
                model=os.environ.get("AGENTL_CAVE_LLM_MODEL",
                                     "claude-sonnet-4-6"),
                api_key=api_key,
            )

    return host, llm

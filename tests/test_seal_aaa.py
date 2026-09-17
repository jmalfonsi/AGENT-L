"""Tests AAA du SCELLEMENT des journaux de rejeu (seal.py).

Un journal de rejeu prétend dire ce que l'agent a observé et décidé. Les tests
qui suivent sont adverses par construction : chacun joue le rôle du
falsificateur et exige que la manœuvre soit **détectée et localisée**.

  * **intégrité** : modifier, insérer, retirer ou réordonner un franchissement
    casse la chaîne, et le rapport dit à quel indice ;
  * **stabilité** : un ré-indentage, un ré-encodage, un changement d'ordre des
    clés JSON ne cassent rien — sans quoi l'alarme serait inutilisable ;
  * **authenticité** : le sceau lie la méta scellée à une clé ; changer la
    source, la trace ou la tête de chaîne l'invalide ;
  * **dissymétrie** : une clé publique vérifie et ne signe pas ;
  * **les trois états restent distincts** : intact+signé, intact+non signé,
    rompu — les confondre est le défaut qu'on cherche à empêcher.

    python -m pytest tests/test_seal_aaa.py -q
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agentl.replay import Journal, ReplayError
from agentl.seal import (ALG_ED25519, ALG_HMAC, SealError, chain_head,
                         generate_ed25519, load_signing_key, load_verify_key,
                         sign, verify_chain)

try:
    import cryptography  # noqa: F401
    HAS_CRYPTO = True
except ImportError:                                        # pragma: no cover
    HAS_CRYPTO = False


def _journal(n: int = 5) -> Journal:
    """Journal jouet, mais de forme réaliste : lectures, appel, erreur."""
    j = Journal(meta={"agent": "sentinel", "source": "a.agent",
                      "source_sha256": "0" * 64})
    for i in range(n):
        j.record("read", f"cpu.load{i}", value=i * 10)
    j.record("invoke", "restart", args={"service": "web"}, value={"ok": True})
    j.record("read", "disk.free", error=OSError("capteur muet"))
    return j


# --------------------------------------------------------------------------
# M1 — la chaîne détecte l'altération et dit où
# --------------------------------------------------------------------------
class TestChainDetectsTampering(unittest.TestCase):

    def test_an_untouched_journal_verifies(self):
        raw = json.loads(_journal().seal("trace").dumps())
        report = verify_chain(raw)
        self.assertTrue(report.ok, report.render())

    def test_changing_a_value_is_localised(self):
        j = _journal().seal("trace")
        raw = json.loads(j.dumps())
        raw["entries"][3]["value"] = 999

        report = verify_chain(raw)

        self.assertFalse(report.ok)
        self.assertEqual(report.first_bad, 3)

    def test_changing_an_argument_is_detected(self):
        """Même outil, argument changé : c'est une autre décision."""
        j = _journal().seal("trace")
        raw = json.loads(j.dumps())
        target = next(i for i, e in enumerate(raw["entries"])
                      if e["kind"] == "invoke")
        raw["entries"][target]["args"]["$dict"][0][1] = "db"

        report = verify_chain(raw)

        self.assertFalse(report.ok)
        self.assertEqual(report.first_bad, target)

    def test_removing_a_middle_entry_is_detected(self):
        j = _journal().seal("trace")
        raw = json.loads(j.dumps())
        del raw["entries"][2]

        self.assertFalse(verify_chain(raw).ok)

    def test_truncating_the_tail_is_detected_by_the_head(self):
        """Cas rusé : les entrées restantes sont cohérentes entre elles. Seule
        la tête scellée trahit la coupe."""
        j = _journal().seal("trace")
        raw = json.loads(j.dumps())
        raw["entries"] = raw["entries"][:3]

        report = verify_chain(raw)

        self.assertFalse(report.ok)
        self.assertIsNone(report.first_bad)
        self.assertNotEqual(report.head_actual, report.head_expected)

    def test_reordering_two_entries_is_detected(self):
        j = _journal().seal("trace")
        raw = json.loads(j.dumps())
        raw["entries"][1], raw["entries"][2] = raw["entries"][2], raw["entries"][1]

        self.assertFalse(verify_chain(raw).ok)

    def test_recomputed_hashes_still_fail_against_the_sealed_head(self):
        """Le falsificateur qui *recalcule* la chaîne bute sur la tête scellée.

        C'est ce qui distingue le chaînage d'un simple total de contrôle : sans
        la clé, il ne peut pas mettre la tête à jour de façon crédible."""
        j = _journal().seal("trace")
        raw = json.loads(j.dumps())
        raw["entries"][2]["value"] = 4242
        # Le falsificateur refait proprement tous les condensats…
        forged = Journal(entries=j.entries, meta={})
        for entry, body in zip(forged.entries, raw["entries"]):
            entry.value = body.get("value")
        raw2 = json.loads(forged.dumps())
        raw2["meta"]["chain_sha256"] = j.meta["chain_sha256"]  # …mais garde la tête

        self.assertFalse(verify_chain(raw2).ok)

    def test_an_unchained_journal_is_not_declared_valid(self):
        report = verify_chain({"meta": {"format": 2}, "entries": []})
        self.assertFalse(report.ok)


# --------------------------------------------------------------------------
# M2 — la chaîne ne crie pas pour rien
# --------------------------------------------------------------------------
class TestChainIsStable(unittest.TestCase):

    def test_reindenting_the_file_changes_nothing(self):
        raw = json.loads(_journal().seal("trace").dumps())
        rewritten = json.loads(json.dumps(raw, indent=8))

        self.assertTrue(verify_chain(rewritten).ok)

    def test_reordering_json_keys_changes_nothing(self):
        """La canonicalisation est ce qui rend l'alarme exploitable."""
        raw = json.loads(_journal().seal("trace").dumps())
        shuffled = {"entries": [dict(reversed(list(e.items())))
                                for e in raw["entries"]],
                    "meta": raw["meta"]}

        self.assertTrue(verify_chain(shuffled).ok)

    def test_saving_twice_gives_the_same_head(self):
        j = _journal().seal("trace")
        self.assertEqual(json.loads(j.dumps())["meta"]["chain_sha256"],
                         json.loads(j.dumps())["meta"]["chain_sha256"])

    def test_an_empty_journal_still_has_a_head(self):
        """« Zéro franchissement » est un fait qui se scelle comme un autre."""
        self.assertTrue(chain_head([]))
        self.assertTrue(verify_chain(json.loads(Journal().seal("t").dumps())).ok)


# --------------------------------------------------------------------------
# M3 — le chargement refuse une pièce corrompue
# --------------------------------------------------------------------------
class TestStrictLoading(unittest.TestCase):

    def test_loading_a_tampered_journal_raises(self):
        raw = json.loads(_journal().seal("trace").dumps())
        raw["entries"][1]["value"] = -1

        with self.assertRaises(ReplayError):
            Journal.loads(json.dumps(raw))

    def test_non_strict_loading_reports_instead_of_raising(self):
        """Un auditeur doit pouvoir *examiner* le dommage."""
        raw = json.loads(_journal().seal("trace").dumps())
        raw["entries"][1]["value"] = -1

        j = Journal.loads(json.dumps(raw), strict=False)

        self.assertFalse(j.chain_report.ok)
        self.assertEqual(j.chain_report.first_bad, 1)

    def test_a_format_1_journal_is_readable_but_never_declared_intact(self):
        legacy = {"meta": {"format": 1}, "entries": []}

        j = Journal.loads(json.dumps(legacy))

        self.assertFalse(j.chain_report.ok)
        self.assertIn("format 1", j.chain_report.render())

    def test_an_unknown_format_is_still_refused(self):
        with self.assertRaises(ReplayError):
            Journal.loads(json.dumps({"meta": {"format": 999}, "entries": []}))


# --------------------------------------------------------------------------
# M4 — la signature HMAC (sans dépendance)
# --------------------------------------------------------------------------
class TestHmacSignature(unittest.TestCase):

    def setUp(self):
        self.key = load_signing_key("secret-de-test")

    def test_a_signed_journal_verifies_with_the_same_secret(self):
        j = _journal().seal("trace", key=self.key)

        report = Journal.loads(j.dumps()).verify_seal(self.key)

        self.assertTrue(report.ok, report.render())
        self.assertEqual(report.alg, ALG_HMAC)

    def test_another_secret_does_not_verify(self):
        j = _journal().seal("trace", key=self.key)

        report = Journal.loads(j.dumps()).verify_seal(
            load_signing_key("autre-secret"))

        self.assertFalse(report.ok)

    def test_changing_the_source_digest_breaks_the_seal(self):
        """Signer la seule tête de chaîne laisserait re-présenter un journal
        authentique comme provenant d'un autre programme."""
        j = _journal().seal("trace", key=self.key)
        raw = json.loads(j.dumps())
        raw["meta"]["source_sha256"] = "f" * 64

        loaded = Journal.loads(json.dumps(raw))

        self.assertTrue(loaded.chain_report.ok)      # les entrées sont intactes…
        self.assertFalse(loaded.verify_seal(self.key).ok)   # …mais pas la méta

    def test_changing_the_trace_digest_breaks_the_seal(self):
        j = _journal().seal("trace", key=self.key)
        raw = json.loads(j.dumps())
        raw["meta"]["trace_sha256"] = "0" * 64

        self.assertFalse(Journal.loads(json.dumps(raw)).verify_seal(self.key).ok)

    def test_missing_signature_and_wrong_signature_are_distinct_verdicts(self):
        """Confondre « rien n'a été promis » et « la promesse est rompue »
        reviendrait à traiter un journal non signé comme un faux."""
        unsigned = Journal.loads(_journal().seal("trace").dumps())
        forged_raw = json.loads(_journal().seal("trace", key=self.key).dumps())
        forged_raw["meta"]["signature"]["value"] = "ab" * 32
        forged = Journal.loads(json.dumps(forged_raw))

        self.assertFalse(unsigned.signed)
        self.assertTrue(forged.signed)
        self.assertIn("non signé", unsigned.verify_seal(self.key).render())
        self.assertIn("invalide", forged.verify_seal(self.key).render())

    def test_verifying_without_a_key_is_not_a_success(self):
        j = Journal.loads(_journal().seal("trace", key=self.key).dumps())

        self.assertFalse(j.verify_seal(None).ok)

    def test_a_key_file_and_its_literal_content_agree(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "k.bin"
            path.write_bytes(b"secret-de-test")

            self.assertEqual(load_signing_key(str(path)).key_id, self.key.key_id)

    def test_no_key_anywhere_means_no_signing_key(self):
        self.assertIsNone(load_signing_key(None))


# --------------------------------------------------------------------------
# M5 — la signature Ed25519 (extra `sign`)
# --------------------------------------------------------------------------
@unittest.skipUnless(HAS_CRYPTO, "extra `sign` (cryptography) non installé")
class TestEd25519Signature(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.priv, self.pub = generate_ed25519(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_a_journal_signed_privately_verifies_publicly(self):
        key = load_signing_key(str(self.priv))
        j = _journal().seal("trace", key=key)

        report = Journal.loads(j.dumps()).verify_seal(load_verify_key(str(self.pub)))

        self.assertTrue(report.ok, report.render())
        self.assertEqual(report.alg, ALG_ED25519)

    def test_the_public_key_cannot_sign(self):
        """Toute la dissymétrie tient là : l'auditeur constate sans contrefaire."""
        public = load_verify_key(str(self.pub))

        with self.assertRaises(SealError):
            sign({"chain_sha256": "x"}, public)

    def test_a_tampered_entry_breaks_the_chain_under_a_valid_seal(self):
        key = load_signing_key(str(self.priv))
        raw = json.loads(_journal().seal("trace", key=key).dumps())
        raw["entries"][2]["value"] = 1

        report = verify_chain(raw)

        self.assertFalse(report.ok)
        self.assertEqual(report.first_bad, 2)

    def test_another_keypair_does_not_verify(self):
        key = load_signing_key(str(self.priv))
        other_priv, other_pub = generate_ed25519(self._tmp.name, name="autre")
        j = Journal.loads(_journal().seal("trace", key=key).dumps())

        report = j.verify_seal(load_verify_key(str(other_pub)))

        self.assertFalse(report.ok)
        self.assertIn("différente", report.render())

    def test_the_private_key_is_not_world_readable(self):
        self.assertEqual(self.priv.stat().st_mode & 0o077, 0)


# --------------------------------------------------------------------------
# M6 — le sceau suit l'ajout d'entrées après coup
# --------------------------------------------------------------------------
class TestSealingIsAPointInTime(unittest.TestCase):

    def test_recording_after_sealing_makes_the_head_diverge(self):
        """Scellé puis complété : la divergence doit se voir, pas se réécrire."""
        j = _journal(2).seal("trace", key=load_signing_key("s"))
        sealed_head = j.meta["chain_sha256"]
        j.record("read", "ajout.tardif", value=1)

        raw = json.loads(j.dumps())

        self.assertEqual(raw["meta"]["chain_sha256"], sealed_head)
        self.assertFalse(verify_chain(raw).ok)

    def test_resealing_after_a_change_produces_a_new_head(self):
        j = _journal(2)
        j.seal("trace")
        first = j.meta["chain_sha256"]
        j.record("read", "ajout", value=1)
        j.seal("trace")

        self.assertNotEqual(j.meta["chain_sha256"], first)
        self.assertTrue(verify_chain(json.loads(j.dumps())).ok)


# --------------------------------------------------------------------------
# M7 — la résolution de clé ne dégrade pas en silence
# --------------------------------------------------------------------------
class TestKeyResolutionFailsLoudly(unittest.TestCase):
    """Un `spec` qui prétendait être une clé ne doit pas devenir un secret.

    Le repli « fichier absent → secret littéral » scellait le journal avec la
    *chaîne du chemin* : une faute de frappe, ou une clé absente du conteneur,
    produisait un sceau au secret public. Sur l'artefact dont le seul rôle est
    d'être opposable à un auditeur, l'ambiguïté doit s'arrêter net.
    """

    def test_a_missing_key_file_is_refused_not_used_as_a_secret(self):
        for spec in ("/introuvable/journal.key", "cle-absente.pem",
                     "./secrets/x.pub", "manquant.secret"):
            with self.subTest(spec=spec):
                with self.assertRaises(SealError) as caught:
                    load_signing_key(spec)
                self.assertIn("introuvable", str(caught.exception))

    def test_a_deliberate_literal_secret_still_works(self):
        key = load_signing_key("secret-tape-a-la-main")

        self.assertEqual(key.alg, ALG_HMAC)
        self.assertTrue(Journal.loads(
            _journal().seal("trace", key=key).dumps()).verify_seal(key).ok)

    def test_an_existing_key_file_is_read_as_key_material(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "journal.key"
            path.write_bytes(b"octets-de-clef")

            key = load_signing_key(str(path))

            self.assertEqual(key.alg, ALG_HMAC)
            self.assertEqual(key.material, b"octets-de-clef")

    def test_the_verification_key_is_refused_the_same_way(self):
        with self.assertRaises(SealError):
            load_verify_key("/introuvable/journal.pub")


if __name__ == "__main__":                                 # pragma: no cover
    unittest.main()

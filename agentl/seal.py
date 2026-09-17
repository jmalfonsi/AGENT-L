"""Scellement des journaux de rejeu — rendre la falsification détectable.

Un journal de rejeu est une pièce d'audit : il prétend dire ce que l'agent a
observé et décidé ce jour-là. Tant qu'il n'est qu'un fichier JSON, cette
prétention ne vaut rien — n'importe qui peut retoucher une valeur, supprimer
un franchissement gênant, ou fabriquer un journal de toutes pièces, et le
rejeu confirmera docilement l'histoire réécrite.

Deux mécanismes, à ne pas confondre :

  * **La chaîne de hachage** (toujours présente, sans clé). Chaque entrée porte
    le condensat de tout ce qui la précède. Modifier, insérer ou retirer un
    franchissement change la tête de chaîne — et, parce que le lien est
    *par entrée*, on sait **où**. C'est de l'intégrité : elle détecte
    l'altération d'un journal, pas la fabrication d'un journal neuf.

  * **La signature** (optionnelle, avec clé). Elle lie la tête de chaîne à un
    détenteur de clé. C'est de l'authenticité : sans la clé, on ne peut pas
    produire un journal qui se présente comme émis par l'agent.

Deux algorithmes, selon ce dont on dispose :

    HMAC-SHA256   stdlib, secret partagé. Le vérificateur détient le même
                  secret que le signataire — donc il pourrait forger. Suffit
                  entre deux composants d'un même système, pas face à un
                  auditeur externe.
    Ed25519       asymétrique, via `cryptography` (extra `sign`). Le
                  vérificateur ne détient que la clé publique : il constate
                  sans pouvoir contrefaire. C'est ce qu'il faut pour un audit.

La direction de sûreté est la même que celle du solveur : on ne déclare un
journal intact que lorsqu'on l'a vérifié. Un journal non scellé n'est jamais
présenté comme valide — il est présenté comme non scellé.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .core import AgentLError

#: Préfixe de domaine. Un condensat produit ici ne peut pas être confondu avec
#: un condensat produit ailleurs pour d'autres octets — c'est ce qui empêche de
#: rejouer une signature d'un contexte dans un autre.
CHAIN_DOMAIN = b"agentl.journal.chain.v1\n"
SIGN_DOMAIN = b"agentl.journal.seal.v1\n"

ALG_HMAC = "HMAC-SHA256"
ALG_ED25519 = "Ed25519"

#: Variable d'environnement consultée à défaut de `--sign-key`.
KEY_ENV = "AGENTL_JOURNAL_KEY"


class SealError(AgentLError):
    """Défaut de scellement — clé illisible, algorithme absent, sceau invalide."""


# --------------------------------------------------------------------------
# Chaîne de hachage
# --------------------------------------------------------------------------
def canonical(obj: Any) -> bytes:
    """Forme canonique d'une valeur JSON : deux journaux égaux, mêmes octets.

    Sans canonicalisation, un simple ré-indentage changerait le condensat et
    ferait crier à la falsification là où rien n'a bougé.
    """
    return json.dumps(obj, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def chain_hashes(entries: List[Dict[str, Any]]) -> List[str]:
    """Condensats chaînés des entrées, dans l'ordre.

    `h[i] = sha256(domaine ‖ h[i-1] ‖ canonique(entrée_i))`, avec `h[-1]` vide.
    Le chaînage est ce qui distingue l'intégrité d'un simple total de
    contrôle : retirer l'entrée #7 d'un journal de 40 casse les 33 suivantes,
    donc ne se rattrape pas en recalculant une seule ligne.
    """
    out: List[str] = []
    previous = ""
    for raw in entries:
        # L'entrée est hachée *sans* son propre condensat : il en est le
        # résultat, il ne peut pas en être une entrée.
        body = {k: v for k, v in raw.items() if k != "h"}
        digest = hashlib.sha256(
            CHAIN_DOMAIN + previous.encode("ascii") + canonical(body)
        ).hexdigest()
        out.append(digest)
        previous = digest
    return out


def chain_head(entries: List[Dict[str, Any]]) -> str:
    """Tête de chaîne — le condensat unique qui résume tout le journal.

    Un journal vide a une tête définie (le domaine seul) plutôt qu'aucune :
    « zéro franchissement » est un fait qui se scelle comme un autre.
    """
    hashes = chain_hashes(entries)
    if hashes:
        return hashes[-1]
    return hashlib.sha256(CHAIN_DOMAIN).hexdigest()


@dataclass
class ChainReport:
    """Verdict d'intégrité. `ok` est vrai *seulement* si tout a été vérifié."""

    ok: bool
    reason: str = ""
    #: Index de la première entrée dont le condensat ne colle pas, si connu.
    first_bad: Optional[int] = None
    head_expected: str = ""
    head_actual: str = ""

    def render(self) -> str:
        if self.ok:
            return f"chaîne intacte ({self.head_actual[:12]}…)"
        where = "" if self.first_bad is None else \
            f" — première altération au franchissement #{self.first_bad}"
        return f"{self.reason}{where}"


def verify_chain(raw: Dict[str, Any]) -> ChainReport:
    """Le journal sérialisé est-il celui qui fut scellé ?

    On travaille sur la forme *sérialisée* et non sur les objets `Entry` :
    c'est le fichier qu'un falsificateur retouche, c'est donc le fichier qu'il
    faut vérifier.
    """
    entries = raw.get("entries", [])
    meta = raw.get("meta", {})
    expected_head = meta.get("chain_sha256")
    if not expected_head:
        return ChainReport(False, "journal non chaîné : aucune tête de chaîne")

    recomputed = chain_hashes(entries)
    for i, (entry, digest) in enumerate(zip(entries, recomputed)):
        stored = entry.get("h")
        if stored is None:
            return ChainReport(False, "entrée sans condensat de chaîne",
                               first_bad=i)
        if stored != digest:
            return ChainReport(False, "condensat de chaîne incohérent",
                               first_bad=i, head_expected=expected_head,
                               head_actual=recomputed[-1] if recomputed else "")

    actual_head = recomputed[-1] if recomputed else chain_head(entries)
    if actual_head != expected_head:
        # Cas typique : des entrées ont été retirées *en queue*. Les condensats
        # restants sont cohérents entre eux, mais la tête ne l'est plus.
        return ChainReport(False, "tête de chaîne différente de la tête scellée "
                                  "(entrées retirées en fin de journal ?)",
                           head_expected=expected_head, head_actual=actual_head)
    return ChainReport(True, head_actual=actual_head, head_expected=expected_head)


# --------------------------------------------------------------------------
# Clés
# --------------------------------------------------------------------------
@dataclass
class SigningKey:
    """Clé de scellement, résolue depuis la ligne de commande ou l'environnement."""

    alg: str
    material: bytes
    #: Identifiant public court, journalisé en clair pour dire *quelle* clé.
    key_id: str

    @property
    def is_asymmetric(self) -> bool:
        return self.alg == ALG_ED25519


def _key_id(public_bytes: bytes) -> str:
    return hashlib.sha256(public_bytes).hexdigest()[:16]


#: Suffixes qui annoncent un fichier de clé et non un secret tapé à la main.
_KEY_SUFFIXES = (".key", ".pem", ".pub", ".secret")


def _looks_like_a_path(raw: str) -> bool:
    """Ce `spec` désigne-t-il visiblement un fichier ?

    La distinction n'est pas cosmétique. `load_signing_key` se rabat sur
    « secret littéral » pour tout ce qui n'est pas un fichier existant : sur
    une faute de frappe, ou sur une clé absente du conteneur, le journal
    partait donc signé par la *chaîne du chemin* — un secret public, deviné par
    quiconque lit la ligne de commande, sur l'artefact dont le seul rôle est
    d'être opposable à un auditeur. Le repli reste offert au secret qu'on
    assume ; il est refusé à ce qui prétendait être une clé.
    """
    return raw.endswith(_KEY_SUFFIXES) or "/" in raw or "\\" in raw


def load_signing_key(spec: Optional[str] = None) -> Optional[SigningKey]:
    """Résout une clé de signature, ou `None` si aucune n'est demandée.

    `spec` accepte trois formes, dans cet ordre de tentative :

        chemin vers un PEM Ed25519 privé   → signature asymétrique
        chemin vers un fichier quelconque  → HMAC sur son contenu
        secret littéral                    → HMAC sur ces octets

    Un `spec` qui *ressemble* à un chemin et ne désigne aucun fichier lève :
    c'est une clé qu'on croyait fournir, pas un secret qu'on a voulu taper.

    À défaut de `spec`, la variable `AGENTL_JOURNAL_KEY` est consultée. Passer
    un secret en clair sur la ligne de commande le rend visible dans la table
    des processus : l'environnement ou un fichier valent mieux, et c'est ce que
    la documentation recommande.
    """
    raw = spec or os.environ.get(KEY_ENV)
    if not raw:
        return None

    path = Path(raw)
    if path.is_file():
        data = path.read_bytes()
        if b"PRIVATE KEY" in data:
            return _load_ed25519_private(data)
        return SigningKey(ALG_HMAC, data, _key_id(data))
    if _looks_like_a_path(raw):
        raise SealError(
            f"clé introuvable : {raw}\n"
            "  Ce nom désigne un fichier, qui n'existe pas. AGENT-L ne se "
            "rabattra pas sur « secret littéral » ici : le journal serait "
            "scellé par la chaîne du chemin, que tout le monde peut lire.\n"
            "  Produire une paire : `agentl seal --keygen <répertoire>`.")
    return SigningKey(ALG_HMAC, raw.encode("utf-8"), _key_id(raw.encode("utf-8")))


def load_verify_key(spec: Optional[str] = None) -> Optional[SigningKey]:
    """Comme `load_signing_key`, mais accepte aussi une clé *publique* Ed25519.

    C'est la dissymétrie qui fait tout l'intérêt : l'auditeur vérifie avec un
    fichier qui ne lui permet pas de signer.
    """
    raw = spec or os.environ.get(KEY_ENV)
    if not raw:
        return None
    path = Path(raw)
    if path.is_file():
        data = path.read_bytes()
        if b"PUBLIC KEY" in data:
            return _load_ed25519_public(data)
    return load_signing_key(spec)


def _cryptography():
    try:
        from cryptography.hazmat.primitives import serialization           # noqa: F401
        from cryptography.hazmat.primitives.asymmetric import ed25519      # noqa: F401
    except ImportError as exc:                                            # pragma: no cover
        raise SealError(
            "signature Ed25519 indisponible : installer l'extra "
            "`pip install agentl[sign]` (paquet `cryptography`). "
            "HMAC-SHA256 reste disponible sans dépendance.") from exc
    return serialization, ed25519


def _load_ed25519_private(pem: bytes) -> SigningKey:
    serialization, ed25519 = _cryptography()
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, ed25519.Ed25519PrivateKey):
        raise SealError("clé privée PEM fournie mais non Ed25519")
    public = key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw)
    private = key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption())
    return SigningKey(ALG_ED25519, private, _key_id(public))


def _load_ed25519_public(pem: bytes) -> SigningKey:
    serialization, ed25519 = _cryptography()
    key = serialization.load_pem_public_key(pem)
    if not isinstance(key, ed25519.Ed25519PublicKey):
        raise SealError("clé publique PEM fournie mais non Ed25519")
    raw = key.public_bytes(encoding=serialization.Encoding.Raw,
                           format=serialization.PublicFormat.Raw)
    # L'algorithme est marqué `.public` : `sign()` le refusera explicitement,
    # seul `verify_signature()` l'accepte. Une clé publique qui se laisserait
    # passer pour une clé de signature produirait un sceau vide sans le dire.
    return SigningKey(ALG_ED25519 + ".public", raw, _key_id(raw))


def generate_ed25519(directory: str | Path, name: str = "journal") -> Tuple[Path, Path]:
    """Écrit une paire Ed25519 : `<name>.key` (privée) et `<name>.pub`.

    La clé privée est écrite en 0600. Une clé de scellement lisible par tous
    ne scelle rien.
    """
    serialization, ed25519 = _cryptography()
    key = ed25519.Ed25519PrivateKey.generate()
    directory = Path(directory)
    priv = directory / f"{name}.key"
    pub = directory / f"{name}.pub"
    priv.write_bytes(key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption()))
    os.chmod(priv, 0o600)
    pub.write_bytes(key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo))
    return priv, pub


# --------------------------------------------------------------------------
# Signature
# --------------------------------------------------------------------------
def signing_payload(meta: Dict[str, Any]) -> bytes:
    """Octets effectivement signés.

    On signe la **méta scellée** — tête de chaîne, empreinte de trace,
    empreinte du programme source — et non les entrées elles-mêmes : la tête de
    chaîne les résume déjà. Le champ `signature` est exclu, sans quoi on
    signerait sa propre signature.

    Signer la seule tête de chaîne ne suffirait pas : un journal authentique
    pourrait alors être re-présenté comme provenant d'un autre programme, en
    changeant `source_sha256`. Tout ce qui qualifie l'exécution entre donc dans
    la signature.
    """
    body = {k: v for k, v in meta.items() if k != "signature"}
    return SIGN_DOMAIN + canonical(body)


def sign(meta: Dict[str, Any], key: SigningKey) -> Dict[str, Any]:
    """Produit le bloc `signature` à déposer dans la méta."""
    payload = signing_payload(meta)
    if key.alg == ALG_HMAC:
        value = hmac.new(key.material, payload, hashlib.sha256).hexdigest()
    elif key.alg == ALG_ED25519:
        _, ed25519 = _cryptography()
        private = ed25519.Ed25519PrivateKey.from_private_bytes(key.material)
        value = private.sign(payload).hex()
    else:
        raise SealError(f"clé publique fournie pour signer ({key.alg}) : "
                        f"une clé publique vérifie, elle ne signe pas")
    return {"alg": key.alg, "key_id": key.key_id, "value": value}


@dataclass
class SignatureReport:
    ok: bool
    reason: str = ""
    alg: str = ""
    key_id: str = ""

    def render(self) -> str:
        if self.ok:
            return f"signature {self.alg} valide (clé {self.key_id})"
        return self.reason


def verify_signature(meta: Dict[str, Any], key: Optional[SigningKey]) -> SignatureReport:
    """Le sceau colle-t-il à la méta et à la clé fournie ?

    Absence de signature et signature fausse sont deux verdicts distincts : le
    premier dit « rien n'a été promis », le second « la promesse est rompue ».
    Les confondre reviendrait à traiter un journal non signé comme un faux, ou
    pire, l'inverse.
    """
    block = meta.get("signature")
    if not block:
        return SignatureReport(False, "journal non signé")
    alg, key_id = block.get("alg", "?"), block.get("key_id", "?")
    if key is None:
        return SignatureReport(False,
                               f"journal signé ({alg}, clé {key_id}) mais aucune "
                               f"clé de vérification fournie — passer --key ou "
                               f"{KEY_ENV}", alg=alg, key_id=key_id)
    if key.key_id != key_id:
        return SignatureReport(False,
                               f"clé fournie ({key.key_id}) différente de la clé "
                               f"de signature ({key_id})", alg=alg, key_id=key_id)

    payload = signing_payload(meta)
    try:
        value = bytes.fromhex(block.get("value", ""))
    except ValueError:
        return SignatureReport(False, "signature illisible", alg=alg, key_id=key_id)

    if alg == ALG_HMAC:
        if key.alg != ALG_HMAC:
            return SignatureReport(False, "clé Ed25519 fournie pour une signature "
                                          "HMAC", alg=alg, key_id=key_id)
        expected = hmac.new(key.material, payload, hashlib.sha256).digest()
        ok = hmac.compare_digest(expected, value)
    elif alg == ALG_ED25519:
        _, ed25519 = _cryptography()
        if key.alg == ALG_ED25519 + ".public":
            public = ed25519.Ed25519PublicKey.from_public_bytes(key.material)
        elif key.alg == ALG_ED25519:
            public = ed25519.Ed25519PrivateKey.from_private_bytes(
                key.material).public_key()
        else:
            return SignatureReport(False, "clé HMAC fournie pour une signature "
                                          "Ed25519", alg=alg, key_id=key_id)
        try:
            public.verify(value, payload)
            ok = True
        except Exception:                                  # noqa: BLE001
            ok = False
    else:
        return SignatureReport(False, f"algorithme de signature inconnu : {alg}",
                               alg=alg, key_id=key_id)

    if not ok:
        return SignatureReport(False, "signature invalide — la méta scellée a "
                                      "changé depuis la signature",
                               alg=alg, key_id=key_id)
    return SignatureReport(True, alg=alg, key_id=key_id)

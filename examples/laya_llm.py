"""Laya (Convai Innovations, Apache 2.0), servi en local, en frontal de Jev.

Laya est un oracle System One comme Jev : une passe avant, des questions
fermées (`noul`, `choice`, `score`), des probabilités — pas de texte. Il tourne
sur la machine (`~/LAYA/serve_cpu.py`, 127.0.0.1:8099) : rien ne sort du
serveur, rien n'est facturé, rien ne dépend d'un service distant. C'est ce
qu'il apporte. Ce qu'il n'apporte pas, sur ce serveur, c'est la vitesse (CPU :
1 à 2,5 s par question contre 0,6 s pour Jev) ni la justesse hors de son
terrain.

Mesures du 2026-09-21 (scripts dans le scratchpad de la session, résumés dans
CHANGELOG 1.10.0) :

  * tri de messages courts, 12 EN + 12 FR appariés, 3 questions directes —
    EN : english 0,92, typed-decisions 0,89, multilingual 0,83, Jev 1,00 ;
    FR : multilingual 0,89, english 0,72, typed-decisions 0,72, Jev 1,00 ;
  * champs fermés de `REASON` réels (AutomationBench, 140 questions) —
    typed-decisions 0,49, english 0,41 ; Jev ≈ 0,97 sur les mêmes ;
  * les 13 `JUDGE` difficiles de `bench/jev_failures.md` — Laya 4 à 5/13,
    Jev 11/13, et multilingual s'y trompe à p ≥ 0,9.

D'où la politique, question par question — tout ce qui n'y entre pas part à
Jev, sans appel local :

  1. **forme** : une question fermée d'un `JUDGE` (question écrite, réponses
     décrites). Un champ de `REASON` n'y entre que sur demande
     (``reason_fields``) : la question y est reconstruite depuis le nom du
     champ, et Laya y est au niveau du hasard, probabilité comprise ;
  2. **options** : au plus ``max_options``, et consigne + options dans le
     budget de tête du checkpoint (192 ou 256 jetons). Au-delà, Laya tronque
     chaque option et réduit la consigne à 8 jetons — la cause du « niveau
     hasard » d'AGENTS_CONSTRUCT ;
  3. **taille** : une question courte (``max_question_tokens``) sur un état
     court (``max_state_tokens``). C'est le critère qui sépare les deux bancs,
     bien avant la fenêtre technique (512/1024) : Laya lit un message, pas un
     dossier ;
  4. **langue** : texte anglais → ``typed-decisions`` ; toute autre langue,
     dans la question ou dans l'état → ``multilingual``. Le checkpoint
     ``english`` ne lit que 512 jetons et ne fait pas mieux ;
  5. **cascade** : une réponse locale sous ``keep_above[checkpoint]`` est
     redemandée à Jev. Sur le tri, typed-decisions ≥ 0,65 gardait ~70 % des
     questions en local sans erreur, multilingual ≥ 0,90 ~67 % sans erreur ;
     multilingual se trompe au-dessus de 0,9 sur de l'anglais, d'où le seuil
     haut et le choix de typed-decisions pour l'anglais.

Les seuils sont calibrés sur ces petits bancs : ils se règlent par variables
d'environnement (``AGENTL_LAYA_*``) et se remesurent sur vos données.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentl.llm import retry_after  # noqa: E402

DEFAULT_URL = "http://127.0.0.1:8099"

#: (jetons au total, budget de tête = consigne + toutes les options), lus dans
#: le `rl_agent_config.json` de chaque checkpoint.
WINDOWS = {"english": (512, 192), "typed-decisions": (1024, 256),
           "multilingual": (1024, 256)}
#: Caractères par jeton, borne **basse** mesurée avec les tokenizers réels sur
#: des états AGENT-L sérialisés (EN : 2,67 ; mmBERT : 1,65 sur de la prose
#: anglaise). Diviser par la borne basse surestime les jetons : l'erreur ne
#: peut qu'envoyer à Jev une question qui aurait tenu, jamais tronquer.
CHARS_PER_TOKEN = {"english": 2.6, "typed-decisions": 2.6, "multilingual": 2.0}
#: Laya coupe chaque option à 48 jetons, marqueur non compris.
OPTION_TOKENS = 48
PROVIDER = "laya-local"


def laya_url() -> str:
    return os.environ.get("AGENTL_LAYA_URL", DEFAULT_URL).rstrip("/")


# ----------------------------------------------------------------- langue
#: Mots-outils, repris de `laya/lang.py` : les langues latines se recouvrent
#: (de, la, que…), d'où la marge exigée avant de déclarer « pas anglais ».
_STOP = {
    "en": {"the", "and", "is", "are", "was", "were", "to", "of", "in", "for", "with",
           "that", "this", "it", "you", "have", "has", "not", "but", "on", "at", "be",
           "as", "from", "will", "can", "would", "there", "their", "what", "which",
           "please", "we", "i", "does", "do", "how", "should"},
    "fr": {"le", "la", "les", "des", "un", "une", "est", "pour", "dans", "que", "qui",
           "avec", "sur", "pas", "plus", "nous", "vous", "être", "cette", "mais", "sont",
           "ont", "aux", "ce", "il", "elle", "quel", "quelle", "doit", "du", "au", "je",
           "et", "à", "ne", "mon", "ma", "mes", "votre", "notre", "chaque", "depuis",
           # élisions : « l'application » donne le mot « l », qui n'existe
           # pas seul en anglais
           "l", "qu", "j", "n", "c"},
    "de": {"der", "die", "das", "und", "ist", "ein", "eine", "den", "dem", "nicht", "mit",
           "für", "auf", "von", "zu", "sich", "auch", "werden", "wurde", "haben", "sind"},
    "es": {"el", "los", "las", "que", "por", "con", "para", "una", "es", "se", "del",
           "como", "pero", "son", "está", "este", "esta", "todo", "más", "muy", "hay"},
    "pt": {"os", "as", "que", "em", "um", "uma", "para", "com", "não", "é", "se", "do",
           "da", "dos", "das", "mas", "são", "está", "este", "esta", "muito", "pelo"},
    "it": {"il", "lo", "gli", "che", "di", "per", "con", "non", "è", "si", "del",
           "della", "sono", "questo", "questa", "anche", "come", "più", "nella"},
    "nl": {"het", "een", "van", "is", "op", "te", "dat", "niet", "met", "voor", "zijn",
           "aan", "door", "maar", "ook", "worden", "deze", "naar", "wordt"},
}
_DIACRITICS = set("àâäãáåçéèêëíìîïñóòôöõøúùûüýÿßæœ")
_WORD = re.compile(r"[^\W\d_]+", re.UNICODE)


def language(text: str) -> str:
    """``"en"``, ``"other"`` (autre langue ou écriture non latine) ou
    ``"unknown"`` (pas de lettres, ou trop peu de mots pour trancher)."""
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return "unknown"
    latin = sum(1 for ch in letters
                if ord(ch) < 0x0250 or 0x1E00 <= ord(ch) <= 0x1EFF)
    if latin < 0.8 * len(letters):
        return "other"                    # le tokenizer anglais ne la lit pas
    words = [w.lower() for w in _WORD.findall(text)]
    if len(words) < 4:
        return "unknown"
    scores = {lg: sum(1 for w in words if w in sw) for lg, sw in _STOP.items()}
    en = scores.pop("en")
    best = max(scores.values(), default=0)
    diacritics = sum(1 for ch in text.lower() if ch in _DIACRITICS) / len(text)
    # Trois façons d'être « pas anglais » : une marge nette de mots-outils ;
    # des mots-outils étrangers et aucun anglais ; des accents et au moins
    # autant de mots-outils étrangers qu'anglais. Le doute (« unknown ») est
    # lu comme de l'anglais par l'appelant — d'où ces règles, pour qu'un
    # message français court ne tombe pas dans le doute.
    if (best >= max(2, en + 2) or (best > 0 and en == 0)
            or (diacritics >= 0.01 and best >= en)):
        return "other"
    return "en" if en else "unknown"


def _strings(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in _strings(v)]
    return []


def estimate_tokens(text: str, checkpoint: str) -> int:
    return math.ceil(len(text) / CHARS_PER_TOKEN[checkpoint])


# ---------------------------------------------------------------- questions
def _options(question: Dict[str, Any]) -> List[str]:
    """Les options telles que Laya les rend dans sa séquence."""
    kind, crit = question.get("type"), question.get("criteria")
    if kind == "noul":
        return ["false: no, the statement does not hold",
                "true: yes, the statement holds"]
    if kind == "score":
        return [f"level {i}: {c}" for i, c in enumerate(crit or [])]
    if isinstance(crit, dict):
        return [k if v in (None, "") else f"{k}: {v}" for k, v in crit.items()]
    return [str(c) for c in crit or []]


def to_laya(question: Dict[str, Any]) -> Tuple[Dict[str, Any], Optional[str], bool]:
    """Une question au format Jev → (question Laya, consigne à porter dans
    l'état, champ de `REASON` ?).

    Jev accepte des consignes structurées ; Laya les sérialise en JSON **dans
    sa tête de 192–256 jetons**, où une consigne de `REASON` débordait le
    budget sur 99 questions sur 140 — tronquant la question elle-même. La
    question courte reste en tête ; la consigne passe dans l'état, qui a la
    place.
    """
    ins = question.get("instructions")
    if not isinstance(ins, dict):
        return dict(question), None, False
    if "field" in ins:                                    # champ de REASON
        target = ins["field"]
        head = f"Value of `{target}`"
        if ins.get("field_definition"):
            head += f" ({ins['field_definition']})"
        ask = ("is it true?" if question.get("type") == "noul"
               else "which option is correct?")
        text, task = f"{head}: {ask}", ins.get("task")
        return {**question, "instructions": text}, task, True
    # JUDGE : la question écrite par l'auteur, telle quelle. Le titre du
    # `JUDGE` (`context`) n'apprend rien que la question ne dise déjà.
    return {**question, "instructions": str(ins.get("question", ""))}, None, False


@dataclass
class Route:
    """Où part une question, et pourquoi — lisible dans les enregistrements."""
    checkpoint: Optional[str]
    why: str
    question: Dict[str, Any] = field(default_factory=dict)
    task: Optional[str] = None


def _env_thresholds(raw: str) -> Dict[str, float]:
    out: Dict[str, float] = {}
    for part in raw.split(","):
        if "=" in part:
            name, value = part.split("=", 1)
            out[name.strip()] = float(value)
    return out


class LayaRouter:  # pragma: no cover - `answer` nécessite le service
    """Politique de routage local/distant et transport vers le service Laya."""

    def __init__(self, url: Optional[str] = None, *,
                 max_state_tokens: Optional[int] = None,
                 max_question_tokens: Optional[int] = None,
                 max_options: Optional[int] = None,
                 keep_above: Optional[Dict[str, float]] = None,
                 english_model: Optional[str] = None,
                 other_model: Optional[str] = "multilingual",
                 reason_fields: Optional[bool] = None,
                 timeout: Optional[float] = None,
                 cooldown: Optional[float] = None):
        env = os.environ.get
        self.url = (url or laya_url()).rstrip("/")
        self.max_state_tokens = int(max_state_tokens if max_state_tokens is not None
                                    else env("AGENTL_LAYA_MAX_STATE", "128"))
        self.max_question_tokens = int(max_question_tokens if max_question_tokens is not None
                                       else env("AGENTL_LAYA_MAX_QUESTION", "40"))
        self.max_options = int(max_options if max_options is not None
                               else env("AGENTL_LAYA_MAX_OPTIONS", "6"))
        self.keep_above = {"typed-decisions": 0.65, "english": 0.75,
                           "multilingual": 0.90}
        self.keep_above.update(_env_thresholds(env("AGENTL_LAYA_KEEP", "")))
        self.keep_above.update(keep_above or {})
        self.english_model = english_model or env("AGENTL_LAYA_EN_MODEL",
                                                  "typed-decisions")
        self.other_model = other_model
        self.reason_fields = (env("AGENTL_LAYA_REASON", "") == "1"
                              if reason_fields is None else reason_fields)
        # Le service sérialise ses inférences (16 en file au plus, ~26 s
        # d'attente) : au-delà de quelques secondes, Jev aurait déjà répondu.
        self.timeout = float(timeout if timeout is not None
                             else env("AGENTL_LAYA_TIMEOUT", "20"))
        #: Service injoignable : on cesse de le consulter pendant `cooldown`
        #: secondes (un redémarrage de `laya.service` en dure ~105).
        self.cooldown = float(cooldown if cooldown is not None
                              else env("AGENTL_LAYA_COOLDOWN", "30"))
        self._down_until = 0.0
        self.responses: List[Dict[str, Any]] = []
        self.retries: List[Dict[str, Any]] = []

    # ------------------------------------------------------------ politique
    def route(self, state: Any, question: Dict[str, Any]) -> Route:
        """La décision pour une question — sans appel réseau."""
        wait = self._down_until - time.monotonic()
        if wait > 0:
            return Route(None, f"service local indisponible (réessai dans {wait:.0f} s)")
        if question.get("type") not in ("noul", "choice", "score"):
            return Route(None, "type de question non pris en charge")
        laya_q, task, from_reason = to_laya(question)
        if from_reason and not self.reason_fields:
            return Route(None, "champ de REASON (Laya ≈ hasard, voir la doc)")
        options = _options(laya_q)
        if len(options) > self.max_options:
            return Route(None, f"{len(options)} options > {self.max_options}")
        text = str(laya_q.get("instructions", ""))
        laya_state = {"task": task, "state": state} if task else state
        state_text = " ".join(_strings(state))
        lang_q = language(text + " " + " ".join(options))
        lang_s = language(state_text)
        if "other" in (lang_q, lang_s):
            checkpoint = self.other_model
            if checkpoint is None:
                return Route(None, "langue autre que l'anglais")
        else:
            checkpoint = self.english_model
        max_len, head_max = WINDOWS[checkpoint]
        q_tokens = estimate_tokens(f"{laya_q['type']} question: {text}", checkpoint)
        if q_tokens > self.max_question_tokens:
            return Route(None, f"question longue (~{q_tokens} jetons)")
        head = q_tokens + sum(
            1 + min(OPTION_TOKENS, estimate_tokens(" " + o, checkpoint))
            for o in options)
        if head > head_max:
            return Route(None, f"options hors budget (~{head} > {head_max} jetons)")
        s_tokens = estimate_tokens(json.dumps(laya_state, ensure_ascii=False,
                                              default=str), checkpoint)
        if s_tokens > self.max_state_tokens or 3 + head + s_tokens > max_len:
            return Route(None, f"état long (~{s_tokens} jetons)")
        return Route(checkpoint, f"{checkpoint} (langue {lang_q}/{lang_s})",
                     laya_q, task)

    def keeps(self, checkpoint: str, p: float) -> bool:
        return p >= self.keep_above.get(checkpoint, 1.01)

    # ------------------------------------------------------------ transport
    def _call(self, state: Any, questions: Dict[str, Any],
              checkpoint: str, attempts: int = 3) -> Dict[str, Any]:
        """Un appel, rejoué **seulement** sur 503 (file pleine, `Retry-After`).

        Pas la reprise commune de `call_with_retry` : elle rejoue aussi une
        connexion refusée, soit ~3 s perdues par question quand le service
        est arrêté — alors que Jev attend à côté. Une connexion refusée ou un
        délai dépassé ouvre le disjoncteur à la place.
        """
        for attempt in range(1, attempts + 1):
            try:
                return self._post(state, questions, checkpoint)
            except urllib.error.HTTPError as exc:
                if exc.code != 503 or attempt >= attempts:
                    raise
                delay = min(5.0, retry_after(exc) or 0.5 * 2 ** (attempt - 1))
                self.retries.append({"attempt": attempt, "delaySeconds": delay,
                                     "error": f"HTTP 503: {str(exc)[:100]}"})
                time.sleep(delay)
            except (urllib.error.URLError, ConnectionError, TimeoutError):
                self._down_until = time.monotonic() + self.cooldown
                raise
        raise RuntimeError("inaccessible")          # pragma: no cover

    def _post(self, state: Any, questions: Dict[str, Any],
              checkpoint: str) -> Dict[str, Any]:
        body = json.dumps({"state": state, "questions": questions,
                           "model": checkpoint}, default=str).encode()
        req = urllib.request.Request(f"{self.url}/predict", data=body,
                                     headers={"Content-Type": "application/json"})
        started = time.monotonic()
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode())
        routing = data.get("routing") or {}
        if routing.get("not_loaded"):
            # Le service a servi un autre checkpoint que celui demandé : la
            # réponse ne vaut pas ce que la politique croyait acheter.
            raise RuntimeError(f"checkpoint {checkpoint} non chargé par le service "
                               f"(servi : {routing.get('model')})")
        usage = data.get("usage") or {}
        self.responses.append({
            "provider": PROVIDER, "model": routing.get("model", checkpoint),
            "questions": len(questions),
            "promptTokens": usage.get("input_tokens"),
            "completionTokens": 0, "costUsd": 0.0,
            "latencySeconds": round(time.monotonic() - started, 3),
            "queueSeconds": round((data.get("wait_ms") or 0) / 1000, 3),
            "status": "completed",
        })
        return data.get("answers") or {}

    def answer(self, state: Any, routes: Dict[str, Route]
               ) -> Tuple[Dict[str, Any], Dict[str, str]]:
        """Pose les questions routées ici : (réponses, erreurs par question).

        Un appel par (checkpoint, consigne) : les questions d'un même appel
        partagent l'état. Ne lève jamais — une question sans réponse revient
        dans les erreurs, et l'appelant la confie à Jev.
        """
        groups: Dict[Tuple[str, Optional[str]], Dict[str, Route]] = {}
        for qid, route in routes.items():
            groups.setdefault((route.checkpoint, route.task), {})[qid] = route
        answers: Dict[str, Any] = {}
        errors: Dict[str, str] = {}
        for (checkpoint, task), group in groups.items():
            laya_state = {"task": task, "state": state} if task else state
            questions = {qid: r.question for qid, r in group.items()}
            try:
                got = self._call(laya_state, questions, checkpoint)
            except Exception as exc:                  # noqa: BLE001
                for qid in group:
                    errors[qid] = f"{type(exc).__name__}: {str(exc)[:160]}"
                continue
            for qid in group:
                if qid in got:
                    answers[qid] = got[qid]
                else:
                    errors[qid] = "réponse absente"
        return answers, errors


def probability(answer: Dict[str, Any]) -> float:
    """P de la réponse rendue, quel que soit le type — ce que compare la
    cascade (et que lirait `judge.<champ>.p`)."""
    if "noul" in answer:
        p_yes = float(answer.get("noul", 0.5))
        return max(p_yes, 1.0 - p_yes)
    probs = answer.get("probabilities") or {}
    if "choice" in answer:
        return float(probs.get(answer.get("choice"), 0.0))
    if "score" in answer:
        nearest = str(int(round(float(answer.get("score", 0.0)))))
        return float(probs.get(nearest, 0.0))
    return 0.0

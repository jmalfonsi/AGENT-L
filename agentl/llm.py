"""Adaptateur LLM.

Le LLM est un **oracle**, pas un pilote : il ne reçoit jamais la main sur le
runtime. Il ne peut que produire :

  * un dictionnaire de champs typés (`REASON ... PRODUCE {...}`)
  * un nom de plan à sélectionner (validé ensuite contre les plans déclarés)
  * une proposition d'action (soumise ensuite au moteur de politiques)

Toute sortie hors de ces formes est rejetée par le runtime.
"""
from __future__ import annotations

import json
import math
import os
import random
import re
import time
import urllib.error
from typing import Any, Callable, Dict, List, Optional

from .core import UNDEFINED, Symbol

# ------------------------------------------------------- reprise sur panne
#: Codes HTTP qu'il vaut la peine de rejouer : 408 (délai dépassé), 429
#: (quota), 5xx (panne serveur). Tout le reste — 400, 401, 403, 404 — vient
#: de notre côté : rejouer ne fait que retarder l'échec en brûlant du quota.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504, 529})

#: Les fournisseurs indiquent souvent le délai dans le corps plutôt que dans
#: un en-tête : « retryDelay: 12s », « please retry in 3.4s ».
_RETRY_HINT = re.compile(r"retry[_\s-]*(?:delay|after|in)\D{0,4}([0-9.]+)\s*s",
                         re.IGNORECASE)


def is_transient(exc: BaseException) -> bool:
    """Une panne qu'un second essai peut résoudre — pas une erreur de contrat.

    La distinction est la seule chose qui compte ici : rejouer un 401 ne le
    transformera jamais en 200, et rejouer un 400 non plus. Les confondre
    donne un agent qui met trois fois plus de temps à échouer.
    """
    if isinstance(exc, urllib.error.HTTPError):     # avant URLError : sa fille
        return exc.code in RETRYABLE_STATUS
    if isinstance(exc, urllib.error.URLError):      # DNS, connexion refusée
        return True
    return isinstance(exc, (TimeoutError, ConnectionError))


def retry_after(exc: BaseException) -> Optional[float]:
    """Délai demandé par le fournisseur, en-tête d'abord puis corps."""
    headers = getattr(exc, "headers", None)
    raw = headers.get("Retry-After") if headers is not None else None
    if raw:
        try:
            return max(0.0, float(str(raw).strip()))
        except ValueError:
            pass                                    # forme date HTTP : ignorée
    match = _RETRY_HINT.search(str(exc))
    return float(match.group(1)) if match else None


def call_with_retry(fn: Callable[[], Any], *, attempts: Optional[int] = None,
                    base_delay: float = 1.0, max_delay: float = 30.0,
                    budget: Optional[float] = None,
                    sleep: Callable[[float], None] = time.sleep,
                    on_retry: Optional[Callable[[int, float, BaseException], None]] = None) -> Any:
    """Rejoue `fn` sur les pannes transitoires, jamais sur une erreur de contrat.

    Pourquoi le rejeu vit ici et pas dans le programme : un hoquet réseau
    n'est pas une décision. Le `.agent` doit voir « l'oracle a répondu » ou
    « l'oracle est tombé », pas les trois essais TCP entre les deux. Sans
    cela, un 429 passager dégradait immédiatement le `REASON` vers ses
    valeurs par défaut — sûr, mais on perdait le raisonnement pour rien.

    Deux bornes, parce qu'un agent régulé ne peut pas attendre indéfiniment :
    un nombre d'essais **et** un budget de temps total. Le budget est vérifié
    *avant* de dormir, donc la fonction ne dépasse jamais son enveloppe.
    """
    if attempts is None:
        attempts = int(os.environ.get("AGENTL_LLM_RETRIES", "3"))
    if budget is None:
        budget = float(os.environ.get("AGENTL_LLM_RETRY_BUDGET", "90"))
    attempts = max(1, attempts)
    started = time.monotonic()
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except Exception as exc:                    # noqa: BLE001
            if attempt >= attempts or not is_transient(exc):
                raise
            hinted = retry_after(exc)
            delay = (hinted if hinted is not None
                     else min(max_delay, base_delay * (2 ** (attempt - 1))))
            # Bruit décorrélé : deux agents repartis à la même seconde
            # reconstituent exactement la rafale qui a produit le 429.
            delay = min(max_delay, delay + random.uniform(0.0, min(1.0, delay * 0.25)))
            if time.monotonic() - started + delay > budget:
                raise
            if on_retry is not None:
                on_retry(attempt, delay, exc)
            sleep(delay)


def missing_from(payload: Any, produce: Dict[str, str]) -> List[str]:
    """Champs du schéma que le modèle n'a pas rendus.

    `_coerce` comble les absents par leur défaut, ce qui est le bon
    comportement mais efface la trace de l'absence : une réponse tronquée
    devenait indiscernable d'une réponse complète et neutre. Cette liste est
    ce qui permet au runtime de faire la différence.
    """
    if not isinstance(payload, dict):
        return list(produce)
    return [key for key in produce if key not in payload]


def render_question(name: str, question: Dict[str, Any]) -> str:
    """La question d'un `JUDGE`, écrite pour un oracle qui ne fait que du texte.

    Un oracle System One reçoit la question telle quelle ; un modèle
    génératif, lui, n'a qu'un prompt. Les deux doivent poser **la même**
    question, sinon comparer leurs réponses ne veut rien dire.
    """
    kind = str(question.get("kind", "")).upper()
    lines = [f"- {name} : {question.get('instructions', '')}"]
    if kind == "NOUL":
        lines.append("    réponse : true si la condition tient, false sinon")
    elif kind == "CHOICE":
        lines.append("    réponse : exactement une de ces valeurs")
        for option, description in (question.get("criteria") or {}).items():
            lines.append(f"      * {option} — {description}")
    elif kind == "SCORE":
        levels = question.get("levels") or []
        lines.append(f"    réponse : un nombre entre 0 et {max(len(levels) - 1, 0)}, "
                     "position sur ces niveaux ordonnés")
        for index, level in enumerate(levels):
            lines.append(f"      * {index} — {level}")
    return "\n".join(lines)


def judge_via_reason(llm: Any, task: str, context: Dict[str, Any],
                     questions: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Répond à un `JUDGE` avec un oracle qui ne sait que raisonner en texte.

    `JUDGE` est une primitive du **langage**, pas de TypeSafe : un programme
    qui l'utilise doit tourner avec n'importe quel oracle. La traduction est
    fidèle sur les valeurs et honnête sur ce qu'elle ne peut pas rendre : un
    modèle génératif n'a pas de probabilité calibrée à offrir, donc `p` et
    `confidence` restent **indéterminés** plutôt qu'inventés. Une politique
    qui lit `judge.x.p >= 0.9` se ferme alors, ce qui est le bon défaut : on
    ne franchit pas un seuil de calibration avec un nombre écrit à la main.
    """
    prompt = (f"{task}\n\n" if task else "") + (
        "Réponds à chaque question ci-dessous, une valeur par champ :\n\n"
        + "\n".join(render_question(name, question)
                    for name, question in questions.items()))
    produce = {name: question.get("schema", "Any")
               for name, question in questions.items()}
    try:
        llm.last_reason_missing = None
    except Exception:                               # noqa: BLE001
        pass                                        # adaptateur tiers
    produced = llm.reason(prompt, context, produce)
    if not isinstance(produced, dict):
        produced = {}
    missing = set(getattr(llm, "last_reason_missing", None)
                  or missing_from(produced, produce))
    return {name: {"value": produced[name]}
            for name in questions
            if name in produced and name not in missing}


def ask_judge(llm: Any, task: str, context: Dict[str, Any],
              questions: Dict[str, Dict[str, Any]]) -> Any:
    """Un `JUDGE` posé à `llm` : sa méthode `judge()` s'il en a une, sinon la
    traduction en `reason()`.

    Le runtime et chaque enveloppe d'oracle (enregistrement, reprise durable,
    pont asynchrone) passent par ici. Une enveloppe qui ne définissait pas
    `judge` le laissait filer par `__getattr__` jusqu'à l'oracle réel : ni
    journalisé, ni rejouable, ni borné par le délai du pont.
    """
    ask = getattr(llm, "judge", None)
    if callable(ask):
        return ask(task, context, questions)
    return judge_via_reason(llm, task, context, questions)


class LLM:
    """Interface minimale attendue par le runtime."""

    def reason(self, task: str, context: Dict[str, Any],
               produce: Dict[str, str]) -> Dict[str, Any]:
        raise NotImplementedError

    def judge(self, task: str, context: Dict[str, Any],
              questions: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """Répond à des questions fermées : `{champ: {value, p, confidence}}`.

        Le défaut traduit en `reason()` : un adaptateur n'a rien à écrire
        pour qu'un `JUDGE` tourne. Un adaptateur qui sait rendre des
        probabilités calibrées (System One) redéfinit cette méthode — c'est
        la seule façon pour un programme d'obtenir `judge.<champ>.p`.
        """
        return judge_via_reason(self, task, context, questions)

    def select_plan(self, context: Dict[str, Any],
                    candidates: List[str]) -> Optional[str]:
        raise NotImplementedError


class MockLLM(LLM):
    """Oracle déterministe : rend les exécutions reproductibles et testables.

    On enregistre des réponses par sous-chaîne de tâche :

        llm = MockLLM({"root cause": {"root_cause": "disk_full",
                                      "confidence": 0.91}})
    """

    def __init__(self, scripted: Optional[Dict[str, Dict[str, Any]]] = None,
                 plan_choice: Optional[Callable[[Dict[str, Any], List[str]],
                                                Optional[str]]] = None):
        self.scripted = scripted or {}
        self.plan_choice = plan_choice
        self.calls: List[Dict[str, Any]] = []
        #: Champs absents de la dernière réponse — lu par le runtime pour
        #: distinguer un oracle qui a répondu d'un oracle qui s'est tu.
        self.last_reason_missing: Optional[List[str]] = None

    def reason(self, task: str, context, produce):
        self.calls.append({"kind": "reason", "task": task})
        for key, payload in self.scripted.items():
            if key.lower() in task.lower():
                self.last_reason_missing = missing_from(payload, produce)
                return _coerce(payload, produce)
        # Aucun script ne correspond : le mock n'a rien à dire, ce qui est
        # exactement une réponse absente.
        self.last_reason_missing = list(produce)
        return _coerce(_defaults(produce), produce)

    def judge(self, task, context, questions):
        """Jugements scriptés. Une valeur nue vaut `{"value": …}` sans
        probabilité : pour tester un seuil d'abstention, écrire
        `{"kind": {"value": "billing", "p": 0.97}}`."""
        self.calls.append({"kind": "judge", "task": task,
                           "questions": list(questions)})
        for key, payload in self.scripted.items():
            if key.lower() not in task.lower():
                continue
            out: Dict[str, Any] = {}
            for name in questions:
                if name not in payload:
                    continue
                answer = payload[name]
                out[name] = (dict(answer) if isinstance(answer, dict)
                             and "value" in answer else {"value": answer})
            return out
        return {}

    def select_plan(self, context, candidates):
        self.calls.append({"kind": "select_plan", "candidates": list(candidates)})
        if self.plan_choice:
            return self.plan_choice(context, candidates)
        return candidates[0] if candidates else None


class AnthropicLLM(LLM):  # pragma: no cover - nécessite le réseau
    """Adaptateur Claude. Impose une réponse JSON stricte, puis la valide."""

    def __init__(self, model: str = "claude-sonnet-5",
                 api_key: Optional[str] = None,
                 max_tokens: Optional[int] = None):
        self.model = model
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        # 1024 était trop court dès qu'un `PRODUCE` dépassait quelques
        # champs : la réponse était tronquée, `_coerce` comblait tout par des
        # défauts, et l'agent raisonnait sur des zéros plausibles.
        self.max_tokens = int(max_tokens or
                              os.environ.get("AGENTL_MAX_OUTPUT_TOKENS", "4096"))
        self.last_reason_missing: Optional[List[str]] = None

    def _call(self, system: str, prompt: str) -> str:
        import urllib.request

        body = json.dumps({
            "model": self.model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            headers={
                "content-type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
        return "".join(b.get("text", "") for b in data.get("content", []))

    def reason(self, task, context, produce):
        system = (
            "Tu es le module de raisonnement d'un runtime agentique AGENT-L. "
            "Tu ne peux pas exécuter d'action. Réponds UNIQUEMENT par un objet "
            "JSON respectant exactement le schéma demandé, sans texte ni balises."
        )
        prompt = (
            f"Tâche : {task}\n\n"
            f"Schéma attendu : {json.dumps(produce)}\n\n"
            f"Contexte :\n{json.dumps(context, default=str, indent=2)}"
        )
        self.last_reason_missing = list(produce)
        raw = call_with_retry(lambda: self._call(system, prompt))
        parsed = _parse_json(raw)
        self.last_reason_missing = missing_from(parsed, produce)
        return _coerce(parsed, produce)

    def select_plan(self, context, candidates):
        if not candidates:
            return None
        system = (
            "Tu sélectionnes un plan pour un runtime AGENT-L. Réponds "
            'uniquement par {"plan": "<nom>"} en choisissant dans la liste.'
        )
        prompt = (f"Plans disponibles : {candidates}\n"
                  f"Contexte :\n{json.dumps(context, default=str, indent=2)}")
        raw = call_with_retry(lambda: self._call(system, prompt))
        choice = _parse_json(raw).get("plan")
        return choice if choice in candidates else None


# --------------------------------------------------------------------- utils
def _parse_json(text: str) -> Dict[str, Any]:
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.S)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                return {}
        return {}


def _base_type(typ: str) -> str:
    """Retire les contraintes ``IN`` et ``DEFAULT`` du type transmis."""
    return typ.split(" DEFAULT ", 1)[0].split(" IN ", 1)[0].strip().lower()


def _declared_default(typ: str) -> Any:
    if " DEFAULT " not in typ:
        return None
    raw = typ.rsplit(" DEFAULT ", 1)[1].strip()
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


def _defaults(produce: Dict[str, str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for key, typ in produce.items():
        low = _base_type(typ)
        declared = _declared_default(typ)
        out[key] = declared if declared is not None else (
            0.0 if low in ("number", "float", "int") else
            False if low in ("bool", "boolean") else "unknown"
        )
    return out


#: Ce qu'un modèle peut écrire pour un booléen, et rien d'autre. Le contrat
#: `PRODUCE { x: Bool }` porte sur une valeur de vérité, pas sur la vérité de
#: Python : `bool("false")` vaut `True`, comme `bool("no")` et `bool("off")`.
#: Un modèle qui répond « false » en toutes lettres — ce que font la plupart
#: quand la sortie n'est pas strictement contrainte — produisait donc
#: exactement l'inverse de sa réponse, en silence, et une garde `WHEN
#: injection_detected` se déclenchait sur une négation.
_TRUE = {"true", "yes", "on", "1", "oui", "vrai"}
_FALSE = {"false", "no", "off", "0", "non", "faux", "", "none", "null"}


def _as_bool(raw: Any, fallback: Any) -> Any:
    """Coercition booléenne stricte : hors vocabulaire, on rend le repli.

    Ni `bool(raw)` — qui rend vrai pour toute chaîne non vide — ni un simple
    « tout ce qui n'est pas vrai est faux » : une réponse illisible n'est pas
    une réponse négative, c'est une absence de réponse, et le repli déclaré
    est ce que le programme a prévu pour ce cas (`DEFAULT`).
    """
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, (int, float)):
        if isinstance(raw, float) and not math.isfinite(raw):
            return fallback
        return bool(raw)
    if isinstance(raw, str):
        low = raw.strip().lower()
        if low in _TRUE:
            return True
        if low in _FALSE:
            return False
    if raw is None:
        return fallback
    if isinstance(fallback, bool):
        return fallback
    return (_as_bool(fallback, UNDEFINED)
            if isinstance(fallback, str) else UNDEFINED)


def _coerce(payload: Dict[str, Any], produce: Dict[str, str]) -> Dict[str, Any]:
    """Un LLM ne dicte pas le schéma : le runtime le lui impose."""
    if not produce:
        return dict(payload)
    out: Dict[str, Any] = {}
    for key, typ in produce.items():
        raw = payload.get(key, _defaults({key: typ})[key])
        low = _base_type(typ)
        # Un flottant non fini n'est une valeur valide pour aucun type du
        # langage. Sans ce contrôle avant le dispatch, NaN devenait `True`
        # pour Bool et la chaîne/symbole `nan` pour String/Symbol.
        if ((isinstance(raw, float) and not math.isfinite(raw))
                or raw is None):
            raw = (_declared_default(typ)
                   if " DEFAULT " in typ else UNDEFINED)
        if raw is UNDEFINED:
            out[key] = UNDEFINED
            continue
        if low in ("number", "float", "int") and isinstance(raw, bool):
            # `float(True)` vaut 1.0 : un modèle qui répond `true` là où un
            # `Number` est attendu produisait donc une confiance de 1.0 —
            # la valeur maximale, obtenue par accident de typage Python. On
            # traite ce cas comme une réponse hors schéma : repli déclaré.
            raw = (_declared_default(typ)
                   if " DEFAULT " in typ else UNDEFINED)
            if raw is UNDEFINED:
                out[key] = UNDEFINED
                continue
        if low in ("number", "float"):
            try:
                number = float(raw)
                if not math.isfinite(number):
                    raise ValueError("non-finite number")
                out[key] = number
            except (TypeError, ValueError, OverflowError):
                fallback = (_declared_default(typ)
                            if " DEFAULT " in typ else UNDEFINED)
                try:
                    number = float(fallback)
                    if not math.isfinite(number):
                        raise ValueError("non-finite default")
                    out[key] = number
                except (TypeError, ValueError, OverflowError):
                    out[key] = UNDEFINED
        elif low == "int":
            try:
                if not math.isfinite(float(raw)):
                    raise ValueError("non-finite integer")
                out[key] = int(raw)
            except (TypeError, ValueError, OverflowError):
                fallback = (_declared_default(typ)
                            if " DEFAULT " in typ else UNDEFINED)
                try:
                    if not math.isfinite(float(fallback)):
                        raise ValueError("non-finite integer default")
                    out[key] = int(fallback)
                except (TypeError, ValueError, OverflowError):
                    out[key] = UNDEFINED
        elif low in ("bool", "boolean"):
            fallback = (_declared_default(typ)
                        if " DEFAULT " in typ else UNDEFINED)
            out[key] = _as_bool(raw, fallback)
        elif low == "string":
            out[key] = str(raw)
        elif low == "symbol":
            out[key] = Symbol(str(raw))
        else:
            out[key] = raw
    return out

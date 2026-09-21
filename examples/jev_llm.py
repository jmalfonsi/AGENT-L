"""Adaptateur System One : Jev (TypeSafe) comme oracle de jugement.

Même contrat que `GeminiLLM` — le modèle est un *oracle*, il ne fait que
remplir un `PRODUCE` ou choisir un plan dans une liste fermée — mais Jev ne
génère pas de texte : il rend, pour une question typée, une **distribution de
probabilité** sur des réponses que le programme a énumérées. C'est exactement
la forme d'un `PRODUCE` à domaine clos, que les modèles génératifs ne
respectent qu'à la coercition près.

Routage champ par champ :

  * ``Symbol IN [a, b, …]`` (ou ``Any IN``)  → Choice : la valeur est l'option
    la plus probable, jamais une chaîne hors domaine ;
  * ``Bool``                                 → Noul : probabilité du oui ;
  * ``Number``/``Int`` sans domaine          → sélection de valeur : les nombres
    présents dans le contexte sont trouvés par le code, Jev désigne le bon
    (ou ``none``), le code le recopie — Jev n'écrit jamais de chiffre ;
  * ``confidence`` et ``<champ>_confidence`` → **dérivés**, pas demandés : la
    probabilité, selon Jev, de la valeur rendue. C'est la seule différence de
    nature avec un oracle génératif, dont la « confiance » est un nombre
    qu'il écrit ;
  * tout le reste (``String``, ``Symbol`` ouvert, ``Number IN [lo, hi]``)
    → l'oracle génératif de repli, pour ces champs seulement.

Incertitude, deux politiques :

  * ``abstain_below`` — le champ est déclaré **absent** : le runtime applique
    son DEFAULT (``unknown``, qui ferme ce qu'il garde). C'est le réglage à
    préférer quand le contexte porte du texte non fiable : une injection fait
    baisser la probabilité de Jev, et le modèle génératif y cède plus souvent
    que lui (banc `bench/jev_adversarial.py`) — l'y renvoyer serait lui
    confier précisément les cas piégés ;
  * ``escalate_below`` — le champ est redemandé à l'oracle de repli (motif
    « SDE cascade » de TypeSafe), pour l'ambiguïté ordinaire.

Si Jev est injoignable, ses champs partent au repli. Sans repli, les champs
non couverts sont déclarés absents et le runtime applique ses DEFAULT.

Frontal local (``local=LayaRouter()``, `examples/laya_llm.py`) : une question
fermée qui entre dans l'enveloppe mesurée de Laya — `JUDGE` court, état court,
peu d'options, checkpoint choisi selon la langue — est posée d'abord au
service local ; sous son seuil de confiance, ou s'il ne répond pas, elle
revient à Jev. Le reste ne quitte jamais Jev.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentl.llm import LLM, _base_type, _coerce, call_with_retry  # noqa: E402

DEFAULT_API_BASE = "https://api.typesafe.ai"
#: Prix public de jev-1.13 : 0,042 $ par million de jetons d'entrée, sortie
#: gratuite (docs.typesafe.ai/models). Sert au compte-rendu, pas au contrôle.
USD_PER_INPUT_TOKEN = 0.042 / 1_000_000

#: Valeurs de repli courantes dans les domaines AGENT-L. Une option sans
#: description est lue au pied de la lettre par Jev ; « unknown » ne dit pas
#: à quelle condition il faut la préférer.
_SENTINELS = {
    "unknown": "The state does not contain enough information to decide.",
    "none": "None of the other options applies.",
    "other": "None of the other options applies.",
    "unclear": "The state is ambiguous between several options.",
}

#: Porte les appels génératifs lancés pendant que Jev répond.
_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="jev-fallback")

#: « 50k+ » est un nombre de cinquante mille, pas de cinquante : le suffixe
#: fait partie de la valeur, il reste dans la chaîne proposée à Jev.
_NUMBER = re.compile(r"(?<![\w.])[-+]?\$?\d[\d,]*(?:\.\d+)?(?:[kK](?![a-zA-Z]))?%?")
NONE = "none"


def api_base() -> str:
    return os.environ.get("TYPESAFE_API_BASE", DEFAULT_API_BASE).rstrip("/")


def _api_key() -> str:
    # Le dépôt range la clé sous TYPESAFE_AI_KEY ; le SDK officiel lit
    # TYPESAFE_API_KEY. Les deux sont acceptés.
    return (os.environ.get("TYPESAFE_API_KEY")
            or os.environ.get("TYPESAFE_AI_KEY", ""))


def _split_schema(typ: str) -> Tuple[str, Optional[List[str]]]:
    """``"Any IN [a, b] DEFAULT a"`` → (``"any"``, ``["a", "b"]``)."""
    base = _base_type(typ)
    head = typ.split(" DEFAULT ", 1)[0]
    if " IN " not in head:
        return base, None
    raw = head.split(" IN ", 1)[1].strip()
    if not (raw.startswith("[") and raw.endswith("]")):
        return base, None
    values = [v.strip().strip('"\'') for v in raw[1:-1].split(",")]
    return base, [v for v in values if v]


def _humanize(field: str) -> str:
    return field.replace("_", " ").replace(".", " ").strip()


def _strings(value: Any) -> List[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, (list, tuple)):
        return [s for v in value for s in _strings(v)]
    return []


def number_candidates(context: Dict[str, Any], limit: int = 60) -> Dict[str, str]:
    """Nombres écrits dans le contexte, avec l'extrait qui les entoure.

    La sélection ne peut rendre qu'un nombre effectivement présent : c'est
    ce qui interdit à l'oracle d'inventer un seuil. L'extrait sert de
    description d'option — « 30 » seul ne dit pas de quoi il est le nombre.
    """
    found: Dict[str, str] = {}
    for text in _strings(context):
        for match in _NUMBER.finditer(text):
            key = match.group(0).strip()
            if key in found or len(found) >= limit:
                continue
            start, end = max(0, match.start() - 60), min(len(text), match.end() + 60)
            found[key] = "…" + " ".join(text[start:end].split()) + "…"
    return found


def parse_number(literal: str) -> Optional[float]:
    raw = literal.replace("$", "").replace(",", "").replace("%", "").strip()
    scale = 1.0
    if raw[-1:] in ("k", "K"):
        raw, scale = raw[:-1], 1000.0
    try:
        return float(raw) * scale
    except ValueError:
        return None


def field_definition(task: str, field: str) -> Optional[str]:
    """La phrase de la consigne qui définit ce champ, si elle existe.

    Une consigne de REASON décrit souvent plusieurs champs d'un coup
    (« sentiment : le ton général. vip_domain : … »). Jev reçoit une
    question par champ et lit à la lettre : lui montrer la définition du
    sien retire un saut d'indirection. Seule la forme « champ : » est
    reconnue — « champ = valeur » est une consigne, pas une définition.
    """
    flat = " ".join(task.split())
    match = re.search(r"(?<![\w.])" + re.escape(field) + r"\s*:\s*([^.;]+)", flat)
    return match.group(1).strip() if match else None


class JevLLM(LLM):  # pragma: no cover - nécessite le réseau
    """Oracle System One, avec repli génératif pour ce qu'il ne sait pas faire."""

    def __init__(self, fallback: Optional[LLM] = None, model: str = "jev-latest",
                 api_key: Optional[str] = None,
                 escalate_below: Optional[float] = None,
                 abstain_below: Optional[float] = None,
                 select_numbers: bool = True,
                 local: Optional[Any] = None):
        self.model = model
        #: Frontal System One local (`LayaRouter`), facultatif.
        self.local = local
        self.api_key = api_key or _api_key()
        self.fallback = fallback
        self.escalate_below = (float(os.environ.get("AGENTL_JEV_ESCALATE", "0"))
                               if escalate_below is None else escalate_below)
        self.abstain_below = (float(os.environ.get("AGENTL_JEV_ABSTAIN", "0"))
                              if abstain_below is None else abstain_below)
        self.select_numbers = select_numbers
        self.calls: List[Dict[str, Any]] = []
        self.responses: List[Dict[str, Any]] = []
        self.retries: List[Dict[str, Any]] = []
        self.last_reason_missing: Optional[List[str]] = None

    # ------------------------------------------------------------ transport
    def _note_retry(self, attempt: int, delay: float, exc: BaseException) -> None:
        self.retries.append({"attempt": attempt, "delaySeconds": round(delay, 2),
                             "error": f"{type(exc).__name__}: {str(exc)[:120]}"})

    def _post(self, state: Any, questions: Dict[str, Any]) -> Dict[str, Any]:
        body = json.dumps({"state": state, "model": self.model,
                           "questions": questions}, default=str).encode()
        req = urllib.request.Request(
            f"{api_base()}/v1/systemone", data=body,
            headers={"content-type": "application/json",
                     "authorization": f"Bearer {self.api_key}"})
        started = time.monotonic()
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode())
        usage = data.get("usage") or {}
        self.responses.append({
            "provider": "typesafe-systemone",
            "model": data.get("model", self.model),
            "questions": len(questions),
            "promptTokens": usage.get("input_tokens"),
            "completionTokens": usage.get("output_tokens"),
            "costUsd": (usage.get("input_tokens") or 0) * USD_PER_INPUT_TOKEN,
            "latencySeconds": round(time.monotonic() - started, 3),
            "status": "completed",
        })
        return data.get("answers") or {}

    def ask(self, state: Any, questions: Dict[str, Any]) -> Dict[str, Any]:
        """Un appel System One brut, avec la reprise sur panne commune."""
        return call_with_retry(lambda: self._post(state, questions),
                               on_retry=self._note_retry)

    def _consult(self, state: Any, questions: Dict[str, Any],
                 local_ok: List[str], record: Dict[str, Any]
                 ) -> Tuple[Dict[str, Any], Dict[str, str], Optional[BaseException]]:
        """Les questions fermées, routées entre le frontal local et Jev.

        Rend (réponses, erreurs par question, panne de Jev) et ne lève pas —
        la panne est rendue pour être relevée telle quelle si rien n'a
        répondu, afin que la trace du runtime nomme la vraie erreur. Une question
        sans réponse revient à l'appelant, qui la confie au repli génératif.
        Les questions locales et celles de Jev partent en même temps ; seule
        la cascade (réponse locale sous son seuil) attend Laya.

        Une réponse locale sous le seuil n'est jamais retenue, même si Jev est
        tombé : le seuil définit ce que Laya sait faire, et en dessous il n'a
        pas répondu — le programme applique alors ce qu'il a prévu pour une
        absence, au lieu d'agir sur un jugement que son auteur a déclaré
        indigne de confiance.
        """
        engines: Dict[str, str] = {}
        routes: Dict[str, Any] = {}
        if self.local is not None and local_ok:
            why: Dict[str, str] = {}
            for qid in local_ok:
                route = self.local.route(state, questions[qid])
                why[qid] = route.why
                if route.checkpoint:
                    routes[qid] = route
            record["localRoutes"] = why
        remote = {q: v for q, v in questions.items() if q not in routes}
        pending = (_POOL.submit(self.local.answer, state, routes)
                   if routes else None)
        answers: Dict[str, Any] = {}
        errors: Dict[str, str] = {}
        jev_down: Optional[str] = None
        jev_exc: Optional[BaseException] = None

        def to_jev(batch: Dict[str, Any], label: str) -> None:
            nonlocal jev_down, jev_exc
            if not batch:
                return
            if jev_down is not None:
                errors.update({q: jev_down for q in batch})
                return
            try:
                got = self.ask(state, batch)
            except Exception as exc:                  # noqa: BLE001
                jev_exc = exc
                jev_down = f"{type(exc).__name__}: {str(exc)[:160]}"
                errors.update({q: jev_down for q in batch})
                return
            for qid in batch:
                if qid in got:
                    answers[qid] = got[qid]
                    engines[qid] = label

        to_jev(remote, "jev")
        if pending is not None:
            from laya_llm import probability as local_p
            local_answers, local_errors = pending.result()
            weak: Dict[str, Any] = {}
            for qid, route in routes.items():
                answer = local_answers.get(qid)
                if answer is not None and self.local.keeps(route.checkpoint,
                                                           local_p(answer)):
                    answers[qid] = answer
                    engines[qid] = f"laya:{route.checkpoint}"
                else:
                    weak[qid] = questions[qid]
            if local_errors:
                record["localErrors"] = local_errors
            to_jev(weak, "jev (cascade)")
            for qid in local_errors:                  # pas une cascade : un silence
                if engines.get(qid) == "jev (cascade)":
                    engines[qid] = "jev (Laya muet)"
        if jev_down is not None:
            record["jevError"] = jev_down
        record["engines"] = engines
        return answers, errors, jev_exc

    # ---------------------------------------------------------- questions
    @staticmethod
    def _instructions(task: str, field: str, question: str) -> Dict[str, Any]:
        out: Dict[str, Any] = {"task": task, "field": _humanize(field)}
        definition = field_definition(task, field)
        if definition:
            out["field_definition"] = definition
        out["question"] = question
        return out

    def _choice(self, task: str, field: str, options: List[str]) -> Dict[str, Any]:
        return {
            "type": "choice",
            "instructions": self._instructions(
                task, field, "Carry out `task` on the state. Which option is "
                             "the correct value of `field`?"),
            "criteria": {opt: _SENTINELS.get(opt.lower()) for opt in options},
        }

    def _noul(self, task: str, field: str) -> Dict[str, Any]:
        return {
            "type": "noul",
            "instructions": self._instructions(
                task, field, "Carry out `task` on the state. Is `field` true?"),
        }

    def _pick_number(self, task: str, field: str,
                     candidates: Dict[str, str]) -> Dict[str, Any]:
        criteria: Dict[str, Any] = dict(candidates)
        criteria[NONE] = "The state states no value for `field`."
        return {
            "type": "choice",
            "instructions": self._instructions(
                task, field, "Carry out `task` on the state. Which number, "
                             "as written in the state, is the value of "
                             "`field`?"),
            "criteria": criteria,
        }

    def _plan(self, task: str, context: Dict[str, Any],
              produce: Dict[str, str]) -> Tuple[Dict[str, Any], Dict[str, str], List[str]]:
        """Répartit les champs : (questions Jev, route par champ, repli)."""
        questions: Dict[str, Any] = {}
        routes: Dict[str, str] = {}
        rest: List[str] = []
        numbers: Optional[Dict[str, str]] = None
        for field, typ in produce.items():
            base, domain = _split_schema(typ)
            if field == "confidence" or field.endswith("_confidence"):
                routes[field] = "derived"
            elif domain and base not in ("number", "float", "int"):
                questions[field] = self._choice(task, field, domain)
                routes[field] = "choice"
            elif base in ("bool", "boolean"):
                questions[field] = self._noul(task, field)
                routes[field] = "noul"
            elif (base in ("number", "float", "int") and not domain
                  and self.select_numbers):
                if numbers is None:
                    numbers = number_candidates(context)
                if numbers:
                    questions[field] = self._pick_number(task, field, numbers)
                    routes[field] = "value"
                else:
                    routes[field] = "fallback"
                    rest.append(field)
            else:
                routes[field] = "fallback"
                rest.append(field)
        # Une confiance dérivée sans rien dont dériver est un champ ordinaire.
        if not questions:
            for field, route in list(routes.items()):
                if route == "derived":
                    routes[field] = "fallback"
                    rest.append(field)
        return questions, routes, rest

    # ------------------------------------------------------------- REASON
    def _fallback_reason(self, task: str, context: Dict[str, Any],
                         sub: Dict[str, str]) -> Tuple[Dict[str, Any], List[str]]:
        self.fallback.last_reason_missing = None
        got = self.fallback.reason(task, context, sub)
        missing = getattr(self.fallback, "last_reason_missing", None)
        return got, list(missing or [])

    @staticmethod
    def _settle(call, fields: List[str],
                record: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
        """Le résultat du repli, ou « ces champs sont absents » s'il a levé.

        Une panne du modèle génératif ne concerne que les champs qu'on lui a
        confiés. La laisser remonter faisait échouer tout le `REASON` : le
        runtime appliquait alors les DEFAULT aux champs que Jev venait de
        rendre, et une panne Gemini effaçait une réponse Jev valide.
        """
        try:
            return call()
        except Exception as exc:                      # noqa: BLE001
            record.setdefault("fallbackErrors", []).append(
                f"{type(exc).__name__}: {str(exc)[:160]}")
            return {}, list(fields)

    @staticmethod
    def _probability_of(route: str, answer: Dict[str, Any], value: Any) -> float:
        """P, selon Jev, de `value` — la valeur **retenue**, qui n'est pas
        forcément celle qu'il avait choisie (champ escaladé)."""
        if route == "noul":
            p_yes = float(answer.get("noul", 0.5))
            if value is True:
                return p_yes
            return 1.0 - p_yes if value is False else 0.0
        probs = answer.get("probabilities") or {}
        if route == "value":
            for literal, p in probs.items():
                if literal != NONE and parse_number(literal) == value:
                    return float(p)
            return 0.0
        return float(probs.get(str(value), 0.0))

    def reason(self, task, context, produce):
        record: Dict[str, Any] = {"kind": "reason", "task": task}
        self.calls.append(record)
        questions, routes, rest = self._plan(task, context, produce)
        record["routes"] = dict(routes)
        out: Dict[str, Any] = {}
        prob: Dict[str, float] = {}        # P(valeur rendue), selon Jev
        answers: Dict[str, Any] = {}
        # Les champs ouverts sont connus avant la réponse de Jev : leur appel
        # génératif part en même temps. En série, un REASON mixte coûtait la
        # latence de Jev PLUS celle du modèle génératif — plus lent que le
        # modèle génératif seul.
        early: Optional[Future] = None
        early_fields = list(rest)
        if early_fields and questions and self.fallback is not None:
            sub = {f: produce[f] for f in early_fields}
            early = _POOL.submit(self._fallback_reason, task, context, sub)
        if questions:
            closed = [f for f in questions if routes[f] in ("choice", "noul")]
            answers, failed, down = self._consult(context, questions, closed, record)
            if failed and not answers:
                if self.fallback is None:
                    self.last_reason_missing = list(produce)
                    raise down or RuntimeError(f"System One muet : {failed}")
                # Aucune réponse System One : ses champs partent au repli,
                # confiances comprises. Ceux déjà confiés au repli en avance
                # y restent : les redemander doublait l'appel génératif.
                rest = [f for f in produce if f not in early_fields]
            elif failed:
                # Panne partielle : seuls les champs restés sans réponse — et
                # leur confiance, qui n'a plus rien dont dériver — partent.
                rest = rest + list(failed) + [
                    f"{f}_confidence" for f in failed if f"{f}_confidence" in produce]
        if early is not None:
            got, missing_fb = self._settle(early.result, early_fields, record)
            for field in early_fields:
                if field in got and field not in missing_fb:
                    out[field] = got[field]
            rest = [f for f in rest if f not in early_fields]
        record["answers"] = answers
        for field, answer in answers.items():
            route = routes.get(field)
            if route == "noul":
                p_yes = float(answer.get("noul", 0.5))
                out[field] = p_yes >= 0.5
                prob[field] = max(p_yes, 1.0 - p_yes)
            elif route in ("choice", "value"):
                choice = answer.get("choice")
                probs = answer.get("probabilities") or {}
                prob[field] = float(probs.get(choice, answer.get("confidence", 0.0)))
                if route == "value":
                    number = parse_number(choice) if choice != NONE else None
                    if number is not None:
                        out[field] = number
                    else:
                        rest.append(field)            # rien de lisible : repli
                else:
                    out[field] = choice
        # Abstention : sous le seuil, pas de valeur — le DEFAULT du programme
        # s'applique. Testée avant la cascade, qui ne voit donc que le reste.
        abstained = [f for f, p in prob.items()
                     if p < self.abstain_below and f in out]
        for field in abstained:
            del out[field]
        record["abstained"] = abstained
        # Cascade : ce que Jev juge incertain est redemandé au modèle génératif.
        escalated = [f for f, p in prob.items()
                     if p < self.escalate_below and f in out]
        record["escalated"] = escalated
        ask_fallback = list(dict.fromkeys(rest + escalated))
        if ask_fallback and self.fallback is not None:
            sub = {f: produce[f] for f in ask_fallback}
            got, missing_fb = self._settle(
                lambda: self._fallback_reason(task, context, sub),
                ask_fallback, record)
            for field in ask_fallback:
                if field in got and field not in missing_fb:
                    out[field] = _coerce({field: got[field]}, {field: produce[field]})[field]
                    if field in escalated:
                        # La valeur retenue est celle du repli : la
                        # probabilité est celle que Jev accordait à CETTE
                        # valeur, qui reste calibrée — pas à la sienne.
                        prob[field] = self._probability_of(
                            routes[field], answers.get(field) or {}, out[field])
        # Confiances dérivées : jamais demandées, toujours calculées.
        for field, route in routes.items():
            if route != "derived" or field in out:
                continue
            if field == "confidence":
                if prob:
                    out[field] = round(min(prob.values()), 4)
            else:
                target = field[: -len("_confidence")]
                if target in prob:
                    out[field] = round(prob[target], 4)
        record["probability"] = prob
        self.last_reason_missing = [f for f in produce if f not in out]
        if not out and record.get("fallbackErrors"):
            # Aucun oracle n'a rien rendu : c'est une panne, pas une réponse
            # vide — le runtime la trace comme telle.
            raise RuntimeError("REASON sans réponse — " + " ; ".join(
                [record.get("jevError", "")] + record["fallbackErrors"]).strip(" ;"))
        return _coerce(out, produce)

    # --------------------------------------------------------------- JUDGE
    def judge(self, task, context, questions):
        """`JUDGE` : les questions du programme, posées telles quelles.

        C'est le seul point où l'adaptateur n'a **rien à inventer**. Pour un
        `REASON`, il doit deviner la question à partir du nom du champ et de
        la consigne globale (`_instructions`) — et les bancs montrent où cela
        casse : `holds_negative` lu comme « concerne les mentions négatives »,
        `current_decision` pris sur le message du mauvais manager. Un `JUDGE`
        porte la question et la description de chaque réponse possible : elles
        partent sans réécriture, et la probabilité revient au programme.

        L'abstention, elle, reste au runtime : c'est le `.agent` qui déclare
        `ABSTAIN BELOW`, pas l'adaptateur. `abstain_below` du constructeur ne
        s'applique donc qu'aux `REASON`, où aucun seuil n'est déclarable.
        """
        record: Dict[str, Any] = {"kind": "judge", "task": task,
                                  "fields": list(questions)}
        self.calls.append(record)
        payload: Dict[str, Any] = {}
        for name, question in questions.items():
            kind = str(question.get("kind", "")).upper()
            instructions: Dict[str, Any] = {
                "question": question.get("instructions", "")}
            if task:
                instructions["context"] = task
            if kind == "NOUL":
                payload[name] = {"type": "noul", "instructions": instructions}
            elif kind == "CHOICE":
                payload[name] = {"type": "choice", "instructions": instructions,
                                 "criteria": dict(question.get("criteria") or {})}
            elif kind == "SCORE":
                payload[name] = {"type": "score", "instructions": instructions,
                                 "criteria": list(question.get("levels") or [])}
        if not payload:
            return {}
        answers, failed, down = self._consult(context, payload, list(payload), record)
        fallback_out: Dict[str, Any] = {}
        if failed:
            if self.fallback is None and not answers:
                raise down or RuntimeError(f"System One muet : {failed}")
            if self.fallback is not None:
                # System One muet sur ces questions : le repli génératif y
                # répond, sans probabilité — le runtime fermera ce qui
                # exigeait un seuil.
                record["fallback"] = sorted(failed)
                from agentl.llm import judge_via_reason
                try:
                    fallback_out = judge_via_reason(
                        self.fallback, task, context,
                        {name: questions[name] for name in failed})
                except Exception as exc:              # noqa: BLE001
                    # Comme pour `reason` : la panne du repli ne concerne que
                    # ses questions — sauf si plus rien n'a répondu.
                    if not answers:
                        raise
                    record.setdefault("fallbackErrors", []).append(
                        f"{type(exc).__name__}: {str(exc)[:160]}")
        out: Dict[str, Any] = dict(fallback_out)
        for name, answer in (answers or {}).items():
            kind = str((questions.get(name) or {}).get("kind", "")).upper()
            if kind == "NOUL":
                p_yes = float(answer.get("noul", 0.5))
                out[name] = {"value": p_yes >= 0.5,
                             "p": max(p_yes, 1.0 - p_yes),
                             "confidence": max(p_yes, 1.0 - p_yes)}
            elif kind == "CHOICE":
                choice = answer.get("choice")
                probs = answer.get("probabilities") or {}
                out[name] = {"value": choice,
                             "p": float(probs.get(choice, 0.0)),
                             "confidence": float(answer.get("confidence", 0.0))}
            elif kind == "SCORE":
                # `score` est une position pondérée sur les niveaux ; la
                # probabilité du niveau le plus proche est ce qui se compare
                # à un seuil d'abstention.
                score = float(answer.get("score", 0.0))
                probs = {int(k): float(v)
                         for k, v in (answer.get("probabilities") or {}).items()}
                nearest = probs.get(int(round(score)), 0.0)
                out[name] = {"value": score, "p": nearest,
                             "confidence": float(answer.get("confidence", 0.0))}
        record["answers"] = answers
        return out

    # -------------------------------------------------------- SELECT_PLAN
    # BOUNDARY-OK: méthode imposée par le protocole LLM d'AGENT-L — le runtime
    # n'accepte qu'un plan déclaré parmi `candidates` ; le modèle propose.
    def select_plan(self, context, candidates):
        record: Dict[str, Any] = {"kind": "select_plan",
                                  "candidates": list(candidates)}
        self.calls.append(record)
        if not candidates:
            return None
        question = {"plan": {
            "type": "choice",
            "instructions": ("Which of these plans should the agent run next, "
                             "given the current state?"),
            "criteria": {name: None for name in candidates},
        }}
        # Jamais au frontal local : le choix d'action est le terrain où Laya
        # est mesuré au niveau du hasard (AGENTS_CONSTRUCT, 2026-09-20).
        try:
            answer = self.ask(context, question).get("plan") or {}
        except Exception as exc:                      # noqa: BLE001
            record["jevError"] = f"{type(exc).__name__}: {str(exc)[:160]}"
            if self.fallback is None:
                raise
            return self.fallback.select_plan(context, candidates)
        choice = answer.get("choice")
        p = float((answer.get("probabilities") or {}).get(choice, 0.0))
        record.update(choice=choice, probability=p)
        if p < self.escalate_below and self.fallback is not None:
            record["escalated"] = True
            return self.fallback.select_plan(context, candidates)
        return choice if choice in candidates else None


def usage_summary(llm: Any) -> Dict[str, Any]:
    """Compte-rendu homogène pour Jev, Gemini ou un JevLLM avec repli et
    frontal local."""
    rows = list(getattr(llm, "responses", []) or [])
    jev = [r for r in rows if r.get("provider") == "typesafe-systemone"]
    fb = getattr(llm, "fallback", None)
    gen = list(getattr(fb, "responses", []) or []) if fb is not None else \
        [r for r in rows if r.get("provider") != "typesafe-systemone"]
    local = list(getattr(getattr(llm, "local", None), "responses", []) or [])
    return {
        "laya_calls": len(local),
        "laya_questions": sum(r.get("questions", 0) for r in local),
        "laya_seconds": round(sum(r.get("latencySeconds", 0.0) for r in local), 2),
        "jev_calls": len(jev),
        "jev_questions": sum(r.get("questions", 0) for r in jev),
        "jev_input_tokens": sum(r.get("promptTokens") or 0 for r in jev),
        "jev_cost_usd": round(sum(r.get("costUsd", 0.0) for r in jev), 6),
        "jev_seconds": round(sum(r.get("latencySeconds", 0.0) for r in jev), 2),
        "gen_calls": len(gen),
        "gen_input_tokens": sum(r.get("promptTokens") or 0 for r in gen),
        "gen_output_tokens": sum(r.get("completionTokens") or 0 for r in gen),
        "gen_seconds": round(sum(r.get("latencySeconds", 0.0) for r in gen), 2),
    }


def oracle_from_env(model: str = "") -> LLM:  # pragma: no cover - réseau
    """Oracle choisi par ``AGENTL_ORACLE`` : ``gemini`` (défaut), ``hybrid``
    (Jev + repli Gemini) ou ``jev`` (Jev seul, sans génération de texte).

    ``AGENTL_LAYA=1`` ajoute, pour ``hybrid`` et ``jev``, le frontal local
    Laya (`laya_llm.LayaRouter`, réglé par ``AGENTL_LAYA_*``).

    Le repli est lu dans `gemini_llm`, voisin de ce module ; ``model`` et
    ``AGENTL_BENCH_MODEL`` désignent le modèle génératif.
    """
    from gemini_llm import GeminiLLM

    kind = os.environ.get("AGENTL_ORACLE", "gemini").strip().lower()
    generative = model or os.environ.get("AGENTL_BENCH_MODEL", "gemini-3.1-flash-lite")
    local = None
    if os.environ.get("AGENTL_LAYA", "") == "1":
        from laya_llm import LayaRouter
        local = LayaRouter()
    if kind == "gemini":
        return GeminiLLM(model=generative)
    if kind == "hybrid":
        return JevLLM(fallback=GeminiLLM(model=generative), local=local)
    if kind == "jev":
        return JevLLM(fallback=None, local=local)
    raise ValueError(f"AGENTL_ORACLE inconnu : {kind!r} (gemini, hybrid, jev)")

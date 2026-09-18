"""Analyseur statique.

Un langage agentique n'a d'intérêt que s'il permet de démontrer des propriétés
**avant** exécution. On vérifie ici ce qu'un prompt ne permet pas de vérifier :

  E001  outil appelé mais non déclaré
  E002  plan référencé mais inexistant
  E003  politique portant sur un outil inconnu
  E004  nom dupliqué (outil / plan / objectif)
  E005  événement sans corps utile
  W101  outil à risque HIGH/CRITICAL non couvert par une politique
  W102  plan à effet de bord sans VERIFY
  W103  chemin observé jamais utilisé dans une expression
  W104  croyance jamais rafraîchie par une observation
  W105  aucun objectif déclaré
  W106  plan inatteignable (ni WHEN, ni EVENT, ni DECIDE, ni appel)
  E006  vraisemblance hors de ]0,1[ dans une EVIDENCE
  W107  hypothèse dont le postérieur n'est jamais consulté
  W108  planificateur activé mais aucun outil ne déclare d'EFFECT
  W109  opérateur dont l'EFFECT ne peut jamais être déclenché (INPUT non liable)
  W110  but du planificateur qu'aucun EFFECT déclaré ne peut satisfaire
  W113  les OUTCOME d'un outil ne totalisent pas 1 (renormalisation appliquée)
  E008  écriture INTO SHARED.<clé> non déclarée dans MEMORY { SHARED { … } }
  E009  appel d'outil dans une expression (un appel n'est pas pur)
  E015  fonction inconnue dans une expression (inévaluable à l'exécution)
  E016  fonction de provenance mal employée (v1.9)
  W114  probabilité non calibrée alimentant une garde de politique
  W115  sortie de LLM non bornée alimentant un seuil de décision
  W116  THRESHOLD d'une hypothèse hors de l'amplitude atteignable
  E010  SCENARIO dont le WITHIN ne laisse pas un tick à l'agent
  E012  nom d’AGENT dupliqué (insensible à la casse)
  E013  signature d’appel TOOL invalide
  E011  outil dont le RISK vaut UNSET (import MCP non revu par l'auteur)
  W117  SCENARIO dont le GIVEN pose un chemin qui n'existe nulle part
  W118  SCENARIO dont l'attente ne porte sur aucun chemin qu'un EFFECT produit
  W119  sortie LLM utilisée comme cible risquée sans ATTESTS
  W120  outil HIGH/CRITICAL sans REQUIRE APPROVAL
  W121  VERIFY auto-confirmé par EFFECT sans OBSERVE
  W122  hypothèse globale autorisant une action ciblée dans FOREACH
  W123  clôture non gardée par l'absence de travail non résolu
  W124  outil risqué sans scénario de sûreté
  W125  texte non fiable suivi d'une action risquée sans garde d'injection
  W126  le planificateur arbitre sur le temps, un opérateur sans DURATION
  W127  DELEGATE vers un sous-agent qu'aucun TOOL ne décrit (risque supposé CRITICAL)
  W128  garde de politique portant sur un identifiant nu non déclaré
  W129  REASON sans USING : tout l'état est transmis au modèle
  W130  USING listant un chemin qu'un NEVER SEND retient — contradiction
  W131  garde de politique sur un chemin observé sans conduite ON UNKNOWN
  W132  ON UNKNOWN DEGRADE sur un chemin dont dépend un NEVER/DENY/APPROVAL
  W133  schéma PRODUCE trop long : risque de troncature silencieuse
  W134  DEFAULT d'un champ PRODUCE qui ne déclenche pas l'interdit qu'il garde
  W135  garde de déclenchement comparant `!=` un chemin que rien ne renseigne

Contrôles inter-agents (`check_program`) :

  E007  MESSAGE adressé à un agent absent du programme
  W111  MESSAGE qu'aucun agent ne sait recevoir
  W112  ON MESSAGE pour un nom que personne n'émet
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Set

from .kernel.provenance import PROVENANCE_FUNCS
from .mcp import UNSET_RISK
from .state import EPISTEMIC_FUNCS, PURE_FUNCS
from .trivalent import applies_when_unknown

#: Fonctions qu'une expression peut appeler : pures, épistémiques, et de
#: provenance. Tout le reste est soit un outil (E009), soit rien (E015).
KNOWN_FUNCS = frozenset(PURE_FUNCS) | frozenset(EPISTEMIC_FUNCS) \
    | PROVENANCE_FUNCS
from .nodes import (
    Agent, CallExpr, CallStmt, ControlStmt, Decide, DelegateStmt, IfStmt,
    ForEachStmt, ListExpr, LoopStmt, MessageStmt, Node, PathExpr, Plan, Program,
    ReasonStmt, SetStmt, Stmt, ThenPlan, UnOp, BinOp, VerifyStmt,
)


#: Comparaisons dont le membre gauche dénote un état plutôt qu'une constante.
_COMPARISON_OPS = {"==", "!=", ">", ">=", "<", "<="}

#: Noms nus que le moteur de politiques lie lui-même : ils sont toujours
#: renseignés, et les signaler serait du bruit.
_RESERVED_GUARD_NAMES = {"confidence", "action"}

#: Premiers segments que le **runtime** publie de lui-même : aucun programme
#: ne les déclare, et ils existent pourtant à l'exécution. Les signaler comme
#: non renseignés (W135) reviendrait à crier sur les faits que le langage
#: fournit — `tools.x.available`, `planner.exhausted`, `reason.degraded`…
_RUNTIME_PATH_PREFIXES = {
    "sensors", "tools", "planner", "reason", "last_action", "payload",
    "answer", "memory", "SHARED", "SHORT_TERM", "LONG_TERM", "KNOWLEDGE",
    "action", "item", "P",
}


@dataclass
class Diagnostic:
    code: str
    severity: str          # error | warning
    message: str
    line: int = 0

    def render(self) -> str:
        mark = "erreur " if self.severity == "error" else "avert. "
        where = f"ligne {self.line}" if self.line else "—"
        return f"{mark}{self.code}  {where:<10} {self.message}"


class Analyzer:
    #: Champs `PRODUCE` au-delà desquels la troncature devient probable.
    #: Valeur empirique : c'est vers dix champs qu'un modèle à raisonnement
    #: commence à épuiser son budget de sortie avant d'avoir fermé le JSON.
    PRODUCE_FIELD_LIMIT = 8

    def __init__(self, agent: Agent):
        self.agent = agent
        self.diags: List[Diagnostic] = []

    # ------------------------------------------------------------------ API
    def run(self) -> List[Diagnostic]:
        self.diags = []
        self._check_duplicates()
        self._check_events()
        self._check_calls_and_plans()
        self._check_policies()
        self._check_expression_calls()
        self._check_unset_risk()
        self._check_declared_durations()
        self._check_delegate_contracts()
        self._check_reason_surface()
        self._check_redactions()
        self._check_sensor_unknown()
        self._check_policy_guard_names()
        self._check_undefined_inequality()
        self._check_risky_tools()
        self._check_verification()
        self._check_observability()
        self._check_reachability()
        self._check_scenarios()
        self._check_hypotheses()
        self._check_planner()
        self._check_shared_keys()
        self._check_decision_inputs()
        self._check_security_authoring()
        if not self.agent.goals:
            self.diags.append(Diagnostic(
                "W105", "warning", "aucun GOAL déclaré : l'agent n'a rien à évaluer"))
        return self.diags

    @property
    def errors(self) -> List[Diagnostic]:
        return [d for d in self.diags if d.severity == "error"]

    # ------------------------------------------------------------- contrôles
    def _check_events(self) -> None:
        """E005 — un `EVENT` sans corps utile.

        Le code était annoncé depuis la v0.6 et n'était **émis nulle part** :
        la documentation promettait un contrôle qui n'existait pas. Un
        gestionnaire vide n'est pas un défaut de style — c'est un événement
        que l'hôte dépose, que l'agent draine, et qui ne déclenche rien : la
        trace montre un `⚡` et la boucle continue comme si de rien n'était.
        """
        for event in self.agent.events:
            if not event.body:
                self.diags.append(Diagnostic(
                    "E005", "error",
                    f"EVENT {event.source} sans corps : l'événement est "
                    f"consommé et ne déclenche rien", event.line))

    def _check_duplicates(self) -> None:
        for label, items in (("outil", [t.name for t in self.agent.tools]),
                             ("plan", [p.name for p in self.agent.plans]),
                             ("objectif", [g.name for g in self.agent.goals])):
            seen: Set[str] = set()
            for name in items:
                if name in seen:
                    self.diags.append(Diagnostic(
                        "E004", "error", f"{label} dupliqué : {name}"))
                seen.add(name)

    def _check_calls_and_plans(self) -> None:
        tools = {t.name for t in self.agent.tools}
        plans = {p.name for p in self.agent.plans}
        for stmt, _ in self._all_statements():
            if isinstance(stmt, CallStmt):
                if stmt.call.name not in tools:
                    self.diags.append(Diagnostic(
                        "E001", "error",
                        f"outil non déclaré : {stmt.call.name}()", stmt.line))
                else:
                    decl = self.agent.tool(stmt.call.name)
                    names = list(decl.inputs)
                    positional = set(names[:len(stmt.call.args)])
                    keywords = set(stmt.call.kwargs)
                    problems = []
                    if len(stmt.call.args) > len(names):
                        problems.append("trop d'arguments positionnels")
                    if positional & keywords:
                        problems.append("paramètres liés deux fois : " + ", ".join(sorted(positional & keywords)))
                    if keywords - set(names):
                        problems.append("paramètres inconnus : " + ", ".join(sorted(keywords - set(names))))
                    missing = set(names) - positional - keywords
                    if missing:
                        problems.append("arguments manquants : " + ", ".join(sorted(missing)))
                    if problems:
                        self.diags.append(Diagnostic("E013", "error",
                            f"{stmt.call.name}() : " + "; ".join(problems), stmt.line))
            for expr in _stmt_exprs(stmt):
                self._check_calls_in(expr, stmt.line, guard=False)
            if isinstance(stmt, ThenPlan) and stmt.plan not in plans:
                self.diags.append(Diagnostic(
                    "E002", "error",
                    f"plan inexistant : {stmt.plan}", stmt.line))

    def _delegate_targets(self) -> Set[str]:
        return {stmt.agent for stmt, _ in self._all_statements()
                if isinstance(stmt, DelegateStmt)}

    def _check_calls_in(self, expr, line: int, *, guard: bool) -> None:
        """E009 / E015 / E016 — ce qu'une expression a le droit d'appeler.

        `x = outil(...)` est refusé à l'exécution : un appel a des effets,
        il ne peut pas être évalué au milieu d'une expression. Le dire ici,
        c'est la thèse du projet — l'erreur se voit avant le run, pas au
        milieu d'une écriture dans le CRM (E009).

        Jusqu'en v1.8 **tout** appel en position d'expression était signalé
        E009, `CONFIDENCE(x)` et `len(xs)` compris, que le runtime évalue
        pourtant très bien ; et un nom inconnu dans une garde de politique
        passait sans un mot — l'évaluation levait à l'exécution, la garde
        devenait indéterminée, et un `NEVER` interdisait l'outil pour
        toujours sans que `check` l'ait dit (E015).
        """
        tools = {t.name for t in self.agent.tools}
        for node in _walk_expr(expr):
            if not isinstance(node, CallExpr):
                continue
            if node.name in PROVENANCE_FUNCS:
                self._check_provenance_call(node, line, guard=guard)
            elif node.name in KNOWN_FUNCS:
                continue
            elif node.name in tools:
                self.diags.append(Diagnostic(
                    "E009", "error",
                    f"appel d'outil dans une expression : "
                    f"{node.name}() — appelle-le dans une "
                    f"instruction, puis lis sa sortie", line))
            else:
                self.diags.append(Diagnostic(
                    "E015", "error",
                    f"fonction inconnue : {node.name}() — l'expression sera "
                    f"inévaluable à l'exécution (fonctions reconnues : "
                    f"{', '.join(sorted(KNOWN_FUNCS))})", line))

    def _check_provenance_call(self, node, line: int, *, guard: bool) -> None:
        """E016 — une fonction de provenance porte sur un chemin (v1.9)."""
        name = node.name
        tools = {t.name for t in self.agent.tools}
        problems: List[str] = []
        first = node.args[0] if node.args else None
        if not isinstance(first, PathExpr):
            problems.append("attend un chemin en premier argument")
        elif first.dotted == "action":
            if name == "ATTESTED":
                problems.append("porte sur une valeur, pas sur l'action "
                                "entière")
            elif not guard:
                problems.append("`action` ne se juge que dans une garde de "
                                "politique")
        max_args = 2 if name == "ATTESTED" else 1
        if len(node.args) > max_args or node.kwargs:
            problems.append(f"au plus {max_args} argument(s)")
        if name == "ATTESTED" and len(node.args) == 2:
            who = node.args[1]
            if not (isinstance(who, PathExpr) and len(who.parts) == 1):
                problems.append("le second argument nomme un outil")
            elif who.parts[0] not in tools:
                problems.append(f"`{who.parts[0]}` n'est pas un TOOL déclaré "
                                f"— aucune attestation ne pourra le citer")
        for problem in problems:
            self.diags.append(Diagnostic(
                "E016", "error", f"{name}() : {problem}", line))

    def _check_expression_calls(self) -> None:
        """Appels dans les expressions hors instructions : gardes, buts…"""
        for rule in self.agent.policies:
            self._check_calls_in(rule.guard, rule.line, guard=True)
        roots = []
        for plan in self.agent.plans:
            roots.append((plan.when, plan.line))
        for goal in self.agent.goals:
            roots.append((goal.condition, goal.line))
            roots.extend((target, goal.line) for target in goal.targets)
        for obs in self.agent.observers:
            roots.append((obs.when, obs.line))
        for event in self.agent.events:
            roots.append((event.when, event.line))
        for handler in self.agent.messages:
            roots.append((handler.when, handler.line))
        for write in self.agent.memory.writes:
            roots.append((write.when, getattr(write, "line", 0)))
        for hypothesis in self.agent.hypotheses:
            roots.extend((item.test, hypothesis.line)
                         for item in hypothesis.evidence)
        if self.agent.loop is not None:
            roots.append((self.agent.loop.until, self.agent.loop.line))
        for expr, line in roots:
            if expr is not None:
                self._check_calls_in(expr, line, guard=False)

    def _check_policies(self) -> None:
        # Depuis la v1.6, `DELEGATE` traverse le moteur de politiques et la
        # cible d'une règle peut donc être un sous-agent : refuser
        # `NEVER forensic` interdirait précisément d'écrire la garde qui
        # protège ce chemin.
        tools = {t.name for t in self.agent.tools} | self._delegate_targets()
        for rule in self.agent.policies:
            if rule.target != "*" and rule.target not in tools:
                self.diags.append(Diagnostic(
                    "E003", "error",
                    f"politique sur un outil inconnu : {rule.target}", rule.line))

    def _declared_names(self) -> Set[str]:
        """Noms simples que le programme renseigne quelque part."""
        names: Set[str] = set()
        for obs in self.agent.observers:
            names.add(obs.path.split(".")[0])
            names.add(obs.path)
        for belief in self.agent.beliefs:
            names.add(belief.path.split(".")[0])
            names.add(belief.path)
        for tool in self.agent.tools:
            names |= set(tool.outputs)
            # Les paramètres d'entrée **sont** renseignés au moment du
            # contrôle : `_ActionScope` lie les arguments de l'appel, et le
            # contrat `INPUT` est vérifié avant la politique — un paramètre
            # déclaré est donc présent. `NEVER block_ip WHEN ip_address ==
            # "127.0.0.1"` est la façon canonique de garder un argument, et la
            # signaler serait crier sur l'idiome le plus sûr du langage.
            names |= set(tool.inputs)
        for stmt, _ in self._all_statements():
            if isinstance(stmt, SetStmt):
                names.add(stmt.target.split(".")[0])
                names.add(stmt.target)
            elif isinstance(stmt, ReasonStmt):
                names |= set(stmt.produce)
            elif isinstance(stmt, DelegateStmt):
                names |= set(stmt.expect)
        for handler in self.agent.messages:
            names.add("payload")
        return names

    def _check_policy_guard_names(self) -> None:
        """W128 — garde de politique portant sur un identifiant nu non déclaré.

        Un identifiant simple que rien ne résout devient une **constante
        symbolique** : c'est ainsi que `open`, `yes` ou `contained` sont des
        valeurs. La règle est nécessaire, mais elle a un revers dans une
        garde :

            NEVER wipe WHEN dry_run == no

        Si rien ne renseigne `dry_run`, la comparaison devient
        `Symbol("dry_run") == Symbol("no")`, donc **fausse** — et l'interdit
        ne s'applique pas. La logique trivalente ne peut pas rattraper ce
        cas : l'évaluateur n'a pas rendu « indéfini », il a rendu une
        constante, et rien ne distingue au niveau de l'expression une
        constante voulue d'une variable oubliée.

        Ce que l'analyseur, lui, sait faire : constater que le programme ne
        renseigne ce nom nulle part. Écrire un chemin pointé
        (`run.dry_run`) rétablit la sémantique trivalente.
        """
        declared = self._declared_names()
        for rule in self.agent.policies:
            # Seules les règles dont la **non-application** est dangereuse.
            # Sur un `ALLOW`, un nom non résolu empêche l'autorisation d'être
            # satisfaite : le verdict retombe sur `DEFAULT`, donc fermé. Y
            # crier serait du bruit — et le bruit fait ignorer le signal.
            if not applies_when_unknown(rule.effect):
                continue
            for node in _walk_expr(rule.guard):
                suspects: List[str] = []
                if isinstance(node, BinOp) and node.op in _COMPARISON_OPS:
                    if isinstance(node.left, PathExpr) \
                            and len(node.left.parts) == 1:
                        suspects.append(node.left.parts[0])
                if not suspects:
                    continue
                for name in suspects:
                    if name in declared or name in _RESERVED_GUARD_NAMES:
                        continue
                    self.diags.append(Diagnostic(
                        "W128", "warning",
                        f"garde de politique sur `{name}`, que rien ne "
                        f"renseigne : lu comme une constante symbolique, la "
                        f"comparaison sera fausse et la règle ne "
                        f"s'appliquera pas — préférer un chemin pointé",
                        rule.line))

    def _check_undefined_inequality(self) -> None:
        """W135 — `!=` sur un chemin que rien ne renseigne, dans une garde.

        Les gardes de politique passent par la logique trivalente : un chemin
        absent y rend `UNKNOWN`, et l'interdit s'applique quand même (§ Kleene).
        Les gardes de **déclenchement** — `WHEN` de plan, `IF`, règle `DECIDE`
        — non : elles passent par l'évaluateur ordinaire, où une absence n'est
        ni égale ni différente de rien… sauf que `!=` est le complément de
        `==`, donc :

            PLAN escalader WHEN incidnet.severity != low

        La faute de frappe rend `UNDEFINED != low`, donc **vrai**, et le plan
        se déclenche à chaque tick sur l'ignorance. Le sens du défaut est ce
        qui le rend grave : une typo dans un `==` ne déclenche rien et se voit
        au premier essai ; dans un `!=` elle déclenche *tout*, et ressemble à
        un agent qui fonctionne.

        Serré volontairement : un seul opérateur (`!=`), un chemin pointé
        (les identifiants nus relèvent de W128), comparé à une constante, et
        aucun préfixe que le runtime publie ou qu'un `FOREACH` projette. Un
        avertissement qui crie à tort n'est plus lu.
        """
        from ._check_flow import guard_sites
        for cond, where, line, known, prefixes in guard_sites(self.agent):
            for node in _walk_expr(cond):
                if not isinstance(node, BinOp) or node.op != "!=":
                    continue
                for side, other in ((node.left, node.right), (node.right, node.left)):
                    if not isinstance(side, PathExpr) or len(side.parts) < 2:
                        continue
                    if isinstance(other, PathExpr) and len(other.parts) > 1:
                        continue
                    path = side.dotted
                    if path in known or side.parts[0] in prefixes:
                        continue
                    self.diags.append(Diagnostic("W135", "warning",
                        f"{where} compare `{path} != …` sans définition préalable "
                        "garantie à ce site : l'absence rend la comparaison vraie "
                        "— poser le chemin en OBSERVE/BELIEF ou avant la garde", line))

    def _known_state_paths(self) -> Set[str]:
        """Chemins pointés que le programme fait exister quelque part."""
        known: Set[str] = set()
        for obs in self.agent.observers:
            known.add(obs.path)
        for belief in self.agent.beliefs:
            known.add(belief.path)
        for tool in self.agent.tools:
            for branch in tool.branches:
                for effect in branch.effects:
                    known.add(effect.path)
            known |= set(tool.outputs) | set(tool.inputs)
        for stmt, _ in self._all_statements():
            if isinstance(stmt, SetStmt):
                known.add(stmt.target)
            elif isinstance(stmt, ReasonStmt):
                known |= set(stmt.produce)
                known |= {f"reason.{key}" for key in stmt.produce}
            elif isinstance(stmt, DelegateStmt):
                known |= set(stmt.expect)
        return known

    def _trigger_guards(self):
        """Gardes évaluées **sans** logique trivalente : celles qui déclenchent.

        Les gardes de politique en sont exclues à dessein : `policy.py` les
        évalue en Kleene, où l'absence rend `UNKNOWN` et l'interdit s'applique
        malgré tout. Le piège du `!=` ne s'y referme pas.
        """
        for plan in self.agent.plans:
            if plan.when is not None:
                yield plan.when, f"la garde WHEN du plan {plan.name}", plan.line
        for event in self.agent.events:
            if event.when is not None:
                yield event.when, f"la garde de EVENT {event.source}", event.line
        for handler in self.agent.messages:
            if handler.when is not None:
                yield (handler.when,
                       f"la garde de ON MESSAGE {handler.name}", handler.line)
        if self.agent.decide:
            for rule in self.agent.decide.rules:
                yield rule.cond, "une règle DECIDE", rule.line
        for stmt, origin in self._all_statements():
            if isinstance(stmt, IfStmt):
                yield stmt.cond, f"un IF dans {origin}", stmt.line

    def _check_reason_surface(self) -> None:
        """W129 — `REASON` sans `USING` : tout l'état part au modèle.

        `USING` restreint désormais réellement le contexte transmis (v1.6).
        Son absence n'est pas une erreur — c'est le défaut historique — mais
        elle vaut exposition complète : croyances, buts, plans et catalogue
        d'outils avec leur risque. Sur un modèle distant, c'est la surface la
        plus large que le programme puisse offrir, et elle est choisie par
        omission plutôt que par décision.
        """
        for stmt, _ in self._all_statements():
            if isinstance(stmt, ReasonStmt) and not stmt.using:
                self.diags.append(Diagnostic(
                    "W129", "warning",
                    "REASON sans USING : tout l'état (croyances, buts, plans, "
                    "outils) est transmis au modèle — lister les chemins "
                    "nécessaires borne l'exposition",
                    stmt.line))

    def _redacts(self, path: str) -> bool:
        """Le chemin tombe-t-il sous un `NEVER SEND` ? (même règle qu'au
        runtime : égalité ou préfixe de segment)."""
        return any(path == r.path or path.startswith(r.path + ".")
                   for r in self.agent.redactions)

    def _redacts_below(self, path: str) -> List[str]:
        """Chemins interdits situés **sous** celui-ci (même règle que
        `Runtime._covers_below`). Envoyer le parent d'un secret est le cas
        que le contrôle descendant seul laissait passer."""
        return sorted(r.path for r in self.agent.redactions
                      if r.path.startswith(path + "."))

    def _guarded_paths(self) -> Set[str]:
        """Chemins dont dépend une règle dont la non-application est
        dangereuse — `NEVER`, `DENY`, `REQUIRE_APPROVAL`."""
        out: Set[str] = set()
        for rule in self.agent.policies:
            if not applies_when_unknown(rule.effect):
                continue
            for node in _walk_expr(rule.guard):
                if isinstance(node, PathExpr):
                    out.add(".".join(node.parts))
        return out

    def _check_redactions(self) -> None:
        """W130 — `USING` listant un chemin qu'un `NEVER SEND` retient.

        Les deux déclarations disent le contraire l'une de l'autre : l'une
        désigne le chemin comme nécessaire au raisonnement, l'autre interdit
        qu'il sorte. Le runtime tranche dans la direction de la sûreté — la
        valeur est remplacée par `⟦retenu⟧` — mais le `REASON` raisonnera
        alors sur un trou, et son auteur croit lui avoir donné la donnée.
        Une contradiction silencieuse entre deux déclarations est pire que
        chacune des deux.

        Le contrôle vaut **dans les deux sens**. `USING { credentials }` avec
        `NEVER SEND credentials.token` ne retenait rien et n'avertissait de
        rien : le secret partait dans le composite parent. Le runtime retient
        désormais la feuille, mais le `REASON` reçoit quand même un trou —
        c'est la même contradiction, vue par le dessus, et elle mérite le
        même avertissement.
        """
        for stmt, _ in self._all_statements():
            if not isinstance(stmt, ReasonStmt) or not stmt.using:
                continue
            for path in stmt.using:
                if self._redacts(path):
                    self.diags.append(Diagnostic(
                        "W130", "warning",
                        f"USING liste `{path}`, qu'un NEVER SEND retient : "
                        f"le modèle recevra `⟦retenu⟧` — retirer l'un des "
                        f"deux plutôt que laisser le runtime arbitrer",
                        stmt.line))
                    continue
                below = self._redacts_below(path)
                if below:
                    self.diags.append(Diagnostic(
                        "W130", "warning",
                        f"USING liste `{path}`, qui contient "
                        f"{', '.join('`' + p + '`' for p in below)} qu'un "
                        f"NEVER SEND retient : le modèle recevra le composite "
                        f"amputé de ces feuilles — nommer dans USING ce qui "
                        f"doit sortir plutôt que le parent du secret",
                        stmt.line))

    def _check_sensor_unknown(self) -> None:
        """W131/W132 — la conduite face à un capteur muet.

        `W131` : une garde `NEVER`/`DENY`/`REQUIRE APPROVAL` porte sur un
        chemin observé qui ne déclare aucun `ON UNKNOWN`. Le fail-closed
        s'applique — c'est la bonne direction — mais l'agent se bloquera
        pour une panne de capteur sans que le programme ait jamais dit ce
        qu'il fallait faire dans ce cas. La disponibilité mérite d'être
        décidée, pas subie.

        `W132` : l'inverse, et il est plus grave. `ON UNKNOWN DEGRADE` sur
        un chemin dont dépend un interdit substitue une valeur **déclarée**
        là où le moteur attendait une mesure. Un repli mal choisi rouvre
        exactement la faille §7.1 — une donnée absente qui n'active plus le
        `NEVER` — cette fois avec la bénédiction du programme. Le repli
        reste permis : il est parfois le bon choix. Il ne doit pas être
        silencieux.
        """
        guarded = self._guarded_paths()
        for obs in self.agent.observers:
            if obs.path not in guarded:
                continue
            if obs.on_unknown == "DEGRADE":
                self.diags.append(Diagnostic(
                    "W132", "warning",
                    f"ON UNKNOWN DEGRADE sur `{obs.path}`, dont dépend un "
                    f"interdit : un capteur muet fera juger la règle sur "
                    f"une valeur déclarée, pas mesurée — vérifier que le "
                    f"repli est celui qui **bloque**",
                    obs.line))
            elif obs.on_unknown is None:
                self.diags.append(Diagnostic(
                    "W131", "warning",
                    f"`{obs.path}` garde un interdit et ne déclare aucun "
                    f"ON UNKNOWN : un capteur muet bloquera l'agent "
                    f"(fail-closed) sans conduite déclarée — ESCALATE nomme "
                    f"la panne, DEGRADE pose un repli assumé",
                    obs.line))

    def _check_delegate_contracts(self) -> None:
        """W127 — `DELEGATE` vers un sous-agent qu'aucun contrat ne décrit.

        Un sous-agent est une fonction Python opaque : rien n'empêche d'y
        écrire en base ou de lancer une commande. Sans `TOOL` homonyme, le
        runtime lui prête `CRITICAL` et le soumet aux politiques comme
        n'importe quelle action — mais le risque réel reste indéclaré, donc
        invérifiable. Déclarer un `TOOL` du même nom donne au sous-agent le
        contrat qui lui manque.
        """
        declared = {t.name for t in self.agent.tools}
        for stmt, _ in self._all_statements():
            if isinstance(stmt, DelegateStmt) and stmt.agent not in declared:
                self.diags.append(Diagnostic(
                    "W127", "warning",
                    f"DELEGATE {stmt.agent} sans TOOL homonyme : risque et "
                    f"effets de bord non déclarés (supposés CRITICAL)",
                    stmt.line))

    def _check_declared_durations(self) -> None:
        """W126 — un opérateur sans `DURATION` quand le temps compte.

        Une durée absente vaut zéro dans la recherche, et un zéro non déclaré
        est indiscernable d'une mesure : le planificateur préférerait
        l'opérateur muet pour la seule raison qu'il se tait. Le signaler
        uniquement quand `DEADLINE` ou `TIME_WEIGHT` est posé — sans modèle de
        temps, l'omission ne biaise rien, et une alarme qui crie partout ne se
        lit plus.
        """
        spec = self.agent.planner
        if spec is None or not spec.enabled:
            return
        if spec.deadline is None and not spec.time_weight:
            return
        for tool in self.agent.tools:
            if tool.is_operator and tool.duration is None:
                self.diags.append(Diagnostic(
                    "W126", "warning",
                    f"opérateur {tool.name}() sans DURATION alors que le "
                    f"planificateur arbitre sur le temps : compté pour 0 s",
                    tool.line))

    def _check_unset_risk(self) -> None:
        """Erreur, et **indépendante de toute politique**.

        Le risque est une entrée du moteur de politiques : le laisser non
        tranché, c'est décider par défaut à la place de l'auteur. Contrairement
        à `W101`, un `ALLOW *` ne fait pas disparaître ce diagnostic — écrire
        une règle attrape-tout n'est pas trancher un risque, c'est l'éluder.

        Produit par `agentl mcp import` : le protocole MCP ne dit rien du
        risque, et les annotations du serveur sont des auto-déclarations que
        rien ne vérifie (§29).
        """
        for tool in self.agent.tools:
            if tool.risk == UNSET_RISK:
                self.diags.append(Diagnostic(
                    "E011", "error",
                    f"outil {tool.name}() de risque {UNSET_RISK} : le risque "
                    f"n'a pas été tranché par l'auteur",
                    tool.line))

    def _check_risky_tools(self) -> None:
        covered = {r.target for r in self.agent.policies}
        if "*" in covered:
            return
        for tool in self.agent.tools:
            if tool.risk in ("HIGH", "CRITICAL") and tool.name not in covered:
                self.diags.append(Diagnostic(
                    "W101", "warning",
                    f"outil {tool.name}() de risque {tool.risk} sans politique",
                    tool.line))

    def _check_verification(self) -> None:
        from ._check_flow import unverified_actions
        for plan in self.agent.plans:
            for call in unverified_actions(self.agent, plan):
                self.diags.append(Diagnostic("W102", "warning",
                    f"plan {plan.name} : {call.call.name}() agit sur le monde sans "
                    "VERIFY postérieur lié à ses effets sur tous les chemins", call.line))

    def _refreshed_paths(self) -> Set[str]:
        """Chemins qu'une exécution peut mettre à jour : perception, inférence,
        effets d'outils, sorties d'outils, sorties de REASON."""
        out = {o.path for o in self.agent.observers}
        out |= {h.explains_path for h in self.agent.hypotheses if h.explains_path}
        for tool in self.agent.tools:
            out |= set(tool.outputs)
            for branch in tool.branches:
                out |= {e.path for e in branch.effects}
        for stmt, _ in self._all_statements():
            if isinstance(stmt, ReasonStmt):
                out |= set(stmt.produce)
            elif isinstance(stmt, DelegateStmt):
                out |= set(stmt.expect)
            elif isinstance(stmt, SetStmt):
                out.add(stmt.target)
        return out

    def _check_observability(self) -> None:
        used = self._referenced_paths()
        # Une observation qui recouvre un `EFFECT` déclaré **est** utilisée,
        # même si aucune expression ne la lit : c'est elle qui permet au
        # runtime de démentir la postcondition (registre de dérive, T9). La
        # signaler inutile pousserait à retirer précisément la perception qui
        # rend le modèle d'effets réfutable.
        used = used | {effect.path for tool in self.agent.tools
                       for branch in tool.branches
                       for effect in branch.effects if not effect.internal}
        observed = {o.path for o in self.agent.observers}
        refreshed = self._refreshed_paths()
        for path in sorted(observed - used):
            self.diags.append(Diagnostic(
                "W103", "warning", f"observation jamais utilisée : {path}"))
        for belief in self.agent.beliefs:
            if belief.path not in refreshed and belief.path in used:
                self.diags.append(Diagnostic(
                    "W104", "warning",
                    f"croyance jamais rafraîchie par OBSERVE : {belief.path}",
                    belief.line))

    def _check_scenarios(self) -> None:
        """Contrôles de bonne formation des critères d'acceptation (v1.4).

        Un scénario faux est pire qu'un scénario absent : il fait passer pour
        testé ce qui ne l'est pas. Les défauts visés sont donc ceux qui
        rendent un test **vert sans rien prouver**.
        """
        seen: Set[str] = set()
        for scenario in self.agent.scenarios:
            if scenario.name in seen:
                self.diags.append(Diagnostic(
                    "E004", "error",
                    f"scénario dupliqué : {scenario.name}", scenario.line))
            seen.add(scenario.name)

            if scenario.within < 1:
                self.diags.append(Diagnostic(
                    "E010", "error",
                    f"SCENARIO {scenario.name} : WITHIN {scenario.within} — "
                    f"il faut au moins un tick pour laisser l'agent agir",
                    scenario.line))

            # Un GIVEN qui pose un chemin que l'agent ne perçoit pas ne sera
            # jamais lu : le scénario teste alors autre chose que ce qu'il dit.
            from .scenario import ANSWER_PATH, APPROVAL_PATH
            known = self._known_state_paths() | {APPROVAL_PATH, ANSWER_PATH, "llm.plan"}
            for tool in self.agent.tools:
                known.add(f"scenario.outcome.{tool.name}")
                known |= {f"{tool.name}.{key}" for key in tool.outputs}
            for stmt, _ in self._all_statements():
                if isinstance(stmt, DelegateStmt):
                    known |= {f"{stmt.agent}.{key}" for key in stmt.expect}
            for effect in scenario.given:
                if effect.path not in known:
                    self.diags.append(Diagnostic(
                        "W117", "warning",
                        f"SCENARIO {scenario.name} : GIVEN pose "
                        f"{effect.path}, qu'aucun OBSERVE, BELIEF ni EFFECT "
                        f"ne fait exister", scenario.line))

            # Une attente ne portant sur aucun chemin qu'une action peut
            # produire est satisfaite (ou non) par l'énoncé seul.
            produced = {e.path for t in self.agent.tools
                        for br in t.branches for e in br.effects}
            for tool in self.agent.tools:
                produced |= set(tool.outputs) | {f"{tool.name}.{k}" for k in tool.outputs}
                produced |= {f"result.{tool.name}.{k}" for k in tool.outputs}
            for stmt, _ in self._all_statements():
                if isinstance(stmt, SetStmt):
                    produced.add(stmt.target)
                elif isinstance(stmt, ReasonStmt):
                    produced |= set(stmt.produce) | {f"reason.{k}" for k in stmt.produce}
                elif isinstance(stmt, DelegateStmt):
                    produced |= set(stmt.expect) | {f"{stmt.agent}.{k}" for k in stmt.expect}
            for assertion in scenario.assertions:
                if assertion.kind in ("CALL", "NEVER CALL", "BLOCKED") and self.agent.tool(assertion.target) is None:
                    self.diags.append(Diagnostic("E014", "error",
                        f"SCENARIO {scenario.name} : outil d'assertion inconnu : {assertion.target}", scenario.line))
                if assertion.kind == "EVENT" and not any(h.source == assertion.target for h in self.agent.events):
                    self.diags.append(Diagnostic("E014", "error",
                        f"SCENARIO {scenario.name} : événement d'assertion inconnu : {assertion.target}", scenario.line))
            for expectation in scenario.expect:
                paths: Set[str] = set()
                _collect_paths(expectation, paths)
                if not (paths & produced):
                    self.diags.append(Diagnostic(
                        "W118", "warning",
                        f"SCENARIO {scenario.name} : l'attente ne porte sur "
                        f"aucun chemin produit par EFFECT, OUTPUT, SET, REASON ou DELEGATE "
                        f"({', '.join(sorted(paths))}) — elle ne teste rien "
                        f"de l'agent", scenario.line))

    def _check_reachability(self) -> None:
        reachable: Set[str] = set()
        for plan in self.agent.plans:
            if plan.when is not None:
                reachable.add(plan.name)
        for stmt, _ in self._all_statements():
            if isinstance(stmt, ThenPlan):
                reachable.add(stmt.plan)
        if self.agent.decide and self.agent.decide.reason is not None:
            reachable.update(p.name for p in self.agent.plans)
        for plan in self.agent.plans:
            if plan.name not in reachable:
                self.diags.append(Diagnostic(
                    "W106", "warning",
                    f"plan inatteignable : {plan.name}", plan.line))

    def _check_hypotheses(self) -> None:
        from .bayes import reachable_range

        used = self._referenced_paths()
        for hyp in self.agent.hypotheses:
            for item in hyp.evidence:
                for label, value in (("LIKELIHOOD", item.likelihood),
                                     ("GIVEN_NOT", item.given_not)):
                    if not 0.0 < value < 1.0:
                        self.diags.append(Diagnostic(
                            "E006", "error",
                            f"{hyp.name} : {label} = {value} hors de ]0,1[",
                            item.line))
                if item.likelihood == item.given_not:
                    self.diags.append(Diagnostic(
                        "W107", "warning",
                        f"{hyp.name} : évidence non informative "
                        f"(P(e|h) = P(e|¬h) = {item.likelihood})", item.line))
            low, high = reachable_range(hyp)
            if hyp.threshold > high:
                self.diags.append(Diagnostic(
                    "W116", "warning",
                    f"{hyp.name} : THRESHOLD {hyp.threshold:g} hors d'atteinte "
                    f"— le postérieur plafonne à {high:.3f}, l'hypothèse ne "
                    f"pourra jamais être confirmée", hyp.line))
            elif hyp.threshold <= low:
                self.diags.append(Diagnostic(
                    "W116", "warning",
                    f"{hyp.name} : THRESHOLD {hyp.threshold:g} sous le plancher "
                    f"{low:.3f} — l'hypothèse est confirmée d'office",
                    hyp.line))
            consulted = (hyp.explains_path is not None
                         or any(p.startswith(hyp.name) or
                                p.startswith(f"hypothesis.{hyp.name}")
                                for p in used))
            if not consulted:
                self.diags.append(Diagnostic(
                    "W107", "warning",
                    f"hypothèse {hyp.name} : postérieur jamais consulté",
                    hyp.line))

    def _check_planner(self) -> None:
        spec = self.agent.planner
        if spec is None or not spec.enabled:
            return
        operators = [t for t in self.agent.tools if t.is_operator]
        if not operators:
            self.diags.append(Diagnostic(
                "W108", "warning",
                "PLANNER activé mais aucun outil ne déclare d'EFFECT",
                spec.line))
            return
        # Un opérateur dont un paramètre n'est ni lié ni homonyme d'un chemin
        # connu ne pourra jamais être instancié.
        known = self._referenced_paths() | self._refreshed_paths()
        for tool in operators:
            total = sum(o.probability for o in tool.outcomes)
            if tool.outcomes and abs(total - 1.0) > 1e-6:
                self.diags.append(Diagnostic(
                    "W113", "warning",
                    f"{tool.name}() : les OUTCOME totalisent {total:g} "
                    f"— renormalisés à 1", tool.line))
            for param in tool.inputs:
                path = tool.bindings.get(param, param)
                if path not in known:
                    self.diags.append(Diagnostic(
                        "W109", "warning",
                        f"{tool.name}() : paramètre `{param}` non liable "
                        f"(chemin `{path}` inconnu) — ajouter BIND", tool.line))
        # Chaque but doit être touché par au moins un EFFECT déclaré.
        produced = {e.path for t in operators
                    for b in t.branches for e in b.effects}
        for goal in (spec.achieve or []):
            touched: Set[str] = set()
            _collect_paths(goal, touched)
            if not (touched & produced):
                self.diags.append(Diagnostic(
                    "W110", "warning",
                    "but du planificateur qu'aucun EFFECT ne peut satisfaire",
                    goal.line))

    # ------------------------------------------------ v1.1 / v1.2 : sûreté
    def _decision_paths(self) -> Set[str]:
        """Chemins dont dépend une décision d'autorisation ou d'action.

        Gardes de politique et préconditions d'opérateur : ce sont les deux
        endroits où une valeur mal calibrée cesse d'être un défaut d'affichage
        pour devenir un défaut de sûreté.
        """
        paths: Set[str] = set()
        for rule in self.agent.policies:
            _collect_paths(rule.guard, paths)
        for tool in self.agent.tools:
            _collect_paths(tool.requires, paths)
        return paths

    def _threshold_paths(self) -> Set[str]:
        """Chemins comparés à un **seuil ordonné** dans une décision.

        Une valeur hors domaine ne devient dangereuse que si elle peut
        franchir un seuil. `host != unknown` est un test de présence : une
        chaîne inattendue ne le retourne pas. `confidence >= 0.9`, si.
        """
        paths: Set[str] = set()
        sources = [r.guard for r in self.agent.policies]
        sources += [t.requires for t in self.agent.tools]
        for node in sources:
            _collect_ordered(node, paths)
        return paths

    def _check_shared_keys(self) -> None:
        declared = set(self.agent.memory.shared)
        for write in self.agent.memory.writes:
            if write.into != "SHARED":
                continue
            if not declared:
                self.diags.append(Diagnostic(
                    "E008", "error",
                    f"écriture dans SHARED.{write.key} sans compartiment "
                    f"SHARED déclaré", write.line))
            elif write.key not in declared:
                self.diags.append(Diagnostic(
                    "E008", "error",
                    f"clé partagée non déclarée : SHARED.{write.key} "
                    f"(déclarées : {', '.join(sorted(declared))})",
                    write.line))

    def _check_decision_inputs(self) -> None:
        decisive = self._decision_paths()

        # W114 — une probabilité issue d'évidences potentiellement corrélées
        # ne devrait pas garder une autorisation sans garde-fou déclaré.
        for hyp in self.agent.hypotheses:
            consulted = any(p == hyp.name or p.startswith(f"{hyp.name}.")
                            or p.startswith(f"hypothesis.{hyp.name}")
                            for p in decisive)
            if not consulted:
                continue
            if len(hyp.evidence) < 3:
                continue
            if hyp.groups or hyp.max_evidence is not None:
                continue
            self.diags.append(Diagnostic(
                "W114", "warning",
                f"{hyp.name} garde une autorisation avec "
                f"{len(hyp.evidence)} évidences supposées indépendantes — "
                f"déclarer un GROUP ou un MAX_EVIDENCE, sinon le postérieur "
                f"est surestimé et le seuil franchi à tort", hyp.line))

        # W115 — une sortie de LLM non bornée qui atteint un *seuil*.
        thresholds = self._threshold_paths()
        for stmt, where in self._all_statements():
            if not isinstance(stmt, ReasonStmt):
                continue
            for field, typ in stmt.produce.items():
                if field not in thresholds or field in stmt.domains:
                    continue
                self.diags.append(Diagnostic(
                    "W115", "warning",
                    f"`{field}: {typ}` produit par REASON dans {where} "
                    f"alimente une décision sans domaine borné — ajouter "
                    f"`IN [...]`, un type ne borne pas une valeur", stmt.line))

        # W133 — le piège qui coûte le plus cher en production, parce qu'il
        # ne fait aucun bruit : au-delà d'une dizaine de champs, un modèle à
        # raisonnement dépasse son budget de sortie, la réponse est tronquée,
        # et *tous* les champs retombent sur leur défaut. Un REASON numérique
        # rend alors des zéros parfaitement plausibles. Le runtime pose
        # désormais `reason.degraded`, mais mieux vaut ne pas y arriver :
        # découper le REASON en deux, ou réduire le schéma.
        for stmt, where in self._all_statements():
            if not isinstance(stmt, ReasonStmt):
                continue
            if len(stmt.produce) > self.PRODUCE_FIELD_LIMIT:
                self.diags.append(Diagnostic(
                    "W133", "warning",
                    f"REASON dans {where} déclare {len(stmt.produce)} champs "
                    f"PRODUCE (seuil {self.PRODUCE_FIELD_LIMIT}) — au-delà, une "
                    f"réponse tronquée fait retomber TOUS les champs sur leur "
                    f"défaut en silence ; découper le REASON ou réduire le "
                    f"schéma, et garder les seuils par `reason.degraded`",
                    stmt.line))

        self._check_default_is_harmless()

    def _check_default_is_harmless(self) -> None:
        """W134 — le `DEFAULT` doit **déclencher** l'interdit qu'il garde.

        Le premier principe du langage veut qu'un modèle hors format retombe
        sur un défaut *inoffensif*. Jusqu'ici la règle était énoncée dans le
        SKILL et vérifiée nulle part — or c'est précisément le chemin que
        prend une panne : réponse tronquée, JSON invalide, oracle muet, tous
        aboutissent au même endroit, et `reason_degraded` ne fait que le
        compter après coup.

        Le contrôle est une simulation, pas une heuristique : on lie le champ
        à son défaut, on laisse tout le reste indéterminé, et on évalue la
        garde. Un `NEVER` qui ressort **définitivement faux** dans ces
        conditions est un interdit que la panne désarme. Une garde qui ressort
        indéterminée s'applique (fail-closed) et ne dit rien — on se tait,
        parce qu'un contrôle bruyant finit ignoré.
        """
        from .state import Evaluator, State
        from .trivalent import UNKNOWN, evaluate

        dangerous = [rule for rule in self.agent.policies
                     if applies_when_unknown(rule.effect) and rule.guard]
        if not dangerous:
            return

        for stmt, where in self._all_statements():
            if not isinstance(stmt, ReasonStmt):
                continue
            for field in stmt.produce:
                default = stmt.defaults.get(field)
                if default is None:
                    guarded = [rule for rule in dangerous
                               if self._guard_mentions(rule.guard, field)]
                    if guarded:
                        targets = ", ".join(
                            f"{rule.effect} {rule.target} ligne {rule.line}"
                            for rule in guarded)
                        self.diags.append(Diagnostic(
                            "W134", "warning",
                            f"`{field}` produit par REASON dans {where} "
                            f"garde {targets} sans DEFAULT explicite : une "
                            f"réponse absente ou numérique non finie sera "
                            f"indéterminée et fermera l'action, mais le "
                            f"contrat de panne n'est pas déclaré — ajouter "
                            f"un DEFAULT qui déclenche l'interdit",
                            stmt.line))
                    continue
                for rule in dangerous:
                    if not self._guard_mentions(rule.guard, field):
                        continue
                    state = State()
                    probe = Evaluator(state)
                    try:
                        value = probe.eval(default)
                        state.set_local(field, value)
                        state.set_local(f"reason.{field}", value)
                        if field == "confidence":
                            state.set_local("action.confidence", value)
                        verdict = evaluate(Evaluator(state), rule.guard)
                    except Exception:  # pragma: no cover - défaut non évaluable
                        continue
                    if verdict is UNKNOWN or verdict:
                        continue
                    self.diags.append(Diagnostic(
                        "W134", "warning",
                        f"`{field}` produit par REASON dans {where} retombe "
                        f"sur un DEFAULT qui ne déclenche pas "
                        f"`{rule.effect} {rule.target}` ligne {rule.line} : "
                        f"une réponse tronquée, un JSON invalide ou un oracle "
                        f"muet ouvriront l'action au lieu de la fermer — "
                        f"choisir un défaut que l'interdit rejette",
                        stmt.line))

    @staticmethod
    def _guard_mentions(guard: Node, field: str) -> bool:
        """Le champ apparaît-il comme chemin nu dans cette garde ?

        Un `PRODUCE` pose un local sous son nom seul ; un chemin pointé
        désigne autre chose et ne doit pas déclencher le contrôle.
        """
        aliases = ([field], ["reason", field])
        if field == "confidence":
            aliases += (["action", "confidence"],)
        return any(isinstance(node, PathExpr) and node.parts in aliases
                   for node in _walk_expr(guard))

    def _check_security_authoring(self) -> None:
        """Provenance, approbation, réobservation et clôture sûre (v1.5)."""
        tools = {tool.name: tool for tool in self.agent.tools}
        risky = {tool.name for tool in self.agent.tools
                 if tool.risk in ("HIGH", "CRITICAL")}

        # W120 — être « couvert par une politique » ne signifie pas être
        # approuvé. Les actions à fort impact ont besoin de l'humain.
        approved = {rule.target for rule in self.agent.policies
                    if rule.effect == "REQUIRE_APPROVAL" and rule.guard is None}
        for name in sorted(risky):
            if name not in approved and "*" not in approved:
                self.diags.append(Diagnostic(
                    "W120", "warning", f"outil {name}() à risque "
                    f"{tools[name].risk} sans REQUIRE APPROVAL inconditionnelle", tools[name].line))

        observed = {observer.path for observer in self.agent.observers}
        produced_by_effect = {effect.path for tool in self.agent.tools
                              for branch in tool.branches
                              for effect in branch.effects}
        # W121 — un VERIFY doit pouvoir forcer une re-perception. Sinon il ne
        # lit que la promesse de l'outil qui vient d'agir.
        for stmt, where in self._all_statements():
            if not isinstance(stmt, VerifyStmt):
                continue
            paths: Set[str] = set(); _collect_paths(stmt.cond, paths)
            self_only = (paths & produced_by_effect) - observed
            if self_only:
                self.diags.append(Diagnostic(
                    "W121", "warning", f"VERIFY dans {where} lit un EFFECT "
                    f"sans OBSERVE post-action : {', '.join(sorted(self_only))}",
                    stmt.line))

        for plan in self.agent.plans:
            stmts = list(self._plan_statements(plan))
            reasons = [stmt for stmt in stmts if isinstance(stmt, ReasonStmt)]
            llm_fields = {alias for stmt in reasons for field in stmt.produce
                          for alias in (field, f"reason.{field}")}
            # Propagation conservative des aliases SET, sans dépendre du nom
            # choisi pour l'argument de l'outil.
            changed = True
            while changed:
                before = set(llm_fields)
                for stmt in stmts:
                    if isinstance(stmt, SetStmt):
                        paths = set(); _collect_paths(stmt.value, paths)
                        if paths & llm_fields:
                            llm_fields.add(stmt.target)
                changed = before != llm_fields
            raw_reason = any(any(token in path.lower()
                                 for token in ("raw", "log", "message", "body", "content"))
                             for stmt in reasons for path in stmt.using)

            # W119 — une cible produite par le modèle n'est acceptable que si
            # un paramètre distinct atteste explicitement cette cible.
            for stmt in stmts:
                if not isinstance(stmt, CallStmt) or stmt.call.name not in risky:
                    continue
                tool = tools[stmt.call.name]
                args = _call_arguments(stmt.call, tool)
                attested = set(tool.attestations.values())
                for param, expr in args.items():
                    # Signal de cible (heuristique), pas preuve sémantique :
                    # les paramètres de contenu tels que body ne sont pas des cibles.
                    target_words = ("target", "host", "ip", "pid", "user", "account",
                                    "file", "path", "recipient", "address", "resource",
                                    "device", "object", "entity", "vm", "container",
                                    "key", "id", "repository")
                    if not any(word in param.lower() for word in target_words):
                        continue
                    paths: Set[str] = set(); _collect_paths(expr, paths)
                    tainted = paths & llm_fields
                    if tainted and param not in attested:
                        self.diags.append(Diagnostic(
                            "W119", "warning", f"{stmt.call.name}().{param} "
                            f"reçoit une sortie LLM ({', '.join(sorted(tainted))}) "
                            f"sans paramètre ATTESTS {param}", stmt.line))

            # W125 — un flux LLM issu de texte brut qui atteint une action
            # risquée exige une garde explicite sur l'injection ou la confiance.
            if raw_reason:
                for call, guards in _guarded_calls(
                        [step_stmt for step in plan.steps for step_stmt in step.body]):
                    if call.call.name not in risky:
                        continue
                    flow: Set[str] = set()
                    for expr in _stmt_exprs(call): _collect_paths(expr, flow)
                    for guard in guards: _collect_paths(guard, flow)
                    if not (flow & llm_fields):
                        continue
                    from ._check_flow import protective_guard
                    if not any(protective_guard(guard) for guard in guards):
                        self.diags.append(Diagnostic(
                            "W125", "warning", f"{call.call.name}() suit un "
                            f"REASON sur texte non fiable sans garde d'injection",
                            call.line))

        # W122 — postérieur global + cible issue de l'élément courant.
        probabilistic_allows = set()
        hypotheses = {h.name for h in self.agent.hypotheses}
        for rule in self.agent.policies:
            if rule.effect != "ALLOW":
                continue
            paths: Set[str] = set(); _collect_paths(rule.guard, paths)
            if paths & hypotheses:
                probabilistic_allows.add(rule.target)
        for stmt, _ in self._all_statements():
            if not isinstance(stmt, ForEachStmt):
                continue
            for nested in _walk_stmts(stmt.body):
                if not isinstance(nested, CallStmt) or nested.call.name not in probabilistic_allows:
                    continue
                paths: Set[str] = set()
                for expr in _stmt_exprs(nested): _collect_paths(expr, paths)
                tool = tools[nested.call.name]
                arguments = _call_arguments(nested.call, tool)
                correlated = False
                for proof, target in tool.attestations.items():
                    if proof in arguments and target in arguments:
                        target_paths: Set[str] = set(); _collect_paths(arguments[target], target_paths)
                        correlated |= any(path.startswith(stmt.var + ".") for path in target_paths)
                if any(path.startswith(stmt.var + ".") for path in paths) and not correlated:
                    self.diags.append(Diagnostic(
                        "W122", "warning", f"{nested.call.name}() cible "
                        f"{stmt.var} mais son ALLOW dépend d'une hypothèse globale — "
                        f"corréler la preuve à l'élément", nested.line))

        # W123 — si le programme connaît un backlog non résolu, toute action
        # qui satisfait la condition de terminaison globale doit être
        # autorisée sous une garde qui le consulte. Une postcondition métier
        # telle que `rollback.done` n'est donc pas confondue avec la clôture.
        pending = {path for path in observed if any(word in path.lower()
                   for word in ("unresolved", "pending", "remaining"))}
        termination: Set[str] = set()
        if self.agent.loop is not None:
            _collect_paths(self.agent.loop.until, termination)
        for tool in self.agent.tools:
            closes = any(effect.path in termination
                         for branch in tool.branches for effect in branch.effects)
            if not closes or not pending:
                continue
            guards = [rule.guard for rule in self.agent.policies
                      if rule.effect == "ALLOW" and rule.target in (tool.name, "*")]
            guarded: Set[str] = set()
            for guard in guards: _collect_paths(guard, guarded)
            if not (guarded & pending):
                self.diags.append(Diagnostic(
                    "W123", "warning", f"{tool.name}() clôt le cycle sans "
                    f"garde sur {', '.join(sorted(pending))}", tool.line))

        # W124 — un scénario de sûreté doit toucher une postcondition de
        # chaque capacité à fort impact.
        scenario_paths: Set[str] = set()
        for scenario in self.agent.scenarios:
            for expect in scenario.expect: _collect_paths(expect, scenario_paths)
        for name in sorted(risky):
            effects = {effect.path for branch in tools[name].branches
                       for effect in branch.effects}
            if not effects or not (effects & scenario_paths):
                self.diags.append(Diagnostic(
                    "W124", "warning", f"outil risqué {name}() sans SCENARIO "
                    f"portant sur l'une de ses postconditions", tools[name].line))

    # --------------------------------------------------------------- parcours
    def _plan_statements(self, plan: Plan):
        for step in plan.steps:
            yield from _walk_stmts(step.body)

    def _all_statements(self):
        for plan in self.agent.plans:
            for stmt in self._plan_statements(plan):
                yield stmt, plan.name
        for event in self.agent.events:
            for stmt in _walk_stmts(event.body):
                yield stmt, f"EVENT {event.source}"
        for handler in self.agent.messages:
            for stmt in _walk_stmts(handler.body):
                yield stmt, f"ON MESSAGE {handler.name}"
        if self.agent.decide:
            for rule in self.agent.decide.rules:
                for stmt in _walk_stmts([rule]):
                    yield stmt, "DECIDE"
        for stmt in _walk_stmts(self.agent.on_verify_fail):
            yield stmt, "ON VERIFY.FAIL"
        if self.agent.loop:
            for stmt in _walk_stmts(self.agent.loop.body):
                yield stmt, "LOOP"

    def _referenced_paths(self) -> Set[str]:
        paths: Set[str] = set()
        for goal in self.agent.goals:
            _collect_paths(goal.condition, paths)
            for target in goal.targets:
                _collect_paths(target, paths)
        for term in self.agent.utility:
            _collect_paths(term.condition, paths)
        if self.agent.planner:
            for achieve in self.agent.planner.achieve:
                _collect_paths(achieve, paths)
        for hyp in self.agent.hypotheses:
            for item in hyp.evidence:
                _collect_paths(item.test, paths)
        for tool in self.agent.tools:
            # Un chemin lié par BIND est consommé par le planificateur.
            paths.update(tool.bindings.values())
            _collect_paths(tool.requires, paths)
            for branch in tool.branches:
                for effect in branch.effects:
                    _collect_paths(effect.value, paths)
        for obs in self.agent.observers:
            _collect_paths(obs.when, paths)
        for plan in self.agent.plans:
            _collect_paths(plan.when, paths)
        for rule in self.agent.policies:
            _collect_paths(rule.guard, paths)
        for event in self.agent.events:
            _collect_paths(event.when, paths)
        for handler in self.agent.messages:
            _collect_paths(handler.when, paths)
        for write in self.agent.memory.writes:
            _collect_paths(write.when, paths)
            paths.update(write.store)
        for stmt, _ in self._all_statements():
            for node in _stmt_exprs(stmt):
                _collect_paths(node, paths)
            if isinstance(stmt, ReasonStmt):
                paths.update(stmt.using)
            if isinstance(stmt, DelegateStmt):
                paths.update(stmt.inputs)
        # un chemin parent compte comme utilisé si un enfant l'est
        expanded = set(paths)
        for path in paths:
            parts = path.split(".")
            for cut in range(1, len(parts)):
                expanded.add(".".join(parts[:cut]))
        return expanded


def check_program(program: Program) -> List[Diagnostic]:
    """Contrôles qui ne peuvent se faire qu'à l'échelle du programme entier.

    Un `MESSAGE` est un contrat entre deux agents : ni l'émetteur ni le
    destinataire ne peut le vérifier seul.
    """
    diags: List[Diagnostic] = []
    names = {}
    for agent in program.agents:
        key = agent.name.lower()
        if key in names:
            diags.append(Diagnostic("E012", "error",
                f"nom d'AGENT dupliqué (sans distinction de casse) : {agent.name}", agent.line))
        names[key] = agent
    sent: Set[str] = set()
    handled: Set[str] = set()

    for agent in program.agents:
        handled |= {h.name for h in agent.messages}
        for stmt in _all_agent_statements(agent):
            if not isinstance(stmt, MessageStmt):
                continue
            sent.add(stmt.name)
            if stmt.broadcast:
                continue
            if stmt.to is None:
                diags.append(Diagnostic(
                    "E007", "error",
                    f"MESSAGE {stmt.name} sans TO ni BROADCAST", stmt.line))
            elif stmt.to.lower() not in names:
                diags.append(Diagnostic(
                    "E007", "error",
                    f"MESSAGE {stmt.name} adressé à un agent inconnu : "
                    f"{stmt.to}", stmt.line))

    for name in sorted(sent - handled):
        diags.append(Diagnostic(
            "W111", "warning", f"message émis que personne ne reçoit : {name}"))
    for name in sorted(handled - sent):
        diags.append(Diagnostic(
            "W112", "warning", f"message attendu que personne n'émet : {name}"))
    return diags


def _all_agent_statements(agent: Agent):
    for stmt, _ in Analyzer(agent)._all_statements():
        yield stmt


def _walk_stmts(stmts):
    for stmt in stmts:
        yield stmt
        if isinstance(stmt, IfStmt):
            yield from _walk_stmts(stmt.then)
            yield from _walk_stmts(stmt.otherwise)
        elif isinstance(stmt, VerifyStmt):
            yield from _walk_stmts(stmt.on_fail)
        elif isinstance(stmt, (LoopStmt, ForEachStmt)):
            yield from _walk_stmts(stmt.body)


def _walk_expr(node):
    """Parcourt un arbre d'expression, nœud par nœud."""
    if node is None:
        return
    yield node
    for child in ("left", "right", "operand"):
        if hasattr(node, child):
            yield from _walk_expr(getattr(node, child))
    if isinstance(node, ListExpr):
        for item in node.items:
            yield from _walk_expr(item)
    if isinstance(node, CallExpr):
        for item in node.args:
            yield from _walk_expr(item)
        for item in node.kwargs.values():
            yield from _walk_expr(item)



def _call_arguments(call: CallExpr, tool) -> dict:
    names = list(tool.inputs)
    out = {names[index]: expr for index, expr in enumerate(call.args)
           if index < len(names)}
    out.update(call.kwargs)
    return out


def _guarded_calls(stmts, guards=()):
    for stmt in stmts:
        if isinstance(stmt, CallStmt):
            yield stmt, guards
        elif isinstance(stmt, IfStmt):
            yield from _guarded_calls(stmt.then, guards + (stmt.cond,))
            yield from _guarded_calls(stmt.otherwise, guards + (UnOp("NOT", stmt.cond),))
        elif isinstance(stmt, (ForEachStmt, LoopStmt)):
            yield from _guarded_calls(stmt.body, guards)
        elif isinstance(stmt, VerifyStmt):
            yield from _guarded_calls(stmt.on_fail, guards)

def _stmt_exprs(stmt: Stmt):
    if isinstance(stmt, CallStmt):
        yield from stmt.call.args
        yield from stmt.call.kwargs.values()
    elif isinstance(stmt, SetStmt):
        yield stmt.value
    elif isinstance(stmt, IfStmt):
        yield stmt.cond
    elif isinstance(stmt, VerifyStmt):
        yield stmt.cond
    elif isinstance(stmt, LoopStmt):
        if stmt.until is not None:
            yield stmt.until
    elif isinstance(stmt, ForEachStmt):
        yield stmt.source
    elif isinstance(stmt, MessageStmt):
        yield from stmt.payload.values()


def _collect_ordered(node, out: Set[str]) -> None:
    """Chemins figurant dans une comparaison d'ordre (`>`, `>=`, `<`, `<=`)."""
    if node is None:
        return
    if isinstance(node, BinOp):
        if node.op in (">", ">=", "<", "<="):
            for side in (node.left, node.right):
                if isinstance(side, PathExpr):
                    out.add(side.dotted)
        _collect_ordered(node.left, out)
        _collect_ordered(node.right, out)
    elif isinstance(node, UnOp):
        _collect_ordered(node.operand, out)


def _collect_paths(node, out: Set[str]) -> None:
    if node is None:
        return
    if isinstance(node, PathExpr):
        out.add(node.dotted)
    elif isinstance(node, BinOp):
        _collect_paths(node.left, out)
        _collect_paths(node.right, out)
    elif isinstance(node, UnOp):
        _collect_paths(node.operand, out)
    elif isinstance(node, ListExpr):
        for item in node.items:
            _collect_paths(item, out)
    elif isinstance(node, CallExpr):
        for item in node.args:
            _collect_paths(item, out)
        for item in node.kwargs.values():
            _collect_paths(item, out)

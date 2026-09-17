"""Vérificateur hors ligne (AGENT-L v1.0).

L'analyseur statique (`analyzer.py`) vérifie la *bonne formation* d'un
programme : outils déclarés, plans atteignables, noms cohérents. Le
vérificateur s'attaque à autre chose — des propriétés de **sûreté** portant
sur l'ensemble des exécutions possibles, établies avant tout appel de modèle.

Huit théorèmes portant sur un agent (T8, la vivacité d'une société,
porte sur un programme entier — voir `liveness.py`).

  T1  Aucun appel interdit n'aboutit.
      Garanti par construction : tout appel traverse le moteur de politiques.
      Ce que le vérificateur apporte, c'est la carte des sites d'appel où
      l'agent *tentera* l'action et se fera bloquer — donc calera.

  T2  Sous chaque interdit, le but reste atteignable ou l'escalade est
      déclarée. Pour chaque `NEVER`, on suppose sa garde vraie, on retire
      l'action correspondante, et on cherche une route dans l'espace des
      opérateurs déclarés. Depuis la v1.4 la recherche va jusqu'à son **point
      fixe** — l'espace d'états est fini, les `EFFECT` étant des affectations
      ground — de sorte qu'une absence de route est *démontrée* et non plus
      seulement constatée. Ce qui est réfuté n'est pas l'absence de route mais
      le fait de **caler en silence** : un agent qui déclare une escalade
      (`IF planner.exhausted … THEN <plan>`) fait ce qu'on attend de lui.

  T3  Aucune capacité n'est morte.
      Un outil déclaré mais qu'aucune politique n'autorise, ou appelé
      uniquement depuis des branches contradictoires, est un mensonge dans
      la spécification de l'agent. Depuis la v1.1 on y ajoute les seuils
      probabilistes inatteignables : une garde `P(h) >= 0.95` est morte si
      les vraisemblances déclarées plafonnent le postérieur à 0,93.

  T4  La surface exposée au LLM est bornée.
      Si `DECIDE.REASON` existe, le modèle peut sélectionner n'importe quel
      plan déclaré. On énumère les outils qu'il atteint ainsi et l'on
      signale ceux qui sont à risque élevé sans garde d'état.

  T5  Les attentes déclarées sont atteignables. (v1.4)
      Les quatre premiers prouvent des propriétés génériques ; T5 est le seul
      à prouver quelque chose que *l'auteur* a exigé. Chaque `SCENARIO` pose
      un état (`GIVEN`) et une attente (`EXPECT`) ; on cherche une route sous
      les politiques. Une attente déjà vraie sous l'énoncé est un invariant :
      hors de portée d'une recherche d'atteignabilité, elle est renvoyée à
      `agentl test`.

  T6  Toute donnée issue du LLM qui pilote une action risquée possède une
      provenance attestée et corrélée à sa cible ; le texte non fiable est
      gardé avant d'atteindre une capacité à fort impact.

  T7  Une transition de terminaison ne peut pas ignorer un travail observé
      comme encore non résolu.

  T9  Le modèle d'effets est réfutable. (v1.7)
      Tout le reste raisonne sur des `EFFECT` écrits à la main. Un `EFFECT`
      qu'aucune `OBSERVE` ne recouvre ne peut jamais être démenti — ni par le
      vérificateur, qui ne connaît pas le monde, ni par le runtime, qui n'en
      recevra aucune perception. La postcondition n'est pas fausse : elle est
      hors du domaine de la preuve. (T8 démontre la vivacité d'une société
      d'agents et porte sur un programme entier, pas sur un agent.)

**Direction de sûreté.** Le solveur sous-jacent ne déclare une formule
insatisfiable que sur démonstration (cf. `solver.py`). Le vérificateur hérite
de cette propriété : il peut manquer un défaut, il n'en invente pas.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import (Any, Dict, FrozenSet, List, Optional, Sequence, Set,
                    Tuple)

from .analyzer import Analyzer
from .core import Symbol
from .nodes import (Agent, AskStmt, BinOp, CallStmt, DelegateStmt, ForEachStmt,
                    IfStmt, Literal, LoopStmt, Node, PathExpr, Plan,
                    PolicyRule, ReasonStmt, SetStmt, Stmt, ThenPlan, ToolDecl,
                    VerifyStmt)
from .solver import MAX_CLAUSES, entails, satisfiable, watch_overflow

MAX_ENTRY_DEPTH = 4


# --------------------------------------------------------------------------
# Structures de rapport
# --------------------------------------------------------------------------
@dataclass
class Finding:
    code: str
    severity: str            # error | warning | info
    title: str
    detail: str = ""
    line: int = 0

    def render(self) -> str:
        mark = {"error": "✗", "warning": "!", "info": "·"}[self.severity]
        where = f"l.{self.line}" if self.line else "—"
        head = f"  {mark} {self.code} {where:>7}  {self.title}"
        return head + (f"\n              {self.detail}" if self.detail else "")


@dataclass
class Theorem:
    key: str
    title: str
    holds: Optional[bool] = None      # None = indéterminé (borné, non prouvé)
    summary: str = ""
    findings: List[Finding] = field(default_factory=list)

    def render(self) -> str:
        badge = {True: "✔ DÉMONTRÉ", False: "✘ RÉFUTÉ",
                 None: "◐ BORNÉ"}[self.holds]
        lines = [f"\n{self.key} — {self.title}", f"  {badge} · {self.summary}"]
        lines += [f.render() for f in self.findings]
        return "\n".join(lines)


@dataclass
class Report:
    agent: str
    theorems: List[Theorem] = field(default_factory=list)

    @property
    def refuted(self) -> List[Theorem]:
        return [t for t in self.theorems if t.holds is False]

    @property
    def findings(self) -> List[Finding]:
        return [f for t in self.theorems for f in t.findings]

    def render(self) -> str:
        head = (f"╔══ vérification de {self.agent} " +
                "═" * max(0, 46 - len(self.agent)))
        body = "\n".join(t.render() for t in self.theorems)
        errors = sum(1 for f in self.findings if f.severity == "error")
        warns = sum(1 for f in self.findings if f.severity == "warning")
        proved = sum(1 for t in self.theorems if t.holds is True)
        tail = (f"\n╚══ {proved}/{len(self.theorems)} théorèmes démontrés · "
                f"{errors} erreur(s), {warns} avertissement(s)")
        return head + "\n" + body + tail


# --------------------------------------------------------------------------
# Sites d'appel et conditions de chemin
# --------------------------------------------------------------------------
@dataclass
class CallSite:
    tool: str
    plan: str
    line: int
    entry: List[Node] = field(default_factory=list)   # conditions d'entrée
    local: List[Node] = field(default_factory=list)   # gardes internes
    origin: str = ""
    has_fallback: bool = False

    @property
    def conditions(self) -> List[Node]:
        return self.entry + self.local

    def render_conditions(self) -> str:
        from .runtime import _render_expr
        parts = [_render_expr(c) for c in self.conditions]
        return " ∧ ".join(parts) if parts else "⊤ (aucune condition)"


#: Profondeur par défaut de la recherche de route. Ce n'est plus une borne de
#: *complétude* depuis la v1.4 mais un garde-fou : l'espace d'états est fini —
#: les `EFFECT` sont des affectations ground — donc la recherche atteint
#: normalement son point fixe bien avant. Le voir atteint est ce qui autorise
#: à réfuter une atteignabilité au lieu de rendre « ◐ BORNÉ ».
DEFAULT_DEPTH = 64

#: Budget d'états distincts. Au-delà, on préfère « non prouvé » à un temps de
#: calcul non borné — la direction de sûreté impose de ne jamais conclure sur
#: une recherche tronquée.
DEFAULT_MAX_NODES = 20000


class Verifier:
    def __init__(self, agent: Agent, depth: int = DEFAULT_DEPTH,
                 max_nodes: int = DEFAULT_MAX_NODES):
        self.agent = agent
        self.depth = depth
        self.max_nodes = max_nodes
        self.never_rules = [r for r in agent.policies if r.effect == "NEVER"]
        self.operators = [t for t in agent.tools if t.is_operator]
        self._entries: Optional[Dict[str, List[Tuple[List[Node], str]]]] = None
        # Partition essentielle : un chemin qu'un EFFECT peut produire est
        # sous le contrôle de l'agent ; les autres relèvent du monde. Pour
        # l'atteignabilité, seuls les premiers doivent être *établis* par une
        # action — les seconds sont librement supposés favorables.
        self.controlled: Set[str] = {
            effect.path for tool in self.operators
            for branch in tool.branches for effect in branch.effects}
        # …et un chemin perçu ou déclaré en BELIEF possède déjà une valeur
        # initiale : sa précondition peut être vraie sans qu'aucune action ne
        # l'ait établie. Seuls les chemins *uniquement* produits par un EFFECT
        # doivent être conquis par le plan.
        self.given: Set[str] = ({o.path for o in agent.observers}
                                | {b.path for b in agent.beliefs})
        self.must_establish: Set[str] = self.controlled - self.given
        self.escalation: Optional[str] = self._declared_escalation()
        # Ce qui rend le parcours des plans sensible au flot : quelles
        # écritures d'état tiennent jusqu'au point d'appel, et lesquelles le
        # monde peut démentir entre-temps (cf. `_FlowCtx`).
        self.flow_ctx = _FlowCtx(agent)

    def _declared_escalation(self) -> Optional[str]:
        """Nom du plan que l'agent déclenche quand plus aucune route n'existe.

        `planner.exhausted` est l'idiome du langage pour « le planificateur n'a
        rien trouvé de permis » : une règle `DECIDE` qui le lit et déclenche un
        plan **est** la déclaration d'escalade. `ON VERIFY.FAIL` en est la
        seconde forme.

        Cette distinction est ce qui sépare, sous un interdit sans repli, un
        agent qui rend la main d'un agent qui cale en silence — le premier fait
        ce qu'on attend de lui, le second est un défaut.
        """
        if self.agent.decide is not None:
            for rule in self.agent.decide.rules:
                if "planner.exhausted" not in _referenced_paths(rule.cond):
                    continue
                for target in _plan_targets(rule.then):
                    return target
        for target in _plan_targets(self.agent.on_verify_fail):
            return target
        return None

    # ------------------------------------------------------------------ API
    def run(self) -> Report:
        report = Report(self.agent.name)
        report.theorems.append(self.theorem_forbidden())
        report.theorems.append(self.theorem_reachability())
        report.theorems.append(self.theorem_dead_capabilities())
        report.theorems.append(self.theorem_llm_surface())
        report.theorems.append(self.theorem_scenarios())
        report.theorems.append(self.theorem_provenance())
        report.theorems.append(self.theorem_termination_liveness())
        report.theorems.append(self.theorem_effect_falsifiability())
        return report

    def _security_theorem(self, key: str, title: str,
                          codes: Set[str]) -> Theorem:
        theorem = Theorem(key, title)
        diagnostics = [d for d in Analyzer(self.agent).run()
                       if d.code in codes]
        theorem.holds = not diagnostics
        theorem.summary = ("aucune rupture détectée" if not diagnostics else
                           f"{len(diagnostics)} rupture(s) de garantie")
        for diagnostic in diagnostics:
            theorem.findings.append(Finding(
                "V" + diagnostic.code[1:], "error", diagnostic.message,
                "corriger le diagnostic de check correspondant avant "
                "d'exécuter l'agent", diagnostic.line))
        if not diagnostics:
            theorem.findings.append(Finding(
                "V125" if key == "T6" else "V126", "info",
                "garantie statique satisfaite"))
        return theorem

    def theorem_provenance(self) -> Theorem:
        return self._security_theorem(
            "T6", "Toute action risquée possède une provenance corrélée",
            {"W119", "W121", "W122", "W125"})

    def theorem_termination_liveness(self) -> Theorem:
        return self._security_theorem(
            "T7", "La terminaison préserve le travail non résolu",
            {"W123"})

    # -------------------------------------------------- conditions d'entrée
    def entry_conditions(self) -> Dict[str, List[Tuple[List[Node], str]]]:
        """Comment chaque plan peut être atteint, et sous quelle condition."""
        if self._entries is not None:
            return self._entries

        direct: Dict[str, List[Tuple[List[Node], str]]] = {
            p.name: [] for p in self.agent.plans}

        for plan in self.agent.plans:
            if plan.when is not None:
                direct[plan.name].append(([plan.when], "garde WHEN"))
        for event in self.agent.events:
            for target in _plan_targets(event.body):
                if target in direct:
                    conds = [event.when] if event.when is not None else []
                    direct[target].append((conds, f"EVENT {event.source}"))
        for handler in self.agent.messages:
            for target in _plan_targets(handler.body):
                if target in direct:
                    conds = [handler.when] if handler.when is not None else []
                    direct[target].append((conds, f"ON MESSAGE {handler.name}"))
        if self.agent.decide:
            for rule in self.agent.decide.rules:
                for target in _plan_targets(rule.then):
                    if target in direct:
                        direct[target].append(([rule.cond], "règle DECIDE"))
            if self.agent.decide.reason is not None:
                # Le LLM choisit parmi *tous* les plans déclarés : la
                # condition d'entrée est vide. C'est le fait le plus
                # important que produise cette analyse.
                for name in direct:
                    direct[name].append(([], "sélection LLM"))
        for target in _plan_targets(self.agent.on_verify_fail):
            if target in direct:
                direct[target].append(([], "ON VERIFY.FAIL"))
        if self.agent.loop:
            for target in _plan_targets(self.agent.loop.body):
                if target in direct:
                    direct[target].append(([], "corps de LOOP"))

        # Propagation : un plan appelé depuis un autre hérite de ses entrées.
        for _ in range(MAX_ENTRY_DEPTH):
            changed = False
            for plan in self.agent.plans:
                for stmt, guards, flow in _walk_guarded(_plan_body(plan),
                                                        self.flow_ctx):
                    if not isinstance(stmt, ThenPlan) or stmt.plan not in direct:
                        continue
                    for conds, origin in list(direct.get(plan.name, [])):
                        candidate = (flow.surviving(conds) + guards,
                                     f"{origin} → {plan.name}")
                        if candidate not in direct[stmt.plan]:
                            direct[stmt.plan].append(candidate)
                            changed = True
            if not changed:
                break

        self._entries = direct
        return direct

    def call_sites(self, tool: str) -> List[CallSite]:
        sites: List[CallSite] = []
        entries = self.entry_conditions()
        fallback = bool(self.agent.on_verify_fail)
        for plan in self.agent.plans:
            for stmt, guards, flow in _walk_guarded(_plan_body(plan),
                                                    self.flow_ctx):
                if not isinstance(stmt, CallStmt) or stmt.call.name != tool:
                    continue
                local_fallback = fallback or any(
                    isinstance(s, VerifyStmt) and s.on_fail
                    for s, _, _ in _walk_guarded(_plan_body(plan),
                                                 self.flow_ctx))
                for conds, origin in (entries.get(plan.name)
                                      or [([], "plan non déclenché")]):
                    # Les conditions d'entrée valaient au *déclenchement* du
                    # plan ; ce que le plan a réécrit depuis les périme au
                    # même titre que ses gardes internes.
                    sites.append(CallSite(tool, plan.name, stmt.line,
                                          flow.surviving(conds), list(guards),
                                          origin, local_fallback))
        return sites

    # ------------------------------------------------------- T1 : interdits
    def theorem_forbidden(self) -> Theorem:
        theorem = Theorem("T1", "Aucun appel interdit n'aboutit")
        if not self.never_rules:
            theorem.holds = True
            theorem.summary = "aucune règle NEVER déclarée"
            return theorem

        checked = 0
        dead = 0
        exposed = 0
        permitted = 0
        degraded = 0
        for rule in self.never_rules:
            targets = ([t.name for t in self.agent.tools]
                       if rule.target == "*" else [rule.target])
            for target in targets:
                for site in self.call_sites(target):
                    checked += 1
                    conditions = site.conditions
                    with watch_overflow() as abandon:
                        is_dead = (rule.guard is None
                                   or entails(conditions, rule.guard))
                        reachable = (not is_dead
                                     and satisfiable(*conditions, rule.guard))
                    if is_dead:
                        dead += 1
                        theorem.findings.append(Finding(
                            "V101", "error",
                            f"branche morte : {target}() est toujours interdit "
                            f"ici", f"plan {site.plan}, atteint par "
                            f"« {site.origin} » ; condition de chemin "
                            f"{site.render_conditions()} — la garde du NEVER "
                            f"(l.{rule.line}) en découle", site.line))
                    elif reachable:
                        exposed += 1
                        theorem.findings.append(Finding(
                            "V102", "warning",
                            f"exposition : {target}() peut être tenté dans un "
                            f"état interdit",
                            f"plan {site.plan} via « {site.origin} » ; le "
                            f"runtime bloquera l'appel"
                            + ("" if site.has_fallback else
                               " et AUCUN repli n'est déclaré : le plan cale"),
                            site.line))
                    else:
                        # Ni mort, ni exposé : la condition de chemin *réfute*
                        # la garde du NEVER. C'est le bon cas — et il doit être
                        # compté. Un site qui ne tombait dans aucune des trois
                        # cases disparaissait du rapport sans laisser de trace.
                        permitted += 1
                    if abandon:
                        degraded += 1
                        theorem.findings.append(Finding(
                            "V114", "warning",
                            f"preuve dégradée : le solveur a renoncé sur "
                            f"{target}()",
                            f"plan {site.plan} — la condition de chemin dépasse "
                            f"{MAX_CLAUSES} clauses en forme normale "
                            f"disjonctive ; au-delà le solveur répond "
                            f"« satisfiable » sans l'avoir établi. Le verdict "
                            f"de ce site n'est pas démontré. Scinder la garde "
                            f"ou réduire les OR imbriqués rend la preuve",
                            site.line))

        # Le planificateur, lui, est clos par construction : une action dont
        # une branche déclenche un NEVER est exclue de la recherche entière.
        forbidden_ops = {r.target for r in self.never_rules}
        planned = [t.name for t in self.operators if t.name in forbidden_ops]
        if planned:
            theorem.findings.append(Finding(
                "V110", "info",
                f"{len(planned)} opérateur(s) sous NEVER exclus de la synthèse",
                ", ".join(sorted(planned)) +
                " — le planificateur interroge la politique par branche"))

        # Une preuve dégradée n'est plus une preuve : le théorème passe de
        # « démontré » à « borné ». Afficher DÉMONTRÉ sur un site où le
        # solveur a capitulé est précisément le mensonge qu'on veut éviter.
        theorem.holds = None if (degraded and dead == 0) else dead == 0
        if checked == 0:
            theorem.summary = ("aucun appel écrit à la main ; seules la "
                               "synthèse et la politique gouvernent ces actions")
        else:
            # La somme doit boucler : aucun site ne sort du rapport sans être
            # rangé dans l'une des trois cases.
            theorem.summary = (f"{checked} site(s) d'appel examiné(s) · "
                               f"{dead} branche(s) morte(s), "
                               f"{exposed} exposition(s), "
                               f"{permitted} permis")
            if degraded:
                theorem.summary += f" · {degraded} verdict(s) non démontré(s)"
        return theorem

    # -------------------------------------------- T2 : atteignabilité du but
    def theorem_reachability(self) -> Theorem:
        theorem = Theorem(
            "T2", "Sous chaque interdit, le but reste atteignable ou "
                  "l'escalade est déclarée")
        goals = self._goals()
        if not goals:
            theorem.holds = True
            theorem.summary = "aucun but déclaré"
            return theorem
        if not self.operators:
            theorem.holds = None
            theorem.summary = "aucun opérateur : atteignabilité non modélisable"
            return theorem

        base, base_exhausted = self._search_route(set(), [])
        if base is None:
            if base_exhausted:
                # L'espace d'états atteignables (fini, sur-approximé de façon
                # optimiste) a été *entièrement* exploré sans atteindre le but :
                # l'inatteignabilité est démontrée — on peut réfuter.
                theorem.holds = False
                theorem.summary = ("espace d'états épuisé : aucune combinaison "
                                   "d'EFFECT n'établit le but")
                theorem.findings.append(Finding(
                    "V106", "error",
                    "but hors d'atteinte des opérateurs déclarés",
                    "aucune combinaison d'EFFECT ne l'entraîne (espace épuisé)"))
            else:
                # La recherche s'est arrêtée sur la borne de profondeur alors
                # que des états restaient à explorer : on n'a RIEN prouvé. Une
                # recherche bornée ne peut pas réfuter l'atteignabilité (§24) ;
                # verdict BORNÉ, pas RÉFUTÉ.
                theorem.holds = None
                theorem.summary = (f"aucune route d'au plus {self.depth} "
                                   f"opérateurs trouvée — borné, non réfuté")
                theorem.findings.append(Finding(
                    "V113", "warning",
                    f"but non atteint dans la borne de {self.depth} opérateurs",
                    "l'inatteignabilité n'est PAS prouvée — augmenter la "
                    "profondeur (depth=) ou ajouter un opérateur"))
            return theorem

        blocked = 0            # absence de repli DÉMONTRÉE, sans escalade
        escalated = 0          # absence de repli, mais escalade déclarée
        unproved = 0           # absence de repli seulement non trouvée
        for rule in self.never_rules:
            excluded = ({t.name for t in self.agent.tools}
                        if rule.target == "*" else {rule.target})
            if not (excluded & {t.name for t in self.operators}):
                continue
            assumed = [rule.guard] if rule.guard is not None else []
            route, route_exhausted = self._search_route(excluded, assumed)
            from .runtime import _render_expr
            hypothesis = (_render_expr(rule.guard) if rule.guard is not None
                          else "toujours")
            if route is None and route_exhausted and self.escalation:
                # Point fixe atteint sans route, mais l'agent l'a prévu : un
                # `planner.exhausted` mène à un plan d'escalade. Le but n'est
                # pas atteint et ce n'est pas un défaut — c'est la conduite
                # attendue. Ce que le théorème interdit, c'est de caler en
                # silence.
                escalated += 1
                theorem.findings.append(Finding(
                    "V112", "info",
                    f"sous « {hypothesis} », aucune route permise — "
                    f"escalade déclarée ({self.escalation})",
                    "démontré : espace d'états épuisé ; l'agent ne cale pas "
                    "en silence", rule.line))
            elif route is None and route_exhausted:
                # Point fixe atteint sans route **et sans escalade** : l'agent
                # calera. L'absence de repli est démontrée, plus seulement
                # constatée — c'est ce que le calcul de point fixe apporte, et
                # c'est ce qui autorise à réfuter.
                blocked += 1
                theorem.findings.append(Finding(
                    "V105", "error",
                    f"sous « {hypothesis} », plus aucune route permise "
                    f"(démontré : espace d'états épuisé)",
                    f"l'interdit de {rule.target}() rend le but inatteignable "
                    f"et aucune escalade n'est déclarée : l'agent calera. "
                    f"Ajouter une règle `IF planner.exhausted … THEN <plan>`, "
                    f"ou réviser le but", rule.line))
            elif route is None:
                unproved += 1
                theorem.findings.append(Finding(
                    "V105", "warning",
                    f"sous « {hypothesis} », aucune route permise trouvée",
                    f"recherche arrêtée avant son point fixe "
                    f"({self.depth} niveaux / {self.max_nodes} états) — "
                    f"l'absence de repli n'est PAS prouvée", rule.line))
            else:
                theorem.findings.append(Finding(
                    "V111", "info",
                    f"sous « {hypothesis} », route de repli : "
                    + " → ".join(route), "", rule.line))

        if blocked:
            theorem.holds = False
        elif unproved:
            theorem.holds = None
        else:
            theorem.holds = True
        parts = []
        if blocked:
            parts.append(f"{blocked} interdit(s) sans repli ni escalade "
                         f"(démontré)")
        if escalated:
            parts.append(f"{escalated} interdit(s) sans repli, escalade "
                         f"déclarée")
        if unproved:
            parts.append(f"{unproved} interdit(s) au repli non prouvé")
        if not parts:
            parts.append("tout interdit garde un repli")
        theorem.summary = (f"route nominale : {' → '.join(base)} · "
                           + " · ".join(parts)
                           + ("" if unproved else " · point fixe atteint"))
        return theorem

    # ------------------------------------------------- T5 : les scénarios
    def theorem_scenarios(self) -> Theorem:
        """Une attente qu'une politique rend inatteignable est un défaut.

        T1-T4 prouvent des propriétés génériques ; T5 est le seul à prouver
        quelque chose que **l'auteur** a exigé. Il attaque les scénarios avec
        la même recherche que T2 : le `GIVEN` pose l'état initial, l'`EXPECT`
        devient le but, et les politiques s'appliquent normalement.

        Comme T2, il hérite de la direction de sûreté : une attente déclarée
        atteignable l'est *sous les déclarations* ; une attente non atteinte
        dans la borne n'est réfutée que si l'espace d'états a été épuisé.
        """
        theorem = Theorem("T5", "Les attentes déclarées sont atteignables")
        scenarios = self.agent.scenarios
        if not scenarios:
            # Vrai à vide, comme T2 sans but déclaré : il n'y a rien à
            # réfuter. Le manque est signalé en `info`, pas en verdict — un
            # « ◐ BORNÉ » ferait croire à un échec de preuve.
            theorem.holds = True
            theorem.summary = "aucune attente déclarée — rien à réfuter"
            theorem.findings.append(Finding(
                "V120", "info",
                "aucun critère d'acceptation déclaré",
                "un SCENARIO rend le programme testable (`agentl test`) et "
                "attaquable par ce théorème"))
            return theorem
        if not self.operators:
            theorem.holds = None
            theorem.summary = ("aucun opérateur : atteignabilité non "
                               "modélisable")
            return theorem

        from .runtime import _render_expr
        refuted = 0
        bounded = 0
        from .scenario import initial_expectations
        for scenario in scenarios:
            initial = _as_conditions([(e.path, _literal_of(e.value))
                                      for e in scenario.given])
            # Une attente déjà vraie sous l'énoncé est un **invariant** : elle
            # doit tenir, pas advenir. Lui chercher une route (« quelle action
            # rend vrai que l'isolement n'a pas eu lieu ? ») n'a pas de sens,
            # et la réfuter faute d'en trouver serait un faux positif.
            invariants, eventualities = initial_expectations(self.agent, scenario)
            if invariants:
                theorem.findings.append(Finding(
                    "V124", "info",
                    f"{scenario.name} : "
                    + " ∧ ".join(_render_expr(e) for e in invariants)
                    + " — invariant sous l'énoncé",
                    "hors de portée de ce théorème : c'est `agentl test` qui "
                    "vérifie qu'il tient à chaque tick", scenario.line))
            if not eventualities:
                continue
            route, exhausted = self._search_route(
                set(), [], goals=eventualities, initial=initial)
            attentes = " ∧ ".join(_render_expr(e) for e in eventualities)
            if route is not None:
                theorem.findings.append(Finding(
                    "V123", "info",
                    f"{scenario.name} : atteignable par "
                    + (" → ".join(route) if route else "l'énoncé lui-même"),
                    "" if route else
                    "l'attente est déjà vraie sous GIVEN — elle ne teste "
                    "rien de l'agent",
                    scenario.line))
            elif exhausted:
                # Espace d'états épuisé : l'inatteignabilité est démontrée.
                refuted += 1
                theorem.findings.append(Finding(
                    "V121", "error",
                    f"{scenario.name} : attente inatteignable",
                    f"aucune combinaison d'EFFECT permise n'entraîne "
                    f"« {attentes} » — politique trop stricte, ou attente "
                    f"fausse", scenario.line))
            else:
                bounded += 1
                theorem.findings.append(Finding(
                    "V122", "warning",
                    f"{scenario.name} : attente non atteinte dans la borne de "
                    f"{self.depth} opérateurs",
                    "l'inatteignabilité n'est PAS prouvée — augmenter la "
                    "profondeur (--depth)", scenario.line))

        if refuted:
            theorem.holds = False
        elif bounded:
            theorem.holds = None
        else:
            theorem.holds = True
        theorem.summary = (f"{len(scenarios) - refuted - bounded}/"
                           f"{len(scenarios)} scénario(s) sans obstacle "
                           f"(recherche bornée à {self.depth})")
        return theorem

    def _goals(self) -> List[Node]:
        if self.agent.planner and self.agent.planner.achieve:
            return list(self.agent.planner.achieve)
        goals: List[Node] = []
        for goal in self.agent.goals:
            if goal.condition is not None:
                goals.append(goal.condition)
        return goals

    def _search_route(self, excluded: Set[str],
                      assumed: Sequence[Node],
                      goals: Optional[Sequence[Node]] = None,
                      initial: Sequence[Node] = ()
                      ) -> Tuple[Optional[List[str]], bool]:
        """Chaînage avant symbolique sur les EFFECT déclarés.

        Un état est un ensemble d'affectations issues des effets. Un opérateur
        est applicable si sa précondition reste satisfiable compte tenu des
        affectations acquises et des hypothèses. Le but est atteint lorsqu'il
        *découle* des affectations — critère volontairement strict.

        Renvoie ``(route, exhausted)``. ``exhausted`` vaut VRAI quand la
        frontière s'est vidée avant la borne — tous les états atteignables ont
        alors été explorés (point fixe), ce qui autorise à *réfuter*
        l'atteignabilité. VRAI aussi quand une route est trouvée (sans objet).
        FAUX quand la recherche s'arrête sur la borne de profondeur : le
        résultat négatif est alors seulement borné, jamais une réfutation.
        """
        goals = list(goals) if goals is not None else self._goals()
        # `initial` : ce que l'énoncé pose comme déjà vrai (le `GIVEN` d'un
        # scénario). Contrairement à `assumed`, il compte aussi pour établir
        # le but — sans quoi une attente déjà satisfaite par l'énoncé
        # passerait pour inatteignable.
        initial = list(initial)
        start: Tuple[Tuple[str, Any], ...] = ()
        if initial and all(entails(initial, g) for g in goals):
            return [], True
        frontier: List[Tuple[Tuple, List[str]]] = [(start, [])]
        seen: Set[Tuple] = {start}

        for _ in range(self.depth):
            if len(seen) > self.max_nodes:
                # Budget d'états épuisé : la recherche n'a pas atteint son
                # point fixe, le résultat négatif ne prouve donc rien.
                return None, False
            nxt: List[Tuple[Tuple, List[str]]] = []
            for assignments, path in frontier:
                facts = _as_conditions(assignments) + initial
                for tool in self.operators:
                    if tool.name in excluded:
                        continue
                    if not self._permitted(tool, facts, assumed):
                        continue
                    if not self._preconditions_met(tool, facts, assumed):
                        continue
                    for branch in tool.branches:
                        updated = dict(assignments)
                        for effect in branch.effects:
                            updated[effect.path] = _literal_of(effect.value)
                        key = tuple(sorted(updated.items(), key=repr))
                        if key in seen:
                            continue
                        seen.add(key)
                        route = path + [tool.name]
                        if all(entails(_as_conditions(key) + initial, g)
                               for g in goals):
                            return route, True
                        nxt.append((key, route))
            if not nxt:
                return None, True          # frontière vide → espace épuisé
            frontier = nxt
        return None, False                 # arrêt sur la borne de profondeur

    def _preconditions_met(self, tool: ToolDecl, facts: Sequence[Node],
                           assumed: Sequence[Node]) -> bool:
        if tool.requires is None:
            return True
        if not satisfiable(*facts, *assumed, tool.requires):
            return False
        for conjunct in _conjuncts(tool.requires):
            touched = _constrained_paths(conjunct)
            if touched and touched <= self.must_establish:
                # Portant uniquement sur des chemins qu'aucune perception ne
                # fournit : il faut qu'une action les ait effectivement établis.
                if not entails(list(facts) + list(assumed), conjunct):
                    return False
        return True

    def _permitted(self, tool: ToolDecl, facts: Sequence[Node],
                   assumed: Sequence[Node]) -> bool:
        """Une politique peut-elle laisser passer cet outil ?"""
        for rule in self.agent.policies:
            if rule.target not in ("*", tool.name):
                continue
            if rule.effect in ("NEVER", "DENY"):
                if rule.guard is None or entails(list(facts) + list(assumed),
                                                 rule.guard):
                    return False
        allows = [r for r in self.agent.policies
                  if r.effect == "ALLOW" and r.target in ("*", tool.name)]
        if allows:
            return any(r.guard is None
                       or satisfiable(*facts, *assumed, r.guard)
                       for r in allows)
        return self.agent.policy_default != "DENY"

    # ------------------------------------------------ T3 : capacités mortes
    def theorem_dead_capabilities(self) -> Theorem:
        theorem = Theorem("T3", "Aucune capacité n'est morte")
        dead = 0
        for tool in self.agent.tools:
            allows = [r for r in self.agent.policies
                      if r.effect == "ALLOW" and r.target in ("*", tool.name)]
            if allows and not any(r.guard is None or satisfiable(r.guard)
                                  for r in allows):
                dead += 1
                theorem.findings.append(Finding(
                    "V103", "error",
                    f"{tool.name}() : aucune règle ALLOW satisfaisable",
                    "l'outil est déclaré mais ne pourra jamais s'exécuter",
                    tool.line))
                continue
            if not allows and self.agent.policy_default == "DENY":
                dead += 1
                theorem.findings.append(Finding(
                    "V107", "error",
                    f"{tool.name}() : jamais autorisé sous DEFAULT DENY",
                    "ajouter une règle ALLOW ou retirer l'outil", tool.line))
                continue
            unreachable = self._unreachable_threshold(allows)
            if unreachable is not None:
                threshold, ceiling, name = unreachable
                dead += 1
                theorem.findings.append(Finding(
                    "V109", "error",
                    f"{tool.name}() : seuil P({name}) ≥ {threshold:g} "
                    f"inatteignable — le modèle plafonne à {ceiling:.3f}",
                    "les vraisemblances déclarées bornent le postérieur ; "
                    "abaisser le seuil ou renforcer les évidences",
                    tool.line))
                continue
            sites = self.call_sites(tool.name)
            if sites and not any(satisfiable(*s.conditions) for s in sites):
                dead += 1
                theorem.findings.append(Finding(
                    "V108", "warning",
                    f"{tool.name}() : tous ses sites d'appel sont impossibles",
                    "les conditions de chemin sont contradictoires", tool.line))
        theorem.holds = dead == 0
        theorem.summary = (f"{len(self.agent.tools)} outil(s) · "
                           f"{dead} capacité(s) morte(s)")
        return theorem

    def _unreachable_threshold(self, allows):
        """Une garde probabiliste peut-elle seulement être franchie ?

        On ne conclut « morte » que si **toute** règle ALLOW est bloquée par un
        seuil inatteignable, et seulement à partir des seuils qui sont des
        *conjoints de premier niveau* de la garde : un seuil placé sous un `OR`
        n'est pas obligatoire (`P(h) >= 0.99 OR override` reste franchissable
        par `override`). Extraire les seuils en descendant dans les `OR`
        inventerait une capacité morte là où il n'y en a pas — direction de
        faute que le vérificateur s'interdit.
        """
        from .bayes import reachable_range

        ranges = {h.name: reachable_range(h) for h in self.agent.hypotheses}
        best = None
        for rule in allows:
            blocking = None
            for name, threshold, strict in _mandatory_probability_atoms(
                    rule.guard):
                if name not in ranges:
                    continue
                ceiling = ranges[name][1]
                reachable = (ceiling > threshold) or (
                    not strict and ceiling >= threshold)
                if not reachable:
                    blocking = (threshold, ceiling, name)
                    break
            if blocking is None:
                # Cette règle peut être satisfaite (aucun seuil obligatoire
                # inatteignable) : la capacité n'est pas morte.
                return None
            if best is None or blocking[0] < best[0]:
                best = blocking
        return best

    # ---------------------------------------- T9 : réfutabilité des effets
    def theorem_effect_falsifiability(self) -> Theorem:
        """T9 — chaque `EFFECT` sur le monde peut être démenti par une
        perception.

        Tout le reste de la vérification raisonne sur des `EFFECT` écrits à la
        main : T2 cherche une route en les appliquant, le planificateur
        enchaîne des `REQUIRES` sur eux, T5 valide un scénario à travers eux.
        Un `EFFECT` faux rend donc `verify` vert et la production fausse — et
        c'est le seul mensonge que le vérificateur ne pouvait pas voir, parce
        qu'il porte sur le monde et non sur le programme.

        Le runtime sait le détecter *a posteriori* : il compare chaque
        perception à la croyance qu'un effet avait posée (`Runtime.drift`).
        Encore faut-il qu'une perception vienne. Sans `OBSERVE` sur le chemin,
        la postcondition n'est **jamais réfutable** : elle n'est pas fausse,
        elle est hors du domaine de la preuve, et c'est pire.

        On ne réclame rien pour un effet marqué `INTERNAL` : il porte sur la
        comptabilité de l'agent (`cycle.done`), il est vrai parce que l'agent
        vient de l'écrire, et aucun capteur ne saurait en dire quoi que ce
        soit.
        """
        theorem = Theorem("T9", "Le modèle d'effets est réfutable")
        observed = {observer.path for observer in self.agent.observers}
        checked = blind = 0
        for tool in self.agent.tools:
            seen: Set[str] = set()
            for branch in tool.branches:
                for effect in branch.effects:
                    if effect.internal or effect.path in seen:
                        continue
                    seen.add(effect.path)
                    if effect.path in observed:
                        checked += 1
                        continue
                    blind += 1
                    theorem.findings.append(Finding(
                        "V150", "warning",
                        f"{tool.name}() prédit {effect.path}, qu\'aucune "
                        f"OBSERVE ne perçoit : la postcondition ne peut "
                        f"jamais être démentie",
                        f"ajouter `OBSERVE {effect.path} …`, ou marquer "
                        f"l\'effet `INTERNAL` s\'il ne porte pas sur le monde",
                        effect.line or tool.line))
        total = checked + blind
        if total == 0:
            theorem.holds = True
            theorem.summary = ("aucun EFFECT sur le monde : rien à confronter")
            return theorem
        theorem.holds = blind == 0
        theorem.summary = (f"{checked}/{total} postcondition(s) confrontable(s) "
                           f"à une perception")
        if blind == 0:
            theorem.findings.append(Finding(
                "V151", "info",
                "chaque EFFECT sur le monde est recouvert par une OBSERVE",
                "le runtime tient le compte des démentis (registre de dérive)"))
        return theorem

    # -------------------------------------------- T4 : surface exposée au LLM
    def theorem_llm_surface(self) -> Theorem:
        theorem = Theorem("T4", "La surface exposée au LLM est bornée")
        open_selection = bool(self.agent.decide
                              and self.agent.decide.reason is not None)
        if not open_selection:
            theorem.holds = True
            theorem.summary = ("aucun DECIDE.REASON : le LLM ne sélectionne "
                               "aucun plan")
            return theorem

        exposed: Dict[str, ToolDecl] = {}
        for plan in self.agent.plans:
            for stmt, _, _ in _walk_guarded(_plan_body(plan)):
                if isinstance(stmt, CallStmt):
                    decl = self.agent.tool(stmt.call.name)
                    if decl is not None:
                        exposed[decl.name] = decl

        risky = 0
        for name, decl in sorted(exposed.items()):
            if decl.risk not in ("HIGH", "SEVERE", "CRITICAL"):
                continue
            guards = [r.guard for r in self.agent.policies
                      if r.effect in ("ALLOW", "REQUIRE_APPROVAL")
                      and r.target in ("*", name)]
            # Une garde absente — mais aussi une garde *constante et vraie*
            # (`IF true`, `IF 1 == 1`, `IF 2 > 1`) — ne borne rien : le LLM
            # déclenche l'outil sans condition d'état. On ne signale que les
            # tautologies fermées (sans chemin ni appel) : une garde qui dépend
            # de l'état ou d'une fonction n'est jamais signalée à tort — le
            # vérificateur peut manquer un défaut, il n'en invente pas.
            if not guards or any(g is None or _is_vacuous_guard(g)
                                 for g in guards):
                risky += 1
                theorem.findings.append(Finding(
                    "V104", "warning",
                    f"le LLM peut déclencher {name}() (risque {decl.risk}) "
                    f"sans condition d'état",
                    "ajouter une garde IF/WHEN à la règle ALLOW, ou exiger "
                    "une approbation", decl.line))

        theorem.holds = risky == 0
        theorem.summary = (f"{len(exposed)} outil(s) atteignables par "
                           f"sélection LLM · {risky} sans garde d'état")
        if not theorem.findings:
            theorem.findings.append(Finding(
                "V112", "info",
                "tout outil à risque élevé atteignable par le LLM est gardé",
                ", ".join(sorted(exposed)) or "aucun outil"))
        return theorem


def _is_vacuous_guard(node: Optional[Node]) -> bool:
    """La garde est-elle une constante vraie, ne bornant aucun état ?

    Sûr par construction : on ne renvoie VRAI que pour une expression *fermée*
    (sans chemin ni appel de fonction) qui s'évalue à vrai. Une garde qui
    dépend de l'état (`threat.status == active`) ou d'une fonction opaque
    (`score(x) > 0`) n'est jamais réputée vide — pas de défaut inventé.
    """
    if node is None:
        return False
    from .nodes import CallExpr
    from .state import Evaluator, State

    def closed(n: Node) -> bool:
        if isinstance(n, PathExpr):
            return False
        if isinstance(n, CallExpr):
            return False
        if isinstance(n, BinOp):
            return closed(n.left) and closed(n.right)
        from .nodes import UnOp
        if isinstance(n, UnOp):
            return closed(n.operand)
        return True

    if not closed(node):
        return False
    try:
        return bool(Evaluator(State()).test(node))
    except Exception:
        return False


# --------------------------------------------------------------------------
# Parcours
# --------------------------------------------------------------------------
def _plan_body(plan: Plan) -> List[Stmt]:
    return [stmt for step in plan.steps for stmt in step.body]


@dataclass(frozen=True)
class Flow:
    """Ce que les écritures d'état déjà exécutées font aux conditions de chemin.

    Une condition de chemin dit « pour arriver ici, ceci était vrai ». Ce
    n'est pas la même chose que « ceci est vrai ici » : entre le moment où
    une garde est franchie et le point courant, un `SET` a pu réécrire ce
    dont elle parlait. Jusqu'en v1.8 le vérificateur confondait les deux, et
    un plan qui s'écrivait

        IF asset.criticality == LOW THEN {
            SET asset.criticality = CRITICAL
            isolate_endpoint()          // NEVER … WHEN criticality == CRITICAL
        }

    passait T1 sans un mot : le solveur voyait `LOW ∧ CRITICAL`, concluait à
    l'insatisfiabilité, et le site n'était classé ni mort ni exposé. Le
    runtime, lui, bloquait l'appel à chaque tick. La preuve était muette
    exactement là où l'agent calait.

    `stale` — chemins réécrits depuis le début du plan. Une condition qui les
    mentionne ne contraint plus l'état courant et doit être **retirée** des
    prémisses (affaiblir les prémisses ne peut que réduire ce qu'on démontre :
    la direction de sûreté est préservée).

    `facts` — égalités que ces écritures établissent et qui tiennent encore.
    Elles ne sont retenues que pour un chemin *durable* : une valeur qu'une
    re-perception ou un `EFFECT` peut contredire n'est pas un fait.
    """

    stale: FrozenSet[str] = frozenset()
    facts: Tuple[Node, ...] = ()

    def surviving(self, conditions: Sequence[Node]) -> List[Node]:
        """Conditions encore valables ici, augmentées des faits acquis."""
        kept = [c for c in conditions if not (_referenced_paths(c) & self.stale)]
        return kept + list(self.facts)


class _FlowCtx:
    """Ce qu'on sait des écritures d'état d'un agent donné.

    Sert à distinguer un `SET` dont la valeur *tient* jusqu'au point d'appel
    d'un `SET` que le monde peut démentir entre-temps. Sans agent (parcours
    où le flot n'importe pas), tout est réputé volatil : on retire les gardes
    périmées, on n'en tire aucun fait. Sûr par défaut, précis quand informé.
    """

    def __init__(self, agent: Optional[Agent] = None) -> None:
        self.observed: FrozenSet[str] = frozenset(
            o.path for o in agent.observers) if agent else frozenset()
        self.tool_writes: Dict[str, FrozenSet[str]] = {}
        self.tool_refresh: Dict[str, FrozenSet[str]] = {}
        if agent is not None:
            for tool in agent.tools:
                self.tool_writes[tool.name] = frozenset(
                    effect.path for branch in tool.branches
                    for effect in branch.effects)
                # `REQUIRES` déclenche `_refresh_for` : les capteurs qu'il
                # mentionne sont re-perçus avant l'appel.
                self.tool_refresh[tool.name] = frozenset(
                    _referenced_paths(tool.requires) & self.observed)

    def durable(self, path: str) -> bool:
        """Un `SET` sur ce chemin produit-il un fait qu'on peut invoquer ?"""
        return path not in self.observed

    def refreshed_by(self, cond: Optional[Node]) -> FrozenSet[str]:
        """Capteurs qu'une re-perception sur cette condition va réécrire."""
        return frozenset(_referenced_paths(cond) & self.observed)

    def written_by(self, stmt: Stmt) -> FrozenSet[str]:
        """Chemins que cette instruction *seule* peut réécrire."""
        if isinstance(stmt, SetStmt):
            return frozenset({stmt.target})
        if isinstance(stmt, CallStmt):
            name = stmt.call.name
            return (self.tool_writes.get(name, frozenset())
                    | self.tool_refresh.get(name, frozenset()))
        if isinstance(stmt, VerifyStmt):
            return self.refreshed_by(stmt.cond)
        if isinstance(stmt, ReasonStmt):
            return frozenset(stmt.produce) | frozenset(
                f"reason.{key}" for key in stmt.produce)
        if isinstance(stmt, AskStmt):
            return frozenset({"answer", f"answer.{stmt.addressee}"})
        if isinstance(stmt, DelegateStmt):
            return frozenset(stmt.expect)
        return frozenset()

    def written_within(self, stmts: Sequence[Stmt]) -> FrozenSet[str]:
        """Chemins qu'un bloc entier peut réécrire, sous-blocs compris."""
        found: Set[str] = set()
        for stmt in stmts:
            found |= self.written_by(stmt)
            for block in _sub_blocks(stmt):
                found |= self.written_within(block)
        return frozenset(found)


def _sub_blocks(stmt: Stmt) -> List[Sequence[Stmt]]:
    if isinstance(stmt, IfStmt):
        return [stmt.then, stmt.otherwise]
    if isinstance(stmt, VerifyStmt):
        return [stmt.on_fail]
    if isinstance(stmt, (LoopStmt, ForEachStmt)):
        return [stmt.body]
    return []


def _is_ground(node: Node) -> bool:
    """La valeur écrite est-elle une constante ? Sinon, aucun fait à en tirer."""
    if isinstance(node, Literal):
        return True
    return isinstance(node, PathExpr) and len(node.parts) == 1


def _walk_guarded(stmts: Sequence[Stmt],
                  ctx: Optional[_FlowCtx] = None,
                  guards: Optional[List[Tuple[Node, int]]] = None,
                  writes: Optional[Dict[str, Tuple[int, Optional[Node]]]] = None,
                  clock: Optional[List[int]] = None):
    """Parcourt les instructions en accumulant gardes **et** écritures d'état.

    Rend `(instruction, gardes encore valables, flot)`. Une garde est datée du
    rang d'écriture en vigueur quand elle a été franchie ; elle est retirée dès
    qu'un chemin qu'elle mentionne est réécrit après cette date. C'est ce qui
    rend le parcours sensible au flot sans jamais renforcer les prémisses à
    tort — voir `Flow`.
    """
    ctx = ctx if ctx is not None else _FlowCtx()
    guards = guards if guards is not None else []
    writes = writes if writes is not None else {}
    clock = clock if clock is not None else [0]

    def touch(path: str, value: Optional[Node]) -> None:
        clock[0] += 1
        writes[path] = (clock[0], value)

    def live() -> List[Node]:
        return [cond for cond, epoch in guards
                if not any(writes[p][0] > epoch
                           for p in _referenced_paths(cond) if p in writes)]

    def flow() -> Flow:
        facts = tuple(
            BinOp("==", PathExpr(path.split(".")), value)
            for path, (_epoch, value) in sorted(writes.items())
            if value is not None)
        return Flow(frozenset(writes), facts)

    for stmt in stmts:
        yield stmt, live(), flow()

        if isinstance(stmt, IfStmt):
            epoch = clock[0]
            yield from _walk_guarded(stmt.then, ctx,
                                     guards + [(stmt.cond, epoch)],
                                     dict(writes), clock)
            if stmt.otherwise:
                negated = _negate(stmt.cond)
                yield from _walk_guarded(stmt.otherwise, ctx,
                                         guards + [(negated, epoch)],
                                         dict(writes), clock)
            # On ignore laquelle des deux branches a couru : tout ce que l'une
            # ou l'autre écrit devient inconnu à la sortie du IF.
            for path in (ctx.written_within(stmt.then)
                         | ctx.written_within(stmt.otherwise)):
                touch(path, None)
            continue

        if isinstance(stmt, VerifyStmt):
            for path in ctx.refreshed_by(stmt.cond):
                touch(path, None)          # `_refresh_for` a re-perçu
            yield from _walk_guarded(stmt.on_fail, ctx, guards,
                                     dict(writes), clock)
            for path in ctx.written_within(stmt.on_fail):
                touch(path, None)
            continue

        if isinstance(stmt, (LoopStmt, ForEachStmt)):
            # Le corps est parcouru sous les mêmes gardes : un élément
            # quelconque de la collection est un cas possible, donc tout
            # appel du corps est atteignable. Le vérificateur ne se trompe
            # que dans un sens — il ne suppose pas la collection vide.
            # En revanche une itération précédente a pu écrire : ce que le
            # corps touche est inconnu *dès l'entrée* du corps.
            body_writes = dict(writes)
            inner = ctx.written_within(stmt.body)
            for path in inner:
                clock[0] += 1
                body_writes[path] = (clock[0], None)
            yield from _walk_guarded(stmt.body, ctx, guards,
                                     body_writes, clock)
            for path in inner:
                touch(path, None)
            continue

        for path in ctx.written_by(stmt):
            value = (stmt.value if isinstance(stmt, SetStmt)
                     and ctx.durable(path) and _is_ground(stmt.value) else None)
            touch(path, value)


PROBABILITY_FUNCS = {"P", "PROBABILITY", "POSTERIOR"}


def _direct_probability_atom(node: Optional[Node]):
    """Seuil probabiliste porté *directement* par ce nœud, sans descente.

    Ne regarde pas à l'intérieur des `AND`/`OR` : sert à décider si un conjoint
    de premier niveau EST lui-même une contrainte `P(h) >= θ` obligatoire.
    """
    if isinstance(node, BinOp):
        if node.op in (">", ">="):
            name = _probability_target(node.left)
            if name is not None and isinstance(node.right, Literal):
                return [(name, float(node.right.value), node.op == ">")]
        elif node.op in ("<", "<="):
            name = _probability_target(node.right)
            if name is not None and isinstance(node.left, Literal):
                return [(name, float(node.left.value), node.op == "<")]
    return []


def _mandatory_probability_atoms(node: Optional[Node]):
    """Seuils probabilistes qui *doivent* tenir pour que la garde tienne.

    Ce sont exactement les seuils portés par un conjoint de premier niveau. Un
    seuil sous un `OR` est facultatif et n'est donc pas retenu.
    """
    out = []
    for conjunct in _conjuncts(node):
        out += _direct_probability_atom(conjunct)
    return out


def _probability_target(node: Node) -> Optional[str]:
    from .nodes import CallExpr

    if isinstance(node, CallExpr) and node.name in PROBABILITY_FUNCS:
        if node.args and isinstance(node.args[0], PathExpr):
            return node.args[0].dotted
    if isinstance(node, PathExpr) and node.parts[-1] == "posterior":
        parts = [p for p in node.parts[:-1] if p != "hypothesis"]
        return ".".join(parts)
    return None


def _conjuncts(node: Optional[Node]) -> List[Node]:
    """Découpe une conjonction de premier niveau. Un OR reste indivisible."""
    if node is None:
        return []
    if isinstance(node, BinOp) and node.op == "AND":
        return _conjuncts(node.left) + _conjuncts(node.right)
    return [node]


def _referenced_paths(node: Optional[Node]) -> Set[str]:
    """Tous les chemins lus par une expression, gardes comprises."""
    out: Set[str] = set()
    stack = [node]
    while stack:
        cur = stack.pop()
        if cur is None:
            continue
        if isinstance(cur, PathExpr):
            out.add(cur.dotted)
        elif isinstance(cur, BinOp):
            stack += [cur.left, cur.right]
        elif hasattr(cur, "operand"):
            stack.append(cur.operand)
        elif hasattr(cur, "args"):
            stack += list(cur.args)
    return out


def _constrained_paths(node: Optional[Node]) -> Set[str]:
    """Chemins réellement *contraints* par l'expression.

    `_collect_paths` de l'analyseur ramasse aussi les identifiants nus qui
    servent de constantes symboliques (`yes`, `contained`). Ici on passe par
    les atomes du solveur, qui distinguent le membre gauche d'une comparaison
    de la valeur à droite.
    """
    from .solver import to_dnf

    out: Set[str] = set()
    for clause in to_dnf(node):
        for atom, _ in clause:
            if atom.kind in ("CMP", "TRUTH") and atom.path:
                out.add(atom.path)
    return out


def _negate(node: Node) -> Node:
    from .nodes import UnOp
    return UnOp("NOT", node, line=node.line)


def _plan_targets(stmts: Sequence[Stmt]) -> List[str]:
    return [stmt.plan for stmt, _, _ in _walk_guarded(stmts)
            if isinstance(stmt, ThenPlan)]


def _literal_of(node: Node) -> Any:
    if isinstance(node, Literal):
        return node.value
    if isinstance(node, PathExpr) and len(node.parts) == 1:
        return Symbol(node.parts[0])
    return Symbol("?")


def _as_conditions(assignments: Sequence[Tuple[str, Any]]) -> List[Node]:
    """Transforme des affectations en contraintes d'égalité exploitables."""
    out: List[Node] = []
    for path, value in assignments:
        left = PathExpr(path.split("."))
        right = (PathExpr([value.name]) if isinstance(value, Symbol)
                 else Literal(value))
        out.append(BinOp("==", left, right))
    return out


def verify(agent: Agent, depth: int = DEFAULT_DEPTH) -> Report:
    return Verifier(agent, depth).run()

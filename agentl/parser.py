"""Parseur à descente récursive d'AGENT-L (voir docs/agentl.ebnf)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .core import ParseError, Symbol
from .state import EPISTEMIC_FUNCS
from .lexer import PHASES, Token, tokenize
from .nodes import (
    Agent, AskStmt, BeliefDecl, BinOp, CallExpr, CallStmt, ControlStmt, Decide,
    DelegateStmt, Effect, EventHandler, EvidenceItem, Goal, Hypothesis, IfStmt,
    Domain, ForEachStmt, ListExpr, Literal, LoopSpec, LoopStmt, MemoryWrite, MemorySpec,
    MessageHandler, MessageStmt, Observer,
    PathExpr, Plan, PlannerSpec, PolicyRule, Program, ReasonStmt, Redaction,
    SetStmt,
    Outcome, Scenario, Step, Stmt, ThenPlan, ToolDecl, UnOp, UtilityTerm,
    VerifyStmt,
)

COMPARISONS = {"==", "!=", ">", ">=", "<", "<="}


class Parser:
    def __init__(self, tokens: List[Token], filename: str = "<source>"):
        self.toks = tokens
        self.i = 0
        self.filename = filename

    # ---------------------------------------------------------------- utils
    @property
    def cur(self) -> Token:
        # Le flux se termine toujours par un jeton EOF ; on s'y « colle » plutôt
        # que de déborder (un IndexError serait un traceback nu, pas une erreur
        # de syntaxe propre).
        if self.i < len(self.toks):
            return self.toks[self.i]
        return self.toks[-1]

    def peek(self, offset: int = 1) -> Token:
        j = self.i + offset
        if 0 <= j < len(self.toks):
            return self.toks[j]
        return self.toks[-1]

    def at(self, kind: str, value: Any = None) -> bool:
        t = self.cur
        return t.kind == kind and (value is None or t.value == value)

    def at_kw(self, *words: str) -> bool:
        return self.cur.kind == "KW" and self.cur.value in words

    def advance(self) -> Token:
        t = self.cur
        if t.kind != "EOF":
            self.i += 1
        return t

    def accept(self, kind: str, value: Any = None) -> Optional[Token]:
        if self.at(kind, value):
            return self.advance()
        return None

    def accept_kw(self, *words: str) -> Optional[Token]:
        if self.at_kw(*words):
            return self.advance()
        return None

    def expect(self, kind: str, value: Any = None) -> Token:
        if not self.at(kind, value):
            got = self.cur
            want = f"{kind} {value!r}" if value is not None else kind
            raise ParseError(
                f"{self.filename}:{got.line}: attendu {want}, obtenu "
                f"{got.kind} {got.value!r}"
            )
        return self.advance()

    def expect_kw(self, word: str) -> Token:
        return self.expect("KW", word)

    def name(self) -> str:
        """Un nom : identifiant ou mot-clé réutilisé comme champ."""
        t = self.cur
        if t.kind in ("IDENT", "KW"):
            self.advance()
            return str(t.value)
        if t.kind in ("NUM", "STR"):
            self.advance()
            return str(t.value)
        raise ParseError(f"{self.filename}:{t.line}: nom attendu, obtenu {t.value!r}")

    def opt_sep(self) -> None:
        """`:` ou `=` optionnels après une étiquette de champ."""
        if self.at("OP", ":") or self.at("OP", "="):
            self.advance()

    def opt_comma(self) -> None:
        self.accept("OP", ",")

    def _number(self, what: str = "nombre") -> float:
        """Lit un jeton numérique (NUM / PCT / DUR) ou lève une erreur propre.

        Sans ce garde-fou, un champ numérique alimenté par un identifiant
        (`CONFIDENCE high`) déclencherait un `ValueError` de `float()` — un
        traceback nu, inacceptable pour un produit audité.
        """
        t = self.cur
        if t.kind in ("NUM", "PCT", "DUR"):
            self.advance()
            return float(t.value)
        raise ParseError(
            f"{self.filename}:{t.line}: {what} attendu, obtenu "
            f"{t.kind} {t.value!r}"
        )

    # ----------------------------------------------------------- expressions
    def expression(self) -> Any:
        return self.or_expr()

    def or_expr(self):
        node = self.and_expr()
        while self.at_kw("OR"):
            line = self.advance().line
            node = BinOp("OR", node, self.and_expr(), line=line)
        return node

    def and_expr(self):
        node = self.not_expr()
        while self.at_kw("AND"):
            line = self.advance().line
            node = BinOp("AND", node, self.not_expr(), line=line)
        return node

    def not_expr(self):
        if self.at_kw("NOT"):
            line = self.advance().line
            return UnOp("NOT", self.not_expr(), line=line)
        return self.comparison()

    def comparison(self):
        node = self.additive()
        if self.cur.kind == "OP" and self.cur.value in COMPARISONS:
            op = self.advance()
            return BinOp(op.value, node, self.additive(), line=op.line)
        if self.at_kw("IN"):
            line = self.advance().line
            return BinOp("IN", node, self.additive(), line=line)
        return node

    def additive(self):
        node = self.multiplicative()
        while self.cur.kind == "OP" and self.cur.value in ("+", "-"):
            op = self.advance()
            node = BinOp(op.value, node, self.multiplicative(), line=op.line)
        return node

    def multiplicative(self):
        node = self.unary()
        while self.cur.kind == "OP" and self.cur.value in ("*", "/"):
            # `*` isolé sert de joker de politique : ne pas le consommer ici
            if self.cur.value == "*" and self.toks[self.i + 1].kind in ("OP",) \
                    and self.toks[self.i + 1].value in ("}", ")"):
                break
            op = self.advance()
            node = BinOp(op.value, node, self.unary(), line=op.line)
        return node

    def unary(self):
        if self.at("OP", "-"):
            line = self.advance().line
            return UnOp("-", self.unary(), line=line)
        return self.primary()

    def primary(self):
        t = self.cur
        if t.kind in ("NUM", "PCT", "DUR", "STR", "DATETIME"):
            self.advance()
            return Literal(t.value, line=t.line)
        if self.at("OP", "("):
            self.advance()
            node = self.expression()
            self.expect("OP", ")")
            return node
        if self.at("OP", "["):
            self.advance()
            items = []
            while not self.at("OP", "]"):
                items.append(self.expression())
                self.opt_comma()
            self.expect("OP", "]")
            return ListExpr(items, line=t.line)
        if t.kind == "KW" and t.value in EPISTEMIC_FUNCS \
                and self.toks[self.i + 1].value == "(":
            self.advance()                    # CONFIDENCE(...) : mot réservé
            return self.call_tail(str(t.value), t.line)
        # `SHARED.<clé>` en position d'expression (v1.6). Le compartiment
        # partagé s'écrivait (`INTO SHARED.k`) sans pouvoir se lire : aucune
        # garde ne pouvait en dépendre, donc aucune décision non plus. Ouvrir
        # le mot réservé comme racine de chemin est ce qui rend le partage
        # réel — et ce qui donne à T8 sa seconde moitié.
        if t.kind == "KW" and t.value == "SHARED" \
                and self.toks[self.i + 1].value == ".":
            return PathExpr(self.bucket_path(), line=t.line)
        if t.kind == "IDENT":
            path = self.path_parts()
            if self.at("OP", "("):
                return self.call_tail(path[-1] if len(path) == 1 else ".".join(path), t.line)
            return PathExpr(path, line=t.line)
        raise ParseError(
            f"{self.filename}:{t.line}: expression attendue, obtenu {t.kind} {t.value!r}"
        )

    def bucket_path(self) -> List[str]:
        """Chemin dont le premier segment peut être un mot réservé.

        `INTO SHARED.blocked_hosts`, `PRIOR FROM LONG_TERM.incidents` : les
        noms de compartiments sont des mots-clés du langage.
        """
        parts = [self.name()]
        while self.at("OP", "."):
            self.advance()
            parts.append(self.name())
        return parts

    def path_parts(self) -> List[str]:
        parts = [str(self.expect("IDENT").value)]
        while self.at("OP", "."):
            self.advance()
            parts.append(self.name())
        return parts

    def call_tail(self, name: str, line: int) -> CallExpr:
        self.expect("OP", "(")
        args: List[Any] = []
        kwargs: Dict[str, Any] = {}
        while not self.at("OP", ")"):
            # forme nommée : ident ':' expr  ou  ident '=' expr
            if self.cur.kind == "IDENT" and self.toks[self.i + 1].kind == "OP" \
                    and self.toks[self.i + 1].value in (":", "="):
                key = str(self.advance().value)
                self.advance()
                kwargs[key] = self.expression()
            else:
                args.append(self.expression())
            self.opt_comma()
        self.expect("OP", ")")
        return CallExpr(name, args, kwargs, line=line)

    # ----------------------------------------------------------- instructions
    def block(self) -> List[Stmt]:
        self.expect("OP", "{")
        body = self.stmt_list_until_brace()
        self.expect("OP", "}")
        return body

    def stmt_list_until_brace(self) -> List[Stmt]:
        body: List[Stmt] = []
        while not self.at("OP", "}") and not self.at("EOF"):
            body.append(self.statement())
            self.opt_comma()
        return body

    def statement(self) -> Stmt:
        t = self.cur

        # Un mot-clé de phase utilisé nu dans un LOOP : `OBSERVE`, `VERIFY`...
        if t.kind == "KW" and t.value in PHASES and not self._starts_construct(t):
            self.advance()
            return ControlStmt("PHASE", t.value, line=t.line)

        if t.kind == "KW":
            kw = t.value
            if kw == "SET":
                self.advance()
                target = ".".join(self.path_parts())
                self.opt_sep()
                return SetStmt(target, self.expression(), line=t.line)
            if kw == "IF":
                return self.if_stmt()
            if kw == "VERIFY":
                return self.verify_stmt()
            if kw == "REASON":
                return self.reason_stmt()
            if kw == "ASK":
                return self.ask_stmt()
            if kw == "DELEGATE":
                return self.delegate_stmt()
            if kw == "MESSAGE":
                return self.message_stmt()
            if kw == "LOOP":
                return self.loop_stmt()
            if kw == "FOREACH":
                return self.foreach_stmt()
            if kw == "RETRY":
                self.advance()
                n = 1
                if self.at("NUM"):
                    n = int(self.advance().value)
                return ControlStmt("RETRY", n, line=t.line)
            if kw in ("ROLLBACK", "ESCALATE"):
                self.advance()
                return ControlStmt(kw, line=t.line)
            if kw == "THEN":
                self.advance()
                return self.then_target()
            if kw in PHASES:
                self.advance()
                return ControlStmt("PHASE", kw, line=t.line)

        if t.kind == "IDENT":
            path = self.path_parts()
            if self.at("OP", "("):
                call = self.call_tail(path[-1] if len(path) == 1 else ".".join(path), t.line)
                return CallStmt(call, line=t.line)
            if self.at("OP", "="):
                self.advance()
                return SetStmt(".".join(path), self.expression(), line=t.line)
            # nom nu = déclenchement d'un plan
            return ThenPlan(".".join(path), line=t.line)

        raise ParseError(
            f"{self.filename}:{t.line}: instruction attendue, obtenu {t.kind} {t.value!r}"
        )

    def _starts_construct(self, tok: Token) -> bool:
        """Le mot-clé introduit-il une vraie instruction plutôt qu'une phase ?"""
        nxt = self.toks[self.i + 1]
        if tok.value == "VERIFY":
            return (nxt.kind in ("STR", "IDENT", "NUM", "PCT", "DUR")
                    or (nxt.kind == "OP" and nxt.value in ("{", "(")))
        return False

    def then_target(self) -> Stmt:
        """Cible d'un THEN : bloc, appel d'outil, ou nom de plan."""
        if self.at("OP", "{"):
            body = self.block()
            return IfStmt(Literal(True), body, line=self.cur.line)
        return self.statement()

    def if_stmt(self) -> IfStmt:
        line = self.expect_kw("IF").line
        cond = self.expression()
        self.expect_kw("THEN")
        then_stmt = self.then_target()
        then_body = then_stmt.then if isinstance(then_stmt, IfStmt) and \
            isinstance(then_stmt.cond, Literal) and then_stmt.cond.value is True \
            else [then_stmt]
        otherwise: List[Stmt] = []
        if self.at_kw("ELSE"):
            self.advance()
            else_stmt = self.then_target()
            otherwise = else_stmt.then if isinstance(else_stmt, IfStmt) and \
                isinstance(else_stmt.cond, Literal) and else_stmt.cond.value is True \
                else [else_stmt]
        return IfStmt(cond, then_body, otherwise, line=line)

    def verify_stmt(self) -> VerifyStmt:
        line = self.expect_kw("VERIFY").line
        label = None
        if self.cur.kind == "STR":
            label = str(self.advance().value)
        if not self.at("OP", "{"):
            return VerifyStmt(self.expression(), label=label, line=line)
        self.expect("OP", "{")
        if self.at_kw("CONDITION"):
            self.advance()
            self.opt_sep()
        cond = self.expression()
        on_fail: List[Stmt] = []
        if self.at_kw("ON"):
            self.advance()
            self.expect_kw("FAIL")
            self.opt_sep()
            on_fail = self.block() if self.at("OP", "{") else [self.statement()]
        self.expect("OP", "}")
        return VerifyStmt(cond, on_fail, label, line=line)

    def reason_stmt(self) -> ReasonStmt:
        line = self.expect_kw("REASON").line
        task = ""
        using: List[str] = []
        produce: Dict[str, str] = {}
        domains: Dict[str, Domain] = {}
        defaults: Dict[str, Node] = {}
        if self.cur.kind == "STR":
            task = str(self.advance().value)
        self.expect("OP", "{")
        while not self.at("OP", "}"):
            if self.at_kw("TASK"):
                self.advance()
                self.opt_sep()
                task = str(self.expect("STR").value)
            elif self.at_kw("USING"):
                self.advance()
                self.opt_sep()
                using = self.name_list()
            elif self.at_kw("PRODUCE"):
                self.advance()
                self.opt_sep()
                produce, domains, defaults = self.typed_fields_domained()
            else:
                raise ParseError(
                    f"{self.filename}:{self.cur.line}: champ REASON inconnu "
                    f"{self.cur.value!r}"
                )
            self.opt_comma()
        self.expect("OP", "}")
        return ReasonStmt(task, using, produce, domains, defaults, line=line)

    def ask_stmt(self) -> AskStmt:
        line = self.expect_kw("ASK").line
        addressee = self.name()
        node = AskStmt(addressee, line=line)
        self.expect("OP", "{")
        while not self.at("OP", "}"):
            if self.at_kw("QUESTION"):
                self.advance(); self.opt_sep()
                node.question = str(self.expect("STR").value)
            elif self.at_kw("REASON"):
                self.advance(); self.opt_sep()
                node.reason = str(self.advance().value)
            elif self.at_kw("TIMEOUT"):
                self.advance(); self.opt_sep()
                node.timeout = self._number("durée")
            elif self.at_kw("DEFAULT"):
                self.advance(); self.opt_sep()
                node.default = self.expression()
            else:
                raise ParseError(
                    f"{self.filename}:{self.cur.line}: champ ASK inconnu {self.cur.value!r}"
                )
            self.opt_comma()
        self.expect("OP", "}")
        return node

    def delegate_stmt(self) -> DelegateStmt:
        line = self.expect_kw("DELEGATE").line
        agent = self.name()
        node = DelegateStmt(agent, line=line)
        self.expect("OP", "{")
        while not self.at("OP", "}"):
            if self.at_kw("TASK"):
                self.advance(); self.opt_sep()
                node.task = str(self.expect("STR").value)
            elif self.at_kw("INPUT"):
                self.advance(); self.opt_sep()
                node.inputs = self.name_list()
            elif self.at_kw("EXPECT"):
                self.advance(); self.opt_sep()
                node.expect = self.name_list()
            else:
                raise ParseError(
                    f"{self.filename}:{self.cur.line}: champ DELEGATE inconnu "
                    f"{self.cur.value!r}"
                )
            self.opt_comma()
        self.expect("OP", "}")
        return node

    def message_stmt(self) -> MessageStmt:
        line = self.expect_kw("MESSAGE").line
        node = MessageStmt(self.name(), line=line)
        self.expect("OP", "{")
        while not self.at("OP", "}"):
            if self.at_kw("TO"):
                self.advance(); self.opt_sep()
                node.to = self.name()
            elif self.at_kw("BROADCAST"):
                self.advance()
                node.broadcast = True
            elif self.at_kw("PAYLOAD"):
                self.advance(); self.opt_sep()
                self.expect("OP", "{")
                while not self.at("OP", "}"):
                    key = self.name()
                    self.opt_sep()
                    node.payload[key] = self.expression()
                    self.opt_comma()
                self.expect("OP", "}")
            else:
                raise ParseError(
                    f"{self.filename}:{self.cur.line}: champ MESSAGE inconnu "
                    f"{self.cur.value!r}")
            self.opt_comma()
        self.expect("OP", "}")
        return node

    def foreach_stmt(self) -> ForEachStmt:
        line = self.expect_kw("FOREACH").line
        var = self.name()
        self.expect_kw("IN")
        source = self.expression()
        max_iter = 200
        if self.at_kw("MAX"):
            self.advance()
            max_iter = int(self.expect("NUM").value)
        return ForEachStmt(var, source, self.block(), max_iter, line=line)

    def loop_stmt(self) -> LoopStmt:
        line = self.expect_kw("LOOP").line
        until = None
        max_iter = 100
        if self.at_kw("UNTIL"):
            self.advance()
            until = self.expression()
        if self.at_kw("MAX"):
            self.advance()
            max_iter = int(self.expect("NUM").value)
        return LoopStmt(self.block(), until, max_iter, line=line)

    # ------------------------------------------------------------- fragments
    def name_list(self) -> List[str]:
        """{ a, b.c, d }  ou  a"""
        names: List[str] = []
        if self.at("OP", "{"):
            self.advance()
            while not self.at("OP", "}"):
                names.append(".".join(self.path_parts()))
                self.opt_comma()
            self.expect("OP", "}")
        else:
            names.append(".".join(self.path_parts()))
        return names

    def typed_fields_bound(self):
        """`{ host: String BIND suspected_host }` → (champs, liaisons)."""
        fields: Dict[str, str] = {}
        binds: Dict[str, str] = {}
        attestations: Dict[str, str] = {}
        self.expect("OP", "{")
        while not self.at("OP", "}"):
            key = self.name()
            typ = "Any"
            if self.at("OP", ":"):
                self.advance()
                typ = self.name()
            fields[key] = typ
            if self.at_kw("BIND"):
                self.advance()
                self.opt_sep()
                binds[key] = ".".join(self.path_parts())
            if self.at_kw("ATTESTS"):
                self.advance()
                self.opt_sep()
                attestations[key] = self.name()
            self.opt_comma()
        self.expect("OP", "}")
        return fields, binds, attestations

    def typed_fields_domained(self):
        """`{ confidence: Number IN [0, 1], kind: Symbol IN [a, b] }`

        Une sortie de LLM qui alimente une garde de politique doit être bornée
        par le programme, pas par la bonne volonté du modèle.
        """
        fields: Dict[str, str] = {}
        domains: Dict[str, Domain] = {}
        defaults: Dict[str, Node] = {}
        self.expect("OP", "{")
        while not self.at("OP", "}"):
            line = self.cur.line
            key = self.name()
            typ = "Any"
            if self.at("OP", ":"):
                self.advance()
                typ = self.name()
            fields[key] = typ
            if self.at_kw("IN"):
                self.advance()
                domains[key] = self.parse_domain(typ, line)
            if self.at_kw("DEFAULT"):
                self.advance()
                self.opt_sep()
                defaults[key] = self.expression()
            self.opt_comma()
        self.expect("OP", "}")
        return fields, domains, defaults

    def parse_domain(self, typ: str, line: int) -> Domain:
        self.expect("OP", "[")
        items: List[Any] = []
        while not self.at("OP", "]"):
            token = self.cur
            if self.at("OP", "-") and self.peek().kind in ("NUM", "PCT", "DUR"):
                # borne négative d'un intervalle : `IN [-1, 1]`
                self.advance()
                items.append(-float(self.advance().value))
            elif token.kind in ("NUM", "PCT", "DUR", "STR"):
                items.append(self.advance().value)
            else:
                items.append(Symbol(self.name()))
            self.opt_comma()
        self.expect("OP", "]")
        numeric = typ.lower() in ("number", "float", "int")
        if numeric and len(items) == 2 and all(
                isinstance(v, (int, float)) for v in items):
            return Domain("RANGE", float(items[0]), float(items[1]), line=line)
        return Domain("SET", values=items, line=line)

    def typed_fields(self) -> Dict[str, str]:
        """{ name: Type, other: Type }"""
        out: Dict[str, str] = {}
        self.expect("OP", "{")
        while not self.at("OP", "}"):
            key = self.name()
            typ = "Any"
            if self.at("OP", ":"):
                self.advance()
                typ = self.name()
            out[key] = typ
            self.opt_comma()
        self.expect("OP", "}")
        return out

    # ------------------------------------------------------------- programme
    def parse_program(self) -> Program:
        prog = Program()
        while not self.at("EOF"):
            prog.agents.append(self.parse_agent())
        if not prog.agents:
            raise ParseError("Aucune déclaration AGENT trouvée")
        return prog

    def parse_agent(self) -> Agent:
        line = self.expect_kw("AGENT").line
        agent = Agent(self.name(), line=line)
        self.expect("OP", "{")
        while not self.at("OP", "}"):
            self.parse_agent_member(agent)
        self.expect("OP", "}")
        return agent

    def parse_agent_member(self, agent: Agent) -> None:
        t = self.cur
        if t.kind != "KW":
            raise ParseError(
                f"{self.filename}:{t.line}: section attendue dans AGENT, "
                f"obtenu {t.value!r}"
            )
        kw = t.value

        if kw == "VERSION":
            self.advance(); self.opt_sep()
            if self.cur.kind not in ("STR", "NUM", "DATETIME"):
                raise ParseError(
                    f"{self.filename}:{self.cur.line}: valeur de VERSION attendue "
                    f"(chaîne ou nombre), obtenu {self.cur.kind} {self.cur.value!r}"
                )
            agent.version = str(self.advance().value)
        elif kw == "DESCRIPTION":
            self.advance(); self.opt_sep()
            agent.description = str(self.expect("STR").value)
        elif kw == "GOAL":
            agent.goals.append(self.parse_goal())
        elif kw == "BELIEF":
            agent.beliefs.extend(self.parse_belief_block())
        elif kw == "OBSERVE":
            agent.observers.extend(self.parse_observe_block())
        elif kw == "MEMORY":
            agent.memory = self.parse_memory_block()
        elif kw == "TOOL":
            agent.tools.extend(self.parse_tool())
        elif kw == "POLICY":
            self.parse_policy_block(agent)
        elif kw == "PLAN":
            agent.plans.append(self.parse_plan())
        elif kw == "EVENT":
            agent.events.append(self.parse_event())
        elif kw == "DECIDE":
            agent.decide = self.parse_decide()
        elif kw == "HYPOTHESIS":
            agent.hypotheses.append(self.parse_hypothesis())
        elif kw == "PLANNER":
            agent.planner = self.parse_planner()
        elif kw == "UTILITY":
            agent.utility = self.parse_utility()
        elif kw == "SCENARIO":
            agent.scenarios.append(self.parse_scenario())
        elif kw == "LOOP":
            agent.loop = self.parse_loop_spec()
        elif kw == "ON":
            self.advance()
            if self.at_kw("MESSAGE"):
                self.advance()
                agent.messages.append(self.parse_message_handler())
            else:
                self.expect_kw("VERIFY")
                self.expect("OP", ".")
                self.expect_kw("FAIL")
                agent.on_verify_fail = self.block()
        else:
            raise ParseError(f"{self.filename}:{t.line}: section inconnue {kw!r}")

    # ----- GOAL
    def parse_goal(self) -> Goal:
        line = self.expect_kw("GOAL").line
        name = "main"
        if self.cur.kind == "IDENT":
            name = self.name()
        goal = Goal(name, line=line)
        self.expect("OP", "{")
        while not self.at("OP", "}"):
            if self.at_kw("MAINTAIN", "ACHIEVE"):
                goal.mode = str(self.advance().value)
                self.opt_sep()
                goal.condition = self.expression()
            elif self.at_kw("TARGET"):
                self.advance(); self.opt_sep()
                if self.at("OP", "{"):
                    self.advance()
                    while not self.at("OP", "}"):
                        goal.targets.append(self.expression())
                        self.opt_comma()
                    self.expect("OP", "}")
                else:
                    goal.targets.append(self.expression())
            elif self.at_kw("WEIGHT"):
                self.advance(); self.opt_sep()
                goal.weight = float(self.expect("NUM").value)
            else:
                # expression nue = condition de maintien
                goal.condition = self.expression()
            self.opt_comma()
        self.expect("OP", "}")
        return goal

    # ----- BELIEF
    def parse_belief_block(self) -> List[BeliefDecl]:
        self.expect_kw("BELIEF")
        out: List[BeliefDecl] = []
        self.expect("OP", "{")
        while not self.at("OP", "}"):
            line = self.cur.line
            path = ".".join(self.path_parts())
            self.opt_sep()
            value = self.expression()
            decl = BeliefDecl(path, value, line=line)
            while self.at_kw("CONFIDENCE", "SOURCE", "UPDATED"):
                kw = str(self.advance().value)
                self.opt_sep()
                if kw == "CONFIDENCE":
                    decl.confidence = self._number("confiance")
                elif kw == "SOURCE":
                    decl.source = self.name()
                else:
                    decl.updated = str(self.advance().value)
            out.append(decl)
            self.opt_comma()
        self.expect("OP", "}")
        return out

    # ----- OBSERVE
    def parse_observe_block(self) -> List[Observer]:
        self.expect_kw("OBSERVE")
        out: List[Observer] = []
        if not self.at("OP", "{"):                     # OBSERVE x EVERY 10s
            out.append(self.parse_observer_entry())
            return out
        self.expect("OP", "{")
        while not self.at("OP", "}"):
            out.append(self.parse_observer_entry())
            self.opt_comma()
        self.expect("OP", "}")
        return out

    def parse_observer_entry(self) -> Observer:
        line = self.cur.line
        obs = Observer(".".join(self.path_parts()), line=line)
        self._refuse_removed_keywords()
        while self.at_kw("WHEN", "ON"):
            kw = str(self.advance().value)
            self.opt_sep()
            if kw == "ON":
                self.parse_observer_unknown(obs)
            else:
                obs.when = self.expression()
        return obs

    #: Mots réservés qui ne participent plus à aucune production. Ils restent
    #: dans le lexer pour être **refusés**, jamais rendus au vocabulaire libre.
    _REMOVED = {
        "EVERY": "`OBSERVE { logs WHEN <condition> }`",
    }

    def _refuse_removed_keywords(self) -> None:
        """`EVERY` a été retiré de la grammaire en v1.8.

        Il n'a jamais rien cadencé : le corps de la condition était un `pass`,
        et l'unité mentait — le lexer rendait `EVERY 60s` en *secondes* quand
        le runtime les comparait à un compteur de ticks, qui n'a aucune durée
        déclarée nulle part dans le langage. Un contrat de cadence qu'aucune
        exécution n'honore vaut moins que pas de contrat du tout.

        Le mot reste **réservé** plutôt que rendu au vocabulaire libre : sans
        cela `OBSERVE logs EVERY 10s` se relirait en silence comme deux
        chemins observés, `logs` et `EVERY`. Un programme antérieur doit être
        refusé, pas mal compris.

        Le contrôle ne passe pas par `at_kw` : le contrat d'auteur extrait les
        mots-clés de chaque bloc depuis les appels du parseur, et un `at_kw`
        continuerait d'annoncer `EVERY` comme faisant partie de la production
        `OBSERVE`. Un mot retiré ne doit figurer dans aucune section.
        """
        if self.cur.kind != "KW":
            return
        replacement = self._REMOVED.get(str(self.cur.value))
        if replacement is None:
            return
        raise ParseError(
            f"{self.filename}:{self.cur.line}: `{self.cur.value}` a été "
            f"retiré de la grammaire en v1.8 — il ne cadençait rien et son "
            f"unité de temps n'avait pas de sens face à un compteur de ticks "
            f"sans durée. Exprimer la cadence par une garde : {replacement}.")

    def parse_observer_unknown(self, obs: Observer) -> None:
        """`ON UNKNOWN ESCALATE` | `ON UNKNOWN DEGRADE <valeur>` (v1.8).

        Un capteur muet laissait le chemin indéfini sans un mot dans la
        trace : les gardes échouaient fermé — correct — et l'agent se
        bloquait pour une raison que rien ne nommait. La conduite face à
        l'inconnu devient déclarée. Ne rien déclarer garde exactement le
        comportement d'avant.
        """
        self.expect_kw("UNKNOWN")
        self.opt_sep()
        if self.accept_kw("ESCALATE"):
            obs.on_unknown = "ESCALATE"
            return
        self.expect_kw("DEGRADE")
        self.opt_sep()
        obs.on_unknown = "DEGRADE"
        obs.fallback = self.expression()

    # ----- MEMORY
    def parse_memory_block(self) -> MemorySpec:
        self.expect_kw("MEMORY")
        spec = MemorySpec()
        self.expect("OP", "{")
        while not self.at("OP", "}"):
            if self.at_kw("SHORT_TERM"):
                self.advance()
                spec.short_term = self.name_list()
            elif self.at_kw("LONG_TERM"):
                self.advance()
                spec.long_term = self.name_list()
            elif self.at_kw("KNOWLEDGE"):
                self.advance()
                spec.knowledge = self.name_list()
            elif self.at_kw("SHARED"):
                self.advance()
                spec.shared = self.name_list()
            elif self.at_kw("WRITE"):
                spec.writes.append(self.parse_memory_write())
            else:
                spec.long_term.append(".".join(self.path_parts()))
            self.opt_comma()
        self.expect("OP", "}")
        return spec

    def parse_memory_write(self) -> MemoryWrite:
        self.expect_kw("WRITE")
        when = None
        store: List[str] = []
        into = "LONG_TERM"
        key = "records"
        self.expect("OP", "{")
        while not self.at("OP", "}"):
            if self.at_kw("WHEN"):
                self.advance(); self.opt_sep()
                when = self.expression()
            elif self.at_kw("STORE"):
                self.advance(); self.opt_sep()
                store = self.name_list()
            elif self.at_kw("INTO"):
                self.advance(); self.opt_sep()
                parts = self.bucket_path()
                into = parts[0].upper()
                if len(parts) > 1:
                    key = ".".join(parts[1:])
            else:
                raise ParseError(
                    f"{self.filename}:{self.cur.line}: champ MEMORY WRITE inconnu"
                )
            self.opt_comma()
        self.expect("OP", "}")
        return MemoryWrite(when, store, into, key)

    # ----- TOOL
    def parse_tool(self) -> List[ToolDecl]:
        self.expect_kw("TOOL")
        # Forme abrégée : TOOL { f() g(x) }
        if self.at("OP", "{"):
            self.advance()
            tools: List[ToolDecl] = []
            while not self.at("OP", "}"):
                line = self.cur.line
                nm = self.name()
                params: Dict[str, str] = {}
                if self.at("OP", "("):
                    self.advance()
                    while not self.at("OP", ")"):
                        pname = self.name()
                        ptype = "Any"
                        if self.at("OP", ":"):
                            self.advance()
                            ptype = self.name()
                        params[pname] = ptype
                        self.opt_comma()
                    self.expect("OP", ")")
                tools.append(ToolDecl(nm, inputs=params, line=line))
                self.opt_comma()
            self.expect("OP", "}")
            return tools

        # Forme complète : TOOL name { INPUT {...} ... }
        line = self.cur.line
        tool = ToolDecl(self.name(), line=line)
        self.expect("OP", "{")
        while not self.at("OP", "}"):
            if self.at_kw("DESCRIPTION"):
                self.advance(); self.opt_sep()
                tool.description = str(self.expect("STR").value)
            elif self.at_kw("INPUT"):
                self.advance()
                tool.inputs, tool.bindings, tool.attestations = self.typed_fields_bound()
            elif self.at_kw("OUTPUT"):
                self.advance()
                tool.outputs = self.typed_fields()
            elif self.at_kw("SIDE_EFFECT"):
                self.advance()
                tool.side_effects = self.name_list()
            elif self.at_kw("REQUIRES"):
                self.advance()
                self.opt_sep()
                if self.at("OP", "{"):
                    self.advance()
                    tool.requires = self.expression()
                    self.expect("OP", "}")
                else:
                    tool.requires = self.expression()
            elif self.at_kw("EFFECT"):
                self.advance()
                self.opt_sep()
                tool.effects = self.effect_block()
            elif self.at_kw("OUTCOME"):
                tool.outcomes.append(self.parse_outcome())
            elif self.at_kw("COST"):
                self.advance()
                self.opt_sep()
                tool.cost = float(self.expect("NUM").value)
            elif self.at_kw("DURATION"):
                # `DURATION 45s` / `4min` : le lexer normalise en secondes.
                # Un nombre nu est refusé — l'unité est ce qui distingue une
                # durée d'un coût, et la deviner serait deviner l'échelle.
                self.advance()
                self.opt_sep()
                tool.duration = float(self.expect("DUR").value)
            elif self.at_kw("RISK"):
                self.advance(); self.opt_sep()
                if self.at("OP", "{"):
                    self.advance()
                    while not self.at("OP", "}"):
                        self.name()
                        self.opt_sep()
                        tool.risk = self.name().upper()
                        self.opt_comma()
                    self.expect("OP", "}")
                else:
                    tool.risk = self.name().upper()
            else:
                raise ParseError(
                    f"{self.filename}:{self.cur.line}: champ TOOL inconnu "
                    f"{self.cur.value!r}"
                )
            self.opt_comma()
        self.expect("OP", "}")
        return [tool]

    def effect_block(self) -> List[Effect]:
        """`EFFECT { threat.status = contained, cycle.done = yes INTERNAL }`

        Le suffixe `INTERNAL` (v1.7) dit que la postcondition porte sur la
        comptabilité de l'agent et non sur le monde : aucun capteur ne peut la
        démentir, et T9 ne la réclame donc pas observable.
        """
        out: List[Effect] = []
        self.expect("OP", "{")
        while not self.at("OP", "}"):
            line = self.cur.line
            path = ".".join(self.path_parts())
            self.opt_sep()
            value = self.expression()
            internal = False
            if self.at_kw("INTERNAL"):
                self.advance()
                internal = True
            out.append(Effect(path, value, internal=internal, line=line))
            self.opt_comma()
        self.expect("OP", "}")
        return out

    # ----- SCENARIO (v1.4)
    def parse_scenario(self) -> Scenario:
        """`SCENARIO nom { GIVEN { … } EXPECT { … } WITHIN n }`

        `GIVEN` réutilise la forme d'`EFFECT` — ce sont les mêmes objets :
        des affectations ground du monde. `EXPECT` prend des expressions
        booléennes, comme un `GOAL`. `WITHIN` peut suivre le bloc `EXPECT`
        ou figurer seul dans le corps ; les deux se lisent bien.
        """
        line = self.expect_kw("SCENARIO").line
        scenario = Scenario(self.name(), line=line)
        seen_within = False
        self.expect("OP", "{")
        while not self.at("OP", "}"):
            if self.at_kw("GIVEN"):
                self.advance()
                scenario.given.extend(self.effect_block())
            elif self.at_kw("EXPECT"):
                self.advance()
                self.expect("OP", "{")
                while not self.at("OP", "}"):
                    scenario.expect.append(self.expression())
                    self.opt_comma()
                self.expect("OP", "}")
                if self.at_kw("WITHIN"):
                    self.advance(); self.opt_sep()
                    scenario.within = int(self._number("nombre de ticks"))
                    seen_within = True
            elif self.at_kw("WITHIN"):
                self.advance(); self.opt_sep()
                scenario.within = int(self._number("nombre de ticks"))
                seen_within = True
            else:
                raise ParseError(
                    f"{self.filename}:{self.cur.line}: GIVEN, EXPECT ou WITHIN "
                    f"attendu dans SCENARIO, obtenu {self.cur.value!r}")
            self.opt_comma()
        self.expect("OP", "}")
        if not scenario.expect:
            # Un scénario sans attente passerait toujours : c'est un test qui
            # ment, le refuser à la lecture coûte moins cher que l'expliquer.
            raise ParseError(
                f"{self.filename}:{line}: SCENARIO {scenario.name} sans EXPECT "
                f"— un scénario sans attente est toujours satisfait")
        if not seen_within:
            scenario.within = 1
        return scenario

    def parse_outcome(self) -> Outcome:
        """`OUTCOME contenu WITH 0.85 { threat.status = contained }`"""
        line = self.expect_kw("OUTCOME").line
        name = self.name()
        probability = 1.0
        if self.at_kw("WITH"):
            self.advance()
            self.opt_sep()
            probability = self._number("probabilité")      # NUM ou PCT
        return Outcome(name, probability, self.effect_block(), line=line)

    def parse_utility(self) -> List[UtilityTerm]:
        """`UTILITY { <condition> VALUE <n> … }`"""
        self.expect_kw("UTILITY")
        terms: List[UtilityTerm] = []
        self.expect("OP", "{")
        while not self.at("OP", "}"):
            line = self.cur.line
            condition = self.expression()
            self.expect_kw("VALUE")
            self.opt_sep()
            sign = -1.0 if self.accept("OP", "-") else 1.0
            terms.append(UtilityTerm(condition,
                                     sign * float(self.expect("NUM").value),
                                     line=line))
            self.opt_comma()
        self.expect("OP", "}")
        return terms

    # ----- POLICY
    def parse_policy_block(self, agent: Agent) -> None:
        self.expect_kw("POLICY")
        self.expect("OP", "{")
        while not self.at("OP", "}"):
            line = self.cur.line
            if self.at_kw("DEFAULT"):
                self.advance(); self.opt_sep()
                agent.policy_default = self.name().upper()
                self.opt_comma()
                continue
            effect = str(self.expect("KW").value)
            if effect == "REQUIRE":
                self.accept_kw("APPROVAL")
                self.expect_kw("FOR")
                target = self.policy_target()
                guard = self.policy_guard()
                agent.policies.append(
                    PolicyRule("REQUIRE_APPROVAL", target, guard, line=line)
                )
            elif effect == "NEVER" and self.at_kw("SEND"):
                # `NEVER SEND a, b` — interdiction de sortie. Elle ne porte
                # pas sur un outil : la lire comme une règle d'action lui
                # donnerait une cible qui n'existe pas.
                self.advance()
                self.opt_sep()
                while True:
                    agent.redactions.append(
                        Redaction(".".join(self.path_parts()), line=line))
                    # La virgule sépare aussi les règles de politique entre
                    # elles : on ne poursuit la liste que si un nom suit.
                    if not (self.at("OP", ",") and self.peek(1).kind == "NAME"):
                        break
                    self.advance()
                    self.opt_sep()
            elif effect in ("NEVER", "DENY", "ALLOW"):
                target = self.policy_target()
                guard = self.policy_guard()
                agent.policies.append(PolicyRule(effect, target, guard, line=line))
            else:
                raise ParseError(
                    f"{self.filename}:{line}: effet de politique inconnu {effect!r}"
                )
            self.opt_comma()
        self.expect("OP", "}")

    def policy_target(self) -> str:
        if self.at("OP", "*"):
            self.advance()
            return "*"
        return ".".join(self.path_parts())

    def policy_guard(self):
        if self.at_kw("IF", "WHEN"):
            self.advance()
            self.opt_sep()
            return self.expression()
        return None

    # ----- PLAN
    def parse_plan(self) -> Plan:
        line = self.expect_kw("PLAN").line
        plan = Plan(self.name(), line=line)
        if self.at_kw("WHEN"):
            self.advance()
            self.opt_sep()
            plan.when = self.expression()
        self.expect("OP", "{")
        if self.at_kw("STEP"):
            while self.at_kw("STEP"):
                plan.steps.append(self.parse_step())
        else:
            plan.steps.append(Step("main", self.stmt_list_until_brace(), line=line))
        self.expect("OP", "}")
        return plan

    def parse_step(self) -> Step:
        line = self.expect_kw("STEP").line
        name = self.name()
        self.opt_sep()
        if self.at("OP", "{"):
            return Step(name, self.block(), line=line)
        body: List[Stmt] = []
        while not self.at_kw("STEP") and not self.at("OP", "}") and not self.at("EOF"):
            body.append(self.statement())
            self.opt_comma()
        return Step(name, body, line=line)

    # ----- EVENT
    def parse_event(self) -> EventHandler:
        line = self.expect_kw("EVENT").line
        source = ".".join(self.path_parts())
        handler = EventHandler(source, line=line)
        self.expect("OP", "{")
        while not self.at("OP", "}"):
            if self.at_kw("WHEN"):
                self.advance(); self.opt_sep()
                handler.when = self.expression()
            elif self.at_kw("THEN"):
                self.advance()
                handler.body.append(self.then_target())
            else:
                handler.body.append(self.statement())
            self.opt_comma()
        self.expect("OP", "}")
        return handler

    def parse_message_handler(self) -> MessageHandler:
        line = self.cur.line
        handler = MessageHandler(self.name(), line=line)
        self.expect("OP", "{")
        while not self.at("OP", "}"):
            if self.at_kw("WHEN"):
                self.advance(); self.opt_sep()
                handler.when = self.expression()
            elif self.at_kw("THEN"):
                self.advance()
                handler.body.append(self.then_target())
            else:
                handler.body.append(self.statement())
            self.opt_comma()
        self.expect("OP", "}")
        return handler

    # ----- DECIDE
    def parse_decide(self) -> Decide:
        line = self.expect_kw("DECIDE").line
        dec = Decide(line=line)
        self.expect("OP", "{")
        while not self.at("OP", "}"):
            if self.at_kw("RULES"):
                self.advance()
                self.expect("OP", "{")
                while not self.at("OP", "}"):
                    dec.rules.append(self.if_stmt())
                    self.opt_comma()
                self.expect("OP", "}")
            elif self.at_kw("REASON"):
                dec.reason = self.reason_stmt()
            elif self.at_kw("IF"):
                dec.rules.append(self.if_stmt())
            else:
                raise ParseError(
                    f"{self.filename}:{self.cur.line}: contenu DECIDE inattendu "
                    f"{self.cur.value!r}"
                )
            self.opt_comma()
        self.expect("OP", "}")
        return dec

    # ----- HYPOTHESIS (v0.4)
    def parse_hypothesis(self) -> Hypothesis:
        line = self.expect_kw("HYPOTHESIS").line
        hyp = Hypothesis(self.name(), line=line)
        self.expect("OP", "{")
        while not self.at("OP", "}"):
            if self.at_kw("DESCRIPTION"):
                self.advance(); self.opt_sep()
                hyp.description = str(self.expect("STR").value)
            elif self.at_kw("PRIOR"):
                self.advance(); self.opt_sep()
                if self.at_kw("FROM"):
                    self.advance()
                    parts = self.bucket_path()
                    hyp.prior_bucket = parts[0].upper()
                    hyp.prior_key = ".".join(parts[1:]) or "records"
                    if self.at_kw("WHERE"):
                        self.advance()
                        self.opt_sep()
                        hyp.prior_filter = self.expression()
                else:
                    hyp.prior = self._number("probabilité a priori")
            elif self.at_kw("CONFIDENCE"):          # alias historique de PRIOR
                self.advance(); self.opt_sep()
                hyp.prior = self._number("probabilité a priori")
            elif self.at_kw("THRESHOLD"):
                self.advance(); self.opt_sep()
                hyp.threshold = self._number("seuil")
            elif self.at_kw("MAX_EVIDENCE"):
                self.advance(); self.opt_sep()
                hyp.max_evidence = self._number("nombre de bits")
            elif self.at_kw("PREDICT"):             # forme abrégée de l'étude
                self.advance(); self.opt_sep()
                hyp.evidence.append(EvidenceItem(self.expression(),
                                                 label="PREDICT"))
            elif self.at_kw("EVIDENCE"):
                self.advance(); self.opt_sep()
                hyp.evidence.extend(self.evidence_block())
            elif self.at_kw("EXPLAINS"):
                self.advance(); self.opt_sep()
                hyp.explains_path = ".".join(self.path_parts())
                if self.at("OP", "=") or self.at("OP", ":"):
                    self.advance()
                    hyp.explains_value = self.expression()
            else:
                raise ParseError(
                    f"{self.filename}:{self.cur.line}: champ HYPOTHESIS inconnu "
                    f"{self.cur.value!r}"
                )
            self.opt_comma()
        self.expect("OP", "}")
        return hyp

    def evidence_block(self, group: str = "") -> List[EvidenceItem]:
        """`EVIDENCE { <test> LIKELIHOOD 0.92 GIVEN_NOT 0.05 … }`

        Un bloc `GROUP <nom> { … }` déclare des évidences corrélées : elles ne
        s'additionnent pas, seule la plus informative compte.
        """
        out: List[EvidenceItem] = []
        self.expect("OP", "{")
        while not self.at("OP", "}"):
            if self.at_kw("GROUP"):
                self.advance()
                name = self.name()
                out.extend(self.evidence_block(name))
                self.opt_comma()
                continue
            line = self.cur.line
            item = EvidenceItem(self.expression(), group=group, line=line)
            while self.at_kw("LIKELIHOOD", "GIVEN_NOT"):
                kw = str(self.advance().value)
                self.opt_sep()
                value = self._number("vraisemblance")
                if kw == "LIKELIHOOD":
                    item.likelihood = value
                else:
                    item.given_not = value
            out.append(item)
            self.opt_comma()
        self.expect("OP", "}")
        return out

    # ----- PLANNER (v0.5)
    def parse_planner(self) -> PlannerSpec:
        line = self.expect_kw("PLANNER").line
        spec = PlannerSpec(line=line)
        self.expect("OP", "{")
        while not self.at("OP", "}"):
            if self.at_kw("ENABLE"):
                self.advance()
                spec.enabled = True
            elif self.at_kw("MAX_DEPTH"):
                self.advance(); self.opt_sep()
                spec.max_depth = int(self.expect("NUM").value)
            elif self.at_kw("MAX_NODES"):
                self.advance(); self.opt_sep()
                spec.max_nodes = int(self.expect("NUM").value)
            elif self.at_kw("APPROVAL_COST"):
                self.advance(); self.opt_sep()
                spec.approval_cost = self._number("coût")
            elif self.at_kw("TARGET_CONFIDENCE"):
                self.advance(); self.opt_sep()
                spec.target_confidence = self._number("confiance")
            elif self.at_kw("DEADLINE"):
                self.advance(); self.opt_sep()
                spec.deadline = float(self.expect("DUR").value)
            elif self.at_kw("TIME_WEIGHT"):
                self.advance(); self.opt_sep()
                spec.time_weight = self._number("pondération")
            elif self.at_kw("ACHIEVE"):
                self.advance(); self.opt_sep()
                spec.achieve.append(self.expression())
            else:
                raise ParseError(
                    f"{self.filename}:{self.cur.line}: champ PLANNER inconnu "
                    f"{self.cur.value!r}"
                )
            self.opt_comma()
        self.expect("OP", "}")
        return spec

    # ----- LOOP (niveau agent)
    def parse_loop_spec(self) -> LoopSpec:
        line = self.expect_kw("LOOP").line
        spec = LoopSpec(line=line)
        if self.at_kw("UNTIL"):
            self.advance()
            spec.until = self.expression()
        if self.at_kw("MAX"):
            self.advance()
            spec.max_iter = int(self.expect("NUM").value)
        spec.body = self.block()
        return spec


# ------------------------------------------------------------------ API
def parse_source(src: str, filename: str = "<source>") -> Program:
    return Parser(tokenize(src), filename).parse_program()


def parse_file(path: str) -> Program:
    with open(path, "r", encoding="utf-8") as fh:
        return parse_source(fh.read(), path)

"""`agentl mutate` — un scénario de sûreté qui ne tombe sous aucune mutation
ne teste rien (v1.12).

Le SKILL l'exigeait depuis la v1.4 (« nommer la mutation de politique qui fait
tomber le scénario ») et rien ne l'outillait : l'auteur copiait le fichier,
retirait une ligne, relançait `agentl test`, et recommençait. Un agent auteur
l'a fait cinq fois à la main ; la cinquième a révélé un `NEVER` rendu
décoratif par une règle `DECIDE` qui répétait sa condition.

### Ce qu'on mute

Uniquement ce qui **relâche** la politique — une mutation qui la resserre ne
dit rien de la sûreté :

  * retrait de chaque `NEVER` et de chaque `DENY` ;
  * retrait de chaque `REQUIRE APPROVAL` ;
  * retrait de la garde de chaque `ALLOW … WHEN` (l'autorisation devient
    inconditionnelle).

`NEVER SEND` n'est pas muté : c'est une interdiction de sortie, qu'aucun
scénario ne juge.

### Ce qu'on juge

Chaque mutant rejoue **tous** les `SCENARIO` de son agent, contre le monde
déclaré. Il est **tué** si au moins un scénario échoue ; il **survit** sinon,
et la règle mutée n'est défendue par aucun test.

Un mutant peut être équivalent — un `NEVER` redondant avec `DEFAULT DENY`
n'a pas de scénario qui puisse le faire tomber. L'auteur le dit, sur la ligne
de la règle ou dans le commentaire contigu juste au-dessus :

    // MUTATION-OK: DEFAULT DENY couvre déjà ce cas
    NEVER purge WHEN …

Comme `BOUNDARY-OK`, une levée sans justification n'est pas lue, et une levée
qui ne couvre aucun mutant survivant est signalée.

Codes de sortie : 0 tout mutant est tué ou levé ; 1 au moins un survivant ;
2 rien n'est jugeable (scénarios déjà rouges, ou aucun scénario).
"""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .nodes import Agent, PolicyRule, Program
from .scenario import run_scenarios

_WAIVER = re.compile(r"(?:#|//)\s*MUTATION-OK\s*:\s*(\S.*?)\s*$")

KINDS = {
    "NEVER": "retrait du NEVER",
    "DENY": "retrait du DENY",
    "REQUIRE_APPROVAL": "retrait du REQUIRE APPROVAL",
    "ALLOW": "ALLOW sans sa garde",
}


@dataclass
class Mutant:
    agent: str
    kind: str
    rule: str
    line: int
    killed_by: List[str] = field(default_factory=list)
    waiver: str = ""

    @property
    def survived(self) -> bool:
        return not self.killed_by and not self.waiver

    def render(self) -> str:
        where = f"ligne {self.line}" if self.line else "—"
        if self.killed_by:
            return (f"  ✔ tué      {where:<10} {self.kind} : {self.rule}\n"
                    f"               par {', '.join(self.killed_by)}")
        if self.waiver:
            return (f"  ◌ levé     {where:<10} {self.kind} : {self.rule}\n"
                    f"               MUTATION-OK : {self.waiver}")
        return (f"  ✘ SURVIT   {where:<10} {self.kind} : {self.rule}\n"
                f"               aucun scénario ne tombe — la règle n'est "
                f"défendue par aucun test")


@dataclass
class MutationReport:
    mutants: List[Mutant] = field(default_factory=list)
    #: Agents dont les scénarios ne sont pas jugeables, avec la raison.
    unjudged: List[Tuple[str, str]] = field(default_factory=list)
    #: Levées qui ne couvrent aucun mutant survivant : (ligne, raison).
    stray_waivers: List[Tuple[int, str]] = field(default_factory=list)

    @property
    def survivors(self) -> List[Mutant]:
        return [m for m in self.mutants if m.survived]

    def exit_code(self) -> int:
        if self.unjudged:
            return 2
        return 1 if self.survivors else 0

    def render(self) -> str:
        lines: List[str] = []
        for agent, why in self.unjudged:
            lines.append(f"  ⚠ {agent} : non jugeable — {why}")
        for mutant in self.mutants:
            lines.append(mutant.render())
        for line, reason in self.stray_waivers:
            lines.append(f"  ⚠ levée sans effet  ligne {line} : « {reason} » — "
                         f"elle ne couvre aucun mutant survivant (une règle de "
                         f"politique sur sa ligne, ou juste en dessous)")
        killed = sum(1 for m in self.mutants if m.killed_by)
        waived = sum(1 for m in self.mutants if m.waiver and not m.killed_by)
        lines.append(f"\n  {killed} tué(s), {len(self.survivors)} survivant(s), "
                     f"{waived} levé(s) sur {len(self.mutants)} mutant(s)")
        return "\n".join(lines)


def _render_rule(rule: PolicyRule) -> str:
    from .runtime import _render_expr
    head = {"REQUIRE_APPROVAL": "REQUIRE APPROVAL FOR"}.get(rule.effect,
                                                            rule.effect)
    text = f"{head} {rule.target}"
    if rule.guard is not None:
        text += f" WHEN {_render_expr(rule.guard)}"
    return text


def mutants_of(agent: Agent) -> List[Tuple[int, str]]:
    """`(rang de la règle, genre)` de chaque mutation qui relâche la politique."""
    out: List[Tuple[int, str]] = []
    for index, rule in enumerate(agent.policies):
        if rule.effect in ("NEVER", "DENY", "REQUIRE_APPROVAL"):
            out.append((index, KINDS[rule.effect]))
        elif rule.effect == "ALLOW" and rule.guard is not None:
            out.append((index, KINDS["ALLOW"]))
    return out


def _mutate(agent: Agent, index: int) -> Agent:
    mutant = copy.deepcopy(agent)
    rule = mutant.policies[index]
    if rule.effect == "ALLOW":
        rule.guard = None
    else:
        del mutant.policies[index]
    return mutant


def waivers(source: str) -> Dict[int, Tuple[str, int]]:
    """Ligne couverte → (raison, ligne du commentaire).

    Une levée couvre la ligne où elle est écrite, ou — placée seule sur sa
    ligne — la première ligne de code qui suit son bloc de commentaires
    contigu. Une ligne vide interrompt le bloc : la levée ne saute pas.
    """
    lines = source.splitlines()
    out: Dict[int, Tuple[str, int]] = {}
    for number, text in enumerate(lines, start=1):
        match = _WAIVER.search(text)
        if not match:
            continue
        reason = match.group(1)
        before = text[:match.start()].strip()
        if before:
            out[number] = (reason, number)
            continue
        following = number + 1
        while following <= len(lines):
            stripped = lines[following - 1].strip()
            if not (stripped.startswith("//") or stripped.startswith("#")):
                break
            following += 1
        if following <= len(lines) and lines[following - 1].strip():
            out[following] = (reason, number)
        else:
            out.setdefault(-number, (reason, number))
    return out


def mutate_program(program: Program, source: str = "") -> MutationReport:
    report = MutationReport()
    granted = waivers(source)
    used: set = set()
    for agent in program.agents:
        base = run_scenarios(agent, echo=False)
        if not base.results:
            report.unjudged.append((agent.name, "aucun SCENARIO : rien ne peut "
                                                "faire tomber un mutant"))
            continue
        red = [r.name for r in base.results if not r.passed]
        if red:
            report.unjudged.append((agent.name, "scénarios déjà rouges sans "
                                                "mutation : " + ", ".join(red)))
            continue
        for index, kind in mutants_of(agent):
            rule = agent.policies[index]
            mutant = Mutant(agent.name, kind, _render_rule(rule), rule.line)
            result = run_scenarios(_mutate(agent, index), echo=False)
            mutant.killed_by = [r.name for r in result.results if not r.passed]
            if not mutant.killed_by and rule.line in granted:
                mutant.waiver = granted[rule.line][0]
                used.add(granted[rule.line][1])
            report.mutants.append(mutant)
    report.stray_waivers = sorted(
        {(comment, reason) for reason, comment in granted.values()
         if comment not in used})
    return report


def mutate_file(path: str) -> MutationReport:
    from pathlib import Path

    from .parser import parse_file

    source = Path(path).read_text(encoding="utf-8")
    return mutate_program(parse_file(path), source)

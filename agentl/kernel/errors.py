"""Exceptions du noyau.

Deux familles, et la distinction est le point :

* `KernelAbort` — une interruption qu'**aucune** frontière tolérante du
  runtime n'a le droit de convertir en panne ordinaire. Le runtime avale les
  exceptions d'un capteur, d'un outil ou d'un oracle pour continuer à
  surveiller ; il ne doit jamais avaler une divergence de rejeu, une
  annulation ou une violation de permis. Avant la v1.9 seule
  `ReplayDivergence` bénéficiait de ce traitement, site par site.
* les pannes du monde (`ActionInDoubt`) — un effet dont on ne sait pas s'il
  a eu lieu. C'est une panne d'outil comme une autre pour la boucle, avec une
  conséquence de plus : ses `EFFECT` ne sont jamais présumés.
"""
from __future__ import annotations

from ..core import AgentLError


class KernelAbort(AgentLError):
    """Interruption que les frontières tolérantes propagent toujours."""


class PermitError(KernelAbort):
    """Un appel à l'hôte sans permis valide — invariant I1 violé.

    Jamais une panne d'outil : un chemin qui atteint `Host.invoke` sans
    passer par le noyau est un défaut du programme hôte ou du runtime, et
    l'exécution s'arrête plutôt que de continuer sur une garantie rompue.
    """


class Cancelled(KernelAbort):
    """Annulation coopérative (v1.9, exécution asynchrone)."""


class ActionInDoubt(AgentLError):
    """L'effet d'une action a pu avoir lieu, sans qu'on sache s'il a eu lieu.

    Levée à la reprise d'une exécution durable quand une intention journalisée
    n'a pas de résultat et que l'hôte ne sait ni honorer une clé d'idempotence
    ni réconcilier ; ou quand un délai expire après l'envoi. Le noyau ne
    relance pas l'action — au plus une fois, jamais deux.
    """

    def __init__(self, message: str, *, action_id: str = "",
                 tool: str = "") -> None:
        super().__init__(message)
        self.action_id = action_id
        self.tool = tool

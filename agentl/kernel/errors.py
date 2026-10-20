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


class ActionRevoked(AgentLError):
    """Une action en suspens, rejugée à la reprise, n'est plus autorisée (1.15).

    Une exécution durable reprend une intention journalisée sans résultat. La
    relancer, c'est agir *maintenant* — sur une décision prise avec les
    lectures du processus mort. Le noyau la rejuge donc sur le monde
    d'aujourd'hui ; si la politique la refuse et que l'hôte établit qu'elle
    n'avait pas eu lieu, elle est révoquée : pas exécutée, et on le sait.
    Un refus de politique, pas une panne — ni disjoncteur, ni `in_doubt`.

    Faute de pouvoir établir qu'elle n'avait pas eu lieu, l'action refusée
    n'est pas révoquée mais indéterminée (`ActionInDoubt`).
    """

    #: La politique interdit (`tools.<outil>.denied`), ou l'approbation
    #: n'a pas été renouvelée (`tools.<outil>.unapproved`). Porté par la
    #: classe, pas par l'instance : un rejeu reconstruit l'exception par son
    #: nom et doit publier le même fait.
    unapproved = False


class ApprovalLapsed(ActionRevoked):
    """L'approbation d'une action reprise a expiré et n'a pas été renouvelée."""

    unapproved = True


class Refused(AgentLError):
    """L'outil a **refusé** la valeur qu'on lui soumettait : une réponse, pas
    une panne (v1.12).

    Un validateur refuse en levant une exception — un appel réussi atteste
    ses arguments, même s'il répond « non ». Levée ainsi, une exception
    quelconque passait pour une panne : elle nourrissait le disjoncteur, et au
    troisième refus d'affilée l'outil était coupé, `tools.<outil>.available`
    à `false`, comme un service mort. `Refused` n'atteste rien, n'est pas
    comptée comme une panne et pose `tools.<outil>.refused`.

        @host.tool("validate_ip")
        def validate_ip(ip):
            if not routable(ip):
                raise Refused(f"{ip} n'est pas routable")
            return {"ok": True}
    """

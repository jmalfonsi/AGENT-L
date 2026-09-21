# `JUDGE` — poser la question, pas seulement le type (v1.10)

`REASON` envoie un **schéma** : `PRODUCE { holds_negative: Bool }`. Le sens du
champ reste dans son nom et dans une consigne qui décrit tous les champs à la
fois. L'oracle doit donc deviner de quoi on parle, et il devine mal exactement
là où ça compte : sur le banc d'AGENT-L, `holds_negative` — « le message
demande-t-il de suspendre les réponses aux mentions négatives ? » — a été lu
comme « la mention est-elle négative ? » et répondu `yes` **à 0,94**. Ni le
type, ni le domaine, ni le `DEFAULT` ne rattrapent une erreur confiante.

`JUDGE` met la question dans le programme, et fait entrer la probabilité de la
réponse dans l'état.

## Forme

```
JUDGE "Trier la mention" {
    USING { m.content, m.author_followers }

    holds_negative: NOUL "Le message demande-t-il de SUSPENDRE les réponses
                          aux mentions négatives ?"
        DEFAULT true                       // le repli doit FERMER

    kind: CHOICE "De quoi cette mention parle-t-elle ?" {
        question:           "elle pose une question sur le produit"
        enterprise_inquiry: "elle exprime un besoin d'entreprise ou de
                             partenariat, même si l'outil de veille l'a
                             étiquetée Generic"
        other:              "aucune des deux"
    } ABSTAIN BELOW 0.80 DEFAULT other

    frustration: SCORE "À quel point l'auteur est-il mécontent ?" [
        "calme et factuel",
        "agacé mais courtois",
        "très mécontent, menace de partir"
    ]
}
```

Trois primitives, et trois seulement — ce sont celles auxquelles un oracle
peut répondre **sans rien écrire** :

| primitive | question | valeur produite | domaine dérivé |
|---|---|---|---|
| `NOUL` | une condition tient-elle ? | `Bool` | — |
| `CHOICE` | laquelle de ces options décrites ? | `Symbol` | l'énumération des options |
| `SCORE` | où sur ces niveaux ordonnés ? | `Number` | `[0, n-1]` |

La consigne entre guillemets est **obligatoire** : sans elle, le parseur
refuse. Chaque option d'un `CHOICE` et chaque niveau d'un `SCORE` porte sa
description ; `check` refuse les descriptions vides (`E017`), un `CHOICE` à une
seule branche et un `SCORE` à moins de deux niveaux — ils ne demandent rien.

## Ce que le programme récupère

Un `JUDGE` **est** un `REASON` : le parseur en dérive `PRODUCE`, les domaines
et les `DEFAULT`, donc coercition, écrêtage, `reason.degraded`, provenance
`LLM`, `USING` et `NEVER SEND` s'appliquent à l'identique. S'y ajoutent, par
champ :

```
judge.kind            = enterprise_inquiry     // aussi sous `kind`
judge.kind.value      = enterprise_inquiry
judge.kind.p          = 0.88                   // probabilité de CETTE réponse
judge.kind.confidence = 0.71                   // concentration de la distribution
```

D'où le motif qui n'était pas écrivable avant :

```
POLICY {
    DEFAULT DENY
    ALLOW send_reply WHEN judge.kind.p >= 0.90
    NEVER auto_close WHEN judge.frustration >= 1.5
}
```

## La probabilité ne s'invente pas

`judge.<champ>.p` n'existe que si l'oracle sait le calibrer. Le protocole
`LLM.judge()` a une implémentation par défaut qui traduit les questions vers
`reason()` — un programme `JUDGE` tourne donc avec **n'importe quel** oracle —
mais elle laisse `p` indéterminé plutôt que de demander le nombre à un modèle
qui l'écrirait. Conséquence à connaître : avec un oracle purement génératif,
une garde sur `judge.x.p` se referme et un champ `ABSTAIN BELOW` s'abstient.
C'est voulu : un seuil de calibration ne se franchit pas avec un chiffre
rédigé.

L'adaptateur `examples/jev_llm.py` (TypeSafe System One) implémente `judge()`
nativement : les questions du programme partent telles quelles, sans
reformulation. Avec `local=LayaRouter()` (`examples/laya_llm.py`), une
question courte sur un état court part d'abord au service Laya de la machine
(`typed-decisions` en anglais, `multilingual` sinon) et revient à Jev sous le
seuil du checkpoint : écrire une question brève et des options décrites
laisse le jugement en local ; une question longue ou un dossier en entrée
partent à Jev. Le `.agent` ne change pas — c'est un choix de l'hôte.

Le jugement est journalisé comme une réponse de `REASON` : `agentl replay`
le rejoue avec sa probabilité, et une reprise durable le lit dans le journal
au lieu de reposer la question à un oracle qui ne répondrait pas forcément
pareil.

## `ABSTAIN BELOW` — déclarer sous quel seuil on ne décide pas

Sous le seuil, ou faute de probabilité, la réponse **n'est pas retenue** : le
champ devient absent, son `DEFAULT` s'applique, `reason.missing` le compte et
la trace le dit. Une abstention est donc traitée comme un oracle muet, pas
comme une valeur de repli.

Pourquoi s'abstenir plutôt que redemander à un modèle génératif : sur 60
injections de consignes dans du texte non fiable, le modèle génératif a
basculé 29 fois, l'oracle de jugement 8 — et 6 de ces 8 bascules gardaient une
probabilité basse. Renvoyer l'incertain vers le génératif revient à lui confier
précisément les entrées piégées. Détail des cas : `bench/jev_failures.md`.

`W136` exige que la conduite soit écrite : un champ `JUDGE` qui garde un
interdit déclare `ABSTAIN BELOW`, ou bien une garde porte sur
`judge.<champ>.p`. Sans l'un des deux, 0,51 pèse autant que 0,99.

## Ce que la primitive change, mesuré

Les 13 échecs relevés sur l'oracle (`bench/jev_failures.md`) ont été rejoués
avec la **même** entrée, le même modèle et le même adaptateur, la seule
différence étant la question : `bench/jev_judge_replay.py`.

| | `REASON` (type seul) | `JUDGE` (question + réponses décrites) |
|---|---|---|
| cas corrects 3 fois sur 3 | 1/13 | **11/13** |
| injections repoussées | 0/8 | **7/8** |

Les deux cas restants disent où est la limite :

- `negative_min_followers` — `JUDGE` ne sait pas extraire un nombre, et c'est
  voulu. La question de présence qui la précède reste répondue à tort, mais à
  0,60 : `ABSTAIN BELOW 0.70` la ferme, là où `REASON` rendait `50k` à 0,80.
- `hold_status` sur une note piégée — la consigne forgée est *dans* la note
  même que la question examine. L'oracle tient à 0,70–0,77 au lieu de 0,93 :
  le seuil déplace le cas vers l'abstention, il ne le corrige pas.

Autrement dit : la question explicite corrige les erreurs de lecture, le seuil
ferme ce qui reste. Aucun des deux ne remplace `NEVER`/`REQUIRE APPROVAL` sur
l'action elle-même.

## Quand ne pas l'utiliser

`JUDGE` ne génère rien. Un brouillon de réponse, un résumé, une cause racine
en texte libre relèvent de `REASON`. Un agent réel mélange les deux, et c'est
le bon découpage : ce qui se **juge** passe par des questions fermées et
probabilisées, ce qui s'**écrit** passe par le modèle génératif — avec la
frontière visible dans le programme.

## Le tester hors ligne

`agentl test` n'appelle aucun oracle : en scénario, la réponse se **pose**,
comme celle d'un `REASON`, et sa probabilité avec elle.

```agentl
SCENARIO un_jugement_incertain_ne_repond_pas {
    GIVEN { mention.content = "pas mal ce truc, mais bon"  mention.handled = no }
    GIVEN { kind = question  judge.kind.p = 0.55 }
    EXPECT NEVER CALL send_reply WITHIN 2
}
```

Poser la seule valeur vaut `p = 1` — le cas nominal n'oblige pas à connaître
le seuil. Le contre-factuel, lui, pose la probabilité : c'est ce qui rend
`ABSTAIN BELOW` et les gardes sur `.p` réellement testés. Exemple complet et
exécutable : `examples/mention_triage.agent` (+ son hôte), `check`, `test` et
`run` verts hors ligne.

## Liste de contrôle

- [ ] chaque question se lit seule, sans le nom du champ ;
- [ ] chaque option et chaque niveau décrit une situation concrète ;
- [ ] une option de sortie existe (`other`, `unknown`) quand aucune ne peut
      convenir — l'oracle ne peut pas choisir ce qui n'est pas énuméré ;
- [ ] `DEFAULT` déclenche l'interdit que le champ garde (`W134`) ;
- [ ] `ABSTAIN BELOW` ou une garde sur `.p` pour tout champ qui décide
      (`W136`) ;
- [ ] `USING` liste les seuls chemins nécessaires (`W129`).

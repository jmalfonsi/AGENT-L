---
name: agentl-author
description: "Crée, modifie, débogue, vérifie et explique des agents AGENT-L et leurs hôtes Python. Utiliser pour tout fichier .agent, TOOL, POLICY, HYPOTHESIS, PLANNER, SCENARIO, FOREACH ou REASON ; pour automatiser des workflows métier vérifiables ; et pour analyser les ticks, refus, approbations, inférences, traces ou diagnostics E/W/V/B. Applique le contrat de grammaire versionné puis la chaîne check→test→verify→boundary→autoloop→run→replay."
---

# Écrire un agent AGENT-L

AGENT-L (dépôt `~/AGENT-L`) est un langage agentique **déclaratif et
vérifié hors ligne** : un fichier `.agent` décrit objectif, croyances, outils,
politiques, hypothèses bayésiennes, plans et critères d'acceptation ; un hôte
Python relie ces contrats au monde réel. La thèse : *le LLM propose, le runtime
décide* — une action interdite n'est jamais engendrée, et `agentl verify` le
prouve sur l'AST avant toute exécution.

## Préflight de compatibilité — avant toute rédaction

La prose n'est pas l'autorité syntaxique. Le SKILL porte un contrat versionné,
verrouillé sur `lexer.py`, `parser.py`, `nodes.py`, l'EBNF et les contrôles de
qualité. **Avant d'écrire ou modifier un `.agent`, exécuter :**

```bash
python3 ~/AGENT-L/SKILLS/agentl-author/scripts/sync_grammar.py --check
```

- si le contrôle est vert, lire
  `references/generated/grammar-contract.md`, puis partir de
  `references/generated/canonical.agent` et `canonical.py` ;
- s'il est rouge, **ne pas improviser la grammaire** et ne pas lancer
  `--write` pour faire taire le test : une évolution du langage exige un bump
  explicite de `AUTHORING_CONTRACT_VERSION`, une régénération et la revue des
  diffs ;
- `grammar-lock.json` donne les versions et empreintes exactes utilisées pour
  produire le SKILL. Le parseur/lexer reste l'autorité exécutable ; l'EBNF et
  la prose doivent le suivre.

Pour un agent à effets de bord ou alimenté par du texte non fiable, lire ensuite
`references/security-authoring.md` : cette référence est obligatoire.

Le script ne se contente pas de comparer des hashes : il parse et analyse
l'exemple canonique, joue ses scénarios, lance `verify` et `boundary`, exécute
un run hors ligne, puis retire un `NEVER` pour vérifier que le scénario négatif
échoue réellement.

## Les deux invariants

**1. Deux fichiers, même nom.** `xxx.agent` (le programme auditable) et
`xxx.py` (l'hôte : `build()` renvoyant `(host, llm)`). Même répertoire, même
radical — pas de `_host`, pas de suffixe. Il n'existe aucune option pour
désigner un autre hôte, donc aucun run ne peut partir par mégarde avec l'hôte
d'un autre agent. Sans `xxx.py`, `agentl run` refuse de démarrer (`B000`).

**Aucune exception pour un agent délégué** : un spécialiste cible d'un
`DELEGATE` porte son propre hôte, et le délégant l'**importe**. Câbler sa
perception dans l'hôte du délégant le rend invérifiable par `boundary` et
inexécutable seul (`references/composition.md` §4).

**2. L'hôte fournit des FAITS. Le `.agent` prend les DÉCISIONS.**
Test à appliquer à chaque ligne de l'hôte : *si la politique de l'entreprise
changeait demain, faudrait-il modifier cette ligne ?* Si oui, elle est au
mauvais endroit. Extraire un domaine d'une adresse, lire une feuille, apparier
un nom, composer un texte : légitime. Décider qu'un ticket est écarté, choisir
une priorité, boucler sur les éléments à traiter : jamais.

C'est la seule règle dont la violation ne produit **aucun signal** : le
programme reste vert et la garantie a disparu, parce que le raisonnement est
passé dans du Python que personne n'audite. `agentl boundary` la contrôle
désormais sur l'AST de l'hôte. **Détail, contre-exemples, et l'angle mort de
l'outil (une levée `# BOUNDARY-OK` défendable peut couvrir une perception
fausse) : `references/business-workflows.md` §1 — à lire, pas à deviner.**

## Procédure — de zéro à un agent livrable

Dans cet ordre. Sauter une étape coûte plus cher que la faire.

1. **Passer le préflight de compatibilité**, puis copier le couple canonique comme point de départ.
2. **Lire le monde avant d'écrire une ligne.** Les noms de paramètres viennent
   d'une signature réelle (`inspect.signature`), les clés de retour d'un appel
   réel sur une copie jetable. Ne devine ni `rows`/`results`, ni
   `comment`/`body` — c'est la première cause de débogage.
3. **Poser les faits d'abord** : quels capteurs, quelles collections, quel
   `read_ok`. Une lecture qui peut échouer doit rendre un fait **distinct**,
   jamais une collection vide.
4. **Écrire les `TOOL`** avec `REQUIRES` / `EFFECT` / `COST` — sans `EFFECT`,
   un outil est invisible au planificateur, et sans `EFFECT` juste, aucun
   `SCENARIO` ne prouve rien.
5. **Écrire la `POLICY` avant les `PLAN`.** Les interdits bornent la recherche ;
   les écrire après revient à décorer un comportement déjà figé.
6. **Écrire les `SCENARIO`** (§ ci-dessous) — les critères d'acceptation, dans
   le programme, avant l'hôte. C'est ce qui distingue un agent AAA d'une démo.
7. **Écrire l'hôte**, en relisant chaque ligne avec l'invariant 2.
8. **Passer les portes** ci-dessous, dans l'ordre.
9. **Jouer le run de panne et le contre-factuel**, sans exception.

## `SCENARIO` — les critères d'acceptation dans le programme (v1.4)

Les théorèmes T1-T4 prouvent des propriétés *génériques*. `SCENARIO` est le
seul endroit où s'écrit ce que **l'auteur** exige :

```agentl
SCENARIO actif_critique_jamais_d_isolement {
    GIVEN {
        asset.criticality = CRITICAL
        operator.approval = yes        // défaut = REFUS : un scénario ne
        suspected_host    = "web-07"   // suppose jamais un humain complaisant
    }
    EXPECT { isolated != confirmed } WITHIN 6
}
```

- s'exécute contre le **monde déclaré** (`agentl test`) : ni hôte, ni réseau,
  ni disque — mais ne prouve **rien sur l'hôte**, seulement que les
  déclarations entraînent l'attente ;
- trois choses se **posent**, ne se devinent pas : l'état du monde, la réponse
  de l'opérateur (`operator.approval`, `operator.answer`), et ce que le modèle
  a proposé (les noms produits par `REASON … PRODUCE`) ;
- **éventualité** (attente fausse au tick 0) → doit *advenir* dans `WITHIN` ;
  **invariant** (attente déjà vraie) → doit *tenir* à chaque tick. Sans cette
  distinction, `EXPECT { isolated != confirmed }` serait vert sans qu'aucun
  tick n'ait tourné ;
- **T5 attaque les éventualités statiquement** ; un invariant lui est hors de
  portée (`V124`) et revient à `agentl test`.

**Un scénario qui passe dans tous les cas ne teste rien.** Écris-en au moins un
qui *mord* — retirer un `NEVER` doit le faire tomber. C'est le test de
mutation, et c'est ce qui transforme une observation en test. Référence :
`examples/soc_analyst.agent`.

## Les portes de qualité, non négociables

N'annonce jamais un agent « fini » sans avoir tout passé, depuis `~/AGENT-L` :

Le contrat du SKILL est la porte zéro :

```bash
python3 ~/AGENT-L/SKILLS/agentl-author/scripts/sync_grammar.py --check
```

```
python3 -m agentl check    examples/xxx.agent     # bonne formation ; 0 erreur E
python3 -m agentl test     examples/xxx.agent     # SCENARIO contre le monde déclaré
python3 -m agentl verify   examples/xxx.agent     # 8 théorèmes ; 0 erreur
python3 -m agentl boundary examples/xxx.agent     # frontière hôte/agent ; 0 décision
python3 -m agentl autoloop examples/xxx.agent     # tient-il sur d'autres données ?
python3 -m agentl run      examples/xxx.agent \
        --html /tmp/xxx.html --record /tmp/xxx.json   # (l'hôte xxx.py est implicite)
python3 -m agentl replay   /tmp/xxx.json          # re-dérive la décision hors ligne
```

Ce que chacune doit montrer :

- **`check`** — aucune erreur `E`. Les `W` se corrigent ou se justifient une
  par une. `E009` (appel d'outil dans une expression) est l'erreur la plus
  fréquente chez un modèle.
- **`test`** — 100 % des scénarios satisfaits, **et** au moins un scénario dont
  tu sais nommer la mutation qui le fait tomber.
- **`verify`** — 0 erreur sur les 8 théorèmes. Attention :
  - `V105` est une **erreur** quand un interdit rend le but inatteignable
    *sans escalade déclarée* → ajouter `IF planner.exhausted … THEN escalate`.
    Avec l'escalade, c'est `V112` (info) : conduite attendue, pas défaut.
  - `V110`, `V111`, `V112`, `V123` sont des **infos** — comportement correct.
  - `V113`/`V122` (« borné, non réfuté ») ne prouvent rien : augmente `--depth`.
- **`boundary`** — 0 décision non justifiée **et** relecture des « levées
  assumées » une par une. Un vert sans cette relecture ne vaut rien.
- **`autoloop`** — les quatre commandes ci-dessus disent si le programme est
  recevable ; aucune ne dit s'il tient sur **d'autres données que celles que tu
  as écrites**. `autoloop` rejoue chaque `SCENARIO` sur des mondes dérivés du
  sien — capteur muet, valeur au bord d'un seuil, opérateur qui refuse — et n'y
  juge que des **invariants** : aucune erreur d'exécution, aucun `VERIFY` en
  échec, aucun outil interdit sans condition exécuté, aucune approbation
  contournée, plus tes `EXPECT` déjà vraies au départ. Trois lectures :
  - une part des cas est **retenue** et n'est ouverte qu'à la fin. 100 % sur ce
    que la boucle a vu et moins sur le lot retenu se lit « appris par cœur »
    (code de sortie 3), pas « presque bon » ;
  - la barrière **« invariants (monde déclaré) »** est la seule à voir un
    scénario vert dont l'exécution a levé une erreur en chemin — `test` ne juge
    que l'attente, pas la façon d'y arriver ;
  - avec `--model gemini-* | claude-*`, la boucle corrige elle-même et
    s'arrête sur trois freins (plafond, budget, absence de progrès). Sans `-o`,
    **rien n'est écrit** : la version que tu as relue n'est jamais écrasée.
  Détail : `references/autoloop.md`.
- **`run`** — toujours **un nominal ET un contre-factuel** (une variable d'env
  qui active le `NEVER`), pour prouver que la politique mord.
- **Test de neutralisation** — casse volontairement les `IF` applicatifs qui
  doublent une politique et vérifie que l'interdit tient seul. Un `NEVER`
  qu'aucun run ne déclenche n'est pas une garantie, c'est une décoration.
- **`blocked: N` se lit.** Chaque refus doit correspondre à un cas que tu sais
  nommer. `blocked: 0` sur un agent qui déclare des `NEVER` signifie en général
  que les interdits ne sont jamais atteints, donc jamais testés.
- **Le run de panne**, aussi obligatoire que le nominal : coupe l'oracle (clé
  LLM invalide) *et* coupe la source (identifiants faux, service injoignable).
  L'agent doit **ne rien faire et le dire** — jamais agir sur du vide, jamais
  conclure « tâche accomplie » sur une perception ratée.
  **Ce n'est pas une propriété du runtime, c'est une obligation de l'auteur.**
  Mesure faite sur un agent réel, oracle coupé : l'agent a survécu, n'a rien
  inventé, a signalé la panne — *et exécuté vingt-cinq actions déterministes*,
  parce qu'aucune de leurs gardes ne dépendait du `REASON`. Rien n'était faux ;
  rien n'était voulu non plus. Si « oracle mort = on ne touche à rien » est
  l'exigence, elle s'écrit (principe 10) ; sinon elle n'existe pas. Le
  disjoncteur d'outil, lui, borne l'acharnement sans qu'on l'écrive : sur la
  même épreuve, **122 tentatives sont devenues 32**.
- **`--record` / `replay`** — le journal scelle les huit points de
  franchissement de frontière sur une empreinte SHA-256 ; `replay` re-dérive la
  décision sans capteur ni réseau, et le verdict est l'égalité caractère pour
  caractère. À joindre dès qu'un run doit être auditable ou rejouable.
- **`--html`** — journal visuel autonome (`references/visual-trace.md`) : chaque
  tick, chaque paramètre d'outil, chaque décision. À joindre à toute revue.

Outils d'inspection ponctuels : `agentl plan` (route synthétisée),
`agentl infer` (postérieurs), `agentl ast`, `agentl viz` (graphe statique HTML)
— tous acceptent `--ticks 0` pour percevoir sans agir.

## Le studio — voir l'agent tourner

```
python3 -m agentl studio examples/xxx.agent --open      # http://127.0.0.1:8765
```

Application web locale (aucune dépendance, hors ligne) : graphe de flux style
N8N, éditeur `.agent` colorié avec diagnostics en direct, inspecteur
(croyances, hypothèses avec seuil, métriques), chronologie filtrable. Ce qu'on
n'y fait qu'au studio :

- **suivre l'exécution** — chaque trace illumine le nœud qui l'a produite, un
  outil refusé reste marqué en erreur, la caméra suit le nœud actif ;
- **régler l'allure** — sélecteur « démo » (jusqu'à 3 s par événement) ;
- **voir l'oracle** — les plans contenant un `REASON` clignotent pendant
  l'appel au LLM ;
- **trancher les approbations** — le studio est l'approbateur, et l'absence de
  réponse vaut refus (*fail-closed*) ;
- **les sociétés** — une bande par agent ; un message illumine ses deux
  extrémités.

L'hôte n'y est pas sélectionnable : l'indicateur montre le `xxx.py` imposé par
la norme, vert s'il déclare bien les capteurs du programme, rouge sinon — et le
bouton *Lancer* refuse. Cette lecture est syntaxique, jamais un import :
vérifier un hôte ne doit pas exécuter de code arbitraire.

## Références — quoi ouvrir, quand

| Fichier | Ouvrir quand |
|---|---|
| `references/generated/grammar-contract.md` | **toujours avant d'écrire** : versions/hashes et champs extraits de l'AST du parseur. Généré, jamais édité à la main. |
| `references/generated/canonical.agent` + `.py` | couple minimal exécutable, régénéré et soumis à toutes les portes plus un test de mutation. Le copier, ne pas le réinventer. |
| `references/security-authoring.md` | **obligatoire pour effets de bord, cibles ou texte non fiable** : `DEFAULT`, `ATTESTS`, approbation, réobservation, rollback, T6/T7 et W119–W125/B008–B014. |
| `references/authoring.md` | **pour écrire** : procédure, formes fragiles et pièges appris en production. À lire en entier avant de coder. |
| `references/business-workflows.md` | **données d'entreprise** (tickets, e-mails, feuilles, CRM) : règle de partage détaillée, `FOREACH`, `REASON` à domaine clos, motifs de `POLICY` qui portent, pièges des API réelles, liste de contrôle de livraison. |
| `references/runtime-semantics.md` | **pour expliquer un run** : pourquoi N ticks, ordre d'évaluation de la politique, ligne bayésienne, séparation de canaux, **glossaire complet E/W/V/B**, métriques, rejeu. |
| `references/composition.md` | **pour orchestrer** : `DELEGATE`, `MESSAGE` + `Society`, `MEMORY { SHARED }`, motif hiérarchique. |
| `references/visual-trace.md` | le journal visuel `--html`. |
| `~/AGENT-L/docs/SPEC.md` | sémantique faisant autorité (§16 inférence, §17 planification, §21 vérification, §22 calibration, §26 rejeu, §27 `SCENARIO`). |
| `~/AGENT-L/docs/agentl.ebnf` | grammaire formelle. |

Exemples de référence : `soc_analyst.agent` (inférence calibrée + planification
+ deux `SCENARIO` dont un test de mutation) · `service_medic.agent` (EVENT,
DELEGATE, autonomie) · `disk_sentinel.agent` (approbation) · `supervisor.agent`
+ `forensic.agent` (hiérarchie) · `gmail_butler.agent` + `gmail_butler_naif.agent`
(workflow réel + variante naïve) · `idor_hunter.agent` (chemin non concluant).

## Principes de conception

1. **Le LLM qualifie ; il ne choisit ni cible ni autorisation.** Les entités et
   cibles viennent de capteurs déterministes et portent une preuve distincte
   `ATTESTS`. Le LLM produit une classe bornée avec un `DEFAULT` inoffensif.
   La confiance d’une menace vient d’une `HYPOTHESIS` calculée.
   « Inoffensif » a un sens précis et **vérifiable** : lié à son défaut, le
   champ doit *déclencher* l'interdit qu'il garde. Troncature, JSON invalide
   et oracle muet aboutissent tous au défaut — c'est le chemin que prend
   toute panne. `W134` simule cette liaison et parle quand l'interdit
   ne s'applique plus.
2. **Toute sortie LLM qui garde une politique doit être bornée** : domaine clos
   `IN [...]` ou intervalle `IN [0,1]`.
3. **La politique borne la recherche, elle ne la corrige pas.** Un `NEVER`
   n'est pas un garde-fou d'exécution : le planificateur l'interroge par
   branche, l'action interdite n'entre dans aucun plan.
4. **Ce qu'on déclare doit exister et être confronté au monde.** Un `EFFECT`
   sans re-perception corrompt les plans *et rend les `SCENARIO` menteurs* ;
   `VERIFY` re-perçoit, `effect_drift` compte les mensonges.
5. **Un outil devient planifiable** dès qu'il déclare `REQUIRES` / `EFFECT` /
   `COST`. Sans `EFFECT`, il est invisible au planificateur.
6. **Adopter une consigne est une action.** Une directive arrivée par les
   données ne doit pas modifier le comportement sans passer par un outil soumis
   à la politique — `NEVER adopt_directive WHEN sender_internal == no`. La
   hiérarchie des sources est une propriété du programme, pas un prompt.
7. **Refuser, c'est ne pas déclarer.** Quand une instruction contredit une
   politique interne, la réponse n'est pas d'écrire « ne fais pas ça » : c'est
   de ne pas déclarer l'outil, et de poser un `NEVER` sur l'argument. Une action
   absente du programme est indisponible.
8. **Indéterminé n'est pas zéro.** Une extraction ratée rend des valeurs par
   défaut ; un seuil à 0 laisse tout passer. Garde chaque seuil issu du monde
   par `NEVER <action> WHEN <seuil> <= 0`.
9. **Le texte libre est le seul canal que la politique ne borne pas.** Un
   `NEVER` contrôle *à qui* l'on écrit, *quand* et *combien de fois* — jamais
   *ce que contient* un champ `String`. Donc : **ne jamais laisser la sortie
   d'un outil de lecture rejoindre un champ de texte libre sortant.** Si un tel
   flux est voulu, c'est un **choix explicite à documenter** en commentaire à
   côté du `TOOL`, avec un destinataire verrouillé par un fait perçu — pas une
   garantie. Se démontre par la **variante naïve** (business-workflows §5).
10. **Une panne est un fait, pas une exception.** Le runtime ne décide jamais
    à la place du programme ce qu'il faut faire quand le monde lâche : il
    *publie*, et le `.agent` tranche. Quatre faits, lisibles dans n'importe
    quelle garde :

    | Fait | Posé quand |
    |---|---|
    | `tools.<nom>.available` | `false` après trois levées d'affilée du même outil (disjoncteur) |
    | `tools.<nom>.failures` | nombre de levées consécutives, remis à zéro au premier succès |
    | `reason.degraded` | `true` quand l'oracle n'a rendu **aucun** champ du `PRODUCE` |
    | `reason.missing` | la liste des champs qu'il n'a pas rendus |

    ```agentl
    NEVER close_batch  WHEN tools.update_row.available == false
    NEVER apply_change WHEN reason.degraded == true
    ```

    Sans ces gardes, un oracle mort n'empêche **rien** : les plans dont la
    garde ne dépend pas du `REASON` continuent de tourner, et l'agent exécute
    des actions déterministes en aveugle. Mesuré, pas supposé — voir le run de
    panne dans les portes de qualité.

## Brancher un vrai LLM

`MockLLM` pour les tests déterministes. Pour un vrai modèle : `AnthropicLLM`
(clé `ANTHROPIC_API_KEY`) dans `agentl/llm.py` ; `examples/gemini_llm.py`
fournit `GeminiLLM` (clé `GEMINI_API_KEY`, réutilise celle de `~/HAL/.env`).
L'adaptateur impose un JSON strict et **coerce** au schéma `PRODUCE` — un
modèle hors format obtient les valeurs par défaut, la trace le montre.

Le piège qui a coûté le plus cher : un schéma `PRODUCE` long (dix champs et
plus) avec un modèle à raisonnement **dépasse le budget de tokens de sortie**,
la réponse est tronquée, et tous les champs retombent silencieusement à leur
défaut. Si un `REASON` numérique rend des zéros suspects, c'est la première
cause à écarter — et le principe 8 est la parade.

# Écrire les deux fichiers — contrat canonique et pièges

Ce document couvre la rédaction d'un agent AGENT-L : le squelette des deux
fichiers, et les pièges appris en production. Pour un agent qui traite des
**données d'entreprise** (tickets, e-mails, lignes de feuille, CRM), lire en
plus `business-workflows.md` — règle de partage hôte/agent, `FOREACH`,
`REASON` à domaine clos, pièges des API réelles.

## 1. Le fichier `.agent` : partir du canon, pas de mémoire

Ne maintiens plus de squelette exhaustif dans cette prose : il divergerait du
parseur. Exécute d'abord :

```bash
python3 ~/AGENT-L/SKILLS/agentl-author/scripts/sync_grammar.py --check
```

Puis lis et copie `references/generated/canonical.agent`. Ce fichier est
régénéré par le script, parsé, analysé, testé, vérifié, contrôlé par
`boundary`, exécuté hors ligne et muté. Le tableau des champs réellement
acceptés, extrait de l'AST de `parser.py`, est dans
`references/generated/grammar-contract.md`.

Les formes ci-dessous sont les seules exceptions à mémoriser, parce qu'elles
ont produit des erreurs réelles chez des agents de codage :

- `GOAL` exige un bloc nommé ou anonyme : `GOAL fini { MAINTAIN x == yes }` ;
- `BELIEF` est un bloc d'affectations, jamais une suite de déclarations
  `BELIEF x PRIOR y` ;
- `PLANNER` n'accepte **aucun `WHEN`**. Mettre l'extinction dans les gardes de
  `PLAN`, les `REQUIRES`, la `POLICY` ou une règle `DECIDE` ;
- `REASON` utilise `USING` et `PRODUCE`. Les noms de `PRODUCE` sont des
  identifiants plats (`decision`), pas des chemins (`approval.decision`) ;
- les sorties supposées d'un `REASON` se posent directement dans le `GIVEN`
  d'un `SCENARIO`. Il n'existe ni bloc `LLM_OUTPUTS`, ni mot-clé `INVARIANT` :
  une attente vraie au tick 0 devient automatiquement un invariant ;
- un appel s'écrit `outil(arg)` ou `outil(x = arg)`, jamais `CALL outil` ;
  `EXPECT CALL outil` est en revanche une assertion de scénario valide ;
- `BIND` sert au **planificateur**. Un appel écrit explicitement doit encore
  fournir tous ses INPUT (E013 : manquant, inconnu, excès ou double liaison).
  Répéter un argument nommé est une erreur de parsing ;
- dans un `SCENARIO`, les types sont réels : une valeur `Number` se pose comme
  `2`, une `String` comme `"2"`.

Les mots réservés sont en majuscules. Les phases autorisées et les champs de
`PLANNER`, `SCENARIO` et `REASON` ne doivent pas être recopiés ici : le contrat
généré les expose directement depuis le parseur.

## 2. L'hôte Python

```python
from __future__ import annotations
import sys
from pathlib import Path

# Auto-résolution du PYTHONPATH : évite les ModuleNotFoundError selon le cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agentl import Host, Symbol
# from gemini_llm import GeminiLLM   # ou: from agentl import AnthropicLLM, MockLLM


def build():
    h = Host()

    # Capteurs : une lecture = une re-perception du monde réel.
    h.sensors["chemin.capteur"] = lambda: Symbol("yes")      # ou un nombre
    h.sensors["chemin.contenu_texte"] = lambda: "contenu brut du journal..."

    # Outils : les paramètres portent les noms déclarés dans INPUT — le
    # runtime appelle toujours par mot-clé.
    def mon_outil(arg):
        arg_str = str(arg)             # un Symbol redevient une chaîne côté monde
        ...                            # VRAIE action, bornée ici aussi
        return {"champ": Symbol("ok")}  # clés = champs OUTPUT

    h.tools["mon_outil"] = mon_outil

    # Sous-agents (DELEGATE) : la sortie honore le contrat EXPECT.
    h.subagents["sous_agent"] = lambda payload: {"resultat": Symbol("success"),
                                                 "score": 0.95}

    # Approbateur : None = refus (fail-closed).
    def approver(request):
        return input(f"\n  🖐  AUTORISER {request.render()} ? (o/N) : ").strip().lower() == "o"

    h.approver = approver
    h.emit("source.evt", severity=Symbol("HIGH"))   # déclenche un EVENT

    return h, None                     # (host, llm) ; llm optionnel


# Pas de `__main__` à écrire : `agentl run xxx.agent` suffit, y compris pour
# une société multi-agents (cf. piège 3 — `build()` rend alors un dict d'hôtes
# indexé par nom d'agent).
```

**Défense en profondeur** : l'outil de l'hôte re-valide ce que le LLM a
produit — une purge vérifie que le chemin reste dans le bac à sable, quel que
soit le `directory` renvoyé. La politique et l'hôte sont deux étages
indépendants.

**Mais l'hôte ne décide pas.** Re-valider une borne de sûreté est légitime ;
choisir quels éléments traiter, quelle priorité appliquer ou quoi écarter ne
l'est pas — cela vide le programme de sa substance sans produire le moindre
signal. Voir la règle de partage dans `business-workflows.md`.

## 3. Pièges appris en production (les respecter fait gagner des heures)

1. **Synthèse vs garde `WHEN` — marqueur d'extinction.** Le planificateur ne
   synthétise qu'à **file de plans vide** (`runtime.phase_synthesize`). Un
   `PLAN ... WHEN <cond>` dont la garde reste vraie se réenfile à chaque tick,
   **bloque la synthèse à jamais**, et si sa première étape échoue on obtient
   une boucle infinie d'appels LLM. → Éteins systématiquement la garde
   (`WHEN condition AND NOT (processed == yes)` puis `SET processed = yes` en
   fin de plan), ou déclenche par une règle `DECIDE` qui s'éteint
   (`IF target == unknown THEN plan`) ou un `EVENT` one-shot.

   Dans le bloc `DECIDE` (squelette §1), le bloc `RULES` est optionnel — une suite de `IF ... THEN <plan>` nue est
   acceptée, et un `REASON` peut y figurer. Le nom qui suit `THEN` est
   confronté à la liste des plans déclarés : un nom inventé est rejeté (E002).

2. **Grammaire de `DELEGATE`.** C'est une **instruction**, pas un bloc de
   déclaration : uniquement dans le corps d'un `STEP`, d'un `EVENT` ou d'un
   `ON MESSAGE`. À la racine de l'`AGENT`, erreur `section inconnue 'DELEGATE'`.

3. **Single-agent vs `Society`.** `python3 -m agentl run` ordonnance
   **aussi** les programmes multi-agents : il détecte `len(program.agents) > 1`
   et construit la `Society` lui-même. Ce qui change, c'est le **contrat de
   `build()`** — il rend alors `(hosts, llms)` **indexés par nom d'agent** :

   ```python
   def build():
       analyst, responder = Host(), Host()
       ...
       return {"SOC_ANALYST": analyst, "RESPONDER": responder}, MockLLM()
   ```

   Un dict d'hôtes est accepté avec un LLM unique partagé **ou** un dict de
   LLM. Un hôte fourni pour un agent absent du programme est une erreur, pas
   un oubli silencieux. Rendre un `Host` simple reste valide : il est alors
   partagé par tous les agents. Lancer `Society(...)` à la main dans un
   `__main__` n'est plus nécessaire — c'est un vestige.

4. **Contrats `INPUT` et typage.** Le runtime vérifie les types déclarés. Un
   `Symbol` perçu satisfait un contrat `String` (il est rendu au monde en
   `str`), et `yes`/`no` satisfont un `Boolean` ; tout autre symbole est
   refusé et l'appel **bloqué** (`⛔ contrat INPUT violé`). Aligne les types
   quand c'est un énuméré, et n'oublie pas : les **noms** de paramètres
   viennent de la signature réelle, jamais d'une supposition.

   Deux points décisifs et peu intuitifs :
   - **tout champ déclaré dans `INPUT` est obligatoire à chaque appel**
     (`argument manquant : x`). Ne déclare donc que les paramètres que tu
     passes *toujours*. Si un outil réel a six paramètres optionnels dont tu
     n'utilises que deux selon le cas, expose **deux outils** dans l'hôte
     plutôt qu'un seul avec des champs facultatifs — le langage n'a pas
     d'optionnalité dans `INPUT` ;
   - **`Symbol` n'est pas vérifié** : seuls `Number`/`Int`/`String`/`Boolean`
     le sont. Un `INPUT { x: Symbol }` accepte n'importe quoi, y compris une
     valeur indéfinie. Si le contrôle de type t'importe, déclare `String`.

5. **`USING` transmet ce qu'on lui donne.** Si tu passes un capteur booléen
   (`USING { has_logs }`, qui vaut `"yes"`), le modèle ne voit aucun texte et
   répond `unknown`. **Transmets toujours le texte brut.**

6. **Domaines transmis au LLM.** Un champ `PRODUCE ... IN [...]` dont le
   domaine n'atteint pas le modèle est **écrêté** vers la valeur de repli
   (métrique `domain_clamps > 0`, ligne de trace « sortie LLM hors domaine »).
   Le runtime transmet le domaine dans le schéma ; si tu écris un adaptateur,
   passe le type enrichi (`Symbol IN [...]`).

   **L'écrêtage retombe sur la DERNIÈRE valeur listée** (`allowed[-1]`).
   Place donc toujours la valeur non concluante en fin de liste —
   `IN [ high, medium, low, unknown ]` et non l'inverse — sinon une réponse
   hors format devient silencieusement une valeur d'action. Et surveille le **budget de
   tokens** : un `PRODUCE` à dix champs et plus, avec un modèle à
   raisonnement, peut être tronqué — tous les champs retombent alors
   silencieusement à leur valeur par défaut.

7. **Refus humain et re-bouclage.** Après un `N` à une `REQUIRE APPROVAL`, le
   planificateur re-proposera la même action au tick suivant. → Marqueur
   d'escalade (`SET escalation.sent = yes`), plus `DENY outil IF
   escalation.sent == yes` et `WHEN NOT (escalation.sent == yes)` dans le
   `PLANNER`.

8. **`V105` n'est pas un bug, c'est un rappel.** « Sous <interdit>, plus
   aucune route permise » signifie qu'il faut une sortie explicite → `PLAN
   escalate_*` (notifier / ouvrir un incident) déclenché par `DECIDE` quand
   `planner.exhausted AND but non atteint AND escalation.sent != yes`.

9. **Un `REQUIRES` est réévalué à l'exécution.** Un plan conforme est joué
   dans un monde qui a bougé : si la précondition est retombée, l'outil est
   ignoré (pas rejoué). Utilise un marqueur (`SET diagnosis.done = yes`) pour
   ordonner diagnostic → action.

10. **Seuils et calibration bougent ensemble.** Recalibrer une `HYPOTHESIS`
    (ajouter un `GROUP`, baisser `MAX_EVIDENCE`) change l'amplitude
    atteignable du postérieur. Avec `PRIOR 0.10` et une preuve unique de
    LR = 18, le postérieur plafonne à 0.667 : un `THRESHOLD 0.75` déclenche
    `W116`, et une politique `IF P(h) >= s` hors amplitude déclenche `V109`.
    Recale l'a priori ou le seuil.

11. **Injection par les données.** Tout texte du monde (journal, message,
    ticket) qui entre par `REASON` ne peut influencer que les champs `PRODUCE`
    bornés — jamais une garde de politique, qui lit un **capteur**. C'est la
    propriété de sûreté clé ; ne la contourne pas en faisant lire une
    politique sur une sortie LLM non bornée.

12. **Mesure ratée ≠ résultat négatif.** C'est le pendant, au niveau du
    *contrôle*, du principe « indéterminé n'est pas faux ». Dès qu'une
    conclusion ou une action repose sur une mesure qui peut **échouer à se
    produire** (capteur en erreur, ligne de base injoignable, requête qui
    tombe, outil qui renvoie une erreur), n'autorise pas l'aval à lire le
    postérieur bas ou le champ vide comme un « négatif confiant ». Il faut un
    chemin **explicitement non concluant** :
    - une garde `DECIDE` sur l'échec de précondition qui **bifurque avant
      l'action à risque** — p.ex. `IF baseline.done == yes AND baseline.status
      != ok THEN report_inconclusive`, le plan de mesure gardant la condition
      saine (`... AND baseline.status == ok THEN run_probe`) ;
    - le plan non concluant pose un état distinct (`SET result =
      inconclusive`, pas `none`), notifie, et **ne lance pas** l'outil
      coûteux ou irréversible ni ne produit de rapport « rien trouvé ».

    Cas réel : un agent IDOR pointé sur une page de *docs* (et non l'API)
    obtenait une base en erreur et des sondes toutes en 404 ; sans ce chemin,
    il rendait un rapport `severity=none` qui se lisait comme un feu vert.

13. **Le contrat de l'hôte dépend de qui l'exécute.** Le modèle ci-dessus
    (`build() -> (host, llm)`) est celui de la CLI `agentl run`. Un banc, un
    harnais de test ou un intégrateur peuvent en imposer un autre — par
    exemple `build(info, world) -> host`, où le monde est fourni de
    l'extérieur. **Lis le lanceur avant d'écrire `build()`** ; la norme de
    nommage `xxx.agent ↔ xxx.py` reste vraie dans tous les cas, la signature
    de `build` non.

14. **`_project` ne lie que des scalaires.** Dans un `FOREACH`, un champ dont
    la valeur est une liste ne donne que `<var>.<champ>.count` — le contenu
    est inaccessible. Si l'élément porte une sous-collection utile (les
    messages d'une conversation, les lignes d'une commande), c'est à l'hôte
    de l'aplatir en un scalaire exploitable (texte concaténé, compteur) :
    c'est de la perception, donc légitime. Vérifie toujours ce que `FOREACH`
    projette réellement avant d'écrire une condition dessus.

15. **Un appel d'outil est une instruction, jamais une expression.**
    `x = outil()` déclenche `E009` : on appelle, puis on lit les clés du
    résultat, qui deviennent des variables portant leur propre nom.

16. **Un `SCENARIO` ne teste que les déclarations, jamais l'hôte.** Il joue
    contre le **monde déclaré** : `GIVEN` pose l'état, chaque outil appelé
    applique ses propres `EFFECT`, les politiques s'appliquent normalement.
    Conséquence directe : **un `EFFECT` qui ment rend le test vert et la
    production fausse.** C'est `agentl boundary`, le théorème **T9** et le
    registre de dérive qui traitent cette question — pas `agentl test`.

    **T9 exige que chaque `EFFECT` sur le monde soit recouvert par une
    `OBSERVE`** (`V150`). Sans perception, la postcondition n'est pas fausse :
    elle est irréfutable, ce qui est pire. Marque `INTERNAL` — et seulement
    dans ce cas — un effet qui porte sur la comptabilité de l'agent et non sur
    le monde :

    ```agentl
    EFFECT { app.status = healthy,  cycle.done = yes INTERNAL }
    ```

    La crédibilité d'un `EFFECT` démenti baisse ensuite d'elle-même (lissage
    de Jeffreys sur le registre), et `MEMORY { LONG_TERM { effect_drift } }`
    la fait survivre à l'exécution. Vérifie `effect_drift = 0` sur un run réel
    avant de faire confiance à un scénario.

    Trois pièges de rédaction :
    - `WITHIN n` doit laisser au moins un tick, sinon `E010` ;
    - un `GIVEN` qui pose un chemin inexistant est signalé `W117` et
      bloque TEST — c'est presque toujours une faute de frappe ;
    - une attente qui ne porte sur **aucun chemin produit par EFFECT, OUTPUT, SET, REASON ou DELEGATE**
      (W118) ne teste aucune évolution produite par l'agent ; même vraie dans
      GIVEN, elle rend TEST invalide. Décrire une postcondition ou une assertion
      sur les appels. Voir [scenarios.md](scenarios.md).

17. **Tester explicitement une non-action.** `EXPECT NEVER CALL outil`
    interdit un appel à l’hôte simulé. Un `EXPECT { x ==
    valeur }` déjà vrai au tick 0 est traité comme un **invariant** — il
    doit tenir après chaque effet, instruction et phase. Son état initial
    doit être explicite ; UNKNOWN ne satisfait jamais EXPECT. C'est ce qui rend testable « l'agent ne doit
    jamais isoler cette machine » : `EXPECT { isolated != confirmed }`, où
    `isolated = confirmed` est l'`EFFECT` propre à l'action interdite. Juger
    l'attente au seul tick 0 la rendrait verte sans qu'aucun tick n'ait
    tourné. Un invariant est hors de portée de T5 (`V124`, info) et revient
    entièrement à `agentl test`.

## 18. Provenance et effets de bord (v1.5)

Pour toute cible, texte non fiable ou action système, appliquer intégralement
`security-authoring.md`. Les deux formes structurantes sont :

```agentl
PRODUCE { classification IN [known, other] DEFAULT other }
INPUT { target: String, evidence_token: String ATTESTS target }
```

Le `DEFAULT` est la valeur fail-closed des absences et sorties hors domaine.
`ATTESTS` relie statiquement la preuve à la cible ; l’hôte conserve la charge
de la fraîcheur, de l’usage unique et de la revalidation juste avant effet.
`check`, T6/T7 et `boundary` rendent désormais ces omissions visibles.


## Scénarios

Lire [scenarios.md](scenarios.md) pour le contrat d'exécution, les stimuli,
les assertions de trace et un exemple exécutable. Le contrat généré expose la
syntaxe reconnue ; les réponses externes doivent rester explicites dans GIVEN.

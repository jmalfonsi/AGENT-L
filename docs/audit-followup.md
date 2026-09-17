# Vérification des audits CHECK, TEST et BOUNDARY

Les constats ont été confrontés au code et aux reproductions exécutables de
`tests/test_audits_check_test.py`. Les audits d'origine sont conservés tels quels.

| Constat | Vérification et traitement |
|---|---|
| CHK-01 | Confirmé. `E012` rejette les noms d'agents dupliqués, sans distinction de casse. |
| CHK-02 | Confirmé. `E013` contrôle les arguments manquants, inconnus, en excès ou liés deux fois. Les types dynamiques restent contrôlés au runtime. |
| CHK-03 | Confirmé. Le parseur rejette immédiatement les arguments nommés répétés. |
| CHK-04 | Confirmé. W135 examine les définitions antérieures à chaque garde ; une affectation ultérieure ou dans une seule branche ne suffit plus. |
| CHK-05 | Confirmé. W102 recherche, après chaque action à effet, une vérification liée à ses effets sur tous les chemins examinés. |
| CHK-06 | Confirmé. Une règle d'approbation conditionnelle ne supprime plus W120. |
| CHK-07 | Confirmé. Alias `reason.<champ>` et `SET` propagés, vocabulaire de cibles étendu, polarité des gardes vérifiée, branche ELSE négative. |
| CHK-08 | Comportement documenté, conservé : les avertissements seuls ne rendent pas `check` bloquant. |
| TST-01 | Confirmé. EXPECT utilise une logique trivalente et distingue les champs simples déclarés des constantes symboliques. `NOT` ne transforme plus une absence en succès. |
| TST-02 | Confirmé. TEST et RUN partagent la boucle ; WITHIN borne l'exécution sans prolonger LOOP MAX ni ignorer UNTIL. |
| TST-03 | Confirmé. Plusieurs OUTCOME possibles exigent un choix explicite dans GIVEN. Aucune sélection par probabilité maximale. |
| TST-04 | Confirmé après adaptation de l'exemple à la grammaire réelle (`REASON { TASK ... }`). Sélection explicite par `llm.plan`. |
| TST-05 | Confirmé. Aucune sortie fabriquée selon son type ; toute sortie obligatoire doit provenir de GIVEN ou d'un effet exécuté. |
| TST-06 | Confirmé pour GIVEN et la simulation d'OUTCOME. Nuance : un EFFECT déterministe invalide pouvait déjà échouer lors de sa seconde évaluation par Runtime. Toute erreur de simulation ou trace ERROR invalide maintenant le scénario. |
| TST-07 | Confirmé. Surveillance après chaque effet simulé, instruction et phase ; ajout d'assertions sur les appels et blocages. |
| TST-08 | Limitation confirmée. Injection explicite de messages et événements dans les véritables gestionnaires du runtime isolé. |
| TST-09 | Confirmé. W117/W118 bloquent TEST. W118 reconnaît aussi les sorties, SET, REASON et DELEGATE, pas seulement EFFECT. |
| TST-10 | Confirmé. Une suite vide n'est plus réussie. CLI : code 2 ; dérogation explicite `--allow-empty`. |
| TST-11 | Confirmé. Une éventualité observée reste satisfaite même si elle devient fausse ensuite ; les invariants continuent d'être surveillés. |
| TST-12 | Confirmé. Le simulateur utilise exactement `approval_granted`, comme le runtime. L'entier 1 n'approuve pas. |
| BND-A01–A07 | Déjà corrigés lors du précédent passage. Les 98 régressions adversariales existantes ont été rejouées. Voir [BOUNDARY.md](BOUNDARY.md). |

Une reproduction supplémentaire a trouvé que Runtime évaluait les EFFECT sans
lier les paramètres de l'appel. Cette portée est maintenant partagée avec celle
de l'action : un effet peut lire ses INPUT et les locales vivantes.

## Écrire les scénarios explicites

```agentl
SCENARIO issue_minoritaire {
    GIVEN {
        transaction.status = pending
        scenario.outcome.payment = corruption
        payment.receipt = "fixture-receipt-1"
        llm.plan = investigate
    }
    EXPECT { transaction.status == corrupted } WITHIN 3
}
```

`scenario.outcome.<outil>` choisit une branche de probabilité strictement
positive. Sans choix, seule une branche possible unique est acceptée. La valeur
reste la même pour les appels successifs dans ce scénario. Écrire un scénario
par issue pertinente ; TEST n'explore pas automatiquement toutes les issues.
`llm.plan` choisit un plan déclaré quand DECIDE sollicite effectivement le LLM.
Les réponses de REASON restent posées sous les noms de PRODUCE.

Une sortie d'outil s'écrit `nom_outil.champ = valeur`. Un champ simple posé dans
GIVEN, ou produit par EFFECT, reste accepté. Sans sortie explicite, le scénario
échoue. Les exemples distribués déclarent leurs accusés, leurs collections et
les états initiaux de leurs invariants.

```agentl
SCENARIO message_malveillant {
    GIVEN MESSAGE alert FROM attacker { value = attack }
    GIVEN EVENT service.alert { severity = high }
    EXPECT NEVER CALL wipe
    EXPECT BLOCKED wipe
    EXPECT EVENT service.alert
    EXPECT NO ERROR
    WITHIN 3
}
```

Les stimuli sont injectés au début et passent par RECEIVE/SELECT_PLAN, les
filtres WHEN et la liaison non fiable de payload du runtime. Un stimulus non
consommé, un gestionnaire inconnu ou une assertion sur un outil inconnu rend le
scénario invalide. `EXPECT CALL outil` exige au moins un appel à l'hôte simulé ;
`NEVER CALL` en exige zéro ; `BLOCKED` exige une trace de blocage pour cet outil.
Les erreurs d'exécution échouent toujours, même sans `EXPECT NO ERROR`.

Les invariants portent sur toute l'exécution observée jusqu'à UNTIL, MAX ou
WITHIN. Une valeur devenue inconnue ne maintient pas un invariant. Les
éventualités sont mémorisées dès leur première satisfaction aux points observés.

## Limites et compatibilité

- CHECK reste une analyse conservative. W102 reconnaît un lien de chemins,
  pas la validité logique complète d'une postcondition. W135 ne prouve pas la
  disponibilité future d'un capteur déclaré ; les appels externes peuvent échouer.
- W119/W125 restent des heuristiques de provenance : noms de cibles et de
  confiance, aliases locaux, pas de preuve générale interprocédurale. Une garde
  de confiance reconnue ne prouve pas la qualité du détecteur d'injection.
- Les scénarios testent le modèle déclaré. Les moniteurs ne voient ni les
  instructions internes d'un outil réel ni les effets omis de son contrat.
- MESSAGE injecte un message dans un runtime isolé, pas une société complète.
  Le transport et les échanges entre plusieurs agents nécessitent des tests
  d'intégration. Les stimuli arrivent une seule fois au début du scénario.
- T5 marque explicitement les stimuli et assertions de trace hors de son modèle
  (`V128`, résultat indéterminé). Un GIVEN invalide est signalé (`V127`).
- `agentl test` retourne 0 pour une suite non vide réussie, 1 pour un échec, 2
  pour un agent sans scénario. `--allow-empty` ne dispense que du dernier cas.
  La CI nomme les sept anciens fichiers d'exemple sans scénario ; cela reste
  une dette de couverture, pas une preuve d'acceptation.
- Les diagnostics Boundary historiques restent suivis individuellement dans
  `boundary-example-debt.json` ; cet audit ne les efface pas.


## Validation de cette révision

- Suite core : **1 070 tests et 231 sous-tests réussis**, Python 3.12.3.
- Couverture de `agentl` : **90,44 %**, seuil requis de 90 % atteint.
- Nouvelles régressions CHECK/TEST : **68 cas** ; corpus Boundary : **98 cas**.
- Exemples : **57 scénarios réussis** et **57 commandes check/verify/test**
  réussies. Les sept fichiers sans scénario utilisent l'exception nommée.
- Inventaire Boundary : aucun nouveau diagnostic par rapport à la dette suivie.
- Contrat auteur **2.8.1** régénéré et validé, grammaire et documentation à jour.

Commandes reproductibles :

```bash
python3 -m pytest -q tests --cov=agentl --cov-report=term-missing --cov-fail-under=90
python3 tools/check_examples.py
python3 tools/check_boundary_examples.py
python3 SKILLS/agentl-author/scripts/sync_grammar.py --check
```

Ce passage ne valide ni les hôtes externes réels ni les applications annexes.
Aucun réseau, modèle réel ou service métier n'a été sollicité par les scénarios.

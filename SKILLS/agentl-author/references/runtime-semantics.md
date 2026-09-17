# Sémantique du runtime — pour *expliquer* un run, pas seulement l'écrire

Ce fichier distille ce qu'il faut pour répondre aux questions d'un opérateur
sur une exécution : « pourquoi N ticks ? », « pourquoi cette action a-t-elle
été bloquée / approuvée / autorisée ? », « que dit cette ligne bayésienne ? »,
« que signifie ce code E/W/V/B ? ». Toutes les affirmations ci-dessous sont
vérifiables dans
`~/AGENT-L/agentl/{runtime,policy,bayes,verifier,analyzer,boundary,replay}.py`
et `docs/SPEC.md`.

---

## 1. La boucle et le compteur de ticks

`Runtime.run()` itère au plus `limit` fois, où `limit` = `--ticks` s'il est
donné, sinon `LOOP ... MAX n`, sinon 10. **Après chaque tick**, si la clause
`UNTIL <cond>` s'évalue vraie, la boucle s'arrête (trace : « condition d'arrêt
UNTIL satisfaite »). Donc :

- **La boucle s'arrête tôt** quand `UNTIL` devient vrai — typiquement
  `UNTIL goal.satisfied`.
- **La boucle va jusqu'au plafond `MAX n`** quand `UNTIL` ne devient jamais
  vrai.

**`goal.satisfied` est vrai ssi le score global des objectifs atteint 1.0.**
Chaque `GOAL` reçoit un score = moyenne de ses conditions (`MAINTAIN`,
`TARGET`), chacune valant 1.0 (vraie) ou 0.0 (fausse) ; le score global est la
moyenne **pondérée par `WEIGHT`**. Il faut donc que *toutes* les conditions de
*tous* les objectifs soient vraies au même tick.

**Lire le nombre de ticks comme un signal :**
- `ticks = 2` (p.ex.) et fin sur « UNTIL satisfaite » → **but atteint**, arrêt
  anticipé.
- `ticks = MAX` (p.ex. 5 sur `MAX 5`) → **but jamais atteint**, arrêté par le
  plafond de sécurité. Cause fréquente : un `NEVER` interdit la seule action
  qui satisferait le but ; l'agent escalade mais ne guérit pas, donc les
  derniers ticks sont du surplace (planificateur épuisé, escalade déjà émise).
  Ce n'est pas un bug — `MAX` joue son rôle de garde-fou de terminaison. Pour
  arrêter proprement plus tôt, ajouter un marqueur à la condition `UNTIL`
  (p.ex. `UNTIL goal.satisfied OR escalation.sent == yes`).

---

## 2. Ordre d'évaluation de la politique (`policy.py::check`)

Le **premier verdict négatif gagne**. Ordre exact :

1. **`NEVER` matchant, garde vraie → DENIED** (irrévocable — aucune
   probabilité, aucun `ALLOW` ne peut le racheter).
2. **`DENY` explicite matchant → DENIED.**
3. **Si des règles `ALLOW` existent pour cette cible :** l'une au moins doit
   matcher (garde vraie), sinon **DENIED** (deny-by-default local).
4. **Sinon**, le `DEFAULT` de l'agent tranche (`DEFAULT DENY` → DENIED ;
   `DEFAULT ALLOW` → on continue).
5. **`REQUIRE APPROVAL` matchant → APPROVAL_REQUIRED** (l'hôte doit approuver ;
   pas d'approbateur = refus, *fail-closed*).
6. Sinon → **ALLOWED**.

Conséquences à savoir expliquer :
- Un `NEVER` n'est **pas** un garde-fou d'exécution : le **planificateur**
  interroge la politique *par branche pendant sa recherche*, donc l'action
  interdite n'entre dans aucun plan (trace : « action écartée à la
  planification »). Elle n'est pas rejetée, elle n'est jamais engendrée.
- Sous incertitude, il suffit qu'**une seule** branche de l'état de croyance
  déclenche le `NEVER` pour que l'action soit écartée : on ne parie pas sur le
  hasard pour contourner un interdit.
- `REQUIRE APPROVAL` est un **coût** (via `APPROVAL_COST` du `PLANNER`), pas un
  mur : le planificateur préfère spontanément une route autonome moins chère,
  et ne réclame un humain que faute de mieux.

**Modèle des régimes** (pour un outil avec `ALLOW ... IF P(h) >= a`,
`NEVER ... WHEN g`, `REQUIRE APPROVAL WHEN P(h) < b`, avec `a < b`) :

| état | verdict |
|---|---|
| garde `g` vraie | **interdit** (NEVER), quelle que soit P |
| `g` faux, `P >= b` | **autorisé, autonome** |
| `g` faux, `a <= P < b` | **autorisé sous approbation** |
| `g` faux, `P < a` | **refusé** (aucun ALLOW satisfait → défaut) |

---

## 3. Anatomie d'une ligne bayésienne (`∿`, `bayes.py`)

Format : `∿ <hypothèse>: <prior> → <posterior> (≥/< seuil <t>) [naïf : <raw>]
[<évidence> = vrai/faux (<±bits> bits) | ... ]`.

- **`prior → posterior`** : l'a priori (déclaré, ou appris via `PRIOR FROM`) et
  le postérieur **calculé** — jamais écrit à la main ni sorti du LLM.
- **`≥ seuil t`** : le postérieur est comparé au `THRESHOLD` (et c'est *lui*,
  pas un type, qui garde les `POLICY ... IF P(h) >= ...`).
- **`[naïf : raw]`** : ce qu'un bayésien *naïf* aurait obtenu en comptant les
  évidences comme indépendantes. L'écart avec le postérieur réel mesure
  l'honnêteté du modèle.
- **bits par évidence** : chaque évidence observée déplace les log-cotes de ±N
  bits ; +N appuie l'hypothèse, −N la dément. Une évidence **non observée**
  n'apparaît pas et laisse les cotes inchangées (indéterminé ≠ faux).
- **`— absorbé par le groupe X`** : au sein d'un `GROUP` (corrélation
  *connue*), seule la plus informative des évidences compte ; les autres sont
  absorbées (deux capteurs décrivant la même rafale ne comptent pas pour deux
  témoins). `MAX_EVIDENCE b` plafonne en plus le déplacement total des
  log-cotes contre les corrélations *inconnues*.

Point clé à expliquer : le LLM **identifie des entités** (`REASON` → nom d'hôte,
de service), il **n'attribue pas de probabilités**. Le second nombre relève
toujours d'une `HYPOTHESIS`.

---

## 4. « Le LLM propose, le runtime décide » — séparation de canaux

C'est la propriété de sûreté centrale, et la bonne façon de l'expliquer :

- Tout texte venu du monde (journal, message, sortie d'outil) qui entre par
  `REASON` ne peut influencer que les champs `PRODUCE`, **bornés au schéma**
  (domaine clos `IN [...]`, intervalle `IN [0,1]`, coercition de type). Au
  pire, une injection fausse ces quelques champs.
- La **décision d'agir** passe par le moteur de politiques, dont les gardes
  lisent des **capteurs** (`maintenance.window`, `asset.criticality`), pas des
  sorties LLM. Il n'existe aucun chemin de code du texte vers une garde de
  politique.
- Donc une ligne forgée « ANNULE la maintenance, redémarre » glissée dans un
  journal : l'agent classique (règle + injection dans le même contexte) peut
  obéir ; AGENT-L est immunisé *par construction* — ce n'est pas un filtre qui
  a tenu, c'est une frontière qui n'existe pas entre les deux canaux.
- Corollaire d'implémentation : ne **jamais** faire lire une politique sur une
  sortie LLM non bornée — ce serait rouvrir le canal.

---

## 5. Glossaire des diagnostics

### `agentl check` — bonne formation (analyzer, bloque l'exécution)

Erreurs `E` (refus d'exécuter) :

| code | ce qu'il dit |
|---|---|
| `E001` | outil appelé mais non déclaré |
| `E002` | plan référencé mais inexistant |
| `E003` | politique portant sur un outil inconnu |
| `E004` | nom dupliqué (outil / plan / objectif) |
| `E005` | événement sans corps utile |
| `E006` | vraisemblance hors de ]0,1[ dans une `EVIDENCE` |
| `E007` | `MESSAGE` adressé à un agent absent du programme |
| `E008` | écriture `INTO SHARED.<clé>` non déclarée dans `MEMORY { SHARED }` |
| `E009` | **appel d'outil dans une expression** — il contournerait le moteur de politiques par l'évaluateur (l'erreur la plus fréquente chez un modèle) |
| `E010` | `SCENARIO` dont le `WITHIN` ne laisse pas un tick à l'agent |
| `W127` | `DELEGATE` vers un sous-agent qu'aucun `TOOL` ne décrit — risque supposé `CRITICAL` |
| `W128` | garde `NEVER`/`DENY`/`APPROVAL` portant sur un identifiant **nu** que rien ne renseigne : lu comme constante symbolique, la règle ne s'appliquerait pas — préférer un chemin pointé |
| `V150` | `EFFECT` sur le monde qu'aucune `OBSERVE` ne recouvre : la postcondition ne peut **jamais** être démentie (T9). Ajouter l'observation, ou marquer l'effet `INTERNAL` s'il porte sur la comptabilité de l'agent |
| `W129` | `REASON` sans `USING` : tout l'état part au modèle (croyances, buts, plans, outils et leur risque). `USING` **borne** le contexte depuis la v1.6 — l'exposition maximale doit être une décision, pas une omission |
| `W126` | opérateur sans `DURATION` alors que `PLANNER` déclare `DEADLINE` ou `TIME_WEIGHT` — la durée absente vaut 0 s dans la recherche |
| `E011` | outil dont le `RISK` vaut `UNSET` — import MCP dont le risque n'a pas été tranché par l'auteur. Aucun `ALLOW *` ne le fait taire. |
| `W130` | `USING` listant un chemin qu'un `POLICY { NEVER SEND … }` retient : le modèle recevra `⟦retenu⟧`. Deux déclarations qui se contredisent — retirer l'une, plutôt que laisser le runtime arbitrer |
| `W131` | chemin observé dont dépend un `NEVER`/`DENY`/`APPROVAL` et qui ne déclare aucun `ON UNKNOWN` : un capteur muet bloquera l'agent (fail-closed) sans conduite déclarée. `ESCALATE` nomme la panne, `DEGRADE` pose un repli assumé |
| `W132` | `ON UNKNOWN DEGRADE` sur un chemin dont dépend un interdit : la règle sera jugée sur une valeur **déclarée**, pas mesurée. Vérifier que le repli est celui qui **bloque** — sinon c'est la faille §7.1 rouverte avec la bénédiction du programme |
| `W133` | `REASON` déclarant plus de huit champs `PRODUCE` : au-delà, un modèle à raisonnement épuise son budget de sortie avant d'avoir fermé le JSON, la réponse est tronquée et **tous** les champs retombent sur leur défaut — des zéros parfaitement plausibles. Découper le `REASON`, réduire le schéma, et garder les seuils par `reason.degraded` |
| `W134` | champ `PRODUCE` dont le `DEFAULT`, une fois lié, **ne déclenche pas** l'interdit qu'il garde. Troncature, JSON invalide et oracle muet mènent tous au défaut : si l'interdit ne s'y applique pas, la panne *ouvre* l'action. Le contrôle est une simulation — champ lié à son défaut, reste indéterminé — et se tait quand la garde ressort indéterminée, puisqu'un `NEVER` indéterminé s'applique |
| `W135` | garde de **déclenchement** — `WHEN` de plan, `IF`, règle `DECIDE` — comparant `!=` un chemin pointé que rien dans le programme ne renseigne. Ces gardes-là passent par l'évaluateur ordinaire, pas par Kleene : une absence y rend `UNDEFINED != valeur`, donc **vrai**, et le plan se déclenche sur l'ignorance à chaque tick. Le sens du défaut fait sa gravité — une typo dans un `==` ne déclenche rien et se voit au premier essai, dans un `!=` elle déclenche tout et ressemble à un agent qui marche. Les gardes de *politique* sont hors périmètre : Kleene les couvre déjà (§7.1) |

Avertissements `W` (à corriger ou justifier un par un) : `W101` outil
HIGH/CRITICAL non couvert par une politique · `W102` plan à effet de bord sans
`VERIFY` · `W103` chemin observé jamais utilisé · `W104` croyance jamais
rafraîchie · `W105` aucun `GOAL` · `W106` plan inatteignable · `W107`
hypothèse dont le postérieur n'est jamais consulté · `W108` planificateur actif
mais aucun `EFFECT` · `W109` opérateur dont l'`EFFECT` ne peut être déclenché
(`INPUT` non liable) · `W110` but du planificateur qu'aucun `EFFECT` ne
satisfait · `W111` `MESSAGE` que personne ne reçoit · `W112` `ON MESSAGE` que
personne n'émet · `W113` `OUTCOME` ne totalisant pas 1 · `W114` probabilité non
calibrée alimentant une garde · `W115` sortie LLM non bornée alimentant un
seuil · `W116` `THRESHOLD` hors amplitude atteignable · `W117` `GIVEN` posant
un chemin inexistant · `W118` attente ne portant sur aucun chemin qu'un
`EFFECT` produit · `W135` garde de déclenchement comparant `!=` un chemin que
rien ne renseigne.

### `agentl verify` — cinq théorèmes prouvés sur l'AST

- **T1 — Aucun appel interdit n'aboutit.** `V101` branche morte (l'appel est
  toujours interdit sur ce chemin) ; `V102` exposition (l'appel peut être tenté
  dans un état interdit, sans repli) ; `V110` (info) opérateur sous `NEVER`
  exclu de la synthèse — comportement correct ; `V114` **preuve dégradée** : la
  condition de chemin a dépassé la borne de clauses du solveur, qui a répondu
  « satisfiable » sans l'établir — le verdict de ce site n'est pas démontré et
  T1 rend « ◐ BORNÉ » au lieu de « DÉMONTRÉ ».

  Le parcours des plans est **sensible au flot** depuis la v1.9 : une condition
  de chemin dit « pour arriver ici, ceci était vrai », ce qui n'est pas « ceci
  est vrai ici ». Un `SET` intercalé entre la garde et l'appel périme la garde,
  qui est retirée des prémisses ; et s'il écrit une constante sur un chemin
  qu'aucune re-perception ne peut contredire, il devient au contraire un fait
  invocable. Sans cela, un plan qui posait `SET asset.criticality = CRITICAL`
  dans une branche gardée par `criticality == LOW` passait T1 sans un mot,
  pendant que le runtime bloquait l'appel à chaque tick.
- **T2 — Sous chaque interdit, le but reste atteignable ou l'escalade est
  déclarée.** Depuis la v1.4 la recherche va jusqu'à son **point fixe**, donc
  une absence de route est *démontrée* et non constatée. Ce qui est réfuté
  n'est pas l'absence de route, c'est le fait de **caler en silence** :
  - `V111` (info) route de repli trouvée sous l'interdit ;
  - `V112` (info) aucune route permise **mais escalade déclarée** — conduite
    attendue, pas un défaut ;
  - `V105` **erreur** : aucune route et aucune escalade → l'agent calera.
    Remède : `IF planner.exhausted … THEN <plan d'escalade>` ;
  - `V105` en avertissement : recherche arrêtée avant son point fixe —
    l'absence de repli n'est **pas** prouvée ;
  - `V113` (avert.) but non atteint dans la borne : augmenter `--depth` ;
  - `V106` (erreur) but hors d'atteinte des opérateurs déclarés.
- **T3 — Aucune capacité n'est morte.** `V109` (erreur) seuil `P(h) >= s`
  inatteignable car le modèle plafonne sous `s` — recaler le seuil OU la
  calibration ; capacités jamais atteignables.
- **T4 — La surface exposée au LLM est bornée.** `V104` le LLM peut déclencher
  un outil à risque sans garde d'état. Sans `DECIDE.REASON`, T4 est trivialement
  démontré : le modèle ne sélectionne aucun plan.
- **T5 — Les attentes déclarées sont atteignables** (v1.4, le seul théorème qui
  prouve ce que *l'auteur* a exigé) : `V120` (info) recensement ; `V123` (info)
  attente atteignable, avec la route ; `V121` **erreur** : aucune combinaison
  d'`EFFECT` permise n'entraîne l'attente — politique trop stricte ou attente
  fausse ; `V122` (avert.) « ◐ BORNÉ », la profondeur a arrêté la recherche ;
  `V124` (info) invariant, hors de portée du théorème → renvoyé à `agentl test`.

**Direction de sûreté** : le vérificateur ne se trompe que dans un sens — il
peut manquer un défaut, il n'en invente pas (le solveur ne déclare
insatisfiable que sur démonstration). Un `verify` qui se tait sur un site
d'appel l'a donc *prouvé* sûr. Corollaire : un verdict « borné, non réfuté »
(`V113`, `V122`) ne prouve **rien** et ne doit jamais être présenté comme un
succès.

### `agentl boundary` — frontière hôte/agent

`B000` hôte introuvable (la norme de nommage n'est pas respectée) · `B001`
comparaison à une valeur métier · `B002` filtrage d'une collection · `B003`
`break`/`continue` dans une boucle de l'hôte · `B004` tri ou extremum (donc
priorisation) · `B005` seuil chiffré appliqué dans l'hôte · `B006` nom de
fonction qui décide (`should_`, `classify_`, `select_`…) · `B007` hôte
volumineux servant un `.agent` sans aucune garde. Levée par
`# BOUNDARY-OK: <motif>` couvrant l'instruction entière ; sans motif, rien
n'est levé. Voir `business-workflows.md` §1.

---

## 6. Métriques de fin de run (à interpréter)

`blocked` > 0 : des actions ont été refusées (politique ou contrat) — normal
dans un contre-factuel, suspect dans un nominal. `domain_clamps` > 0 : le LLM
est sorti d'un domaine `IN [...]` et a été écrêté (souvent = le domaine n'est
pas assez explicite, ou le modèle est faible). `effect_drift` > 0 : un `EFFECT`
déclaré n'a pas été confirmé par la re-perception — un plan a menti sur son
résultat, **et tout `SCENARIO` reposant sur cet `EFFECT` est menteur aussi**.
`approvals` : nombre de passages par un humain. `plans_synthesized` : plans
construits par le planificateur (non écrits à la main). Dans le journal HTML
(`--html`), ces cinq-là passent au rouge dès qu'elles sont non nulles.

Trois métriques de **résilience** (v1.8.1), à lire ensemble :
`tool_failures` compte les appels d'outil qui ont levé ; `circuit_open` compte
les appels **non tentés** parce que le disjoncteur était ouvert. Un
`circuit_open` élevé n'est pas un défaut — c'est la preuve que l'agent a cessé
de marteler un service mort. Mais `circuit_open > 0` avec `tool_failures`
faible signale un seuil trop bas pour la fiabilité réelle de la dépendance.
`reason_degraded` compte les `REASON` dont l'oracle n'a rendu **aucun** champ :
non nul dans un nominal, c'est une panne d'oracle déguisée en indécision, et
toute décision prise ce tick-là repose sur des valeurs par défaut.

Attention à ne pas confondre `blocked` et `circuit_open` : le premier compte
les interdits — une propriété du programme —, le second une indisponibilité —
une propriété du monde. Les additionner dans un tableau de bord donne un
chiffre qui ne veut rien dire.

### Comparer un fait booléen

Les faits que le moteur pose lui-même — `sensors.<chemin>.available`,
`tools.<nom>.available`, `reason.degraded`, `last_action.blocked` — sont des
booléens, alors qu'un `.agent` ne sait écrire que des symboles. Les deux
vocabulaires mordent, `== false` comme `== no`, `== true` comme `== yes` :

```agentl
NEVER close_batch WHEN tools.update_row.available == false   // équivaut à == no
```

Avant la v1.8.1, la comparaison se faisait sur le texte : `str(False)` valait
`"False"`, jamais `"false"`, et la garde était **fausse alors que l'outil
était bien indisponible**. `check` ne disait rien, l'auteur croyait la panne
gardée, l'interdit ne s'appliquait pas. C'était le pire mode de défaillance du
langage — une garantie disparue sans un seul signal — et il touchait
`sensors.*.available` depuis son introduction. Un chemin **jamais posé** reste,
lui, indéterminé : sous un `NEVER`, il s'applique.

---

## 7. Rejeu déterministe (`--record` / `replay`, v1.4)

`agentl run --record J.json` journalise les **huit points de franchissement de
frontière** — `Host.read/invoke/ask/approve/drain`, `DELEGATE`,
`LLM.reason/select_plan`. Tout ce qui sort du runtime est là, et rien d'autre.

`agentl replay J.json` re-dérive la décision **sans capteur, sans outil, sans
réseau**. Ce qu'il faut savoir pour l'expliquer :

- **Le verdict est l'égalité caractère pour caractère** de la trace, scellée
  sur une empreinte SHA-256 — pas l'absence de plantage. Un rejeu vert prouve
  que la décision ne dépendait que du journal.
- **Les pannes se rejouent en pannes**, avec le nom de classe d'origine : un
  service injoignable au moment de l'enregistrement le reste au rejeu. C'est ce
  qui rend le run de panne auditable.
- **Journal tronqué, réordonné, ou arguments d'outil différents** →
  `ReplayDivergence`. C'est un diagnostic, pas un incident : il nomme le point
  exact où le monde a changé.
- **Le journal est scellé sur le programme.** Si le `.agent` a changé depuis
  l'enregistrement, `replay` refuse ; `--force` passe outre, `--source` désigne
  un autre programme.
- **`meta.lossy`** : une valeur non sérialisable a été rencontrée à
  l'enregistrement — le journal est lacunaire, le rejeu ne prouve plus rien de
  complet. À lire avant de conclure.
- Les sociétés multi-agents sont couvertes. API : `Journal`, `RecordingHost`,
  `RecordingLLM`, `ReplayHost`, `ReplayLLM`, `verify_trace`
  (`agentl/replay.py`, SPEC §26).

## 8. Théorèmes de sécurité v1.5

- **T6 — provenance corrélée** : réfute toute sortie LLM pilotant une cible
  risquée sans `ATTESTS`, toute auto-confirmation non observée, corrélation
  globale/non locale et action sur texte brut sans garde d’injection.
- **T7 — terminaison sûre** : réfute une transition satisfaisant le `LOOP
  UNTIL` lorsqu’elle ne consulte pas le backlog observé encore non résolu.
- **T8 — vivacité de la société** (v1.6, `agentl/liveness.py`, SPEC §21) : le
  seul théorème du **programme entier**, évalué dès qu'il y a plus d'un agent.
  Point fixe sur un graphe d'attente de signaux (`MESSAGE`) : réfute un cycle
  d'attente sans point d'entrée (`V130`), un signal attendu que nul n'émet
  (`V132`), un contexte qui ne s'exécute jamais (`V135`). Points d'entrée
  reconnus : garde `WHEN`, `EVENT`, règle `DECIDE`, `ON VERIFY.FAIL`, `THEN`.
  `DECIDE.REASON` rend le verdict « non prouvé » (`V136`) — le modèle peut
  proposer tout plan déclaré. `SHARED` n'est pas relu par le runtime, donc
  T8 ne couvre que `MESSAGE` (`V137`).

# Auto-amélioration (`agentl autoloop`)

`check`, `verify`, `boundary` et `test` disent si un programme est
**recevable**. Aucun ne dit s'il **tient sur d'autres données que celles que
l'auteur a écrites**. C'est le seul trou que cette commande comble. Rendu par
`agentl/autoloop.py`.

```bash
python3 -m agentl autoloop examples/xxx.agent            # diagnostic seul
python3 -m agentl autoloop examples/xxx.agent --model gemini-3.1-flash-lite -o /tmp/corrige.agent
python3 -m agentl autoloop examples/xxx.agent --host-pass
```

## Ce qu'elle fait, dans l'ordre

1. **La barrière** — analyse, preuve, frontière, scénarios déclarés, puis une
   cinquième que rien ne posait jusqu'ici : les **invariants du monde
   déclaré**. Tant qu'elle n'est pas franchie, aucun cas dérivé n'est produit :
   corriger sur des données inventées un programme qui ne se tient pas déjà
   debout n'a pas de sens.
2. **Les cas dérivés** — chaque `SCENARIO` est rejoué sur des mondes obtenus en
   modifiant son `GIVEN`.
3. **Le lot retenu** — ouvert seulement à la fin, jamais montré à la boucle.
4. **Le second temps** (`--host-pass`) — une exécution contre l'hôte réel, en
   lecture seule.

## L'oracle : qui dit ce qui est attendu ?

C'est la question qui décide de la valeur de tout le reste. Faire juger le
modèle qu'on corrige serait un oracle qui bouge : il suffirait qu'il écrive une
attente complaisante pour obtenir 100 %. La réponse retenue est l'**invariant** :
une propriété vraie quelles que soient les données, donc transportable sur un
monde que personne n'a prévu.

**Quatre invariants universels**, vrais pour tout programme sur toute donnée :

| | ce qui est exigé |
|---|---|
| `U1` | aucune erreur d'exécution |
| `U2` | aucun `VERIFY` déclaré en échec |
| `U3` | aucun outil interdit **sans condition** par la `POLICY` n'a été exécuté |
| `U4` | aucun outil exigeant une approbation n'a été exécuté sans elle |

`U3` et `U4` ne retiennent que les règles **sans garde**. Une règle gardée
dépend des données : la juger reviendrait à redemander au runtime ce qu'il
vient de décider — un contrôle qui ne peut jamais échouer ne contrôle rien.

**Les invariants déclarés** sont tes `EXPECT` **déjà vraies au tick 0** — la
distinction qu'`agentl test` fait déjà. `EXPECT { sandbox.stale_count != 0 }`
n'exige pas qu'un événement advienne : elle exige que la purge n'ait *pas* lieu.
Une telle attente se transporte. Une éventualité (`escalation.sent == yes`) ne
se transporte pas, puisque son avènement dépend précisément des données qu'on
vient de changer : elle n'est **jamais** jugée sur un cas dérivé, ni comptée.

## Ce que la boucle a le droit de changer

Transporter une attente ne suffit pas : encore faut-il ne pas détruire la
**raison** qui la rend vraie. « Un répertoire protégé n'est jamais purgé » tient
parce que `sandbox.protected = yes` ; faire varier `protected` ne réfute pas le
programme, cela change de scénario. D'où deux étages :

- **étage « données »** — seuls varient les chemins qui n'entrent dans **aucune**
  décision : ni garde de `POLICY`, ni `REQUIRES`, ni `WHEN`, ni `IF`, ni
  `VERIFY`, ni attente — et, de proche en proche, aucune **évidence d'une
  hypothèse citée** dans une de ces conditions. Sans cette dernière fermeture,
  faire varier un compteur qui n'apparaît dans aucune garde déplacerait quand
  même `P(…)`, franchirait le seuil d'un `ALLOW … IF P(…)`, et l'agent agirait —
  à bon droit. Le contexte de décision étant intact, les invariants déclarés y
  sont jugés ;
- **étage « contexte »** — tout peut varier, approbation de l'opérateur
  comprise, donc **seuls les invariants universels sont jugés**.

Les valeurs ne sont pas tirées dans le vide : elles viennent des **littéraux
auxquels le programme compare lui-même ce chemin**, encadrés (`t-1`, `t`,
`t+1`), plus l'**absence de donnée** — la panne la moins souvent écrite dans un
test et la plus fréquente en production. Un symbole ne bascule vers `yes`/`no`
que s'il en porte déjà un : proposer `yes` à un niveau de menace ne teste rien.

## Le lot retenu, et le par-cœur

Une boucle qui voit tous les cas pendant qu'elle se corrige finit par coder les
cas, pas la tâche. Une part (`--holdout`, 0,3 par défaut) est donc retenue :
jamais exécutée pendant la boucle, jamais citée dans une consigne de correction.
Les cas sont engendrés **une seule fois**, depuis la première version qui
franchit la barrière — les réengendrer donnerait à la boucle le pouvoir de
choisir ses propres épreuves.

Trois codes de sortie, et ils ne disent pas la même chose :

| code | lecture |
|---|---|
| `0` | l'agent tient, lot retenu compris |
| `1` | l'agent ne tient pas |
| `3` | **appris par cœur** : 100 % sur ce que la boucle a vu, des ruptures sur le lot retenu |

Confondre `1` et `3` ferait passer le plus dangereux des deux pour le plus
bénin — celui qui affiche 100 %.

## L'arrêt

« Boucler jusqu'à 100 % » n'est pas une condition d'arrêt : si le modèle n'y
arrive pas, la boucle tourne et brûle des jetons. Trois freins :

- `--max-attempts` (6) — le plafond ;
- `--budget SECONDES` — le coût ;
- `--patience` (2) — l'absence de progrès, le plus utile des trois : un modèle
  qui tourne en rond le fait très vite.

La consigne de correction porte **ce qui s'est passé** — contrôles en échec,
mondes dérivés et invariants rompus — jamais un cas retenu. Elle dit aussi que
supprimer un `SCENARIO` compte comme un échec : les cas étant figés, un scénario
disparu rend ses cas perdus, pas escamotés.

Sans `-o`, **rien n'est écrit**. Une boucle d'auto-amélioration qui réécrit son
fichier d'entrée fait perdre la seule version que l'auteur avait relue.

## Le second temps (`--host-pass`)

C'est le seul moment où la donnée vient d'ailleurs que du programme : les
capteurs réels parlent. Mais un agent encore en correction ne doit pas agir pour
de bon, donc tout outil qui déclare un effet de bord ou porte un `RISK`
`HIGH`/`CRITICAL` est **neutralisé**, et l'approbation n'est jamais accordée.

Conséquence à lire, et le rapport la nomme : un outil neutralisé n'a pas produit
son `EFFECT`, donc **la dérive d'effet n'est pas jugée** dans cette passe. Un
second temps vert ne veut pas dire « les `EFFECT` déclarés sont vrais » — il
faudrait laisser l'agent agir, ce que cette passe refuse par construction. Ce
que la passe prouve : le programme ne casse pas sur des perceptions réelles, et
sa politique tient hors du monde déclaré.

## Ce qu'elle ne fait pas

- Elle ne prouve rien sur l'hôte : comme `test`, elle s'exécute contre le
  **monde déclaré**. Un `EFFECT` qui ment rend ses cas verts. C'est `boundary`
  et le registre de dérive d'effet qui traitent cette question.
- Elle n'invente pas d'attente. Un programme sans `SCENARIO` ne produit aucun
  cas dérivé : il n'y a pas de `GIVEN` à faire varier, et le rapport le montre
  par un compte de cas nul plutôt que par un vert trompeur.
- Elle ne remplace pas le run de panne ni le contre-factuel : elle raisonne sur
  des données, pas sur des pannes d'infrastructure.

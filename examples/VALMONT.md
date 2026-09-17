# VALMONT — supervision d'usine par une hiérarchie d'agents AGENT-L

Trois agents AGENT-L qui surveillent l'**Usine Valmont — Site Nord**
(métallurgie, usinage et traitement de surface : 85 000 m², 640 personnes,
4 lignes, 3×8), simulée par **ENTERPRISE-SIM** sur le port 4500.

```
              VALMONT_SUPERVISION  ── commande l'installation
                 │                 ── dialogue avec le responsable de site
     DELEGATE ───┼─── DELEGATE
       │                    │
 VALMONT_ALARMES     VALMONT_PRODUCTION
 (surveille)         (optimise)
```

Chaque étage est un agent complet : ses capteurs, son inférence bayésienne, sa
politique, ses scénarios, sa preuve. Les sous-agents **ne touchent jamais**
l'installation : ils rendent un jugement borné par un contrat `EXPECT`, et le
superviseur décide des commandes sous **sa** politique.

| Fichier | Rôle |
|---|---|
| `valmont_supervision.agent` / `.py` | superviseur : délègue, commande, dialogue, clôt la ronde |
| `valmont_alarmes.agent` / `.py` | sous-agent alarmes : `verdict_alarme`, `classe_alarme`, `confiance_alarme` |
| `valmont_production.agent` / `.py` | sous-agent production : `conseil_production`, `goulot_production`, `confiance_production` |
| `valmont_sim.py` | client HTTP d'ENTERPRISE-SIM — transport et perception, **aucune décision** |

`confiance_production` est la confiance dans le **conseil rendu**. Le
sous-agent calcule `P(bridage_utilites)` : ce postérieur est utilisé directement
pour `brider`, et son complément `1 - P(bridage_utilites)` pour `relancer`.

La clôture locale ne dépend pas de la disponibilité du chatbot. Son résultat
porte `notification=envoyee` ou `notification=incertaine`; un timeout du chat
reste visible dans la trace sans rouvrir la ronde ni déclencher un second POST.

---

## Démarrage

ENTERPRISE-SIM doit tourner (`cd ~/ENTERPRISE-SIM && npm start`).

```bash
cd ~/AGENT-L

# Ronde à blanc : rien n'est envoyé à l'usine, oracle déterministe local.
python3 -m agentl run examples/valmont_supervision.agent --html /tmp/valmont.html

# Ronde réelle : les commandes partent, l'oracle Gemini est autorisé,
# et l'approbation humaine est accordée.
VALMONT_LLM_EXTERNE=1 VALMONT_DRY_RUN=0 VALMONT_APPROVAL=oui \
    python3 -m agentl run examples/valmont_supervision.agent \
    --html /tmp/valmont.html --record /tmp/valmont.json

# Chaque sous-agent tourne et se vérifie SEUL (norme X.agent ↔ X.py).
python3 -m agentl run examples/valmont_alarmes.agent
python3 -m agentl run examples/valmont_production.agent
```

| Variable | Défaut | Effet |
|---|---|---|
| `VALMONT_DRY_RUN` | `1` | **aucune commande n'est envoyée**. `0` pour agir réellement. |
| `VALMONT_LLM_EXTERNE` | *(non)* | opt-in avant toute sortie de données vers un LLM hébergé. |
| `VALMONT_APPROVAL` | *(refus)* | `oui` accorde l'approbation humaine. L'absence de réponse vaut refus. |
| `VALMONT_SIM_URL` | `http://localhost:4500` | adresse du simulateur. |
| `VALMONT_TOKEN` | `demo-operator-key` | jeton ENTERPRISE-SIM. |

---

## Ce que le programme garantit

> **v1.9 — le volet alarmes a été refait.** L'agent traitait un *compteur* :
> `alarm.ack *`, `maintenance.repair *`, et une route qui fermait le volet sans
> rien faire. Un compteur ne dit pas QUELLE alarme — une alarme jamais
> inventoriée était acquittée quand même, et rien ne garantissait qu'il ne
> restait plus rien à la clôture. Désormais l'agent inventorie, **demande au
> responsable de site comment traiter**, vérifie chaque consigne reçue, puis
> traite au cas par cas ce que le responsable n'a pas couvert.

### 1. Le responsable de site **propose**, l'agent dispose

Sur **la cadence**, rien ne change : il garde un droit de **veto** et rien de
plus. Sa réponse n'entre que par un `REASON` à domaine clos `[accord, refus]`
dont le repli est **`refus`**.

```agentl
NEVER brider_cadence   WHEN avis_responsable != accord
NEVER relancer_cadence WHEN avis_responsable != accord
```

Sur **les alarmes**, il gagne un droit de **proposition** — et il faut le dire
franchement, parce que c'est un changement de nature. Ses commandes n'arrivent
plus comme du texte à interpréter mais comme des objets `{command, args}` : le
chatbot d'ENTERPRISE-SIM les émet déjà, et le serveur tourne avec
`chat.allowCommands = false`, donc il n'en exécute aucune lui-même et répond
« utiliser un agent externe via `POST /commands` ». Ce qui borne ce droit n'est
plus le silence de l'agent, ce sont **quatre contrôles qu'il conduit lui-même**,
tous écrits dans le `.agent` :

| # | Contrôle | Ce qui le porte |
|---|---|---|
| 1 | **Cohérence** — la commande existe au catalogue *publié*, ses arguments sont conformes au *schéma publié*, et sa cible est une entité que l'agent a lui-même observée ce tick | `proposition.recevable`, et surtout : une cible non attestée n'a **pas de jeton**, donc aucun effet possible |
| 2 | **Implications** — chaque commande porte une conséquence *déclarée dans le fichier* | `proposition.implication` ; une commande absente du tableau garde `inconnue`, et `inconnue` est un refus |
| 3 | **Échéance** — un blocage transitoire n'est pas un refus | `proposition.echeance` ; la consigne est **armée** avec sa garde, survit à la ronde, et repart quand le paramètre a évolué |
| 4 | **Opportunité** — l'agent conteste là où il a une route déclarée meilleure | `proposition.meilleure` : acquitter n'est pas réparer, une critique se remonte |

Le contrôle 2 est ce qui rend praticable le **catalogue complet** du simulateur
sans l'ouvrir : trois classes d'implication passent sous `REQUIRE APPROVAL`
(`coupure_d_alimentation`, `arret_equipement`, `engage_des_personnes`), et deux
sont interdites — `altere_la_simulation`, parce que modifier le banc n'est pas
une conduite d'exploitation, et `inconnue`, qui est le défaut fermé. Ouvrir une
commande de plus demande d'écrire une ligne dans le `.agent`, sous les yeux de
quelqu'un.

Observé en ronde réelle : le responsable a demandé `maintenance.repair` sur un
équipement dont aucun défaut n'était `ACTIVE`. L'agent l'a **refusé** avec son
motif (`sans_objet_c_est_deja_fait`) et le lui a dit.

### 2. Aucune alarme ouverte ne reste sans disposition

Chaque alarme inventoriée reçoit une disposition nommée — réparée, acquittée,
escaladée, ou laissée en l'état *avec son motif*. Trois verrous portent la
propriété, et **il faut retirer les trois** pour que la ronde se clôture sur un
inventaire incomplet (mesuré par test de mutation) :

```agentl
NEVER cloturer_l_inventaire WHEN alarmes.non_traitees > 0
REQUIRES { consignes.examinees == yes AND alarmes.non_traitees <= 0 }
NEVER cloturer_ronde        WHEN alarmes.non_traitees > 0
```

### 3. Une alarme critique n'est jamais traitée sans regard humain

```agentl
REQUIRE APPROVAL FOR reparer_le_defaut WHEN site.alarmes_critiques > 0
NEVER executer_consigne WHEN proposition.implication == marque_vue_sans_regard
                         AND site.alarmes_critiques > 0
```

Approbation *fail-closed* ; après un refus, l'agent **escalade et cesse**
(`DENY reparer_le_defaut IF approbation.refusee == yes`).

`alarm.ack *` acquitte **tout**, y compris la critique. Acquitter n'est pas
clôturer — l'alarme reste active — mais cela la marque « vue » sans que
personne ne l'ait vue, et c'est exactement le signal sur lequel un exploitant
compte. La v1.9 va plus loin que l'interdire : `*` **ne nomme aucune entité
observée**, il n'est donc pas attestable, et le traitement alarme par alarme le
remplace. Une consigne `alarm.ack *` du responsable est refusée pour cette
raison même.

### La règle d'or des interdits : fermer une route, c'est ouvrir une sortie

Les replis `renoncer_a_la_cadence` (COST 6) et `differer_une_alarme` (COST 4)
existent pour cette raison, et sont délibérément **plus chers** que l'action
qu'ils remplacent (5, et 2 ou 3) : un repli moins cher que l'action serait
préféré à elle, et masquerait l'interdit au lieu d'en tirer les conséquences —
son test de mutation cesserait de mordre.

Et la sortie du renoncement a elle-même une limite, qui est le seul usage du
postérieur dans le nouveau volet — **inversé** par rapport à l'ancien :

```agentl
NEVER differer_une_alarme WHEN confiance_alarme >= 0.70
```

Le posté­rieur ne sert plus à *autoriser* une intervention (le fait « ce défaut
est ouvert » suffit, et n'a pas besoin d'une probabilité) : il sert à **fermer
la route du renoncement**. Au-dessus du seuil, « je laisse en l'état » cesse
d'être une conduite acceptable, la ronde ne peut plus se clore, et l'escalade
part. Un incident majeur probable que personne n'a autorisé à traiter ne doit
pas se solder en silence.

### 4. Le verdict global **garde**, il ne **cible** pas

Distinction que la refonte rend nécessaire, et que le contrôle statique W122
énonce en général : un postérieur calculé sur l'état du *site* peut légitimement
autoriser ou interdire une classe d'actes, mais il ne peut pas choisir sur
QUELLE alarme agir. La cible vient des faits de l'élément (`a.defaut_actif`,
`a.severite`) et d'un jeton lié à elle ; le verdict dit seulement si le
diagnostic est exploitable.

### 5. Perception ratée ou oracle mort ⇒ **aucune commande**

Les deux sous-agents rendent `indetermine` dès que leur API se tait ou que leur
oracle retombe sur son repli, et le superviseur n'agit que sur un verdict exact :

```agentl
NEVER reparer_le_defaut     WHEN verdict_alarme     == indetermine
NEVER brider_cadence        WHEN conseil_production != brider
```

---

## Résultats des portes de qualité

| Porte | `valmont_alarmes` | `valmont_production` | `valmont_supervision` |
|---|---|---|---|
| `check` | 0 diagnostic | 0 diagnostic | 13 avert. (3 × W104 assumés, 10 × W109) |
| `test` | 7/7 | 5/5 | **12/12** |
| `verify` | **8/8 théorèmes** | **8/8** | **8/8**, 0 erreur |
| `boundary` | 0 décision | 0 décision | **tenue**, 21 levées justifiées |
| `pytest` | — | — | **811** (dont 19 nouveaux sur la v1.9) |

**Les trois `W104` assumés** portent sur `consigne.cadence_bridee` (70 %),
`consigne.cadence_nominale` (100 %) et `consigne.cadence_conformite` (50 %).
Ces croyances n'ont volontairement aucun capteur : ce sont des valeurs de
*politique*, et une usine qui pourrait redéfinir sa propre consigne dégradée
depuis le monde perçu n'aurait plus de garde-fou.

**Les dix `W109`** signalent des paramètres « non liables » : ce sont ceux des
outils appelés dans un `FOREACH`, dont les arguments viennent de l'élément
courant (`a.id`, `p.jeton`) et non d'un `BIND` global. C'est précisément le
motif que la v1.9 recherche — une preuve liée à la cible plutôt qu'au compteur.

**Les 21 levées `boundary`** sont toutes du même genre : résolution
d'identifiant (« dans quel registre du simulateur vit cet id »), tenue du
registre de dispositions, recettes de retour arrière, péremption de jetons, et
un assainissement de sortie que le `.agent` ne peut pas faire puisqu'il ne
manipule pas de chaînes. Aucune ne peut *autoriser* un effet — seulement
l'empêcher ou décrire comment le défaire.

### Tests de mutation — ce qui mord, et ce qui est sur-déterminé

Retirer la règle et rejouer le scénario. Ce tableau est **mesuré**, pas rédigé
d'après l'intention :

| Scénario | Mutation | Verdict |
|---|---|---|
| `responsable_muet_la_cadence_ne_bouge_pas` | `NEVER brider_cadence … avis_responsable != accord` | ✅ tombe |
| `rejet_hors_limites_aucune_relance` | `NEVER relancer_cadence … rejet_conforme != yes` | ✅ tombe |
| `accord_de_conformite_la_ligne_est_bridee` | `ALLOW brider_pour_conformite` | ✅ tombe |
| `veto_sur_la_cadence_n_empeche_pas_de_clore_le_volet_alarmes` | `ALLOW renoncer_a_la_cadence` | ✅ tombe |
| `aucune_cloture_tant_qu_une_alarme_reste_sans_disposition` | **les 3 verrous ensemble** (voir ci-dessous) | ✅ tombe |
| `usine_injoignable_la_ronde_ne_se_clot_pas` | `NEVER cloturer_ronde … read_ok != yes`, seul puis combiné | ❌ tient |
| `verdicts_non_concluants_aucune_commande` | 3 × `NEVER … verdict_alarme == indetermine`, puis 3 × `NEVER … conseil_production != …`, puis les `REQUIRES` | ❌ tient |

**Le cas de la clôture est instructif.** Aucun retrait isolé ne la fait tomber :
trois verrous portent la propriété n°2, et chacun suffit seul —
`NEVER cloturer_l_inventaire WHEN alarmes.non_traitees > 0`, le
`REQUIRES … AND alarmes.non_traitees <= 0` du même outil, et
`NEVER cloturer_ronde WHEN alarmes.non_traitees > 0`. Il faut retirer **les
trois** ; le scénario rompt alors au tick 3.

**Les deux qui tiennent sont sur-déterminés**, et c'est dit plutôt que caché :
la propriété y est portée par la garde de plan `WHEN site.read_ok == yes`, par
la chaîne de `REQUIRES diagnostics.done == yes`, et par les interdits, chacun
suffisant. Ils valent comme **non-régression**, pas comme test discriminant
d'un interdit précis. Aucune mutation essayée — simple, par paire ou par
triplet — ne les fait tomber.

### Ce que les scénarios ne peuvent pas atteindre

`GIVEN` pose des scalaires : la grammaire n'a pas de littéral d'enregistrement,
on ne peut donc pas y poser une liste d'alarmes ou de consignes avec leurs
champs. Le corps des `FOREACH` — le choix de route alarme par alarme, la
corrélation jeton/cible, le tri entre implications déclarées — n'est pas
atteignable par un scénario. C'est la même limite que `linux_defender.agent`.

Trois choses la compensent, et il faut les lire ensemble :

* les jugements des quatre contrôles sont des croyances **scalaires**, donc
  lisibles par une garde de politique — c'est pour cela qu'ils ne vivent pas
  dans les locales du `FOREACH` ;
* `agentl verify` démontre T1 et T2 sur les chemins de l'élément courant, ce
  qu'aucun scénario ne saurait énumérer ;
* **19 tests `pytest` hors ligne** montent l'hôte réel sur un transport de banc
  et exercent la projection des consignes sur les vraies données du simulateur :
  `alarm.ack *` non attestable, alarme inventée par le modèle non attestée,
  défaut déjà `REPAIRING` sans objet, argument hors des bornes publiées, nom
  hors catalogue écrasé sur `hors_catalogue`, jeton d'une alarme inutilisable
  sur une autre, jeton à usage unique, registre armé qui survit, se déduplique,
  se périme et se vide une fois la consigne tranchée.

### Runs de panne — obligatoires, et joués

| Épreuve | Résultat |
|---|---|
| **Usine injoignable** (`VALMONT_SIM_URL=http://127.0.0.1:1`) | `tool_calls=0`, aucune commande, ronde laissée **ouverte**, escalade tentée |
| **Oracle mort** (clé Gemini invalide) | tout retombe à `indetermine`, `avis_responsable=refus`, `reason_degraded=1`, **aucune commande** — seuls les deux outils « clore sans agir » s'exécutent |
| **Approbation refusée** | 1 sollicitation (et non 4), escalade au tick 4, `DENY` ensuite |
| **Rejeu** (`--record` / `replay`) | ✔ trace identique caractère pour caractère (sha256) |

### La barrière `autoloop` du superviseur, telle quelle

`autoloop` refuse le superviseur sur un point :

```
✘ invariants — alarme_critique_sans_humain_aucune_commande :
  U2 vérification en échec : but synthétisé
```

La propriété de sûreté **tient** (aucune commande à aucun tick, le scénario est
vert) ; ce qui est signalé, c'est qu'une vérification a échoué *en chemin*.
C'est inhérent au couple `REQUIRE APPROVAL` + planificateur : demander à un
humain qui refuse laisse nécessairement un plan synthétisé inachevé. L'exemple
de référence du dépôt, `examples/supervisor.agent`, échoue **exactement de la
même façon** (`boite_critique_sans_humain_rien_ne_bouge`). Ce n'est pas corrigé
ici, c'est documenté.

---

## Trois pièges rencontrés, et ce qu'ils coûtent

Ils ne se devinent pas à la lecture de la spécification ; chacun a été trouvé
par une porte, pas par une relecture.

**1. Un `EXPECT` de `DELEGATE` déclaré en `BELIEF` est masqué pour toujours.**
Le runtime dépose le retour d'un sous-agent dans l'espace *non fiable*, que
`State.get` consulte en **dernier** — pour qu'un sous-agent ne puisse rien
masquer. Déclarer `verdict_alarme` en `BELIEF` faisait donc lire éternellement
la valeur a priori : le superviseur demandait un diagnostic et n'agissait
jamais dessus, sans le moindre signal. La forme correcte est `OBSERVE`, l'hôte
consignant ce que chaque spécialiste a rendu.

**2. Un champ `OUTPUT` masque un symbole littéral dans son propre `EFFECT`.**
`OUTPUT { close: Symbol }` avec `EFFECT { ronde.status = close }` : `close` se
résolvait sur la sortie de l'outil (`yes`), pas sur le symbole. Le registre de
dérive l'a vu — `effect_drift=4`, « prédisait `yes`, observé `close` » — et
c'est la seule raison pour laquelle ça n'est pas passé inaperçu.

**3. Une règle `DECIDE` est morte après un plan en échec.** `exec_stmts` rend
la main dès que `verify_failure` est armé, et c'est `exec_stmts` qui exécute le
`THEN` d'une règle `DECIDE`. Après un plan synthétisé qui échoue, aucune règle
`DECIDE` ne peut plus rien déclencher — exactement la situation pour laquelle
une escalade existe. Ici l'escalade passe donc par une **garde de plan**
(`_enqueue`, insensible au drapeau), la règle `DECIDE` restant présente parce
que c'est elle que le théorème T2 exige pour transformer un `V105` (erreur) en
`V112` (conduite attendue).

---

## Le défaut qu'un run réel a trouvé, et qu'aucune porte n'avait vu

Run du 2026-08-13 16:09 (`valmont.json`) : verdict `acquittement` avec **88
alarmes en attente**, conseil `relancer`, et un responsable de site qui refuse
en argumentant (départ Q24 en cours de réparation).

La sûreté a tenu — veto honoré, aucune commande, escalade envoyée, rejeu
conforme. Mais l'agent **n'a pas acquitté les 88 alarmes**, alors que rien ne
le lui interdisait.

Cause : un conseil soumis au veto (`brider`, `relancer`) que le responsable
refuse laissait le volet cadence **sans aucune route** — `maintenir_cadence`
exige que le conseil ne soit ni l'un ni l'autre. `cloturer_ronde` devenait donc
inatteignable, et **le planificateur abandonne un but inatteignable en
entier** : il a laissé tomber le volet alarmes avec lui.

Aucune porte ne pouvait le voir : `verify` prouve qu'une route existe *sous
chaque interdit pris isolément*, pas sous la conjonction « conseil soumis au
veto **et** veto refusé ». Il fallait ce monde-là, et c'est un run réel qui l'a
produit.

Correctif : `TOOL renoncer_a_la_cadence` (COST 6, donc **plus cher** que
`brider`/`relancer` à 5 — renoncer ne doit jamais être préféré à l'action quand
celle-ci est permise, sans quoi cet outil masquerait le veto). Le scénario de
non-régression `veto_sur_la_cadence_n_empeche_pas_de_clore_le_volet_alarmes`
rejoue ce monde exact, et retirer l'outil le fait tomber.

Depuis le correctif, sur le même monde : le volet alarmes se traite et se clôt,
puis `renoncer_a_la_cadence` → `cloturer_ronde`, en 3 ticks, `verify_fail=0`.

## La critique que l'agent ne voyait pas

Question posée devant la supervision : *pourquoi l'agent n'a-t-il rien fait de
l'alarme critique A-000065 (rejet non conforme, STEP) ?* Trois causes
enchaînées, dont la première était un défaut de perception.

**1. Il ne l'avait jamais vue.** `active_alarm_text()` transmettait à l'oracle
les 8 alarmes les plus **récentes**. La critique, levée huit heures plus tôt,
était sortie de la fenêtre — chassée par quatre répétitions du *même*
déclenchement de départ. Tri par récence au lieu de gravité, et pas de
déduplication. Corrigé : regroupement sur la condition (équipement + message,
avec le compte d'occurrences), ordre par la gravité **déclarée par le
simulateur** puis par la date. Les compteurs, eux, l'avaient toujours vue —
le chiffre était juste, le texte était faux.

**2. Aucun outil n'y répondait, et c'était voulu — mais silencieux.**
`faultId` vide : dérive de procédé, pas panne d'équipement. Aucune commande du
catalogue n'y remédie (principe 7 : refuser, c'est ne pas déclarer l'outil).
L'agent doit alors le dire **nommément**, pas remonter un compteur. Nouveau
verdict `escalade_humaine` chez le spécialiste, nouvel outil
`escalader_une_alarme` chez le superviseur — et depuis la v1.9 cette escalade
est faite **alarme par alarme**, sur l'élément courant de l'inventaire, avec un
jeton lié à lui.

Ce que le chat reçoit ne contient que des **identifiants produits par le
simulateur** (`A-000065`, `STEP`) et un compteur — aucun libellé, aucun détail,
aucun texte lu dans le monde (principe 9). Le responsable de site a la
supervision sous les yeux.

**3. Le détail de l'alarme était trompeur** (côté ENTERPRISE-SIM).
`WWTP_NONCOMPLIANT` contrôle cinq paramètres, le détail n'en affichait que
trois — et le fautif n'était pas dedans :

```
avant : pH 6.8 [5.5-8.5] | DCO 194/300 | MES 68/100          ← tout dans les clous
après : HORS LIMITE — Temperature 33.6/30 degC · conforme : pH 6.8 … | Debit 32.8/45 m3/h
```

Résultat, sur l'usine réelle :

```
↳ [VALMONT_ALARMES] verdict=escalade_humaine classe=securite_environnement
🚨 ESCALADE : A-000065 sur STEP remontee au responsable (1 critique sans defaut)
ticks=3  verify_fail=0  effect_drift=0   — aucune commande sur l'installation
```

## Le levier réel, et le placebo que proposait le chatbot

L'escalade était honnête mais incomplète : **un levier existait**, et l'agent
ne l'avait pas.

Interrogé sur la marche à suivre, le chatbot a répondu qu'il fallait démarrer
la tour aéroréfrigérante TAR2. Le modèle du simulateur dit autre chose
(`server/sim/water.js:212`) :

```js
const surfaceLoad = state.lines.L3.actual_pct / 100;
wwtp.temp_c = approach(wwtp.temp_c, 18 + 0.35 * state.env.t_out_c + 6 * surfaceLoad, 1800, dt);
```

Deux termes : la **température extérieure** et la **charge de la ligne 3**.
`w.cooling` n'apparaît nulle part. Démarrer TAR2 est sans effet sur ce rejet —
et la commande serait de toute façon annulée au tick suivant, la tour étant
sous conduite automatique (`water.js:70`). Une explication plausible produite
par un modèle, pas une causalité. C'est exactement le motif pour lequel une
réponse en langue naturelle n'entre ici que par un `REASON` à domaine clos :
**elle peut bloquer une action, jamais en choisir une.**

Le vrai levier est le bridage de la ligne de traitement de surface, qui
déverse ses bains de rinçage vers la STEP. Câblé ainsi :

| Élément | Rôle |
|---|---|
| `site.rejet_conforme`, `site.charge_traitement_pct` | deux **faits** du simulateur, jamais un raisonnement |
| `consulter_sur_conformite` + `avis_conformite` | consultation **distincte**, avec sa propre question |
| `brider_pour_conformite` (coût 4) | `production.rate line=L3 rate_pct=50` |
| `signaler_rejet_non_conforme` (coût 7) | la sortie, si l'accord manque |

**Pourquoi une consultation séparée.** Le 2026-08-13, le responsable a refusé
une *relance* en invoquant précisément l'alarme environnementale. Réutiliser
`avis_responsable` aurait fait lire ce refus comme un refus de *brider* —
l'inverse de ce qu'il disait. Deux questions, deux croyances, deux défauts au
refus.

**L'interdit central**, et le seul de ce programme qu'aucun modèle ne peut
lever — ni un sous-agent, ni le chatbot :

```agentl
NEVER relancer_cadence  WHEN site.rejet_conforme != yes
NEVER maintenir_cadence WHEN site.rejet_conforme != yes
```

Le second a été trouvé par un scénario en échec : `maintenir_cadence` coûte 1,
il fermait le volet cadence *avant* que le bridage soit envisagé. « Le point de
fonctionnement est bon » ne se dit pas d'une usine qui déverse hors seuils.

Sur l'usine, la chaîne complète :

```
↳ [VALMONT_ALARMES] verdict=escalade_humaine  classe=securite_environnement
⌘ écartée [relancer_cadence — NEVER relancer_cadence]
« Oui, je donne mon accord pour brider la ligne L3 » → avis_conformite=accord
🌡  L3 bridee a 50 % (etait a 79 %) — conformite du rejet
ticks=3  verify_fail=0  effect_drift=0
```

Prédiction du modèle, aux conditions du jour (extérieur 25,3 °C, L3 à 79 %,
STEP à 31,3 °C pour une limite à 30,5) : à L3 = 50 %, la cible devient
`18 + 8,86 + 3,00 = 29,9 °C`. L'alarme retombe — avec **0,6 °C de marge**.
Par 35 °C dehors, le plancher `18 + 0,35 × 35 = 30,25 °C` dépasse le seuil à
production nulle : aucun agent ne peut alors rien, et c'est l'escalade qui
reste la bonne conduite.

## Frontière hôte / agent

`valmont_sim.py` ouvre des sockets, extrait des scalaires et concatène des
libellés. Le test appliqué à chaque ligne : *si la politique d'exploitation
changeait demain, faudrait-il modifier cette ligne ?*

Une correction née de ce test : `site.defauts_ouverts` lisait `summary.faults`,
qui compte aussi les défauts **déjà pris en charge** par les équipes postées du
simulateur. `maintenance.repair *` ne cible que les `ACTIVE` — l'agent lançait
donc une intervention sur un défaut en cours et l'API répondait « aucun defaut
actif », trois fois de suite, jusqu'à l'ouverture du disjoncteur d'outil. Le
fait doit parler le vocabulaire de la commande qu'il autorise.

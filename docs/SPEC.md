# AGENT-L — sémantique de référence (v1.9.0)

Ce document fixe le **sens** de chaque primitive. La grammaire (`agentl.ebnf`)
dit ce qu'on peut écrire ; ce document dit ce que cela fait. L'implémentation
de référence est le paquet `agentl/`.

---

## 1. Le modèle

Un agent est le neuvain

$$A = (S,\ G,\ B,\ O,\ T,\ P,\ M,\ V,\ \pi)$$

| symbole | rôle | déclaré par |
|---|---|---|
| $S$ | espace des états | implicite |
| $G$ | objectifs | `GOAL` |
| $B$ | croyances | `BELIEF` |
| $O$ | fonction d'observation | `OBSERVE` |
| $T$ | actions/outils | `TOOL` |
| $P$ | politiques | `POLICY` |
| $M$ | mémoire | `MEMORY` |
| $V$ | vérificateurs | `VERIFY` |
| $\pi$ | politique de décision | `PLAN`, `DECIDE`, `EVENT`, `PLANNER` |
| $H$ | hypothèses | `HYPOTHESIS` |

L'état agentique au pas $t$ est le triplet

$$s_t = (x_t,\ b_t,\ m_t)$$

- $x_t$ — **monde observé** : dictionnaire plat de chemins pointés vers valeurs.
- $b_t$ — **croyances** : chemin $\mapsto$ (valeur, confiance, source, fraîcheur).
- $m_t$ — **mémoire** : trois compartiments `SHORT_TERM`, `LONG_TERM`, `KNOWLEDGE`.

À quoi s'ajoute une portée d'exécution $\ell_t$ (résultats d'outils, sorties de
`REASON`, charge utile d'événement) qui ne survit pas au programme mais domine
les autres espaces lors de la résolution d'un chemin.

**Ordre de résolution d'un chemin** : $\ell_t \prec b_t \prec x_t \prec m_t$.
Un identifiant simple non résolu s'évalue en **symbole** (`healthy`, `HIGH`) —
c'est ce qui permet d'écrire `service.status == healthy` sans déclarer
`healthy`.

---

## 2. Le cycle

$$
\text{Observe} \to \text{Believe} \to \text{Evaluate} \to \text{Plan} \to
\text{Act} \to \text{Verify} \to \text{Learn}
$$

Formellement, un tick applique :

| phase | équation |
|---|---|
| `OBSERVE` | $o_t \sim O(s_t)$, puis $x_{t+1} \leftarrow x_t \oplus o_t$ |
| `RECEIVE` | remise des messages en attente (§19) |
| `UPDATE_BELIEFS` | $b_{t+1} = \mathrm{Update}(b_t, o_t)$ |
| `UPDATE_HYPOTHESES` | $p(h \mid e_t)$ pour chaque $h \in H$ (§16) |
| `EVALUATE_GOALS` | $g_t = \mathrm{eval}(G, s_t) \in [0,1]$ |
| `SELECT_PLAN` | $\rho_t = \pi(G, b_t, m_t, E_t)$, synthèse comprise (§17) |
| `EXECUTE` | $a_t \in \rho_t$, sous réserve de $P$ |
| `VERIFY` | $v_t = \mathrm{Verify}(s_{t+1}, G)$ |
| `UPDATE_MEMORY` | $m_{t+1} = \mathrm{Write}(m_t, s_{t+1})$ |

Le bloc `LOOP` d'un agent **ordonne** ces phases. En son absence, l'ordre
canonique ci-dessus s'applique. `LOOP UNTIL <expr> MAX n` fixe la condition
d'arrêt et la borne de terminaison — **`MAX` est obligatoirement fini** :
un agent AGENT-L termine toujours.

---

## 3. `GOAL` — un objectif n'est pas une instruction

Un objectif porte une **fonction d'évaluation**, pas un appel :

$$\mathrm{eval}(g, s) = \frac{1}{|C_g|}\sum_{c \in C_g} \mathbf{1}[\,c(s)\,] \in [0,1]$$

où $C_g$ réunit la condition `MAINTAIN`/`ACHIEVE` et chaque `TARGET`.
Le score global est la moyenne pondérée par `WEIGHT` :

$$\mathrm{score}(s) = \frac{\sum_g w_g \cdot \mathrm{eval}(g,s)}{\sum_g w_g}$$

Deux variables dérivées sont publiées dans $x_t$ et donc lisibles partout :
`goal.score` et `goal.satisfied` ($\equiv \mathrm{score} \ge 1$).

`MAINTAIN` et `ACHIEVE` s'évaluent identiquement ; la distinction est
sémantique (invariant permanent vs. condition terminale) et sert à
l'analyse statique et aux futurs planificateurs.

---

## 4. `BELIEF` — le monde ≠ ce que l'agent en croit

Une croyance est un quadruplet $(v, p, \sigma, \tau)$ : valeur, confiance
$p\in[0,1]$, source, horodatage.

```
BELIEF { server.health = degraded CONFIDENCE 0.82 SOURCE monitoring
                                  UPDATED 2026-07-26T20:15 }
```

Règle de mise à jour par l'observation :

$$
\mathrm{Update}(b, o)[k] =
\begin{cases}
(o[k],\ 0.95,\ \texttt{observation},\ t) & \text{si } k \in \mathrm{dom}(o)\\
b[k] & \text{sinon}
\end{cases}
$$

La confiance d'un capteur écrase celle d'un a priori : une observation est
plus fiable qu'une croyance héritée, jamais l'inverse. Chaque révision
$b_t[k] \neq b_{t+1}[k]$ est tracée. `x.confidence` est lisible en expression.

---

## 5. `OBSERVE` — perception sélective

Un observateur est un triplet (chemin, cadence, garde). Une observation
gardée n'est effectuée que si sa garde est vraie dans $s_t$, ce qui rend
l'agent **événementiel** plutôt que scrutateur :

```
OBSERVE network      WHEN incident.suspected
OBSERVE logs         WHEN logs.pending > 0
```

`EVERY <durée>` existait ici jusqu'en v1.8. Il ne cadençait rien — le corps de
la condition était un `pass` — et son unité mentait : le lexer rendait `60s` en
secondes quand le runtime les comparait au compteur de ticks, qui n'a aucune
durée déclarée dans le langage. Le mot reste **réservé**, et un programme qui
l'emploie est refusé à l'analyse : sans cela `OBSERVE logs EVERY 10s` se
relirait en silence comme deux chemins observés. La cadence s'exprime par une
garde, qui elle est honorée.

Un chemin observé est un contrat : l'hôte doit fournir un capteur pour lui.
Un chemin observé mais jamais lu déclenche `W103`.

---

## 6. `TOOL` — un contrat, pas une fonction

```
TOOL isolate_endpoint {
    INPUT       { host: String }
    OUTPUT      { isolated: Symbol }
    SIDE_EFFECT { network.acl, endpoint.connectivity }
    RISK        { operational = HIGH }
}
```

Le runtime dispose donc, pour chaque action : nom, description, signature
typée, effets de bord déclarés et niveau de risque. Trois conséquences
normatives :

1. **Un outil non déclaré n'existe pas.** L'appel est bloqué à l'exécution
   et rejeté à l'analyse (`E001`).
2. Le contrat `INPUT` est **vérifié avant** le passage en politique. Argument
   manquant, mal typé ou non déclaré ⇒ action refusée.
3. `RISK` et `SIDE_EFFECT` sont des **entrées du moteur de politiques** et de
   l'analyseur (`W101`, `W102`), pas de la documentation.

Types reconnus : `Number`, `Int`, `String`, `Bool`, `Symbol`, plus tout nom
de type applicatif (non contraint, mais conservé dans l'AST).

4. **Une clé `OUTPUT` est déclarée, pas fiable.** Le contrat dit la forme de
   la réponse, jamais qui l'a écrite : un outil qui rapporte une page, un
   ticket ou un courriel rapporte du texte d'un tiers. Les clés admises sont
   donc liées sous leurs formes **préfixées** — `result.<outil>.<clé>` dans
   les locales, `<outil>.<clé>` dans le monde — et sous leur nom **nu** dans
   `state.untrusted`, consulté en dernier (§7.3). Une réponse d'outil ne peut
   ainsi pas masquer une observation ni une croyance homonyme.

---

## 7. `POLICY` — couche de sécurité indépendante du LLM

C'est la primitive centrale du langage. **Aucun chemin d'exécution ne permet
d'atteindre un outil sans traverser le moteur de politiques.**

Algorithme normatif, pour une action $a$ dans l'état $s$ :

```
1.  ∃ NEVER r : cible(r) ∈ {a.tool, *} ∧ garde(r)(s)     → DENIED (irrévocable)
2.  ∃ DENY  r : idem                                      → DENIED
3.  soit A = { ALLOW r : cible(r) ∈ {a.tool, *} }
        si A ≠ ∅ ∧ ∄ r ∈ A : garde(r)(s)                  → DENIED
        si A = ∅ ∧ DEFAULT = DENY                         → DENIED
4.  ∃ REQUIRE APPROVAL r : cible(r) ∈ {a.tool,*} ∧ garde(r)(s)
                                                          → APPROVAL_REQUIRED
5.  sinon                                                 → ALLOWED
```

Propriétés voulues :

- **`NEVER` est irrévocable** : aucune conjonction d'`ALLOW`, aucune confiance
  du LLM, aucune approbation humaine ne le lève.
- **`DEFAULT DENY` rend l'ensemble des capacités explicite** : ajouter un outil
  ne l'autorise pas ; il faut l'autoriser.
- **`APPROVAL_REQUIRED` échoue fermé** : sans approbateur enregistré, l'action
  est refusée.

Les gardes disposent de variables réservées à la décision :
`action.tool`, `action.risk`, `action.origin` (`plan` | `llm` | `event`),
`action.confidence`, `action.args.*`, plus les arguments de l'appel liés par
leur nom.

Échelle ordinale intégrée pour les comparaisons de niveaux :
`NONE < INFO < LOW < MEDIUM < HIGH < SEVERE < CRITICAL`.
Elle rend `incident.severity >= HIGH` et `action.risk >= HIGH` bien définis.

---

### 7.1 Gardes indéterminées — logique trivalente (v1.6)

Le moteur annonçait échouer fermé : « une interdiction dont la garde est
indécidable s'applique ». L'implémentation ne tenait cette promesse que pour
les gardes qui **lèvent une exception**. Or le cas le plus fréquent ne lève
rien :

```agentl
NEVER restart_service WHEN maintenance.window == open
```

Capteur indisponible, chemin mal orthographié, observation pas encore faite :
la comparaison rendait `False` en silence, l'interdit ne s'appliquait pas, et
**l'action passait**. Mode de défaillance le plus dangereux du langage — la
trace montre un appel autorisé, le certificat reste intact et vide.

Les gardes de politique suivent désormais la **logique trivalente forte de
Kleene** :

$$\neg U = U \qquad U \wedge \bot = \bot \qquad U \wedge \top = U
\qquad U \vee \top = \top \qquad U \vee \bot = U$$

Kleene et non « inconnu = vrai » : `x == open OR service.critical` reste vrai
dès que le second membre l'est. Une logique plus grossière transformerait toute
garde touchant un chemin absent en interdiction, et un outil qui bloque tout
n'est pas plus sûr — il est débranché.

**Le sens de la sûreté dépend de l'effet** :

| Effet | Garde indéterminée |
|---|---|
| `NEVER`, `DENY` | **s'applique** — on ne peut pas prouver l'innocuité |
| `ALLOW` | ne satisfait pas |
| `REQUIRE APPROVAL` | route vers l'humain |

**Portée délibérément limitée aux gardes de politique.** Pour une garde de
plan, « indéfini → faux » est *déjà* fermé : le plan ne se déclenche pas, donc
rien n'agit. C'est seulement pour un `NEVER` que la même convention s'ouvre.

**Limite assumée.** Un identifiant **nu** non résolu est une *constante
symbolique* — c'est ainsi que `open`, `yes` et `contained` sont des valeurs.
`WHEN dry_run == no` compare donc deux symboles et rend faux, sans passer par
l'indétermination. La trivalence ne peut rien y faire : l'évaluateur n'a pas
rendu « indéfini », il a rendu une constante. `W128` signale ce cas — une
garde `NEVER`/`DENY`/`APPROVAL` portant sur un nom simple que le programme ne
renseigne nulle part — et invite à écrire un chemin pointé. Les noms de
paramètres d'outil ne sont pas concernés : `NEVER block_ip WHEN ip_address ==
"127.0.0.1"` est l'idiome canonique, et le contrat `INPUT` est vérifié avant la
politique.

**L'argument prime sur la locale homonyme (v1.8).** Dans la portée d'une garde
de politique, le nom d'un paramètre désigne **ce qui part vers l'outil**, même
si le programme porte par ailleurs une locale ou une croyance du même nom.
Sans cette règle, `SET target = safe` suivi de `delete(target=protected)`
faisait juger `safe` à un `NEVER … WHEN target == protected` : l'interdit se
contournait par une affectation antérieure, sans rien de malveillant dans le
programme. La locale recouverte est consignée dans la trace (`POLICY`) — deux
déclarations qui se rencontrent en silence valent un avertissement, même quand
le moteur tranche bien. Le chemin explicite `action.args.<nom>` reste
disponible et rend toujours la même valeur ; hors garde de politique, rien ne
change : la portée n'existe que le temps d'un contrôle.

### 7.2 `DELEGATE` passe par le moteur (v1.6)

`DELEGATE` appelait directement la fonction enregistrée dans
`host.subagents` : ni contrôle de politique, ni risque déclaré, ni
approbation. Un programme sous `DEFAULT DENY` voyait donc son sous-agent
s'exécuter quand même — le moteur n'était pas « le point de passage obligé »
qu'annonce §7, et T1 ne disait rien de ce chemin. Trois exemples du dépôt
exploitaient la faille sans le savoir.

La cible de la règle est le **nom du sous-agent** : `NEVER forensic`,
`ALLOW forensic IF …` s'écrivent comme pour un outil, et `DEFAULT DENY` le
couvre sans rien écrire. Un sous-agent décrit par un `TOOL` homonyme emprunte
son risque et ses effets de bord ; sinon `W127` le signale et le risque
supposé est `CRITICAL` — un sous-agent est une fonction Python opaque, rien
n'empêche d'y écrire en base ou de lancer une commande.

### 7.3 Provenance : une charge utile ne masque rien (v1.6)

`State.get()` consulte les locales **en premier**. Or les charges utiles de
message et d'événement y étaient liées sous leur nom nu. Conséquence :

```
monde observé : asset.criticality = CRITICAL
charge utile  : asset.criticality = LOW
→ NEVER wipe WHEN asset.criticality == CRITICAL ne tirait pas
```

Une donnée **non fiable venue de l'hôte désactivait une interdiction
absolue**. C'est exactement ce que le langage prétend rendre impossible.

Deux espaces désormais :

- les formes **préfixées** (`payload.x`, `event.source`, `message.from`) vont
  dans les locales — leur provenance est lisible dans le programme ;
- les noms **nus** vont dans `state.untrusted`, consulté **en dernier**, après
  le monde, les croyances et la mémoire.

Une charge utile ne masque donc plus rien : elle ne comble que ce que rien
d'autre ne renseigne. Le comportement utile est préservé — un événement
apporte toujours ses données au plan qu'il déclenche — et la collision est
tracée quand elle survient. Le retour d'un `DELEGATE` suit la même règle : il
est de provenance externe au même titre.

Depuis la v1.8.2, le retour d'un **outil** aussi. C'était la dernière entrée
qui liait un nom nu dans les locales, alors que c'est la porte par laquelle
arrive l'injection **indirecte** — page web, ticket, corps de courriel, RAG :

```
monde observé   : criticality = CRITICAL
retour d'outil  : criticality = LOW        ← rédigé par un tiers
→ NEVER wipe WHEN criticality == CRITICAL tirait de nouveau
```

Le contrat `OUTPUT` borne la **forme** de la réponse et jette les clés non
déclarées ; il ne dit rien de sa **provenance**. Les trois frontières —
charge utile, sous-agent, outil — sont désormais traitées pareil.

### 7.4 `USING` borne réellement la surface exposée au modèle (v1.6)

`REASON … USING { a, b }` se lisait comme la déclaration de ce que le
programme accepte de montrer à un modèle. Le runtime, lui, ajoutait
simplement un champ `focus` : croyances complètes, buts, plans et **catalogue
d'outils avec leur risque** partaient de toute façon.

Depuis la v1.6, un `USING` non vide **restreint** le contexte transmis :

```
{ "tick": 12, "focus": { "sandbox.log_content": "…" } }
```

Ni outils ni plans : `REASON` produit des valeurs, il ne choisit pas d'action.

Un `REASON` **sans** `USING` reçoit toujours le contexte complet — c'est le
défaut historique, et le restreindre à *rien* casserait le raisonnement d'un
programme qui n'a rien demandé. `W129` le signale : l'exposition maximale doit
être une décision, pas une omission. La sélection de plan (`DECIDE.REASON`)
n'est pas concernée : le modèle doit y voir les plans pour en nommer un.

C'est précisément la limite que `NEVER SEND` lève en v1.8 (§32) : `USING`
borne ce qu'un `REASON` **montre**, il ne dit rien du point de sortie le plus
large. Un programme ne pouvait donc pas retenir un secret.

### 7.5 Une approbation se dit explicitement (v1.6)

`Host.approve()` rendait `bool(self.approver(request))`. Tout ce qui n'est pas
vide approuvait donc : la chaîne `"no"`, `"refusé"`, un dict
`{"decision": "denied"}`, l'objet de réponse d'un service qui vient de
refuser. Le seul cas correctement traité était `False` — celui qu'un
approbateur déjà bien typé rend de lui-même.

Sur le chemin qui existe précisément pour arrêter une action à risque,
l'ambiguïté se résout désormais en refus. Approuvent, et rien d'autre :

- le booléen `True` ;
- un mot d'accord reconnu, symbole ou chaîne (`yes`, `y`, `true`, `ok`,
  `approve`, `approved`, `oui`, `accord`, `accepte`, `accepté`), à la casse et
  aux espaces près.

Toute autre valeur refuse — y compris un mot inconnu : on ne devine pas une
intention sur ce chemin-là. La règle est appliquée aussi côté runtime, un
hôte pouvant être une sous-classe, un enregistreur de rejeu ou un service
distant.

### 7.6 Un booléen ne satisfait pas un contrat numérique (v1.6)

`bool` hérite de `int` en Python : `isinstance(True, int)` est vrai. Un outil
déclarant `retention_days: Int` acceptait donc `False`, et l'hôte recevait
`0` — un `purge(retention_days=False)` passait le contrat et supprimait tout.

Un contrat d'outil est une frontière avec le monde réel : il refuse une valeur
d'un autre genre plutôt que de la convertir en silence. `Int`, `Number` et
`Float` refusent désormais un booléen. L'inverse était déjà correct
(`isinstance(1, bool)` est faux), l'asymétrie vient de Python.

Même règle à la coercition des sorties de modèle : `float(True)` vaut `1.0`,
c'est-à-dire qu'un modèle répondant `true` là où un `Number` est attendu
produisait une confiance **maximale**, par accident de typage. Ce cas est
traité comme une réponse hors schéma : repli sur la valeur déclarée.

### 7.7 Un booléen du runtime vaut le symbole qui le nomme (v1.8.1)

Le versant symétrique, et il était bien plus grave. Certains faits sont posés
par le moteur lui-même, en `bool` : `sensors.<chemin>.available` (§31),
`tools.<nom>.available`, `reason.degraded`, `last_action.blocked`. Un `.agent`,
lui, ne sait écrire que des symboles.

L'égalité comparait ces deux mondes par leur texte. `str(False)` vaut
`"False"` — jamais `"false"`, jamais `"no"`. Donc :

    NEVER close_batch WHEN tools.update_row.available == false

était **faux alors que l'outil était bien indisponible**. L'interdit ne
s'appliquait pas, `agentl check` ne signalait rien, et l'auteur avait toutes
les raisons de croire la panne gardée. C'est le mode de défaillance le plus
coûteux que le langage puisse produire — une garantie qui disparaît sans
émettre un seul signal — et il touchait `sensors.*.available` depuis son
introduction en v1.6.

L'égalité fait désormais le pont dans les deux vocabulaires du langage :
`true`/`yes` dénotent le vrai, `false`/`no` le faux, et un symbole hors de ce
vocabulaire n'est égal à aucun booléen. Le test de type reste strict — `bool`
héritant de `int`, `1 == true` ne doit pas devenir vrai au passage, et ne l'est
pas. Un chemin **jamais posé** garde son statut distinct : indéterminé, donc
appliquant l'interdit (§7.1).

## 8. `PLAN`, `DECIDE`, `EVENT` — la fonction $\pi$

$\pi$ est déterminée dans cet ordre de priorité :

1. **`EVENT`** — les événements déposés par l'hôte sont drainés ; leur charge
   utile est liée à la portée locale, la garde `WHEN` est évaluée, le corps
   s'exécute (typiquement : `THEN <plan>`).
2. **Garde de plan** — `PLAN p WHEN <expr>` met `p` en file si `<expr>` tient.
3. **`DECIDE.RULES`** — règles déterministes `IF … THEN …`.
4. **`PLANNER`** (v0.5) — si la file est encore vide, le planificateur tente
   de **synthétiser** une séquence d'actions atteignant les buts, à
   l'intérieur de l'espace autorisé par $P$ (§17).
5. **`DECIDE.REASON`** — en dernier recours seulement : le LLM est sollicité.
   Il **propose un nom de plan** ; le runtime le confronte à la liste des
   plans déclarés et rejette tout nom inventé.

Cette hiérarchie est le point d'architecture : le déterministe est consulté
avant le probabiliste, la recherche avant la génération, jamais l'inverse.

Un `PLAN` est une suite de `STEP`, chacune une suite d'instructions. Les
étapes sont exécutées dans l'ordre ; la première `VERIFY` en échec interrompt
la séquence.

---

## 9. `VERIFY` — primitive de premier ordre

`VERIFY` produit `PASS` ou `FAIL` et **transfère le contrôle** en cas d'échec :

```
VERIFY "service rétabli" {
    CONDITION service.status == healthy
    ON FAIL { RETRY 1
              create_ticket("Redémarrage sans effet", "Escalade niveau 2") }
}
```

Résolution de l'échec :

1. gestionnaire local `ON FAIL`, sinon gestionnaire global `ON VERIFY.FAIL` ;
2. si le gestionnaire contient `RETRY n`, le **plan entier** est rejoué au plus
   $n$ fois ;
3. les tentatives épuisées, la variable `still_failed` est posée à vrai et le
   reste du gestionnaire s'exécute (typiquement `ESCALATE` ou un plan de
   secours).

Une comparaison portant sur une valeur indéfinie vaut **faux** et est
consignée : une vérification ne peut pas réussir par ignorance. Cela vaut
**dans les deux sens** — `VERIFY { incident.resolved != open }` échoue tant
que le chemin n'existe pas. C'est le régime strict, propre à `VERIFY` : une
garde de plan, elle, demande « dois-je agir ? », et « pas encore fait » est
une réponse légitime tirée d'un chemin jamais posé (§8).

**Deux absences ne se comparent pas** — nulle part, quel que soit le régime.
`missing.a == missing.b` et `missing.a != missing.b` valent tous deux faux.
Jusqu'en v1.8 le premier valait **vrai**, le singleton interne servant de
valeur : une garde se satisfaisait de sa propre ignorance, et sous `DEFAULT
ALLOW` cela conduisait à une action réelle. Une absence face à une valeur
**connue** reste en revanche « différente » : c'est ce que dit
`request.sent != yes`, et en faire un faux rendrait tout plan d'amorçage
inatteignable.

**Re-perception ciblée.** Avant d'évaluer sa condition, `VERIFY` rafraîchit
les chemins observés que celle-ci met en jeu. C'est la lettre de la sémantique
— $v_t = \mathrm{Verify}(s_{t+1}, G)$ porte sur le monde *après* l'action,
pas sur les croyances héritées du début du tick. Sans cette règle, un plan qui
répare puis vérifie échouerait systématiquement.

---

## 10. `MEMORY` — apprendre sans toucher aux poids

Trois compartiments (`SHORT_TERM`, `LONG_TERM`, `KNOWLEDGE`) et une règle
d'écriture conditionnelle :

```
MEMORY WRITE { WHEN  isolated == confirmed
               STORE { attack_type, suspected_host, confidence }
               INTO  LONG_TERM }
```

L'écriture est **idempotente sur enregistrement successif identique** : la
mémoire opérationnelle ne se remplit pas de doublons tant que le monde n'a
pas changé. C'est ce mécanisme qui donne à l'agent une expérience cumulée
sans réentraînement.

---

## 11. `REASON`, `ASK`, `DELEGATE` — les trois oracles

Ces trois primitives ont la même forme logique : *sortir du programme pour
obtenir une information, puis rentrer sous contrat.*

| primitive | oracle | contrat de retour |
|---|---|---|
| `REASON` | LLM | schéma `PRODUCE { champ: Type }`, coercition imposée |
| `ASK` | humain | réponse liée à `answer`, `DEFAULT` si silence/timeout |
| `DELEGATE` | autre agent | `EXPECT { … }`, champs manquants signalés |

Dans les trois cas le runtime **impose** le schéma : ce n'est jamais l'oracle
qui décide de la forme de sa réponse.

### 11.1 Absence, repli explicite et nombres non finis (v1.8.2)

Pour `REASON`, absence et valeur numérique invalide ne sont pas des valeurs
métier. Le runtime applique les règles suivantes dans cet ordre :

1. un champ rendu est coercé vers son type déclaré ;
2. `NaN`, `+inf` et `-inf`, sous forme numérique ou textuelle, sont rejetés ;
3. un champ absent ou numérique non fini reçoit son `DEFAULT` **s'il est
   explicitement déclaré et valide** ;
4. sans `DEFAULT` explicite, il est lié à `UNDEFINED` et reste donc
   indéterminé pour la logique trivalente des politiques (§7.1).

Le runtime n'invente plus `0`, `false` ou `unknown` pour remplacer le silence
d'un oracle. Cette règle est normative dès qu'une sortie `PRODUCE` alimente
une politique : l'indétermination applique un `NEVER`/`DENY`, ne satisfait pas
un `ALLOW` et route un `REQUIRE APPROVAL` vers l'humain. `reason.degraded` et
`reason.missing` rendent en outre le silence observable par le programme.

`W134` signale soit l'absence de `DEFAULT` explicite sur un champ qui garde une
action, soit un `DEFAULT` qui rendrait l'interdit définitivement faux. Le
premier cas reste sûr à l'exécution — il échoue fermé — mais laisse le contrat
de panne implicite ; le second peut ouvrir l'action et doit être corrigé.

Depuis la v0.4, la division du travail est explicite : **le LLM identifie des
entités, il n'attribue plus de probabilités.** Un modèle de langage est bon
pour dire « l'hôte suspect est PC-042 » et mauvais pour dire « avec 0.94 de
confiance ». Ce second nombre relève de `HYPOTHESIS`.

---

## 12. L'invariant d'architecture

> **Le LLM propose ; le runtime décide.**

Chaîne complète d'une action, sans court-circuit possible :

```
proposition (plan | DECIDE | LLM)
        ↓
contrat TOOL déclaré ?          → non : BLOCKED (E001)
        ↓
contrat INPUT respecté ?        → non : BLOCKED
        ↓
moteur de politiques            → DENIED : BLOCKED
        ↓
approbation humaine requise ?   → refus : BLOCKED
        ↓
exécution par l'hôte
        ↓
liaison des sorties déclarées + marquage des effets de bord
        ↓
VERIFY
        ↓
MEMORY
```

Le LLM n'a jamais accès à l'hôte, ni à `world`, ni à la file de plans. Le
contexte qui lui est transmis est une **projection en lecture seule** :
objectifs et scores, croyances avec leur confiance, catalogue d'outils avec
leur risque, noms de plans. Il ne reçoit pas de shell — il n'en existe pas.

---

## 13. Analyse statique — codes de base

Ce que le langage permet de prouver avant tout appel de modèle :

| code | nature | contrôle |
|---|---|---|
| `E001` | erreur | outil appelé mais non déclaré |
| `E002` | erreur | plan référencé mais inexistant |
| `E003` | erreur | politique portant sur un outil inconnu |
| `E004` | erreur | nom dupliqué (outil, plan, objectif) |
| `E005` | erreur | `EVENT` sans corps : l'événement est consommé et ne déclenche rien |
| `W101` | avert. | outil `HIGH`/`CRITICAL` non couvert par une politique |
| `W102` | avert. | action à effet sans VERIFY postérieur lié sur tous les chemins examinés |
| `W103` | avert. | observation jamais utilisée |
| `W104` | avert. | croyance utilisée mais jamais rafraîchie |
| `W105` | avert. | aucun objectif déclaré |
| `W106` | avert. | plan inatteignable |

`agentl run` **refuse d'exécuter** un programme comportant une erreur. C'est
la différence de nature avec un prompt : un prompt ne se compile pas.

---

## 14. Écarts assumés avec l'étude initiale

L'étude utilisait une syntaxe illustrative. Trois normalisations ont été
nécessaires pour obtenir une grammaire non ambiguë :

1. **Mots réservés en majuscules uniquement.** `retry` de l'étude s'écrit
   `RETRY` ; en contrepartie `healthy`, `none`, `unknown` restent du
   vocabulaire libre.
2. **`REASON` prend une tâche quotée et un schéma de sortie.** Le texte libre
   de l'étude (`determine most likely root cause`) devient
   `TASK "…" PRODUCE { … }` : sans schéma, la sortie du LLM n'est pas
   vérifiable.
3. **`HYPOTHESIS` porte des vraisemblances, pas une confiance.** L'étude
   écrivait `CONFIDENCE: 0.87` dans le bloc `HYPOTHESIS` — un nombre posé à
   la main, donc invérifiable. La v0.4 le remplace par `PRIOR` +
   `LIKELIHOOD`/`GIVEN_NOT` par évidence, et **calcule** le postérieur.
   `CONFIDENCE` reste accepté comme alias de `PRIOR`.
4. **`POLICY … FOR risk > HIGH`** devient
   `REQUIRE APPROVAL FOR * WHEN action.risk >= HIGH` : la cible et la
   condition sont séparées, ce qui rend le moteur décidable.

Le reste de l'étude — y compris `STEP 1:` sans accolades et `TOOL { f() g() }`
en forme abrégée — est accepté tel quel par le parseur.

---

## 15. Analyse statique — codes ajoutés en v0.4 / v0.5

| code | nature | contrôle |
|---|---|---|
| `E006` | erreur | vraisemblance hors de $]0,1[$ dans une `EVIDENCE` |
| `W107` | avert. | hypothèse dont le postérieur n'est jamais consulté, ou évidence non informative ($P(e\mid h) = P(e\mid\lnot h)$) |
| `W108` | avert. | `PLANNER` activé mais aucun outil ne déclare d'`EFFECT` |
| `W109` | avert. | opérateur dont un paramètre n'est ni lié par `BIND` ni homonyme d'un chemin connu |
| `W110` | avert. | but du planificateur qu'aucun `EFFECT` déclaré ne peut satisfaire |

---

## 16. `HYPOTHESIS` — la confiance cesse d'être déclarée (v0.4)

Jusqu'en v0.3, une confiance était un nombre écrit à la main. C'est un aveu
d'impuissance : l'agent affirme une incertitude qu'il n'a pas mesurée.

Une hypothèse porte désormais un a priori et une liste de **tests
observables**, chacun avec ses vraisemblances :

```
HYPOTHESIS credential_attack {
    PRIOR 0.05
    EVIDENCE {
        wazuh.alert_count > 20             LIKELIHOOD 0.92 GIVEN_NOT 0.06
        network.anomaly_score > 0.75       LIKELIHOOD 0.85 GIVEN_NOT 0.20
        endpoint.integrity == compromised  LIKELIHOOD 0.75 GIVEN_NOT 0.08
    }
    THRESHOLD 0.90
    EXPLAINS  threat.kind
}
```

Révision en cotes, incrémentale et numériquement stable :

$$O(h) = \frac{p}{1-p}, \qquad O(h \mid e) = O(h)\prod_i \mathrm{LR}_i,
\qquad p(h \mid e) = \frac{O(h\mid e)}{1 + O(h \mid e)}$$

$$
\mathrm{LR}_i =
\begin{cases}
P(e_i \mid h) \,/\, P(e_i \mid \lnot h) & e_i \text{ observé vrai}\\[2pt]
(1 - P(e_i \mid h)) \,/\, (1 - P(e_i \mid \lnot h)) & e_i \text{ observé faux}\\[2pt]
1 & e_i \text{ indéterminé}
\end{cases}
$$

Trois points de sémantique méritent d'être explicites.

**Indéterminé ≠ faux.** `Evaluator.test` renvoie faux sur une valeur
indéfinie — c'est le bon choix pour un `VERIFY`, qui ne doit pas réussir par
ignorance. Ce serait le mauvais choix ici : ne pas avoir observé une évidence
et l'avoir observée absente n'ont pas du tout le même effet sur le
postérieur. L'inférence utilise donc un test à trois valeurs, et une évidence
indéterminée laisse les cotes inchangées.

**Le résultat est traçable.** Chaque évidence expose sa contribution en
$\log_2 \mathrm{LR}_i$ — en bits. La trace montre non seulement le
postérieur, mais quelle observation l'a produit :

```
∿ credential_attack: 0.05 → 0.970 (≥ seuil 0.90)
  [wazuh.alert_count > 20 = vrai (+3.94 bits)
   | endpoint.integrity == compromised = vrai (+3.23 bits)
   | network.anomaly_score > 0.75 = vrai (+2.09 bits)]
```

**Le postérieur alimente les politiques.** Il est publié sous
`<nom>.posterior`, `<nom>.supported`, `<nom>.bits`, et les fonctions
épistémiques `P(h)`, `CONFIDENCE(chemin)`, `UNCERTAINTY(chemin)` sont
utilisables dans n'importe quelle expression. On peut donc conditionner une
autorisation à l'état épistémique de l'agent :

```
ALLOW isolate_endpoint          IF   P(credential_attack) >= 0.95
REQUIRE APPROVAL FOR *          WHEN UNCERTAINTY(threat.status) > 0.30
```

`EXPLAINS <chemin>` réécrit une croyance dès que le seuil est franchi, avec le
**postérieur comme confiance** et `hypothesis:<nom>` comme source. La confiance
devient dérivée et auditable.

L'indépendance conditionnelle des évidences sachant $h$ est celle du
classifieur bayésien naïf : fausse en toute rigueur, suffisante pour du
triage. Ce qui compte est que le nombre produit soit **reproductible et
explicable**, ce qu'une confiance sortie d'un LLM n'est pas.

---

## 17. `PLANNER` — synthétiser au lieu de sélectionner (v0.5)

En v0.3, $\pi$ choisissait parmi des `PLAN` écrits à la main. En v0.5 elle
peut en **construire** un.

Le modèle de transition ne demande aucune primitive nouvelle : il vient de
contrats que le langage possédait déjà, complétés de trois champs.

```
TOOL quarantine_account {
    INPUT       { account: String BIND suspected_account }
    RISK        MEDIUM
    REQUIRES    { endpoint.inspected == yes AND suspected_account != unknown }
    EFFECT      { threat.status = contained }
    COST        4
}
```

| champ | rôle STRIPS |
|---|---|
| `REQUIRES` | précondition |
| `EFFECT` | postcondition |
| `COST` | coût (à défaut : dérivé de `RISK` — `LOW`=1 … `CRITICAL`=25) |
| `BIND` | instanciation des paramètres depuis l'état |

Un outil sans `EFFECT` reste utilisable dans un plan écrit à la main mais est
**invisible au planificateur** : on ne planifie pas avec ce dont on ne sait
pas modéliser l'effet.

### L'invariant de la recherche

C'est le point qui compte, et il tient en une phrase :

> Pour chaque action candidate, le moteur de politiques est interrogé dans
> l'état abstrait où elle serait exécutée. Une action refusée n'est pas
> engendrée.

Un plan violant un `NEVER` n'existe donc **dans aucune branche de l'arbre de
recherche** — pas seulement au moment de l'exécution. La politique cesse
d'être un filtre a posteriori pour devenir une **borne de l'espace de
recherche**. C'est la différence entre un agent qu'on empêche d'agir mal et
un agent qui ne conçoit pas d'agir mal.

Corollaire opérationnel : `REQUIRE APPROVAL` n'est pas un mur mais un **coût**
(`APPROVAL_COST`). Le planificateur préfère spontanément une solution
autonome et ne sollicite un humain que lorsqu'il n'en existe pas de moins
chère. Dans l'exemple de référence :

```
inspect_endpoint(2) → quarantine_account(4)          = 6    ← retenu
inspect_endpoint(2) → isolate_endpoint(8) + appro(10) = 20
```

### Recherche

Best-first sur les cotes de coût, avec pour heuristique le nombre de buts
encore faux. Cette heuristique n'est **pas admissible** en toute généralité —
une action peut satisfaire plusieurs buts — donc l'optimalité n'est pas
garantie : c'est une recherche gloutonne informée, bornée par `MAX_DEPTH` et
`MAX_NODES`. L'honnêteté sur ce point vaut mieux qu'un A\* revendiqué à tort.

L'état abstrait est un calque sur l'état réel : la recherche ne mute jamais
$s_t$. Les nœuds sont dédupliqués sur la projection de l'état aux chemins
apparaissant dans les buts, préconditions et effets.

### Retour à l'exécution

Le plan synthétisé est matérialisé en un `PLAN` ordinaire, exécuté par le
même chemin que n'importe quel autre — donc **repassant intégralement par le
moteur de politiques**. La recherche a filtré, elle n'a rien autorisé.

Il se termine toujours par la vérification du but qui l'a produit : une
action planifiée n'est jamais réputée réussie sur la foi de son modèle
d'effet. Symétriquement, un `EFFECT` exécuté est enregistré comme croyance de
confiance 0.80 et de source `effect:<outil>` — inférieure à celle d'une
observation (0.95), pour qu'une perception ultérieure prime toujours sur une
postcondition supposée.

Enfin, si aucune séquence permise n'atteint le but, `planner.exhausted`
devient vrai. C'est un fait de première classe, sur lequel une règle `DECIDE`
peut escalader. L'agent ne force pas : il constate l'impasse et le dit.

---

### 17.1 Le temps dans la planification (v1.6)

Jusqu'ici le planificateur ignorait qu'une action prend 45 s et une autre
4 min : les deux coûtaient pareil. Or arbitrer entre rapide-et-bruyant et
lent-et-sûr **est** le problème du domaine.

```agentl
TOOL kill_switch       { … COST 20  DURATION 45s  }
TOOL careful_isolation { … COST 2   DURATION 4min }

PLANNER { ENABLE  ACHIEVE threat.status == contained
          DEADLINE 2min  TIME_WEIGHT 0.1 }
```

`DURATION` se note avec une unité (`ms`, `s`, `min`, `h`, `d`) et se
normalise en secondes. **Un nombre nu est refusé** : l'unité est ce qui
distingue une durée d'un coût, et la deviner reviendrait à deviner l'échelle.
Une durée n'est **jamais dérivée** — un coût se devine à partir du risque, une
durée non.

Deux leviers, volontairement distincts :

| Champ `PLANNER` | Nature | Effet |
|---|---|---|
| `DEADLINE <durée>` | borne **dure** | un plan dont la durée cumulée dépasse la borne n'est pas engendré |
| `TIME_WEIGHT <n>` | taux de change | `coût_effectif += n × durée_en_secondes` |

`TIME_WEIGHT` vaut `0.0` par défaut : un programme antérieur planifie à
l'identique, la v1.6 est une généralisation stricte. Le langage fournit le
taux de change, l'auteur le fixe — câbler une préférence pour la vitesse
serait décider du domaine à sa place.

**Deux façons d'accumuler, et c'est voulu.** Le coût s'accumule en
*espérance*, pondéré par la probabilité de déclenchement : c'est un budget. La
durée s'accumule **pleine**, sans pondération : une action qui ne se déclenche
qu'une fois sur trois prend tout son temps quand elle se déclenche, et une
échéance se tient sur le pire cas. Confondre les deux ferait passer pour
tenable un plan qui ne l'est pas.

Un plan écarté par l'échéance l'est **à la planification**, comme une action
interdite : il n'est pas produit puis rejeté, il n'est pas engendré. Le motif
est nommé (`careful_isolation — échéance dépassée (4min > 2min)`).

`W126` signale un opérateur sans `DURATION` **lorsque, et seulement lorsque**,
`DEADLINE` ou `TIME_WEIGHT` est déclaré : une durée absente vaut zéro dans la
recherche, et un zéro non déclaré est indiscernable d'une mesure — le
planificateur préférerait l'opérateur muet pour la seule raison qu'il se tait.
Sans modèle de temps, l'omission ne biaise rien et n'est pas signalée.

**Correction de fond apportée au passage.** La recherche marquait un état
comme visité définitivement dès qu'une route y menait, **fût-ce la plus
chère** : à effets égaux, le plan retenu dépendait de l'ordre de déclaration
des outils et non de leur coût. Le critère annoncé — le score — n'était donc
pas celui appliqué. La frontière conserve désormais, par état, un **front de
Pareto sur (coût, durée)** : une route n'est écartée que si elle est dominée
sur les deux dimensions. Deux dimensions et non une, parce qu'une route plus
chère mais plus rapide reste utile sous `DEADLINE`.

## 18. Analyse statique — codes ajoutés en v0.6 / v0.7

| code | nature | contrôle |
|---|---|---|
| `E007` | erreur | `MESSAGE` adressé à un agent absent du programme |
| `W111` | avert. | message émis que personne ne reçoit |
| `W112` | avert. | `ON MESSAGE` pour un nom que personne n'émet |
| `W113` | avert. | les `OUTCOME` d'un outil ne totalisent pas 1 |

`E007`, `W111` et `W112` relèvent de `check_program` : un message est un
contrat entre deux agents, aucun des deux ne peut le vérifier seul.

---

## 19. `MESSAGE` et `SHARED` — la société d'agents (v0.6)

`DELEGATE` (v0.3) traite le sous-agent comme un oracle : appel synchrone,
réponse immédiate, contrat `EXPECT`. C'est la bonne primitive quand on sait
qui interroger et qu'on attend la réponse. La v0.6 couvre l'autre cas.

### Messagerie

```
MESSAGE check_host { TO network_agent
                     PAYLOAD { host = alert.host, severity = alert.severity } }

ON MESSAGE traffic_verdict { WHEN payload.confidence >= 0.80
                             THEN { SET verdict = payload.verdict } }
```

Un message est déposé dans la boîte du destinataire et traité **au tick
suivant**, en phase `RECEIVE`, avant toute perception. Un agent ne peut donc
jamais agir sur un message et l'observer dans le même souffle : l'asynchronie
est structurelle, pas simulée.

**Portée de la charge utile.** Elle est liée sous `payload.*` et sous ses clés
nues, et le reste jusqu'à la fin du tick — sans quoi un `THEN <plan>` mettrait
en file un plan incapable de lire ce qui l'a déclenché.

Symétriquement, et c'est le point délicat : **si la garde `WHEN` échoue, la
liaison est défaite**. Une charge utile refusée qui resterait liée masquerait
les croyances homonymes, et l'agent agirait sur une information qu'il vient
de rejeter. La même règle s'applique à `EVENT`.

### Mémoire partagée

`MEMORY { SHARED { … } }` désigne un unique dictionnaire, partagé par
référence entre tous les agents de la société, et **versionné**. Chaque
écriture incrémente la version. Un agent qui écrit alors que la version a
bougé depuis sa dernière écriture voit la sienne acceptée — dernier écrivain
gagnant — mais **le conflit est tracé et compté** :

```
⇄ écriture concurrente   [vue v1, réelle v2 — dernier écrivain l'emporte]
```

Ce que la société ne fait pas, délibérément : pas de transaction, pas de
consensus, pas d'ordre global sur les messages autre que le tour de rôle. Ces
garanties se paient, et rien dans le langage ne les promet. Un système
distribué qui ment sur ses conflits ne se débogue pas.

---

## 20. `OUTCOME` et `UTILITY` — planifier sous incertitude (v0.7)

Les postconditions sûres de la v0.5 sont un mensonge commode : confiner un
compte ne garantit pas de contenir un attaquant qui en possède d'autres. Un
opérateur déclare donc une **distribution** :

```
OUTCOME contenu   WITH 0.70 { threat.status = contained, account.suspended = yes }
OUTCOME contourne WITH 0.30 { account.suspended = yes, alarm.raised = yes }
```

`EFFECT { … }` reste le cas dégénéré : une issue unique de probabilité 1. Les
probabilités sont renormalisées si leur somme s'écarte de 1, et l'écart est
signalé (`W113`).

### État de croyance

La recherche ne porte plus sur un état mais sur une distribution d'états
$\beta = \{(s_i, p_i)\}$. Appliquer une action éclate chaque branche selon
ses issues, et laisse inchangées les branches où elle n'est pas applicable :

$$\beta' = \{(s_i \oplus o,\; p_i \cdot P(o)) : a \text{ applicable en } s_i,\ o \in O_a\}
\;\cup\; \{(s_i, p_i) : a \text{ non applicable en } s_i\}$$

Les branches indiscernables sur les chemins suivis sont fusionnées, leurs
masses additionnées.

### Critère

$$\mathrm{score}(\pi) = \underbrace{\textstyle\sum_i p_i\,U(s_i)}_{\text{utilité espérée}}
\;-\; \underbrace{\textstyle\sum_{a \in \pi} c_a \cdot P(a\ \text{déclenche})}_{\text{coût espéré}}$$

`UTILITY` déclare $U$ par termes additifs sur les conditions vérifiées. Le
coût d'une action est **pondéré par la probabilité qu'elle se déclenche** :
une action dont la précondition n'est vraie que dans 30 % des mondes ne coûte
que 30 % de son prix.

Sans bloc `UTILITY`, $U \equiv 0$ et le critère se réduit exactement au coût
minimal de la v0.5. **La v0.7 est une généralisation stricte** : un programme
v0.5 produit le même plan, au même coût.

La recherche s'arrête lorsque $P(\text{but}) \ge$ `TARGET_CONFIDENCE`, mais
**ne renvoie pas la première solution conforme** — elle renvoie la
mieux-scorée. La différence n'est pas cosmétique : sur l'exemple de
référence, sortir au premier plan trouvé donnerait `isolate_endpoint` seul
($P = 0{,}97$, score 59) au lieu de l'escalade en deux temps (score 81).

### Ce que le planificateur découvre

Personne n'a écrit d'escalade. Elle sort de l'optimisation :

```
quarantine_account → isolate_endpoint [p=0.30] → isolate_endpoint [p=0.01]
    coût 6.47, P(but)=1.00, E[U]=88.0, score=81.5
```

L'action douce d'abord ; la brutale seulement dans les mondes où la première
a échoué, à 30 % de son prix.

### Plan conforme, exécution contingente

Le résultat est un **plan conforme** : une séquence unique, bonne en
espérance sur toutes les branches, pas un arbre de contingence. Un plan
conditionnel exigerait d'observer la branche réalisée — ce que `VERIFY` fait,
mais à l'exécution.

Le rattrapage se fait donc à l'exécution, et il est indispensable :
**`REQUIRES` est réévalué avant chaque appel**. Une action dont la
précondition est retombée depuis la planification est ignorée, avec trace.
Sans cette règle, le plan ci-dessus couperait le réseau d'un poste déjà
assaini dans 70 % des cas. La contingence est obtenue par rebouclage — le
runtime replanifie au tick suivant —, pas par branchement.

### L'invariant de sûreté, renforcé

> Une action dont **une seule branche** de l'état de croyance déclenche une
> interdiction `NEVER` est exclue de la recherche entière.

On ne parie pas sur l'incertitude pour contourner un interdit. Un refus pour
un autre motif (`DENY`, `ALLOW` non satisfaite) rend seulement l'action
inopérante dans les branches concernées.

---

## 21. Le vérificateur hors ligne (v1.0)

L'analyseur statique vérifie la **bonne formation**. Le vérificateur établit
des propriétés de **sûreté** sur l'ensemble des exécutions, avant tout appel
de modèle. La différence se voit sur `examples/unsafe.agent` : `agentl check`
ne trouve rien, `agentl verify` en réfute quatre sur huit.

Huit théorèmes portent sur un agent (T1–T7, T9) ; T8 porte sur un programme
entier — la vivacité d'une société d'agents. T9 est le dernier venu (v1.7) et
le seul à porter sur la **qualité des prémisses** plutôt que sur le programme :
il vérifie que le modèle d'effets sur lequel tous les autres raisonnent peut
être démenti par le monde (§30).

### Le solveur

Un mini-solveur limité à la théorie dont le langage a besoin : égalités et
inégalités sur des chemins, comparaisons numériques, échelle ordinale
intégrée. Mise en DNF bornée, puis test de consistance par chemin
(intervalles, points requis, points exclus).

**Direction de sûreté**, qui gouverne tout le reste :

$$\texttt{satisfiable}(\varphi) \text{ peut répondre VRAI à tort,}
\quad \text{jamais FAUX à tort.}$$

On ne déclare une formule insatisfiable que sur démonstration. Le
vérificateur hérite de cette propriété : **il peut manquer un défaut, il n'en
invente pas**. Tout ce que le solveur ne sait pas traiter — appels de
fonction, arithmétique entre chemins — devient un atome opaque, donc
librement satisfiable.

### Conditions de chemin

Pour chaque site d'appel, le vérificateur reconstitue la conjonction des
gardes qui y mènent : garde du plan, garde de l'événement ou du message,
règle `DECIDE`, `IF`/`ELSE` traversés (la branche `ELSE` ajoute la négation).
Les entrées se propagent d'un plan à l'autre par `THEN <plan>`.

Un cas mérite d'être isolé : si `DECIDE.REASON` existe, **le LLM peut
sélectionner n'importe quel plan déclaré**, et la condition d'entrée est donc
$\top$. C'est souvent le fait le plus important que produise l'analyse.

### T1 — Aucun appel interdit n'aboutit

Garanti par construction : tout appel traverse le moteur de politiques. Ce
que le vérificateur apporte est la carte des sites où l'agent *tentera*
l'action et se fera bloquer.

Pour chaque règle `NEVER` de garde $g$ et chaque site de condition $C$ :

| relation | verdict |
|---|---|
| $C \models g$ | `V101` **erreur** — branche morte : l'appel est toujours interdit |
| $C \wedge g$ satisfiable | `V102` avert. — exposition ; le runtime bloquera, et si aucun repli n'existe, le plan cale |
| $C \wedge g$ insatisfiable | prouvé sûr, aucun signalement |

Le troisième cas compte autant que les deux premiers : sur `unsafe.agent`, la
branche `ELSE` nie exactement la garde du `NEVER` et est **blanchie**. Un
vérificateur qui crie partout ne sert à rien.

### T2 — Sous chaque interdit, le but reste atteignable ou l'escalade est déclarée

Chaînage avant symbolique sur les `EFFECT` déclarés. Pour chaque `NEVER`, on
suppose sa garde vraie, on retire l'action, on cherche une route.

La finesse est dans les préconditions. Un chemin apparaissant dans un `EFFECT`
est **sous contrôle de l'agent** ; un chemin observé ou déclaré en `BELIEF`
possède déjà une valeur initiale. Seuls les chemins *uniquement* produits par
un `EFFECT` doivent être conquis par une action — les autres sont librement
supposés favorables. C'est ce qui distingue `endpoint.inspected == yes` (à
établir : force `inspect_endpoint` en tête de route) de
`threat.status != contained` (fourni par la perception).

**Point fixe (v1.4).** L'espace d'états est fini — les `EFFECT` sont des
affectations ground — donc la recherche va jusqu'à ce que la frontière se
vide, sous un simple garde-fou de sécurité (64 niveaux, 20 000 états).
Atteindre ce point fixe change la nature du résultat : une absence de route
est **démontrée**, non plus seulement constatée. C'est ce qui fait passer les
agents du dépôt de « ◐ BORNÉ » à « ✔ DÉMONTRÉ », et ce qui autorise, à
l'inverse, une réfutation.

**Ce qui est réfuté n'est pas l'absence de route, c'est le fait de caler en
silence.** Un interdit qui rend le but inatteignable n'est pas en soi un
défaut : renoncer peut être la conduite juste. Ce qui est inacceptable, c'est
que l'agent s'arrête sans rien dire. Le théorème regarde donc si une escalade
est **déclarée** — `IF planner.exhausted … THEN <plan>`, ou `ON VERIFY.FAIL` :

| Route sous l'interdit | Escalade déclarée | Verdict |
|---|---|---|
| trouvée | — | ✔ · `V111` route de repli |
| aucune, **démontré** | oui | ✔ · `V112` renoncement assumé |
| aucune, **démontré** | non | ✘ **RÉFUTÉ** · `V105` l'agent calera |
| aucune, recherche tronquée | — | ◐ BORNÉ · `V105` non prouvé |

La dernière ligne est la direction de sûreté : coupée avant son point fixe, la
recherche ne réfute jamais.

### T3 — Aucune capacité n'est morte

Un outil que `DEFAULT DENY` n'autorise jamais (`V107`), dont aucune règle
`ALLOW` n'est satisfaisable (`V103`), ou dont tous les sites d'appel ont une
condition contradictoire (`V108`), est un mensonge dans la spécification de
l'agent.

### T4 — La surface exposée au LLM est bornée

En présence de `DECIDE.REASON`, on énumère les outils atteignables par
sélection de plan et l'on signale ceux de risque `HIGH` ou plus dont la règle
`ALLOW` ne porte aucune garde d'état (`V104`).

### Codes

| code | nature | théorème |
|---|---|---|
| `V101` | erreur | T1 — branche morte |
| `V102` | avert. | T1 — exposition à un état interdit |
| `V103` | erreur | T3 — aucune règle `ALLOW` satisfaisable |
| `V104` | avert. | T4 — outil à risque atteignable par le LLM sans garde |
| `V105` | avert. | T2 — un interdit rend le but inatteignable |
| `V106` | erreur | T2 — but hors d'atteinte des opérateurs déclarés |
| `V107` | erreur | T3 — outil jamais autorisé sous `DEFAULT DENY` |
| `V108` | avert. | T3 — tous les sites d'appel sont impossibles |
| `V109` | erreur | T3 — seuil probabiliste hors de l'amplitude atteignable (§22) |
| `V110-112` | info | traces de raisonnement (opérateurs exclus, routes de repli, surface gardée) |
| `V113` | avert. | T2 — but non atteint **dans la borne** : non prouvé, pas réfuté |
| `V114` | avert. | T1 — preuve dégradée : le solveur a renoncé (borne de clauses) et le verdict du site n'est pas démontré |
| `V120` | info | T5 — aucun `SCENARIO` déclaré, donc rien à réfuter |
| `V121` | erreur | T5 — attente qu'aucune combinaison d'`EFFECT` n'entraîne (§27) |
| `V122` | avert. | T5 — attente non atteinte dans la borne de profondeur (§27) |
| `V123` | info | T5 — scénario atteignable, avec sa route |
| `V124` | info | T5 — attente déjà vraie sous l'énoncé : invariant, renvoyé à `agentl test` (§27) |
| `V125` | info | T6 — garantie de provenance statique satisfaite (§25) |
| `V126` | info | T7 — garantie de terminaison statique satisfaite |
| `V130-137` | — | T8, vivacité de la société — table détaillée sous « T8 » plus bas dans ce §21 |
| `V150` | avert. | T9 — `EFFECT` sur le monde qu'aucune `OBSERVE` ne recouvre (§30) |
| `V151` | info | T9 — chaque `EFFECT` sur le monde est confrontable |

---

## 22. Calibration — quand un défaut d'affichage devient un défaut de sûreté (v1.1)

La v0.4 a remplacé la confiance déclarée par un postérieur calculé, et
présenté cela comme un progrès de sûreté. Ce l'était à moitié.

Le classifieur bayésien naïf suppose les évidences conditionnellement
indépendantes sachant $h$. Dans l'exemple de référence,
`wazuh.alert_count > 20` et `network.anomaly_score > 0.75` décrivent la
**même rafale** : leurs rapports de vraisemblance ont été multipliés comme
s'il s'agissait de deux témoins distincts. Contribution annoncée $+6{,}03$
bits là où l'évidence conjointe en vaut peut-être $4$.

Tant que ce nombre sert à trier, c'est un défaut de calibration. Mais la
v0.4 l'a rendu **autoritatif** :

```
ALLOW isolate_endpoint IF P(credential_attack) >= 0.95
```

La surconfiance fait alors franchir un seuil d'autorisation à un état qui ne
le mérite pas. C'est plus grave que le score halluciné qu'on prétendait avoir
supprimé : celui-là avait au moins l'air suspect.

### `GROUP` — corrélation connue

```
EVIDENCE {
    GROUP rafale {
        wazuh.alert_count > 20        LIKELIHOOD 0.92 GIVEN_NOT 0.06
        network.anomaly_score > 0.75  LIKELIHOOD 0.85 GIVEN_NOT 0.20
    }
    endpoint.integrity == compromised LIKELIHOOD 0.80 GIVEN_NOT 0.05
}
```

Au sein d'un groupe, seule l'évidence de $|\log_2 \mathrm{LR}|$ maximal
contribue ; les autres sont **absorbées**, et la trace le dit. C'est le
traitement conservateur usuel de l'évidence redondante : on ne moyenne pas,
on garde la plus informative, de sorte que le résultat reste au moins aussi
fort que le meilleur témoin isolé.

### `MAX_EVIDENCE` — corrélation inconnue

```
MAX_EVIDENCE 8      (* bits *)
```

Plafonne $|\sum_i \log_2 \mathrm{LR}_i|$ quelle que soit la quantité
d'indices accumulés. Filet contre les corrélations que l'auteur n'a pas vues.

Ni l'un ni l'autre ne rend le modèle exact. Les deux l'empêchent de mentir
dans le sens dangereux. Sur l'exemple de référence : $0{,}982$ naïf contre
$0{,}928$ calibré — et le trace affiche les deux.

### Amplitude atteignable, et seuils morts

Les vraisemblances étant déclarées, les bornes du postérieur se **calculent
statiquement** :

$$p_{\max} = \sigma\!\left(\log_2\frac{p_0}{1-p_0} + \min\Big(\sum_{g} \max_{i \in g} b_i^{+},\ \texttt{MAX\_EVIDENCE}\Big)\right)$$

Le vérificateur s'en sert (`V109`, théorème T3) : une garde
`ALLOW … IF P(h) >= 0.95` sur un modèle plafonnant à $0{,}928$ est une
**capacité morte**, que ni le typage ni la logique des gardes ne révèlent.
L'analyseur applique le même raisonnement au `THRESHOLD` de l'hypothèse
elle-même (`W116`) — c'est ainsi qu'a été trouvé un seuil de $0{,}80$ posé
depuis la v0.4 sur une hypothèse plafonnant à $0{,}67$, donc jamais
confirmable.

Corollaire opérationnel, et leçon générale : **recalibrer un modèle oblige à
recalibrer les seuils qui en dépendaient.** L'outillage doit le rappeler,
sinon la correction d'un biais casse silencieusement les décisions.

### Domaines de sortie

`PRODUCE { confidence: Number }` type la valeur mais ne la borne pas. Rien
n'empêche le modèle de renvoyer 5000, et cette valeur atteint les gardes.
C'était le dernier canal par lequel un LLM pouvait influencer une
autorisation avec une valeur non contrainte.

```
PRODUCE { confidence: Number IN [0, 1],
          root_cause: Symbol IN [connection_pool_exhausted, disk_full,
                                 memory_leak, unknown] }
```

Le runtime écrête toute valeur **finie** hors intervalle et trace l'écrêtage.
Une valeur non finie est rejetée conformément au §11.1 ; elle n'est jamais
conservée dans l'état. L'analyseur signale (`W115`) toute
sortie de `REASON` comparée à un **seuil ordonné** dans une garde ou une
précondition, sans domaine déclaré. La restriction aux comparaisons d'ordre
est délibérée : `host != unknown` est un test de présence qu'une valeur
inattendue ne franchit pas, `confidence >= 0.9` en est un que si.

---

## 23. Une mémoire qui se relit (v1.2)

### `PRIOR FROM` — l'a priori devient empirique

La spec promettait depuis la v0.3 « une mémoire opérationnelle sans modifier
les poids ». Elle était en réalité **en écriture seule** : les enregistrements
s'accumulaient, rien ne calculait dessus.

```
PRIOR 0.05
PRIOR FROM LONG_TERM.incidents WHERE threat.kind == credential_attack
```

Comptage avec lissage de Jeffreys, $\hat p = (k + 1/2)/(n + 1)$ : un
historique unanime ne produit ni 0 ni 1, et un historique vide retombe sur la
valeur déclarée. Un agent qui a vu quarante faux positifs de scan ne démarre
plus avec l'a priori d'un agent neuf. C'est le même geste que la v0.4 a fait
pour le postérieur, appliqué un cran plus tôt.

### Dérive du modèle d'effets

Le planificateur raisonne sur des `EFFECT` déclarés que **rien ne
confrontait au réel**. Un `EFFECT` faux corrompt silencieusement tous les
plans.

Chaque fois qu'une perception succède à une croyance de source
`effect:<outil>`, le runtime compare et tient le compte :

```
‼ modèle d'effet démenti : repair() prédisait state = fixed
    [observé broken — 3/3 démentis]
```

Le registre `Runtime.drift` accumule confirmations et démentis par couple
(outil, chemin). Ce n'est pas une correction automatique — c'est un signal
que le modèle sur lequel on planifie s'écarte du monde.

### Mémoire partagée : la clé, pas le compartiment

En v0.6, `MEMORY { SHARED { blocked_hosts } }` déclarait des noms de
compartiments qui étaient **décoratifs** : toute écriture atterrissait dans
une même liste `records`, et la version était globale au compartiment. Deux
agents écrivant des faits sans aucun rapport se déclaraient mutuellement en
conflit ; à dix agents le signal se serait noyé.

```
WRITE { WHEN … STORE { … } INTO SHARED.blocked_hosts }
```

La cible est désormais une clé nommée, versionnée séparément, et validée à
l'analyse contre le compartiment déclaré (`E008`). Une clé écrite par un seul
agent n'entre plus jamais en conflit ; une clé partagée en produit un vrai,
tracé.

### Codes ajoutés en v1.1 / v1.2

| code | nature | contrôle |
|---|---|---|
| `E008` | erreur | écriture `INTO SHARED.<clé>` non déclarée dans le compartiment |
| `V109` | erreur | seuil probabiliste d'une politique hors de l'amplitude atteignable (T3) |
| `W113` | avert. | les `OUTCOME` d'un outil ne totalisent pas 1 |
| `W114` | avert. | probabilité non calibrée gardant une autorisation (≥ 3 évidences, ni `GROUP` ni `MAX_EVIDENCE`) |
| `W115` | avert. | sortie de `REASON` comparée à un seuil ordonné sans domaine `IN` |
| `W116` | avert. | `THRESHOLD` d'une hypothèse hors de son amplitude atteignable |

---

## 24. `FOREACH` — traiter une collection sans quitter l'état scalaire (v1.3)

L'état d'AGENT-L est scalaire : $x_t$, $b_t$ et les locales n'accueillent que
des valeurs comparables. C'est ce qui rend le moteur de politiques et le
solveur décidables. Or un workflow réel porte sur des collections — treize
e-mails, huit lignes de feuille, quatre conversations.

`FOREACH` est le pont entre les deux, et il ne relâche rien :

```agentl
FOREACH e IN emails MAX 200 {
    REASON "classer le sentiment" PRODUCE { sentiment IN [positive, negative, neutral] }
    IF e.sender_internal == no THEN post_feedback(...)
}
```

**Sémantique.** Soit $S$ la valeur de la source. Si $S$ n'est ni une liste ni
un dictionnaire, la boucle **ne s'exécute pas** et la trace nomme le défaut
ainsi que les collections effectivement liées : indéterminé n'est pas vide.
Sinon, pour chaque élément $e_i$ ($i < \text{MAX}$, 200 par défaut) :

1. **Projection.** `_project` lie des locales `<var>.<champ>`, et
   *uniquement des scalaires*. Une chaîne devient un `Symbol`, un dictionnaire
   imbriqué est aplati d'un niveau, une sous-liste ne donne que sa taille sous
   `<var>.<champ>.count`. **Ce que le programme ne peut pas comparer, il ne
   peut pas non plus le lire par accident** — et l'hôte qui veut exposer le
   contenu d'une sous-liste doit l'aplatir explicitement, donc visiblement.
   `<var>.index` est également lié.
2. **Exécution du corps**, sous les mêmes gardes que tout autre bloc : les
   politiques sont réévaluées pour chaque élément, un `REASON` est appelé une
   fois par élément, un `VERIFY` en échec interrompt la boucle.
3. **Défaisance.** Toutes les liaisons introduites sont retirées, y compris
   en cas d'exception (décision 7). Sans cela l'élément $i+1$ hériterait des
   champs absents de l'élément $i$ et serait jugé sur les faits d'un autre.

**Conséquence de sûreté.** Comme la projection est scalaire et les politiques
réévaluées par élément, un `NEVER` porte sur *chaque* élément traité, jamais
sur la collection en bloc. Un lot de cent tickets dont un seul est interdit
laisse passer les quatre-vingt-dix-neuf autres et bloque le centième — la
granularité de la décision est celle de la donnée.

`E009` complète le dispositif : un appel d'outil dans une expression
(`x = outil()`) est refusé à l'analyse, car il contournerait le moteur de
politiques par la porte de l'évaluateur.

---

## 25. La frontière hôte / agent (v1.3)

Les théorèmes du §21 portent sur le `.agent`. Ils ne disent rien d'un hôte qui
aurait déjà pris la décision avant que le programme ne s'exécute :

```python
tickets = [t for t in all_tickets if t.priority == "Urgent"]   # l'hôte a trié
```

Le programme reste vérifiable, sa politique reste tenue — et elle ne garde
plus rien, puisqu'elle ne voit que ce que l'hôte a bien voulu montrer. Le
certificat est intact et vide. C'est le mode de défaillance le plus dangereux
du langage, parce qu'il est silencieux.

**Règle de partage.** *L'hôte fournit des FAITS ; le `.agent` prend les
DÉCISIONS.* Le test opérationnel : « si la politique de l'entreprise changeait
demain, cette ligne devrait-elle être modifiée ? » Si oui, elle est du côté
du programme.

`agentl boundary X.agent` analyse l'**AST** de `X.py` et de ses imports locaux
transitifs, sans les exécuter, et signale :

| code | | ce qui est signalé |
|---|---|---|
| `B000` | erreur | hôte absent — la norme de nommage n'est pas satisfaite |
| `B001` | erreur | comparaison à une valeur métier (`status == "Blocked"`) |
| `B002` | erreur | filtrage d'une collection sur un critère métier |
| `B003` | avert. | `break` / `continue` — un élément écarté dans l'hôte |
| `B004` | avert. | tri ou extremum : l'hôte priorise |
| `B005` | erreur | seuil numérique appliqué dans l'hôte |
| `B006` | erreur | nom de fonction qui décide (`should_`, `classify_`, `select_`…) |
| `B007` | erreur | hôte volumineux servant un agent sans la moindre garde |

**Exemption d'absence.** Seules les identités explicites avec `None`, leurs
négations et leurs combinaisons sont exemptées du contrôle des filtres. Une
vérité nue (`if x.flag`, `if predicate(x)`, `if x`, et leurs négations) peut
porter une décision métier et n'est plus automatiquement exemptée.
Les comparaisons sont examinées dans les deux sens, y compris avec des petits
seuils, des constantes nommées résolues et des collections de valeurs.

**Ce que l'outil ne peut pas trancher.** « Filtrer une liste vide » et
« filtrer les tickets d'organisations bloquées » ont la même forme en Python.
Le contrôleur ne devine donc pas ; il exige une justification écrite :

```python
# BOUNDARY-OK: appariement par identifiant, plomberie, aucun critère métier
```

La levée couvre l'**instruction entière**, calculée sur les bornes de l'AST —
une compréhension à moitié levée serait le pire des cas. Elle reste dans le
code, s'affiche dans le rapport sous « levées assumées », et **une levée sans
motif ne lève rien**. Le marqueur doit appartenir à un token Python
`COMMENT` : une chaîne ou une docstring ne constitue jamais une levée.
Les diagnostics gardent leur fichier d'origine ; une levée ne franchit pas
un fichier. Les états `INCOMPLETE / UNVERIFIABLE` restent bloquants.

Ce contrôle heuristique peut produire des faux positifs et des faux négatifs.
Un résultat accepté signifie seulement qu'aucun diagnostic bloquant n'a été
reconnu sur la surface affichée. Il ne constitue pas une preuve de sécurité.
Le [contrat Boundary](BOUNDARY.md) décrit le périmètre, les options
`--project-root` / `--external-policy`, le flot local et ses limites.

La chaîne complète est donc : `check` (bien formé) → `verify` (sûr) →
`boundary` (honnête) → `run`.

---

## 26. Rejeu déterministe (v1.4)

Une trace permet de **lire** pourquoi l'agent a agi. Elle ne permettait pas de
**re-dériver** la décision — ce qu'un auditeur demande dès la première revue.

Ce qui rend l'ajout bon marché : le cœur n'a **aucune source de
non-déterminisme propre**. Ni horloge, ni tirage aléatoire, ni identifiant
volatil n'entre dans une décision ; la trace elle-même ne porte pas
d'horodatage. Tout ce qui varie d'une exécution à l'autre traverse la
frontière de l'hôte ou celle du modèle, soit huit points d'entrée :

```
Host.read()   Host.invoke()   Host.ask()     Host.approve()
Host.drain()  DELEGATE        LLM.reason()   LLM.select_plan()
```

### Le journal

`RecordingHost` et `RecordingLLM` **enveloppent** l'hôte et l'oracle réels —
ils ne les remplacent pas, de sorte que les attributs métier d'un hôte
restent joignables — et notent chaque franchissement : genre, clé, arguments,
valeur *ou* exception. `ReplayHost` et `ReplayLLM` relisent ce journal, dans
l'ordre, sans capteur, sans outil, sans réseau. Un auditeur rejoue donc la
décision **sans accès au système d'origine**.

```
agentl run    soc_analyst.agent --record run.json
agentl replay run.json
```

### La propriété visée, et comment elle est vérifiée

Le journal est **scellé** sur l'empreinte SHA-256 de la trace produite. Le
succès d'un rejeu n'est pas « ça n'a pas planté » : c'est l'égalité
caractère pour caractère entre la trace rejouée et la trace scellée. C'est
testable en intégration continue, et c'est testé sur les exemples du dépôt,
société multi-agents comprise.

Trois exigences de fidélité, faciles à manquer :

1. **Les pannes se rejouent en pannes.** Le runtime est tolérant : un capteur
   qui lève ne fait pas tomber la boucle, il produit une ligne `ERROR` où
   figure `type(exc).__name__`. Un rejeu qui rendrait `None` au lieu de lever
   changerait la trace ; on réinstancie donc une exception portant le nom
   d'origine.
2. **`Symbol` n'est pas `str`.** `healthy` et `"healthy"` se comparent égaux
   mais ne s'affichent pas pareil. L'encodage les distingue.
3. **La divergence est bruyante.** Journal tronqué, ordre changé, arguments
   d'outil différents : `ReplayDivergence`, jamais un rejeu qui s'arrange.
   Une valeur *falsifiée* ne peut pas, elle, être détectée à la lecture — elle
   produit simplement une autre trace, et c'est l'empreinte scellée qui
   l'attrape.

### §26.1 Scellement — intégrité et authenticité du journal

L'empreinte de trace répond à « cette exécution se re-dérive-t-elle ? ». Elle
ne répond pas à « ce fichier est-il celui qui fut produit ? » : qui détient le
journal peut le réécrire, empreinte comprise. Deux mécanismes distincts s'y
emploient, et les distinguer est le point important.

**Chaîne de hachage** (format de journal 2, toujours présente, sans clé) :

    h[i] = sha256("agentl.journal.chain.v1\n" ‖ h[i-1] ‖ canonique(entrée_i))

`canonique` est la sérialisation JSON à clés triées et sans espaces : deux
écritures du même journal donnent les mêmes octets, donc un ré-indentage ne
déclenche pas l'alarme. Chaque entrée porte son `h`, et `meta.chain_sha256`
porte la tête, figée au scellement. Le chaînage — plutôt qu'un total de
contrôle global — donne deux propriétés : une altération est *localisée*
(premier indice incohérent), et une entrée retirée en milieu de journal casse
tout ce qui suit, donc ne se rattrape pas ligne à ligne. Une troncature en
*fin* de journal laisse les entrées restantes cohérentes entre elles : c'est
la tête scellée qui la trahit, et c'est pourquoi elle est stockée séparément.

`Journal.load` refuse par défaut un journal dont la chaîne est rompue
(`strict=True`) ; `strict=False` charge pour *examiner* le dommage et laisse
le verdict dans `chain_report`. La chaîne établit l'intégrité — elle détecte
l'altération d'un journal, pas la fabrication d'un journal neuf.

**Signature** (optionnelle, avec clé) : c'est ce qui ferme cette dernière
brèche. On signe la **méta scellée** — tête de chaîne, `trace_sha256`,
`source_sha256`, format — et non les entrées, que la tête résume déjà. Signer
la seule tête ne suffirait pas : un journal authentique pourrait être
re-présenté comme provenant d'un autre programme en changeant
`source_sha256`.

| algorithme | dépendance | vérificateur | usage |
|---|---|---|---|
| `HMAC-SHA256` | stdlib | détient le même secret — **il pourrait forger** | entre composants d'un même système |
| `Ed25519` | extra `sign` | ne détient que la clé publique | audit externe |

Trois verdicts, jamais confondus : chaîne intacte + sceau valide (pièce
authentique), chaîne intacte sans sceau (non contrefait mais non attribué),
chaîne rompue (corrompu). Traiter un journal non signé comme un faux serait
aussi trompeur que l'inverse. Sceau valide *avec* chaîne rompue est le cas
signalé explicitement : les entrées ont été retouchées après signature.

### Ce que le rejeu ne prouve pas

- **Rien n'atteste *quand*.** La signature lie un journal à une clé, pas à un
  instant. Un journal peut être re-signé plus tard avec la même clé, et rien
  dans le fichier ne l'en distingue. Un horodatage tiers reste à faire.
- **Une valeur opaque casse la promesse.** Ce qui ne sait pas se sérialiser
  est enregistré par son `repr` et le journal se déclare **lacunaire**
  (`meta.lossy`), signalé à l'enregistrement — pas découvert à la première
  divergence.
- **Le programme doit être le même.** L'empreinte de la source est au
  journal ; rejouer sur un programme modifié est autorisé mais annoncé, et
  ne reproduit plus l'exécution scellée.

---

## 27. `SCENARIO` — les critères d'acceptation dans le programme (v1.4)

Les quatre premiers théorèmes prouvent des propriétés **génériques** : aucun
appel interdit n'aboutit, le but reste atteignable, aucune capacité n'est
morte, la surface exposée au modèle est bornée. Aucun ne prouve quoi que ce
soit de ce que **l'auteur** a exigé. `SCENARIO` porte cette exigence dans le
programme :

```
SCENARIO actif_critique_le_confinement_passe_par_le_compte {
    GIVEN {
        wazuh.alert_count  = 37
        asset.criticality  = CRITICAL
        operator.approval  = yes
        suspected_host     = "web-07"
        suspected_account  = "svc_backup"
    }
    EXPECT { threat.status == contained } WITHIN 6
}
```

Deux usages, et c'est ce qui rend la primitive rentable : **exécutable** par
le runtime (`agentl test`) et **attaquable** par le vérificateur (T5).

### Contre quoi il s'exécute

Contre le **monde déclaré**, pas contre l'hôte réel : `GIVEN` pose l'état
initial, chaque outil appelé applique ses propres `EFFECT`, et les politiques
s'appliquent normalement. Le test est donc hermétique — ni réseau, ni disque,
ni hôte à écrire — mais il ne prouve **rien sur l'hôte** : il prouve que *les
déclarations entraînent l'attente*. Un `EFFECT` qui ment rendra un test vert
et une production fausse ; c'est `agentl boundary` et le registre de dérive
d'effet qui traitent cette question-là.

Trois choses ne sont pas devinées, elles se **posent** dans le `GIVEN` :

| Ce qu'on pose | Comment | Par défaut |
|---|---|---|
| L'état du monde | `wazuh.alert_count = 37` | inconnu |
| La réponse de l'opérateur | `operator.approval = yes` · `operator.answer = …` | refus, sans réponse |
| Ce que le modèle a proposé | le nom produit par `REASON … PRODUCE` | `DEFAULT` explicite, sinon `UNDEFINED` |

Le défaut de l'opérateur est le **refus**, comme dans le runtime : un scénario
ne suppose jamais un humain complaisant par accident, il doit l'écrire — et
cela se lit dans le test. Poser la réponse du modèle est cohérent avec le
modèle du langage (le LLM propose, le runtime décide) : en test, c'est l'auteur
qui écrit la proposition, au lieu qu'elle se cache dans un hôte.

### Éventualité ou invariant

La distinction est ce qui empêche un scénario d'être vert sans avoir rien
exécuté :

- **éventualité** — l'attente est fausse au tick 0 : elle doit *devenir* vraie
  dans la borne `WITHIN` ;
- **invariant** — l'attente est déjà vraie au tick 0 : elle doit *tenir* à
  chaque effet simulé, instruction et phase, jusqu’à UNTIL, MAX ou WITHIN.

Le second cas est celui de tout scénario qui exige qu'une action **ne
survienne pas** — `EXPECT { isolated != confirmed }`. Le juger au tick 0 le
rendrait vert sans qu'aucun tick n'ait tourné : c'est précisément le test qui
ment qu'on cherche à rendre impossible.

### T5 — l'attaque statique

Pour chaque éventualité, T5 reprend la recherche de T2 : `GIVEN` pose l'état,
`EXPECT` devient le but, les politiques s'appliquent. Une attente qu'aucune
combinaison d'`EFFECT` permise n'entraîne est un défaut (`V121`) — politique
trop stricte, ou attente fausse. Comme T2, le théorème hérite de la direction
de sûreté : il ne réfute qu'espace d'états épuisé, et rend « ◐ BORNÉ »
(`V122`) quand la borne de profondeur a arrêté la recherche.

Un invariant, lui, est **hors de portée** de T5 : chercher quelle action rend
vrai que l'isolement n'a pas eu lieu n'a pas de sens. Il est signalé (`V124`)
et renvoyé à `agentl test`, qui vérifie qu'il tient à chaque tick.

### Le test de mutation

Un scénario qui passe dans tous les cas ne teste rien. Celui livré avec
`soc_analyst.agent` est écrit pour mordre : aucun compte à suspendre, donc
l'isolement est la seule route vers le confinement — et l'actif est critique.
Retirez le `NEVER`, et le scénario tombe. C'est ce qui en fait un test.

Nouveaux diagnostics : `E010` (`WITHIN` ne laissant pas un tick), `W117`
(`GIVEN` posant un chemin qui n'existe nulle part), `W118` (attente ne portant
sur aucun chemin produit par EFFECT, OUTPUT, SET, REASON ou DELEGATE).

---


### Diagnostics de provenance et de sûreté v1.5

| Code | Niveau | Signification |
|---|---|---|
| `W119` | avert. | sortie LLM utilisée comme cible risquée sans `ATTESTS` |
| `W120` | avert. | outil `HIGH`/`CRITICAL` sans `REQUIRE APPROVAL` |
| `W121` | avert. | `VERIFY` auto-confirmé par un `EFFECT` non observé |
| `W122` | avert. | hypothèse globale autorisant une action ciblée dans `FOREACH` |
| `W123` | avert. | clôture non gardée par l’absence de travail non résolu |
| `W124` | avert. | outil risqué sans scénario de sûreté |
| `W125` | avert. | texte non fiable suivi d’une action risquée sans garde d’injection |


### T6 et T7

**T6 — provenance corrélée.** Le vérificateur réfute une action risquée quand
une cible issue du modèle n’est pas liée à un paramètre distinct `ATTESTS`,
quand une postcondition est auto-confirmée sans réobservation, quand une
probabilité globale autorise une cible locale non corrélée, ou quand un flux
issu d’un texte brut atteint l’action sans garde d’injection.

**T7 — terminaison sûre.** Le vérificateur identifie les chemins de la
condition `LOOP UNTIL`. Un outil qui les satisfait est réfuté si l’agent
observe un backlog `pending`, `remaining` ou `unresolved` sans le
consulter dans sa règle `ALLOW`. Une postcondition métier telle que
`rollback.done` n’est pas confondue avec la terminaison globale.

### T8 — vivacité de la société (v1.6)

Les sept premiers théorèmes se démontrent **un agent à la fois**. Avec
`MESSAGE` (§19), cela ne suffit plus :

```
A n'agit que sur réception de `m`, que seul B émet ;
B n'émet `m` que sur réception de `n`, que seul A émet.
```

Chaque agent est irréprochable isolément — T2 conclut, pour chacun, que son
but reste atteignable — et le programme est bloqué. **T2 était donc faux à
l'échelle du programme**, non par défaut d'implémentation mais parce que rien
ne l'y vérifiait.

T8 est le seul théorème du programme entier. `agentl verify` l'évalue dès que
le fichier déclare plus d'un agent.

**L'abstraction.** Ni pi-calcul ni réseaux de Petri : les moyens de blocage du
langage sont peu nombreux et nommés. On construit un **graphe d'attente** dont
les nœuds sont des *signaux* (`msg:<nom>`, `shared:<clé>`) et les producteurs
des *contextes* — plan, gestionnaire de message, écriture mémoire — chacun
gardé par une **disjonction de conjonctions** de signaux. Une conjonction vide
signifie « atteignable sans aucun message » : c'est le point d'entrée.

La question devient un plus petit point fixe, calculé par saturation :

$$\texttt{productible}(s) \iff \exists\ c : s \in \texttt{emits}(c)
\ \wedge\ \exists\ g \in \texttt{gates}(c) : g \subseteq \texttt{productible}$$

Un signal hors du point fixe n'est **jamais** émis — ni au premier tick, ni au
millième. Le calcul termine : l'ensemble des signaux est fini et ne fait que
croître.

**Points d'entrée reconnus** : garde `PLAN … WHEN`, `EVENT`, règle `DECIDE`,
`ON VERIFY.FAIL`, et propagation `THEN p` de plan à plan jusqu'au point fixe.

**Direction de sûreté**, héritée du solveur :

$$\text{T8 peut manquer un blocage, il n'en déclare jamais un à tort.}$$

Un signal est réputé productible dès qu'il existe un chemin **de forme**, sans
chercher si les gardes numériques de ce chemin peuvent être vraies. Un agent
aux gardes contradictoires se bloquera sans que T8 l'ait vu — faux négatif,
acceptable. Un signal déclaré non productible l'est parce qu'aucun chemin
n'existe, ce qui suffit à conclure.

| Code | Signification |
|---|---|
| `V130` | interblocage : cycle d'attente sans point d'entrée, le cycle est nommé |
| `V131` | signal jamais émis bien qu'un contexte l'émette (l'émetteur est lui-même mort) |
| `V132` | signal attendu qu'aucun contexte n'émet |
| `V133` | cycle de messages vivant qui n'écrit rien — livelock présumé (avertissement) |
| `V134` | T8 démontré |
| `V135` | contexte qui ne s'exécute jamais, avec les signaux qu'il attend |
| `V136` | `DECIDE.REASON` présent : T8 ne peut ni prouver ni réfuter |
| `V137` | `SHARED` écrit et lisible par personne |

**Ce que T8 ne prouve pas.**

*L'échappatoire du LLM.* `DECIDE { REASON … }` autorise le modèle à proposer
n'importe quel plan **déclaré** — le runtime rejette les noms inventés, pas les
noms existants (§8). Chez un tel agent, tout plan est atteignable et aucun
blocage n'est démontrable. T8 rend alors « ◐ non prouvé » (`V136`) plutôt qu'un
verdict rassurant sur une hypothèse fausse. C'est le même geste que le « ◐
BORNÉ » de T2 : distinguer *l'absence de preuve* de *la preuve d'absence*.

*La famine partielle.* T8 raisonne sur « émis au moins une fois », pas sur
« émis aussi souvent qu'il le faut ». Un agent servi trop rarement n'est pas
signalé.

*La granularité des clés partagées.* Une clé est un signal **indivisible** :
T8 sait qu'elle a été écrite, pas *ce* qui y a été écrit. Deux agents qui se
coordonnent sur la valeur d'un enregistrement plutôt que sur son existence
sortent de la portée du théorème.

### Lire la mémoire partagée (v1.6)

`MEMORY { SHARED }` s'écrivait sans pouvoir se relire : `State.get()` ne
consultait que `SHORT_TERM`, `LONG_TERM` et `KNOWLEDGE`. Les écritures étaient
tracées et versionnées, et **n'influençaient aucune décision** — un état
« partagé » que personne ne peut observer ne partage rien, et la moitié
`shared:` du graphe d'attente était vide.

`SHARED.<clé>` est désormais un chemin d'expression. Quatre formes, et pas une
de plus — un langage de requête sur la mémoire serait une autre fonctionnalité,
et celle-ci doit rester lisible dans une garde :

| Forme | Valeur |
|---|---|
| `SHARED.k` | la suite d'enregistrements (`[]` si déclarée et jamais écrite) |
| `SHARED.k.count` | combien d'enregistrements |
| `SHARED.k.version` | version de la clé (compteur d'écritures, `0` si jamais écrite) |
| `SHARED.k.last[.champ]` | le dernier enregistrement, ou l'un de ses champs |

La lecture est **explicite**, contrairement aux autres compartiments qui se
lisent par nom nu : deux agents partageant une clé `incidents` ne doivent pas
la confondre avec leur propre `LONG_TERM.incidents`. Le préfixe dit d'où vient
la donnée — le minimum pour une décision qu'un autre agent a rendue possible.

Une clé absente rend `UNDEFINED`, jamais `0` ni `[]` : ces valeurs se
compareraient silencieusement. Seule `.version` rend `0`, parce que c'est un
compteur d'écritures et que « jamais écrite » y est bien zéro.

`V137` signale une clé écrite que **nulle garde ne relit** : l'écriture et le
versionnement sont payés pour rien, et l'auteur croit coordonner deux agents
qui ne se coordonnent pas. Avertissement, jamais erreur.

### Diagnostics de frontière hôte v1.5

| Code | Signification |
|---|---|
| `B008` | primitive destructive sans dry-run explicite |
| `B009` | exécution de commande avec `shell=True` |
| `B010` | cible destructive sans attestation ou revalidation |
| `B011` | journal lu sans curseur ni déduplication |
| `B012` | action sans recette de rollback |
| `B013` | données brutes vers un LLM externe sans opt-in |
| `B014` | registres différents du contrat `.agent`, ou `INCOMPLETE / UNVERIFIABLE` si leur inventaire est inconnu |
| `B015` | `DESCRIPTION` d'outil au ton impératif — texte d'un tiers qui pilote le modèle (§29) |
| `B016` | surface locale incomplète ou dépendance externe refusée par la politique choisie |

## 28. Ce qui reste ouvert

- **Le solveur est incomplet.** Pas d'arithmétique entre chemins, pas de
  quantificateurs. Un SMT élargirait T1 et T2 sans changer leur énoncé.
- **T2 ne suit que les affectations ground.** Le point fixe (§21) rend la
  recherche complète *sur ce modèle* : il n'y a ni arithmétique entre
  chemins, ni valeurs symboliques ouvertes. Un `EFFECT` calculé
  (`x = y + 1`) sort du modèle, et la route qu'il ouvrirait reste invisible.
- **Le plan est conforme, pas contingent.** Conséquence plus profonde qu'un
  surcoût : un planificateur conforme **ne peut pas valoriser
  l'information**. `inspect_endpoint` n'est jamais choisi *pour savoir*,
  seulement parce qu'il est précondition. Un agent de diagnostic incapable
  de préférer un diagnostic, c'est structurel.
- **Le temps est modélisé, mais reste déclaré.** Depuis la v1.6 le
  planificateur arbitre sur `DURATION`, `DEADLINE` et `TIME_WEIGHT` (§17.1) —
  une action de 45 s ne coûte plus autant qu'une de 4 min. Mais la durée est
  écrite par l'auteur, comme l'`EFFECT` l'était avant T9 : rien ne la
  confronte au temps réellement passé. Un `DURATION` qui ment fausse un plan
  sans qu'aucune trace ne le dise.
- **Le vérificateur ne vérifie pas le runtime.** « Aucun appel n'atteint un
  outil sans traverser le moteur de politiques » est une propriété du code
  Python, pas un théorème. Les théorèmes tiendraient encore si
  quelqu'un ajoutait un chemin appelant `host.invoke` directement.
- **Le rejeu n'est pas horodaté.** L'intégrité et l'authenticité d'un journal
  s'établissent désormais (§26.1 : chaîne de hachage, signature Ed25519 ou
  HMAC), mais rien n'atteste de la *date* de l'exécution : une clé encore
  valide permet de re-signer un journal à n'importe quel moment. Un
  horodatage tiers reste à faire.
- **Le contrôle de frontière reste heuristique.** `boundary` parcourt les
  imports locaux et suit certaines liaisons et certains chemins de contrôle.
  Il ne prouve ni les intentions ni les effets des validateurs, ne fournit
  pas d'analyse générale de taint et ne couvre pas l'intérieur des bibliothèques
  tierces. Un calcul arithmétique ou un dispatch dynamique peut cacher une
  décision. Les exclusions et les surfaces incomplètes sont affichées ;
  les limites sémantiques nécessitent une revue humaine.
- **Un scénario ne prouve rien sur l'hôte.** `SCENARIO` (§27) s'exécute
  contre le monde *déclaré* : un `EFFECT` qui ment sur ce que fait réellement
  l'outil rend un test vert et une production fausse. Depuis la v1.7, T9
  garantit qu'un tel mensonge est **réfutable** — une perception viendra le
  juger, et sa crédibilité en tiendra compte (§30). Réfutable n'est pas
  réfuté : il faut encore que l'exécution ait lieu, et assez longtemps. Un
  mode « scénario contre hôte réel, effets comparés aux effets déclarés »
  reste le prolongement naturel.

Aucune de ces limites n'est masquée à l'exécution : chacune se lit dans une
trace, un verdict « ◐ BORNÉ », ou un compteur.

---

## 29. Pont MCP — importer sans emprunter la confiance

Le protocole MCP décrit **un appel** ; `TOOL` déclare **un contrat**. Trois des
sept champs d'un `TOOL` n'ont aucune source côté MCP, et le pont refuse de les
combler.

| Champ `TOOL` | Source MCP |
|---|---|
| nom | `name`, préfixé `serveur__outil` |
| `DESCRIPTION` | `description`, **si l'auteur l'accepte** |
| `INPUT` | `inputSchema` |
| `OUTPUT` | `outputSchema`, à défaut `{ result: Json }` |
| `RISK` | **rien** → `UNSET` |
| `SIDE_EFFECT` | **rien** |
| `EFFECT` / `OUTCOME` / `COST` | **rien** |

Trois conséquences normatives.

**1. Un outil importé n'est pas planifiable.** Sans `EFFECT`, `is_operator` est
faux : l'outil s'appelle depuis un `PLAN` écrit à la main, le planificateur ne
le synthétise pas. Inventer une postcondition corromprait à la fois le
planificateur et le vérificateur — c'est le biais de §28 (« un `EFFECT` qui
ment »), et l'import se garde d'y contribuer.

**2. Le risque est refusé jusqu'à ce qu'un humain le tranche.** `RISK` sort à
`UNSET`, et `E011` fait échouer `agentl check` — donc `agentl run`, qui refuse
un programme portant une erreur. Contrairement à `W101`, un `ALLOW *` ne le
fait pas taire : écrire une règle attrape-tout n'est pas trancher un risque.
`UNSET` est rangé **au-dessus** de `CRITICAL` dans l'échelle ordinale, de sorte
qu'une garde échoue fermée dans les deux sens si un tel outil atteignait
malgré tout le moteur de politiques.

Les annotations MCP (`readOnlyHint`, `destructiveHint`, `idempotentHint`,
`openWorldHint`) sont **reproduites en commentaire, jamais en valeur**. Ce sont
des auto-déclarations du serveur que rien ne vérifie ; en dériver un `RISK`
reviendrait à laisser la partie auditée écrire son propre audit.

**3. Le catalogue est figé à l'import.** MCP publie une liste d'outils
dynamique ; « un outil non déclaré n'existe pas » (§6) est une règle du
langage. L'import scelle l'empreinte SHA-256 du `tools/list` dans l'en-tête du
programme produit — descriptions comprises, puisqu'elles atteignent le contexte
du modèle. `MCPHost` confronte cette empreinte au premier contact et **lève
avant tout appel** en cas d'écart, en nommant ce qui a changé. Un outil apparu
côté serveur est aussi une dérive : il n'est pas appelable (`E001`), mais sa
présence dit que le serveur n'est plus celui qu'on a audité.

### Descriptions non fiables

Une `DESCRIPTION` importée est du texte rédigé par un tiers, et elle est lue
par le modèle dans `DECIDE.REASON` et la sélection de plan. Une description
impérative n'est donc pas une maladresse de rédaction, c'est un canal
d'instruction ouvert à un tiers. `B015` la signale à la frontière — le lieu
juste, puisque la question est bien de savoir quelle part de la décision vient
d'ailleurs que du programme auditable. `--strip-descriptions` n'importe que les
noms.

Comme tout le module `boundary`, cette heuristique est faillible : elle peut
manquer une instruction déguisée ou signaler un texte innocent. Son diagnostic
est un signal à relire, pas une preuve de malveillance.

### Limite assumée

Les contraintes que la signature `INPUT` ne sait pas porter (`minLength`,
`pattern`, `oneOf`…) sont recopiées **en commentaire** : le contrat AGENT-L ne
les vérifiera pas, et il vaut mieux le dire que le laisser découvrir.
## 30. `EFFECT` confronté au réel (v1.7)

Toute la chaîne de preuve repose sur des `EFFECT` **écrits à la main** : T2
cherche une route en les appliquant, le planificateur enchaîne des `REQUIRES`
dessus, T5 valide un scénario à travers eux. Un `EFFECT` faux rend donc
`verify` vert et la production fausse. C'est le seul mensonge que le
vérificateur ne peut pas voir seul : il porte sur le monde, pas sur le
programme.

### T9 — le modèle d'effets doit être réfutable

Le runtime sait comparer une perception à la postcondition qui l'a précédée
(registre de dérive, §23). Encore faut-il qu'une perception vienne. Sans
`OBSERVE` sur le chemin, la postcondition n'est **jamais réfutable** : elle
n'est pas fausse, elle est hors du domaine de la preuve — et c'est pire, parce
que rien ne le signale.

Mesuré sur le dépôt lui-même avant correction : **24 des 35 `EFFECT` déclarés
dans `examples/` étaient irréfutables.** Deux tiers du modèle sur lequel
`verify` raisonnait ne pouvaient pas être démentis ; le certificat était vert
et il ne pouvait pas être autre chose.

```
T9 — Le modèle d'effets est réfutable
  ✘ RÉFUTÉ · 0/4 postcondition(s) confrontable(s) à une perception
  ! V150    l.44  restart_pod() prédit app.status, qu'aucune OBSERVE ne perçoit :
                  la postcondition ne peut jamais être démentie
```

### `INTERNAL` — ce qui ne porte pas sur le monde

Une partie des `EFFECT` ne décrit pas le monde mais la comptabilité de
l'agent : `cycle.done`, `report.written`, `probe.done`. Aucun capteur ne peut
les démentir, et c'est légitime — ils sont vrais parce que l'agent vient de
les écrire. Réclamer une `OBSERVE` dessus serait du bruit, et un avertissement
qu'on apprend à ignorer ne protège plus de rien.

```agentl
EFFECT { app.status = healthy,  cycle.done = yes INTERNAL }
```

Le marqueur est **déclaratif et non deviné**. Une heuristique sur le nom
(`*.done`, `*.sent`) aurait exempté n'importe quel effet mondain nommé
`quarantine.sent`, et le projet s'interdit d'inventer un défaut comme d'en
masquer un. La distinction entre « rien ne peut vérifier cette prédiction » et
« cette prédiction ne porte sur rien de vérifiable » est écrite par l'auteur,
qui seul la connaît.

### La crédibilité devient mesurée

Le registre de dérive comptait **dans le vide** : un effet démenti trois fois
sur trois reposait sa croyance avec `CONFIDENCE 0.80` — exactement le poids
d'un effet toujours confirmé. Le compte est désormais lissé à la Jeffreys,
$(k + 1/2)/(n + 1)$, le geste déjà fait par `PRIOR FROM` pour l'a priori :

| historique | crédibilité de la postcondition |
|---|---|
| aucun jugement | `0.80` — la valeur déclarée, pas le ½ de Jeffreys |
| 0 confirmé / 3 | `0.125` |
| 2 confirmés / 2 | `0.833` — deux confirmations ne font pas une certitude |

Ce n'est **pas** une correction automatique du modèle : l'`EFFECT` reste celui
que l'auteur a écrit, et le planificateur continue de raisonner dessus. C'est
sa crédibilité qui devient empirique, lisible par `CONFIDENCE(chemin)` dans
une garde, et visible en fin d'exécution :

```
modèle d'effets confronté au réel
  ✘ restart() → service.state : 0/3 confirmé(s), crédibilité 0.12
```

### Un registre qui survit à l'exécution

Une dérive se manifeste sur des dizaines de cycles ; un processus qui repart
de zéro repart **crédule**. Déclarer la clé fait du registre de la mémoire
opérationnelle comme le reste :

```agentl
MEMORY { LONG_TERM { effect_drift } }
```

Le registre est un **compte courant**, pas un journal : il est réécrit, non
empilé. Non déclarée, la clé n'est pas écrite — on n'invente pas de la mémoire
dans le dos de l'auteur. `Runtime.seed_memory()` est la couture par laquelle
l'hôte rend le registre d'une exécution précédente ; le langage ne dit pas où
la mémoire vit.

| code | nature | contrôle |
|---|---|---|
| `V150` | avertissement | `EFFECT` sur le monde qu'aucune `OBSERVE` ne recouvre — jamais réfutable |
| `V151` | info | chaque `EFFECT` sur le monde est confrontable à une perception |


---

## 31. La conduite face à un capteur muet (v1.8)

AGENT-L échoue fermé, et c'est la bonne direction : une donnée indéterminée
applique un `NEVER`, ne satisfait pas un `ALLOW`, route une approbation vers
l'humain (§7.1). Mais jusqu'en v1.7, un capteur qui ne rendait rien ne
produisait **aucun événement** : le chemin restait indéfini, les gardes
échouaient fermé, et l'agent se bloquait sans qu'une ligne de trace dise
pourquoi. Une panne de capteur et un chemin mal orthographié produisaient le
même silence.

La tension entre sûreté et continuité opérationnelle ne se résout pas : elle
se **déclare**.

```agentl
OBSERVE {
    asset.criticality ON UNKNOWN ESCALATE
    disk.usage_percent ON UNKNOWN DEGRADE 100
}
```

| conduite | effet |
|---|---|
| *(rien)* | comportement historique : le chemin reste indéfini, les gardes échouent fermé — plus une ligne de trace et `sensors.<chemin>.available = false` |
| `ESCALATE` | la panne devient une escalade nommée, **une seule fois par panne** : un capteur mort réveillerait sinon l'astreinte à chaque tick, et une alerte répétée n'est plus lue |
| `DEGRADE <valeur>` | la valeur de repli **déclarée** est substituée, tracée, et posée avec la confiance `0.3` et la source `fallback` |

Trois propriétés tiennent la direction de sûreté :

- **Rien n'est inventé sans déclaration.** Sans `ON UNKNOWN`, le runtime ne
  comble aucun trou. Le défaut reste le blocage.
- **Un repli n'est pas une mesure.** Sa confiance basse et sa source
  `fallback` sont lisibles par une garde : `CONFIDENCE(disk.usage_percent)`
  distingue une hypothèse d'une observation.
- **La panne est un fait du monde.** `sensors.<chemin>.available` est un
  chemin comme un autre : le programme peut en décider lui-même.

Un **outil** en panne suit exactement la même convention (v1.8.1). Après trois
échecs consécutifs — seuil réglable par `Runtime(tool_breaker=…)`, zéro pour
désarmer — le runtime cesse d'appeler l'outil et publie deux faits :

| chemin | contenu |
|---|---|
| `tools.<nom>.available` | `false` dès que le seuil est franchi, `true` au premier succès |
| `tools.<nom>.failures` | échecs consécutifs, remis à zéro par un succès |

    NEVER close_batch WHEN tools.update_row.available == false

Le disjoncteur ne décide rien à la place du programme : il **cesse de
marteler** un service mort et publie l'état. La *demi-ouverture* laisse
repasser un seul appel de sondage tous les `tool_cooldown` ticks, sans quoi un
service revenu à la vie resterait coupé jusqu'à la fin du run. Son refus se
compte dans `circuit_open`, jamais dans `blocked` : le premier est une
indisponibilité du monde, le second un interdit du programme.

`W131` signale un chemin dont dépend un interdit et qui ne déclare aucune
conduite — la disponibilité mérite d'être décidée, pas subie. `W132` signale
le versant inverse, et il est plus grave : un `DEGRADE` sous un interdit fait
juger la règle sur une valeur déclarée. Un repli mal choisi rouvre exactement
la faille §7.1, cette fois avec la bénédiction du programme. Le repli reste
permis — il est parfois le bon choix — mais il ne doit pas être silencieux.

| code | nature | contrôle |
|---|---|---|
| `W131` | avertissement | garde d'interdit sur un chemin observé sans conduite `ON UNKNOWN` |
| `W132` | avertissement | `ON UNKNOWN DEGRADE` sur un chemin dont dépend un `NEVER`/`DENY`/`REQUIRE APPROVAL` |
| `W133` | avertissement | `REASON` de plus de huit champs `PRODUCE` : le budget de sortie s'épuise avant la fermeture du JSON |
| `W134` | avertissement | champ `PRODUCE` gardant une action sans `DEFAULT` explicite, ou avec un `DEFAULT` qui ne déclenche pas l'interdit |
| `W135` | avertissement | garde de déclenchement (`WHEN`, `IF`, `DECIDE`) comparant `!=` un chemin que rien ne renseigne |

### L'oracle en panne mène au `DEFAULT` explicite, sinon à `UNDEFINED`

`W133` et `W134` se lisent ensemble, parce qu'ils gardent le **même chemin**.
Réponse tronquée, JSON invalide, oracle injoignable, clé révoquée : quelle que
soit la panne, la coercition (§11.1) applique les seuls replis explicitement
déclarés. Sans eux, les champs absents restent `UNDEFINED` ; ils ne deviennent
ni zéro plausible, ni faux plausible, ni chaîne neutre. Le `DEFAULT` n'est
donc pas une commodité de rédaction — c'est *le comportement déclaré de
l'agent en panne d'oracle*, et il mérite d'être choisi comme tel.

`W134` vérifie le repli plutôt que de le recommander : le champ est lié à son
`DEFAULT`, le reste laissé indéterminé, et la garde évaluée. Un `NEVER`
définitivement faux dans ces conditions est un interdit que la panne désarme.
En l'absence de `DEFAULT`, `W134` demande de rendre le contrat de panne
explicite même si l'exécution reste fermée grâce à `UNKNOWN` (§7.1).

Ce que le runtime ne peut pas décider à la place de l'auteur : un oracle muet
n'arrête pas l'agent. Les plans dont la garde ne dépend d'aucun `REASON`
continuent de s'exécuter — sur une mesure réelle, vingt-cinq actions
déterministes ont été menées à bien pendant que l'oracle était coupé. Rien
n'était faux ; rien n'était voulu non plus. « Oracle mort = on ne touche à
rien » s'écrit, avec `reason.degraded` :

    NEVER apply_change WHEN reason.degraded == true

---

## 32. `NEVER SEND` — l'interdiction de sortie (v1.8)

`USING` (§7.4) borne ce qu'un `REASON` montre. Il n'a jamais rien borné de
`select_plan`, qui transmet l'intégralité des croyances, ni de l'appel qu'un
`DECIDE.REASON` déclenche. Un programme n'avait donc **aucun moyen** de
retenir un secret : la seule barrière disponible ne couvrait pas le point de
sortie le plus large.

```agentl
POLICY {
    NEVER SEND credentials, patient.identite
    NEVER wipe IF asset.criticality == CRITICAL
}
```

Une redaction n'est pas une règle d'action : elle n'a pas de cible d'outil,
pas de garde, et elle ne se discute pas au moment de l'appel. Elle porte sur
**toutes** les sorties vers le fournisseur de modèle.

- **Par préfixe de segment.** `NEVER SEND credentials` couvre
  `credentials.token` mais pas `credentials_publics` — c'est sous les feuilles
  que sont les secrets, et une interdiction qui s'arrêterait au chemin exact
  ne retiendrait rien.
- **Et dans l'autre sens : le parent d'un secret est retenu à la feuille.**
  Interdire `credentials.token` ne disait rien de `credentials` ; envoyer le
  composite parent livrait donc le secret entier, sans redaction ni
  diagnostic. La protection se contournait en désignant le nœud du dessus,
  ce que fait exactement un `USING { credentials }`. Le contexte porte
  désormais `{'token': '⟦retenu⟧', 'user': 'alice'}` : la feuille est retenue,
  ses voisines partent. Retenir tout `credentials` amputerait le raisonnement
  de données que le programme n'a jamais protégées. Une valeur composite que
  le runtime ne sait pas parcourir est en revanche retenue **entière** : une
  interdiction qu'on ne sait pas appliquer finement s'applique en grand.
- **La clé reste visible, la valeur non.** Le contexte porte `⟦retenu⟧`. Une
  clé qui disparaîtrait se lirait comme une absence de donnée ; le modèle doit
  savoir qu'il raisonne sur un état amputé, sans apprendre ce qui lui manque.
- **La retenue est tracée**, au même rang qu'un blocage de politique : une
  donnée retenue est une décision du moteur, pas un silence.

`W130` signale un `USING` qui liste un chemin qu'un `NEVER SEND` retient. Les
deux déclarations disent le contraire l'une de l'autre ; le runtime tranche
dans la direction de la sûreté, mais le `REASON` raisonnera sur un trou dont
son auteur se croit protégé. Il signale aussi **le sens inverse** — un `USING`
qui nomme le parent d'un chemin retenu — et invite alors à nommer ce qui doit
sortir plutôt que le nœud au-dessus du secret.

Ce que `NEVER SEND` ne fait pas : il ne couvre pas ce qu'un **hôte** envoie
lui-même à un fournisseur. La frontière du §28 reste la frontière.

| code | nature | contrôle |
|---|---|---|
| `W130` | avertissement | `USING` listant un chemin qu'un `NEVER SEND` retient, ou le parent d'un tel chemin |


## 33. Corrections des audits CHECK et TEST

`E012` rejette les noms d'AGENT dupliqués sans distinction de casse ; `E013`
rejette les signatures d'appel TOOL invalides. Un argument nommé répété est
une erreur de parsing. `E014` rejette une assertion de scénario sur une cible
inconnue. W102/W135 examinent l'ordre et les branches ; une approbation
conditionnelle ne supprime pas W120. W119 comprend les aliases reason/SET et
les cibles resource/device/id notamment ; W125 vérifie la polarité des gardes.
Ces avertissements demeurent conservateurs et non bloquants dans CHECK.

TEST utilise la logique trivalente pour EXPECT : UNKNOWN, même nié, n'est
jamais une réussite. Une éventualité devient définitivement satisfaite quand
elle a été observée vraie. Les invariants sont surveillés entre les actions.
La boucle partagée respecte UNTIL et MAX ; WITHIN ajoute un plafond.

Les OUTPUT sont posés dans GIVEN ou produits par les effets exécutés. Aucun
neutre de type n'est fabriqué. Plusieurs OUTCOME possibles exigent
`scenario.outcome.<outil> = branche` ; la sélection de plan exige
`llm.plan = nom`. GIVEN et EFFECT invalides, trace ERROR, W117/W118 rendent le
scénario rouge. Une suite vide retourne 2, sauf `--allow-empty` explicite.

`GIVEN EVENT source { ... }` et `GIVEN MESSAGE nom FROM acteur { ... }`
injectent les stimuli au début. EXPECT accepte un bloc d'expressions ou
`CALL outil`, `NEVER CALL outil`, `BLOCKED outil`, `EVENT source`, `NO ERROR`.
Ce test reste celui d'un runtime isolé, sans transport inter-agents réel.
T5 ne prouve pas ces nouveaux stimuli/assertions (`V128`, indéterminé) et
signale les GIVEN invalides (`V127`).

Les détails, reproductions, adaptations des exemples et limites sont décrits
dans [audit-followup.md](audit-followup.md). La grammaire EBNF fait foi pour
les formes acceptées.

---

## 34. Le noyau de confiance et les permis (v1.9)

Jusqu'en v1.8, l'autorisation et l'appel à l'hôte vivaient dans
l'interpréteur (`runtime.py`) : toute ligne de l'interpréteur, du
planificateur ou du Studio pouvait, par erreur, appeler `Host.invoke`. La
garantie « aucune action interdite n'atteint le monde » dépendait donc de
plusieurs milliers de lignes.

Elle dépend désormais d'un **noyau** : `agentl/kernel/` (action canonique,
permis, porte d'autorisation, provenance), plus `policy.py`, `trivalent.py`,
`state.py` et `core.py`. La liste fait foi dans `agentl.kernel.TCB_FILES`, et
un test échoue si elle dépasse son budget.

### Le passage d'une action

```
proposition ──▶ copie figée ──▶ politique (§7) ──▶ approbation sur une autre copie
            ──▶ permis (usage unique, lié au condensat) ──▶ Host.invoke
```

1. La proposition est **figée** : une copie isolée, que ni le planificateur
   ni l'approbateur ne peuvent modifier. Une proposition non copiable est
   refusée.
2. La politique est évaluée sur la copie. Une exception vaut `DENY`.
3. L'approbateur reçoit une **seconde** copie. Seul le booléen `True` ou un
   mot d'accord reconnu (`APPROVAL_WORDS`) approuve. Une exception de
   l'approbateur vaut refus.
4. Le permis est émis **en dernier**. Il est lié au condensat canonique
   `(genre, cible, arguments)`, à usage unique, et un nouveau permis annule
   le précédent non présenté.

`Host.invoke`, `AsyncHost.invoke` et le registre `host.subagents` appellent
`require_permit(genre, cible, arguments)`. Sans permis actif, ou avec un
permis émis pour d'autres arguments, ils lèvent `PermitError`. Un permis ne
se duplique pas (sa copie est lui-même) et ne se sérialise pas. Un test d'architecture vérifie **sur le
code source** que seul le noyau appelle l'hôte.

### Les invariants

`tests/test_kernel_invariants_aaa.py` énonce dix invariants et porte un test
par invariant :

| | Invariant |
|---|---|
| I1 | aucune action n'atteint l'hôte sans permis émis par le noyau |
| I2 | `NEVER` ne se contourne jamais, ni par approbation, ni par `ALLOW` |
| I3 | une garde indéterminée ne crée jamais une autorisation |
| I4 | une donnée non fiable ne masque pas une donnée fiable |
| I5 | le LLM ne peut désigner qu'un plan déclaré |
| I6 | une action approuvée est exactement celle qui s'exécute |
| I7 | le rejeu ne crée aucune action absente du journal d'origine |
| I8 | la provenance d'une donnée survit à ses transformations |
| I9 | un crash suivi d'une reprise ne double pas un effet |
| I10 | une sortie du modèle n'est jamais une autorité |

Le protocole (I1, I6, I9) est aussi modélisé en TLA+ et vérifié
exhaustivement par TLC sur des bornes finies, avec des mutants qui doivent
être attrapés (`docs/formal/README.md`, `tools/check_formal.py`).

### Ce qui ne change pas

La trace est identique à l'octet : les journaux dorés publiés avec la v1.8.2
(`tests/golden/v1.8.2`) se rejouent sans changement, en synchrone comme en
asynchrone.

## 35. Provenance portée par les valeurs (v1.9)

### Le problème

W119, W125 et T6 (v1.5) reconnaissent la provenance par des **motifs** dans
l'AST : un nom `REASON` qui atteint un paramètre de cible, un texte brut qui
précède une action. Une valeur recopiée par `SET`, multipliée, choisie dans
une branche ou venue d'un plan que le modèle a sélectionné échappe à ces
motifs.

### Les étiquettes

Chaque valeur de l'état porte une **étiquette** : l'ensemble des sources qui
ont servi à la calculer.

| Source | Origine | Non fiable |
|---|---|---|
| `DECLARED` | littéral, déclaration du programme | |
| `RUNTIME` | fait calculé par le runtime | |
| `OBSERVED` | capteur déclaré (`OBSERVE`) | |
| `HUMAN` | réponse d'opérateur (`ASK`) | |
| `EFFECT` | postcondition prédite par un `EFFECT` | |
| `INFERRED` | postérieur bayésien | |
| `FALLBACK` | repli déclaré d'un capteur muet | |
| `TOOL` | sortie d'outil | ✓ |
| `LLM` | sortie de `REASON`, plan choisi par le modèle | ✓ |
| `MESSAGE`, `EVENT`, `DELEGATE` | charges utiles, retour de sous-agent | ✓ |
| `SHARED`, `MEMORY` | mémoire écrite par un autre agent, rechargée | ✓ |
| `EXTERNAL` | valeur que l'hôte déclare non fiable | ✓ |
| `UNKNOWN` | aucune étiquette connue | ✓ |

Règles de propagation :

- l'étiquette d'une expression est l'**union** de ce qu'elle lit (y compris
  les deux côtés d'un `AND`, même quand le premier suffit) ;
- une valeur affectée sous une décision porte aussi l'étiquette de cette
  décision (**flux implicite**) : branche `IF` sur une donnée reçue, plan
  choisi par le modèle, gestionnaire d'événement ;
- aucune transformation ne retire une source. Une étiquette trop large
  refuse davantage ; trop étroite, elle blanchirait ;
- l'hôte peut **dégrader** une lecture (`agentl.kernel.provenance.untrusted`
  ajoute `EXTERNAL`), jamais l'élever.

### Les fonctions

`UNTRUSTED(x)`, `TRUSTED(x)`, `LLM_DERIVED(x)`, `ATTESTED(x)`,
`ATTESTED(x, outil)`, `ORIGIN(x)`. Leur argument est un **chemin**, lu avec la
même précédence que partout ailleurs. Dans une garde de politique, un nom
d'argument de l'action désigne cet argument, et `action` désigne l'action
entière : ses arguments et la décision qui l'a produite.

```
NEVER wipe_host WHEN UNTRUSTED(host) AND NOT ATTESTED(host, check_wipeable)
NEVER transfer  WHEN LLM_DERIVED(to) AND NOT ATTESTED(to, resolve_account)
NEVER deploy    WHEN UNTRUSTED(action)
```

**Attestation.** Quand un outil s'exécute avec succès, chaque valeur de ses
arguments est **attestée** par cet outil. `ATTESTED(x, v)` le lit. Une
attestation n'est pas une origine : l'étiquette de `x` ne change pas
(*validated ≠ trusted*). Un validateur refuse donc en **levant**. Un
validateur qui rend `{"ok": "no"}` a accepté la valeur en argument, et l'a
donc attestée.

**Sens de sûreté.** Une valeur sans étiquette connue est `UNKNOWN`, rangée
parmi les sources non fiables. Une valeur indéfinie rend la fonction
indéfinie, donc la garde indéterminée : un `NEVER` s'applique, un `ALLOW` ne
compte pas (§7.1).

### Diagnostics

| Code | Signification |
|---|---|
| `E015` | fonction inconnue dans une expression — l'expression serait inévaluable |
| `E016` | fonction de provenance mal employée : argument qui n'est pas un chemin, `action` hors d'une garde de politique, `ATTESTED(action)`, second argument qui ne nomme pas un `TOOL` |

Avant la v1.9, tout appel en position d'expression était signalé `E009`,
fonctions pures comprises (`CONFIDENCE(x)`, `len(xs)`), et un nom inconnu dans
une garde passait sans diagnostic.

### Limite actuelle

Les gardes de provenance sont évaluées **à l'exécution**. `W119`, `W125` et T6
ne les créditent pas encore : un programme protégé par
`NEVER … WHEN UNTRUSTED(target)` reste signalé tant qu'il n'a pas le motif
statique (`ATTESTS`). Les deux formes se complètent.

## 36. Exécution durable (v1.9)

Le rejeu (§26) re-dérive une exécution **terminée**. L'exécution durable
reprend une exécution **interrompue**. Elle repose sur la même propriété : le
cœur n'a aucune source de non-déterminisme propre. Reprendre, c'est
ré-exécuter le programme depuis le tick 0 en servant chaque franchissement de
frontière depuis un journal écrit au fil de l'eau, puis continuer en direct.

### Protocole

```
permis ──▶ intention écrite + fsync ──▶ Host.invoke ──▶ résultat écrit + fsync
fin de tick ──▶ point de contrôle (empreintes d'état et de trace)
```

Chaque action porte un identifiant `run:agent:tTICK:aSEQ` et une **clé
d'idempotence** dérivée de l'exécution, de l'agent, du rang et du condensat
de l'action. La clé est stable d'une reprise à l'autre. Un outil la lit par
`agentl.kernel.current_action().idempotency_key`.

### Reprise

1. Le journal est relu et sa chaîne vérifiée. Une dernière ligne à moitié
   écrite (jamais synchronisée, donc jamais suivie d'un appel) est écartée.
   Une ligne corrompue au milieu arrête tout.
2. Le programme est ré-exécuté. Chaque franchissement est servi par le
   journal : aucun effet, aucun appel au modèle. Une divergence (programme
   modifié, approbation journalisée pour d'autres arguments) arrête la
   reprise (`ReplayDivergence`).
3. Chaque point de contrôle est comparé à l'état re-dérivé.
4. Une intention sans résultat est **tranchée** :
   - l'outil est déclaré `idempotent=True` → relancée avec la même clé ;
   - un réconciliateur (`@host.reconciler`) dit ce qui s'est passé → son
     résultat est journalisé, ou l'action est exécutée s'il rend
     `NOT_EXECUTED` ;
   - sinon → **indéterminée** : non relancée, `ActionInDoubt`, trace
     `ERROR`, `SIDE_EFFECT` marqués `.dirty`, aucun `EFFECT` présumé, et
     `tools.<outil>.in_doubt = true` (posé à `false` au démarrage, donc lisible
     par une garde sans indétermination).
5. Passé le journal, l'exécution continue en direct.

### Garanties

- une action dont le résultat est journalisé ne se ré-exécute jamais ;
- une action interrompue s'exécute **exactement une fois** si l'hôte honore
  la clé ou réconcilie, **au plus une fois** sinon ;
- lectures, questions et approbations en vol sont refaites ; les événements
  d'un `drain` en vol sont perdus (au plus une fois).

La clé ne se fait respecter que par le service de l'hôte : le runtime la
génère, il ne peut pas l'imposer à un tiers. La chaîne du journal est un
SHA-256 sans clé : elle détecte la corruption, pas un faussaire qui recalcule
tout.

### Interface

```
agentl run FICHIER.agent --durable RÉPERTOIRE [--run-id ID]
agentl durable status RÉPERTOIRE
agentl durable export RÉPERTOIRE [-o journal.json]     # puis agentl replay
```

`DurableRun(agents, host, llm, store=FileStore(dir) | SQLiteStore(path, id)
| MemoryStore())`, `.run(max_ticks)`, `.export()`. Une société s'exécute sous
un seul journal.

## 37. Exécution asynchrone (v1.9)

`agentl.aio` ajoute des hôtes et des modèles `async` sans changer la
sémantique du langage.

- **Séquentiel par agent.** Deux actions concurrentes d'un même agent
  seraient jugées sur un état que l'autre est en train de changer : un TOCTOU
  créé par le runtime lui-même. La concurrence est donc entre agents et aux
  frontières.
- **Cœur déterministe.** Un tick s'exécute dans un fil de travail. Chaque
  franchissement de frontière y devient une coroutine confiée à la boucle
  d'événements. La trace ne dépend que de ce que les frontières ont rendu.
- **Bornes** (`Limits`) : outils, appels au modèle et lectures concurrents
  bornés par sémaphores (contre-pression), délais par genre d'appel,
  capacité des boîtes de réception.
- **Délais.** Capteur → ne perçoit rien. Modèle → oracle muet (`DEFAULT`).
  **Outil → action indéterminée** (`ActionInDoubt`) : la requête est partie,
  l'effet a pu avoir lieu.
- **Annulation** coopérative : `Cancelled`, que rien n'avale, au prochain
  franchissement. Les appels en vol sont annulés.
- **`AsyncSociety`** : tous les agents tiquent en même temps sur un instantané
  de la mémoire partagée. Messages et écritures `SHARED` sont fusionnés à la
  barrière, dans l'ordre déclaré. Même programme, mêmes hôtes : même
  résultat, quel que soit l'ordonnanceur.

## 38. Validation externe (v1.9)

- **Propriétés** (`tests/test_properties_aaa.py`) : sémantique de Kleene des
  gardes, sens de sûreté de la politique, solveur, parseur (mutation de
  sources), opérations sur les permis. Nombre de tirages réglable par
  `AGENTL_PROPERTY_RUNS`.
- **Compatibilité entre versions** (`tests/test_release_compat_aaa.py`) : les
  journaux dorés d'une version publiée se rejouent à l'identique.
- **Modèle formel** du protocole du noyau (§34).
- **Banc comparatif** (`bench/frameworks/`) : le même modèle compromis
  scripté contre AGENT-L, LangGraph, PydanticAI et CrewAI, chacun avec son
  mécanisme de sûreté documenté ; oracle extérieur, un processus par phase,
  versions figées, `run.py --check` pour la CI. Les résultats et leurs
  limites sont dans `bench/frameworks/README.md`.

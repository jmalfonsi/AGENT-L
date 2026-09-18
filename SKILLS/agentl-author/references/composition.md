# Composition : hiérarchies d'agents et tâches lourdes

AGENT-L **compose avec des collaborateurs déclarés — il ne recrute pas.**
L'ensemble des agents et sous-agents est fixé par l'hôte à la construction ; un
agent ne peut pas inventer un nouvel agent à l'exécution (même règle que « un
outil non déclaré n'existe pas »). Trois mécanismes, du plus simple au plus
riche.

## 1. `DELEGATE` — sous-agent oracle synchrone (v0.3)

La primitive pour une tâche **lourde ou spécialisée**. Appel, attente, réponse
liée par `EXPECT` :

```agentl
DELEGATE forensic {
    TASK   "analyser l'e-mail et rendre un verdict borné"
    INPUT  { mail.reported }
    EXPECT { malicious, threat_class, forensic_confidence }
}
```

Côté hôte, `host.subagents["forensic"]` est **n'importe quel callable** : une
fonction, un autre LLM, un outil coûteux, ou **un runtime AGENT-L imbriqué**
(voir §4). Le runtime vérifie le contrat : une clé `EXPECT` manquante est
tracée en erreur. Le sous-agent ne peut renvoyer que ces champs — sa sortie est
bornée exactement comme celle d'un `REASON`.

**En test, un `DELEGATE` se pose comme un `REASON`.** Un `SCENARIO` du
délégant écrit dans son `GIVEN` les champs `EXPECT` que le spécialiste est
supposé avoir rendus :

```agentl
SCENARIO verdict_malveillant_mise_en_quarantaine {
    GIVEN {
        forensic.done = yes
        malicious = yes
        threat_class = credential_phishing
        mailbox.criticality = normal
        incident.status = open
        quarantine_mail.moved = yes
    }
    EXPECT { incident.status == handled } WITHIN 5
}
```

C'est cohérent avec la frontière : le délégant ne voit *que* ces champs, donc
son scénario ne teste que sa propre conduite face à un verdict donné. Le
spécialiste, lui, porte ses propres `SCENARIO` et se vérifie seul. Un champ
non posé reste absent, et le contrat `EXPECT` le signale comme en production.

**Piège de rédaction** : n'oublie pas de poser aussi ce qu'exigent les
**contrats `INPUT`** des outils que le scénario doit atteindre. Un paramètre
lié par `BIND` à un chemin non posé fait échouer le contrat, l'appel est
bloqué, et le scénario échoue pour une raison qui n'a rien à voir avec la
propriété testée.

## 2. `MESSAGE` + `Society` — pairs asynchrones (v0.6)

Pour plusieurs agents **en parallèle** :

```agentl
MESSAGE check_host { TO network_agent  PAYLOAD { host = alert.host } }
...
ON MESSAGE traffic_verdict { WHEN confidence > 0.8  THEN ... }
```

Remise asynchrone (adressée `TO` ou diffusée), traitée à la phase `RECEIVE` du
tick suivant du destinataire — jamais dans le même souffle que l'émission.
`Society` (`agentl/society.py`) ordonne les runtimes en tour de rôle et achemine
les enveloppes. Exemple : `examples/soc_team.agent`.

**`agentl run` ordonnance la société lui-même** — il n'y a pas de script à
lancer à la main. Ce qui change, c'est le contrat de `build()`, qui rend alors
`(hosts, llms)` **indexés par nom d'agent** :

```python
def build():
    return {"SOC_ANALYST": analyst, "RESPONDER": responder}, MockLLM()
```

Un dict d'hôtes avec un LLM partagé, ou un dict de LLM : les deux passent. Un
hôte fourni pour un agent absent du programme est une **erreur**, pas un
oubli silencieux. Un `Host` simple reste accepté — il est alors partagé.

Ce que les portes de qualité deviennent sur une société :

- `agentl check` ajoute les contrôles **inter-agents** (`check_program`) :
  `E012` noms d'AGENT dupliqués sans distinction de casse,
  `E007` message adressé à un agent absent, `W111` message que personne ne
  reçoit, `W112` `ON MESSAGE` que personne n'émet. Ce sont les seules erreurs
  qu'aucune relecture d'un fichier isolé ne peut trouver.
- `agentl test` exécute les `SCENARIO` **agent par agent**, jamais la société :
  GIVEN MESSAGE teste un gestionnaire avec un message injecté, mais ne teste
  pas le transport ni un échange entre plusieurs runtimes. Voir [scenarios.md](scenarios.md).
- `agentl boundary` analyse `xxx.py` et ses imports locaux. Si ces imports
  assemblent plusieurs hôtes distincts, B014 reste explicitement incomplet :
  contrôler chaque couple et relire leur composition.
- `agentl run --record` / `replay` **couvrent les sociétés**, échanges compris.

## 3. `MEMORY { SHARED }` — état partagé versionné par clé (v1.2)

Un dictionnaire unique ; chaque écriture incrémente la version de *sa* clé,
dernier écrivain gagnant, **conflit tracé et compté**. Coordination sur des
faits (`SHARED.blocked_hosts`) sans faux conflits entre écritures sans rapport.

## 4. Le motif hiérarchique : un DELEGATE vers un agent AGENT-L imbriqué

C'est la brique pour l'orchestration lourde et auditable : un **superviseur**
délègue à un **spécialiste** qui est lui-même un agent AGENT-L complet — sa
perception, son inférence, sa politique, sa preuve `verify`. Exemple de
référence : `examples/supervisor.agent` (superviseur) délègue à
`examples/forensic.agent` (spécialiste), câblé dans
`examples/supervisor.py` :

```python
from agentl import Host, MockLLM, Runtime, Symbol, parse_file

def _run_forensic(payload: dict) -> dict:
    sub = Host()
    sub.sensors["mail.spf_result"] = lambda: Symbol(...)   # perception du spécialiste
    sub.tools["deep_scan"] = lambda: {"verdict_ready": Symbol("yes")}
    forensic = parse_file("examples/forensic.agent").agents[0]
    rt = Runtime(forensic, sub, MockLLM()).run()           # ← runtime imbriqué
    inf = rt.inferences.get("phishing")                    # SON postérieur calculé
    return {                                                # ne remonter que l'EXPECT
        "malicious": Symbol("yes" if inf and inf.supported else "no"),
        "threat_class": rt.state.beliefs.get("threat_class").value,
        "forensic_confidence": round(inf.posterior, 3),
    }

h.subagents["forensic"] = _run_forensic
```

Ce que la démo prouve (les trois scénarios se rejouent) :
- **malveillant** : le spécialiste calcule `P(phishing)=0.895` → `malicious=yes` ;
  le superviseur **synthétise** `quarantine_mail`.
- **bénin** (`MAIL_SCENARIO=benign`) : `P=0.003` → `malicious=no` ; le
  superviseur **clôt** l'incident. Le verdict change parce que l'inférence du
  spécialiste change, pas parce qu'on l'a écrit.
- **boîte critique** (`MAILBOX_CRITICALITY=CRITICAL`) : la politique **du
  superviseur** (`REQUIRE APPROVAL WHEN mailbox.criticality == CRITICAL`) exige
  un humain — indépendamment de la politique du spécialiste.

Points de conception :
- **Frontière = contrat.** Le superviseur ne voit jamais l'état interne du
  spécialiste, seulement les champs `EXPECT`. Marshaler *uniquement* ces champs.
- **Un agent délégué reste un agent : il porte son propre hôte, sous son
  propre nom.** La norme `X.agent ↔ X.py` ne souffre aucune exception pour un
  spécialiste. Câbler sa perception directement dans l'hôte du superviseur
  paraît économique et coûte trois choses : `agentl boundary` rend `B000`
  (hôte introuvable) et ne contrôle donc **jamais** la frontière du
  spécialiste ; l'agent n'est plus exécutable ni observable seul ; et les deux
  étages peuvent diverger, puisque la perception n'a plus qu'une seule copie,
  au mauvais endroit. Le bon montage : `forensic.py` expose son `build()`, et
  `supervisor.py` l'**importe** pour construire le sous-agent.
- **Chaque étage se vérifie seul** — et cela vaut pour les **cinq** portes,
  pas seulement `check` et `verify` : `boundary` et `test` s'exécutent aussi
  fichier par fichier. Ne pas déduire leur réussite du nom des exemples :
  `forensic.agent` reste notamment un exemple historique sans SCENARIO,
  exempté explicitement en CI ; ajouter ses critères pour une nouvelle livraison.
- **Le déterminisme reste possible.** Ici les deux agents sont sans LLM
  (inférence pure), donc `run` est reproductible et sans réseau. Remplacer par
  `GeminiLLM` là où un vrai jugement en langage est nécessaire.
- **Les métriques ne fusionnent pas.** Les `inferences` du sous-agent
  n'apparaissent pas dans les métriques du superviseur — ce sont deux runtimes.

## 5. Confiance, concurrence et reprise entre agents (v1.9)

**Ce qu'un autre agent écrit n'est pas fiable par défaut.** Une charge utile
`MESSAGE` porte la source `MESSAGE`, un retour `DELEGATE` la source
`DELEGATE`, une lecture `SHARED.<clé>` la source `SHARED`. Les trois sont
**non fiables** : un agent compromis ou trompé ne doit pas pouvoir choisir la
cible de l'action d'un autre. Se protéger dans le destinataire :

```agentl
NEVER isolate WHEN UNTRUSTED(host) AND NOT ATTESTED(host, lookup_asset)
```

Le destinataire re-valide la cible par un outil à lui. Il ne fait pas
confiance à l'identité revendiquée par l'expéditeur : un scénario d'attaque
l'écrit avec `GIVEN MESSAGE … FROM attacker`.

**Exécution réellement concurrente** : `agentl.aio.AsyncSociety` fait tiquer
tous les agents en même temps, chacun sur un instantané de la mémoire
partagée. Messages et écritures `SHARED` sont fusionnés à la barrière de fin
de tour, dans l'ordre déclaré des agents. Le résultat ne dépend pas de
l'ordonnanceur. `Limits(inbox_capacity=…)` borne les boîtes de réception :
un message refusé est tracé et compté, jamais perdu en silence.

**Reprise après panne** : `agentl run --durable DIR` accepte une société. Un
seul journal ordonne tous les agents ; la reprise re-dérive la société entière
puis continue (`runtime-semantics.md` §10).

## La limite, assumée

Pas de spawn dynamique, pas de transaction distribuée, pas de consensus.
L'ordre global des messages est le tour de rôle (`Society`) ou la barrière de
fin de tour (`AsyncSociety`), rien de plus.
Ces garanties se paient ; le langage ne les promet pas. Pour paralléliser une
charge, on la place dans un `DELEGATE` (éventuellement un agent imbriqué) ou on
déploie des pairs sous une `Society`.

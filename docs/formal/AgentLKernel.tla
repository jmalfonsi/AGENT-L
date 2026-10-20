---------------------------- MODULE AgentLKernel ----------------------------
(***************************************************************************)
(* Modèle du noyau d'exécution d'AGENT-L (v1.9).                            *)
(*                                                                         *)
(* Ce qui est modélisé, et à quoi cela correspond dans le code :           *)
(*                                                                         *)
(*   Decide    Kernel.authorize : politique déterministe, approbation      *)
(*             journalisée et servie par le journal à la reprise.          *)
(*   Tamper    un hôte enveloppant qui réécrit les arguments après la      *)
(*             décision (agentl/kernel/permit.py, require_permit).         *)
(*   Intent    DurableJournal.dispatch : intention écrite avant l'appel ;  *)
(*             à la reprise, résultat servi ou action tranchée (_settle).  *)
(*   Dispatch  Host.invoke sous permis ; un hôte idempotent ignore une     *)
(*             seconde requête portant la même clé.                        *)
(*   Result    le résultat journalisé après l'appel.                       *)
(*   Crash     le processus meurt à n'importe quel pas : tout ce qui est   *)
(*             en mémoire disparaît, le journal et le monde restent ; une  *)
(*             approbation en queue de journal est retirée (1.12.3).       *)
(*                                                                         *)
(* Depuis la 1.15, le monde change et le temps passe pendant un crash :    *)
(*                                                                         *)
(*   Perceive  la lecture des capteurs que nomment les gardes, journalisée *)
(*             et donc servie telle quelle à la reprise.                   *)
(*   Rejudge   Kernel.recheck : avant d'exécuter une action décidée sur    *)
(*             une lecture rejouée — intention en suspens, ou action       *)
(*             nouvelle d'un tick re-dérivé —, le monde d'aujourd'hui est  *)
(*             relu, et l'humain reconsulté si l'approbation est périmée.  *)
(*                                                                         *)
(* Le programme est une suite de N actions, rejouée dans le même ordre à   *)
(* chaque reprise : c'est le déterminisme du cœur (SPEC §26). Une écriture *)
(* au journal est atomique : synchronisée (fsync) ou absente — une ligne   *)
(* déchirée est écartée à la relecture (FileStore.load).                   *)
(*                                                                         *)
(* Ce qui ne l'est pas : l'évaluation des gardes (couverte par les tests   *)
(* de propriétés P1-P4), le solveur (P5-P6), l'hôte lui-même au-delà de sa *)
(* promesse d'idempotence ou de réconciliation.                            *)
(***************************************************************************)
EXTENDS Naturals, Sequences

CONSTANTS N,          \* nombre d'actions du programme
          MaxCrashes  \* crashs explorés par exécution

Acts     == 1..N
Verdicts == {"ALLOW", "DENY", "APPROVE"}
Modes    == {"plain", "idempotent", "reconcile"}
Judged   == "judged"                   \* les arguments que la politique a vus
NoPermit == [status |-> "none", act |-> 0, args |-> "none"]

VARIABLES policy,   \* verdict de la politique, par action (déterministe)
          mode,     \* promesse de l'hôte : rien, idempotence, réconciliation
          pc,       \* prochaine action du programme
          phase,    \* "idle" | "authorized" | "intent" | "sent"
          permit,   \* le permis en cours — au plus un
          sent,     \* arguments effectivement présentés à l'hôte
          wal,      \* journal durable : survit aux crashs
          count,    \* effets appliqués dans le monde, par action
          effArgs,  \* arguments avec lesquels un effet a eu lieu
          crashes,
          aborted,  \* PermitError : l'exécution s'arrête
          facts,    \* le monde, aujourd'hui, interdit-il l'action ? (1.15)
          lapsed,   \* le temps écoulé périme-t-il les approbations d'avant ?
          stale,    \* histoire : un effet a eu lieu contre le monde du moment
          lapsedUse \* histoire : une action est partie sur une approbation périmée

vars == <<policy, mode, pc, phase, permit, sent, wal, count, effArgs,
          crashes, aborted, facts, lapsed, stale, lapsedUse>>

\* Chaque écriture porte l'incarnation du processus qui l'a faite : ce que la
\* reprise sait de son préfixe (DurableJournal._prefix).
Rec(t, i, yes, how) == [type |-> t, act |-> i, yes |-> yes, how |-> how,
                        inc |-> crashes]
Has(t, i)       == \E k \in 1..Len(wal) : wal[k].type = t /\ wal[k].act = i
Answer(i, yes)  == \E k \in 1..Len(wal) :
                     wal[k].type = "approve" /\ wal[k].act = i /\ wal[k].yes = yes
\* La lecture journalisée qui a servi à décider : vraie si le monde interdisait.
Forbidden(i)    == \E k \in 1..Len(wal) :
                     wal[k].type = "read" /\ wal[k].act = i /\ wal[k].yes
\* …faite par un processus mort depuis : la reprise la rejoue, périmée.
StaleRead(i)    == \E k \in 1..Len(wal) :
                     wal[k].type = "read" /\ wal[k].act = i /\ wal[k].inc < crashes
Authorized(i)   == ~Forbidden(i)
                   /\ (policy[i] = "ALLOW"
                       \/ (policy[i] = "APPROVE" /\ Answer(i, TRUE)))
Settled(i, how) == \E k \in 1..Len(wal) :
                     wal[k].type = "result" /\ wal[k].act = i /\ wal[k].how = how
InDoubt(i)      == Settled(i, "in_doubt")
Rejudged(i)     == Settled(i, "revoked") \/ Settled(i, "refused")
ApprovedNow(w, i) == \E k \in 1..Len(w) :
                       w[k].type \in {"approve", "renew"} /\ w[k].act = i
                       /\ w[k].yes /\ w[k].inc = crashes
Done            == pc = N + 1 /\ phase = "idle"

Init ==
  /\ policy \in [Acts -> Verdicts]
  /\ mode \in Modes
  /\ pc = 1 /\ phase = "idle" /\ permit = NoPermit /\ sent = "none"
  /\ wal = << >>
  /\ count = [i \in Acts |-> 0] /\ effArgs = [i \in Acts |-> {}]
  /\ crashes = 0 /\ aborted = FALSE
  /\ facts = [i \in Acts |-> FALSE] /\ lapsed = FALSE
  /\ stale = FALSE /\ lapsedUse = FALSE

\* ------------------------------------------------------------ décision
Grant(i) == /\ permit' = [status |-> "issued", act |-> i, args |-> Judged]
            /\ sent' = Judged
            /\ phase' = "authorized"
            /\ UNCHANGED pc

Refuse(i) == /\ pc' = i + 1 /\ phase' = "idle" /\ permit' = NoPermit
             /\ UNCHANGED sent

\* La lecture des capteurs des gardes, journalisée : une reprise la sert
\* depuis le journal, et re-dérive donc la décision du processus mort.
Perceive ==
  /\ ~aborted /\ phase = "idle" /\ pc <= N /\ ~Has("read", pc)
  /\ wal' = Append(wal, Rec("read", pc, facts[pc], ""))
  /\ UNCHANGED <<policy, mode, pc, phase, permit, sent, count, effArgs,
                 crashes, aborted, facts, lapsed, stale, lapsedUse>>

Decide ==
  /\ ~aborted /\ phase = "idle" /\ pc <= N /\ Has("read", pc)
  /\ LET i == pc IN
       \/ /\ Forbidden(i) /\ Refuse(i) /\ UNCHANGED wal    \* garde vraie
       \/ /\ ~Forbidden(i) /\ policy[i] = "DENY" /\ Refuse(i) /\ UNCHANGED wal
       \/ /\ ~Forbidden(i) /\ policy[i] = "ALLOW" /\ Grant(i) /\ UNCHANGED wal
       \/ /\ ~Forbidden(i) /\ policy[i] = "APPROVE" /\ Answer(i, TRUE)  \* servi
          /\ Grant(i) /\ UNCHANGED wal
       \/ /\ ~Forbidden(i) /\ policy[i] = "APPROVE" /\ Answer(i, FALSE) \* servi
          /\ Refuse(i) /\ UNCHANGED wal
       \/ /\ ~Forbidden(i) /\ policy[i] = "APPROVE" /\ ~Has("approve", i)
          /\ \E yes \in BOOLEAN :                           \* demandé
               /\ wal' = Append(wal, Rec("approve", i, yes, ""))
               /\ IF yes THEN Grant(i) ELSE Refuse(i)
  /\ UNCHANGED <<policy, mode, count, effArgs, crashes, aborted, facts,
                 lapsed, stale, lapsedUse>>

\* ------------------------------------------------ l'environnement triche
Tamper ==
  /\ ~aborted /\ phase = "authorized" /\ sent = Judged
  /\ sent' = "rewritten"
  /\ UNCHANGED <<policy, mode, pc, phase, permit, wal, count, effArgs,
                 crashes, aborted, facts, lapsed, stale, lapsedUse>>

\* --------------------------------------------------- intention, règlement
Serve(i) == /\ pc' = i + 1 /\ phase' = "idle"
            /\ permit' = [permit EXCEPT !.status = "spent"]

Go == /\ phase' = "intent" /\ permit' = [permit EXCEPT !.status = "dispatching"]
      /\ UNCHANGED pc

\* Partir. Une approbation exigée doit dater de cette incarnation, ou ne pas
\* être périmée : sinon l'histoire le retient (NoLapsedApproval).
Launch(i, w) ==
  /\ wal' = w /\ Go /\ UNCHANGED <<count, effArgs>>
  /\ lapsedUse' = (lapsedUse \/ (policy[i] = "APPROVE" /\ lapsed
                                  /\ ~ApprovedNow(w, i)))

\* Refusée au rejugement : réglée sans effet nouveau.
Drop(i, w, how) ==
  /\ wal' = Append(w, Rec("result", i, FALSE, how))
  /\ Serve(i) /\ UNCHANGED <<count, effArgs, lapsedUse>>

\* Rejuger avant de relancer (1.15) : le monde d'aujourd'hui, et l'humain si
\* l'approbation est périmée — c'est alors lui qui répond, librement.
Asked(i)   == policy[i] = "APPROVE" /\ lapsed /\ ~facts[i]
Rejudge(i) == IF facts[i] THEN {FALSE} ELSE IF Asked(i) THEN BOOLEAN ELSE {TRUE}
Renewal(i, ok) == IF Asked(i) THEN Append(wal, Rec("renew", i, ok, "")) ELSE wal

Intent ==
  /\ ~aborted /\ phase = "authorized" /\ permit.status = "issued"
  /\ LET i == permit.act IN
       \/ /\ Has("result", i)                  \* journalisé : servi
          /\ Serve(i) /\ UNCHANGED <<wal, count, effArgs, lapsedUse>>
       \/ /\ ~Has("result", i) /\ ~Has("intent", i)   \* neuf : écrire
          /\ IF StaleRead(i) /\ facts[i]       \* décidée sur une lecture rejouée
             THEN Drop(i, wal, "revoked")       \* que le monde dément
             ELSE Launch(i, Append(wal, Rec("intent", i, TRUE, "")))
       \/ /\ ~Has("result", i) /\ Has("intent", i)    \* en suspens
          /\ \/ /\ mode = "reconcile" /\ count[i] > 0  \* l'effet a eu lieu
                /\ wal' = Append(wal, Rec("result", i, TRUE, "reconciled"))
                /\ Serve(i) /\ UNCHANGED <<count, effArgs, lapsedUse>>
             \/ /\ (mode = "idempotent" \/ (mode = "reconcile" /\ count[i] = 0))
                /\ \E ok \in Rejudge(i) :      \* relance, même clé : rejugée
                     IF ok THEN Launch(i, Renewal(i, ok))
                     ELSE Drop(i, Renewal(i, ok),
                               IF mode = "reconcile" THEN "revoked" ELSE "refused")
             \/ /\ mode = "plain"                      \* indéterminé
                /\ wal' = Append(wal, Rec("result", i, FALSE, "in_doubt"))
                /\ Serve(i) /\ UNCHANGED <<count, effArgs, lapsedUse>>
  /\ UNCHANGED <<policy, mode, sent, crashes, aborted, facts, lapsed, stale>>

\* ------------------------------------------------------------- l'hôte
Dispatch ==
  /\ ~aborted /\ phase = "intent" /\ permit.status = "dispatching"
  /\ LET i == permit.act IN
       IF sent # permit.args
       THEN \* require_permit : ce ne sont pas les arguments jugés
            /\ aborted' = TRUE
            /\ UNCHANGED <<phase, count, effArgs, stale>>
       ELSE /\ count' = [count EXCEPT ![i] =
                           IF mode = "idempotent" /\ @ > 0 THEN @ ELSE @ + 1]
            /\ effArgs' = [effArgs EXCEPT ![i] = @ \cup {sent}]
            \* Un effet nouveau, alors que le monde du moment l'interdit.
            /\ stale' = (stale \/ (facts[i] /\ count'[i] > count[i]))
            /\ phase' = "sent"
            /\ UNCHANGED aborted
  /\ UNCHANGED <<policy, mode, pc, permit, sent, wal, crashes, facts, lapsed,
                 lapsedUse>>

Result ==
  /\ ~aborted /\ phase = "sent"
  /\ LET i == permit.act IN
       /\ wal' = Append(wal, Rec("result", i, TRUE, "ok"))
       /\ Serve(i)
  /\ UNCHANGED <<policy, mode, sent, count, effArgs, crashes, aborted, facts,
                 lapsed, stale, lapsedUse>>

\* ------------------------------------------------------------- panne
Crash ==
  /\ ~aborted /\ ~Done /\ crashes < MaxCrashes
  /\ crashes' = crashes + 1
  /\ pc' = 1 /\ phase' = "idle" /\ permit' = NoPermit /\ sent' = "none"
  \* Pendant que l'agent est mort, le monde change et le temps passe (1.15).
  /\ facts' \in [Acts -> BOOLEAN]
  /\ lapsed' \in BOOLEAN
  \* Une approbation en queue de journal n'a rien engagé : retirée à la
  \* réouverture, l'humain sera reconsulté (1.12.3).
  /\ wal' = IF wal # << >> /\ wal[Len(wal)].type = "approve"
            THEN SubSeq(wal, 1, Len(wal) - 1) ELSE wal
  /\ UNCHANGED <<policy, mode, count, effArgs, aborted, stale, lapsedUse>>

Halted == (Done \/ aborted) /\ UNCHANGED vars

Next == Perceive \/ Decide \/ Tamper \/ Intent \/ Dispatch \/ Result \/ Crash
          \/ Halted

\* Équité faible sur la progression : un pas possible finit par être fait.
\* Pas sur `Crash` ni `Tamper` — l'environnement n'est pas tenu d'agir.
Progress == Perceive \/ Decide \/ Intent \/ Dispatch \/ Result

Spec == Init /\ [][Next]_vars /\ WF_vars(Progress)

\* =============================================================== invariants
TypeOK ==
  /\ pc \in 1..(N + 1)
  /\ phase \in {"idle", "authorized", "intent", "sent"}
  /\ permit.status \in {"none", "issued", "dispatching", "spent"}
  /\ count \in [Acts -> 0..2]
  /\ facts \in [Acts -> BOOLEAN] /\ lapsed \in BOOLEAN

\* I1 — aucun effet sans autorisation : garde lue fausse, puis politique ou
\* approbation journalisée.
NoUnauthorizedEffect == \A i \in Acts : count[i] > 0 => Authorized(i)

\* I6 — l'hôte n'agit qu'avec les arguments que la politique a jugés.
OnlyJudgedArgs == \A i \in Acts : effArgs[i] \subseteq {Judged}

\* I9 — un crash suivi d'une reprise ne double jamais un effet.
AtMostOnce == \A i \in Acts : count[i] <= 1

\* Exactement une fois, là où l'hôte le permet — sauf si le monde, pendant le
\* crash, a retiré l'autorisation : l'action est alors rejugée et refusée.
ExactlyOnceWhenPromised ==
  (Done /\ ~aborted /\ mode \in {"idempotent", "reconcile"})
    => \A i \in Acts : (Authorized(i) /\ ~Rejudged(i)) => count[i] = 1

\* Rien ne se perd en silence : une action autorisée a eu lieu une fois, ou
\* elle est déclarée indéterminée, ou rejugée et refusée — jamais oubliée.
NothingLostSilently ==
  (Done /\ ~aborted) => \A i \in Acts : Authorized(i)
                          => (count[i] = 1 \/ InDoubt(i) \/ Rejudged(i))

\* L'indétermination n'est jamais une paresse : elle n'existe que faute de
\* promesse de l'hôte.
InDoubtOnlyWithoutPromise == \A i \in Acts : InDoubt(i) => mode = "plain"

\* I10 — rejuger avant d'agir (1.15) : aucun effet n'a lieu quand le monde du
\* moment l'interdit, même si la décision journalisée — prise sur une lecture
\* d'avant le crash — l'autorisait.
NoEffectAgainstToday == ~stale

\* Une approbation périmée ne couvre aucune action : l'humain est reconsulté.
NoLapsedApproval == ~lapsedUse

\* Révoquer, c'est affirmer que l'action n'a pas eu lieu : jamais à tort.
RevokedMeansNotExecuted == \A i \in Acts : Settled(i, "revoked") => count[i] = 0

\* Vivacité : quelle que soit la suite de crashs (bornée), l'exécution finit —
\* terminée, ou arrêtée net sur un permis refusé. Une reprise qui cale sur
\* son propre journal est un défaut, pas un état sûr.
Termination == <>(Done \/ aborted)

\* Un résultat journalisé ne se contredit pas : au plus un par action.
OneResult ==
  \A i \in Acts : \A k, l \in 1..Len(wal) :
    (wal[k].type = "result" /\ wal[l].type = "result"
     /\ wal[k].act = i /\ wal[l].act = i) => k = l
=============================================================================

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
(*             en mémoire disparaît, le journal et le monde restent.       *)
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
          aborted   \* PermitError : l'exécution s'arrête

vars == <<policy, mode, pc, phase, permit, sent, wal, count, effArgs,
          crashes, aborted>>

Rec(t, i, yes, how) == [type |-> t, act |-> i, yes |-> yes, how |-> how]
Has(t, i)       == \E k \in 1..Len(wal) : wal[k].type = t /\ wal[k].act = i
Answer(i, yes)  == \E k \in 1..Len(wal) :
                     wal[k].type = "approve" /\ wal[k].act = i /\ wal[k].yes = yes
Authorized(i)   == policy[i] = "ALLOW"
                     \/ (policy[i] = "APPROVE" /\ Answer(i, TRUE))
InDoubt(i)      == \E k \in 1..Len(wal) :
                     wal[k].type = "result" /\ wal[k].act = i
                     /\ wal[k].how = "in_doubt"
Done            == pc = N + 1 /\ phase = "idle"

Init ==
  /\ policy \in [Acts -> Verdicts]
  /\ mode \in Modes
  /\ pc = 1 /\ phase = "idle" /\ permit = NoPermit /\ sent = "none"
  /\ wal = << >>
  /\ count = [i \in Acts |-> 0] /\ effArgs = [i \in Acts |-> {}]
  /\ crashes = 0 /\ aborted = FALSE

\* ------------------------------------------------------------ décision
Grant(i) == /\ permit' = [status |-> "issued", act |-> i, args |-> Judged]
            /\ sent' = Judged
            /\ phase' = "authorized"
            /\ UNCHANGED pc

Refuse(i) == /\ pc' = i + 1 /\ phase' = "idle" /\ permit' = NoPermit
             /\ UNCHANGED sent

Decide ==
  /\ ~aborted /\ phase = "idle" /\ pc <= N
  /\ LET i == pc IN
       \/ /\ policy[i] = "DENY" /\ Refuse(i) /\ UNCHANGED wal
       \/ /\ policy[i] = "ALLOW" /\ Grant(i) /\ UNCHANGED wal
       \/ /\ policy[i] = "APPROVE" /\ Answer(i, TRUE)       \* servi
          /\ Grant(i) /\ UNCHANGED wal
       \/ /\ policy[i] = "APPROVE" /\ Answer(i, FALSE)      \* servi
          /\ Refuse(i) /\ UNCHANGED wal
       \/ /\ policy[i] = "APPROVE" /\ ~Has("approve", i)    \* demandé
          /\ \E yes \in BOOLEAN :
               /\ wal' = Append(wal, Rec("approve", i, yes, ""))
               /\ IF yes THEN Grant(i) ELSE Refuse(i)
  /\ UNCHANGED <<policy, mode, count, effArgs, crashes, aborted>>

\* ------------------------------------------------ l'environnement triche
Tamper ==
  /\ ~aborted /\ phase = "authorized" /\ sent = Judged
  /\ sent' = "rewritten"
  /\ UNCHANGED <<policy, mode, pc, phase, permit, wal, count, effArgs,
                 crashes, aborted>>

\* --------------------------------------------------- intention, règlement
Serve(i) == /\ pc' = i + 1 /\ phase' = "idle"
            /\ permit' = [permit EXCEPT !.status = "spent"]

Go == /\ phase' = "intent" /\ permit' = [permit EXCEPT !.status = "dispatching"]
      /\ UNCHANGED pc

Intent ==
  /\ ~aborted /\ phase = "authorized" /\ permit.status = "issued"
  /\ LET i == permit.act IN
       \/ /\ Has("result", i)                  \* journalisé : servi
          /\ Serve(i) /\ UNCHANGED <<wal, count, effArgs>>
       \/ /\ ~Has("result", i) /\ ~Has("intent", i)   \* neuf : écrire
          /\ wal' = Append(wal, Rec("intent", i, TRUE, ""))
          /\ Go /\ UNCHANGED <<count, effArgs>>
       \/ /\ ~Has("result", i) /\ Has("intent", i)    \* en suspens
          /\ \/ /\ mode = "idempotent"                 \* relance, même clé
                /\ Go /\ UNCHANGED <<wal, count, effArgs>>
             \/ /\ mode = "reconcile" /\ count[i] > 0  \* l'effet a eu lieu
                /\ wal' = Append(wal, Rec("result", i, TRUE, "reconciled"))
                /\ Serve(i) /\ UNCHANGED <<count, effArgs>>
             \/ /\ mode = "reconcile" /\ count[i] = 0  \* il n'a pas eu lieu
                /\ Go /\ UNCHANGED <<wal, count, effArgs>>
             \/ /\ mode = "plain"                      \* indéterminé
                /\ wal' = Append(wal, Rec("result", i, FALSE, "in_doubt"))
                /\ Serve(i) /\ UNCHANGED <<count, effArgs>>
  /\ UNCHANGED <<policy, mode, sent, crashes, aborted>>

\* ------------------------------------------------------------- l'hôte
Dispatch ==
  /\ ~aborted /\ phase = "intent" /\ permit.status = "dispatching"
  /\ LET i == permit.act IN
       IF sent # permit.args
       THEN \* require_permit : ce ne sont pas les arguments jugés
            /\ aborted' = TRUE
            /\ UNCHANGED <<phase, count, effArgs>>
       ELSE /\ count' = [count EXCEPT ![i] =
                           IF mode = "idempotent" /\ @ > 0 THEN @ ELSE @ + 1]
            /\ effArgs' = [effArgs EXCEPT ![i] = @ \cup {sent}]
            /\ phase' = "sent"
            /\ UNCHANGED aborted
  /\ UNCHANGED <<policy, mode, pc, permit, sent, wal, crashes>>

Result ==
  /\ ~aborted /\ phase = "sent"
  /\ LET i == permit.act IN
       /\ wal' = Append(wal, Rec("result", i, TRUE, "ok"))
       /\ Serve(i)
  /\ UNCHANGED <<policy, mode, sent, count, effArgs, crashes, aborted>>

\* ------------------------------------------------------------- panne
Crash ==
  /\ ~aborted /\ ~Done /\ crashes < MaxCrashes
  /\ crashes' = crashes + 1
  /\ pc' = 1 /\ phase' = "idle" /\ permit' = NoPermit /\ sent' = "none"
  /\ UNCHANGED <<policy, mode, wal, count, effArgs, aborted>>

Halted == (Done \/ aborted) /\ UNCHANGED vars

Next == Decide \/ Tamper \/ Intent \/ Dispatch \/ Result \/ Crash \/ Halted

\* Équité faible sur la progression : un pas possible finit par être fait.
\* Pas sur `Crash` ni `Tamper` — l'environnement n'est pas tenu d'agir.
Progress == Decide \/ Intent \/ Dispatch \/ Result

Spec == Init /\ [][Next]_vars /\ WF_vars(Progress)

\* =============================================================== invariants
TypeOK ==
  /\ pc \in 1..(N + 1)
  /\ phase \in {"idle", "authorized", "intent", "sent"}
  /\ permit.status \in {"none", "issued", "dispatching", "spent"}
  /\ count \in [Acts -> 0..2]

\* I1 — aucun effet sans autorisation : politique, ou approbation journalisée.
NoUnauthorizedEffect == \A i \in Acts : count[i] > 0 => Authorized(i)

\* I6 — l'hôte n'agit qu'avec les arguments que la politique a jugés.
OnlyJudgedArgs == \A i \in Acts : effArgs[i] \subseteq {Judged}

\* I9 — un crash suivi d'une reprise ne double jamais un effet.
AtMostOnce == \A i \in Acts : count[i] <= 1

\* Exactement une fois, là où l'hôte le permet.
ExactlyOnceWhenPromised ==
  (Done /\ ~aborted /\ mode \in {"idempotent", "reconcile"})
    => \A i \in Acts : Authorized(i) => count[i] = 1

\* Rien ne se perd en silence : une action autorisée a eu lieu une fois, ou
\* elle est déclarée indéterminée.
NothingLostSilently ==
  (Done /\ ~aborted) => \A i \in Acts : Authorized(i)
                          => (count[i] = 1 \/ InDoubt(i))

\* L'indétermination n'est jamais une paresse : elle n'existe que faute de
\* promesse de l'hôte.
InDoubtOnlyWithoutPromise == \A i \in Acts : InDoubt(i) => mode = "plain"

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

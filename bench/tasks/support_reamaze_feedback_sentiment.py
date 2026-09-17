"""Hôte AGENT-L pour support.reamaze_feedback_sentiment.

Règle de partage : cet hôte ne fournit que des FAITS et de la plomberie.
Aucun filtrage métier, aucune priorité, aucune boucle sur les éléments :
le choix de qui est analysé, de quel sentiment, de qui est routé et de ce
qui est journalisé est pris exclusivement dans le `.agent` (FOREACH +
REASON + POLICY).

Trois adaptations purement mécaniques y sont faites :

1. `reamaze_get_conversations` — le runtime ne projette que des scalaires
   (`messages` est une liste, donc invisible du programme). On aplatit donc
   le corps des messages client dans un champ scalaire `body`. C'est une
   reformulation du même fait, pas un jugement.
2. `reamaze_tag_conversation` / `reamaze_route_conversation` — le contrat
   `INPUT` d'AGENT-L exige *tous* les arguments déclarés à chaque appel.
   On expose donc deux arités du même outil du banc plutôt que de forcer
   le programme à passer une valeur vide.
3. `google_sheets_add_row` — l'outil du banc attend un objet JSON `cells`,
   or l'état d'AGENT-L est scalaire. On accepte des paires (clé, valeur)
   nommées par l'appelant et on les assemble. Les noms de colonnes comme
   les valeurs viennent du `.agent`.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "/home/ubuntu/AGENT-L")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import ab_bridge as ab  # noqa: E402
from agentl import Symbol  # noqa: E402


def build(info, world):
    host = ab.make_host(info, world)
    raw = dict(host.tools)

    def get_conversations():
        out = raw["reamaze_get_conversations"]()
        for conv in out.get("conversations", []):
            # BOUNDARY-OK: extraire la parole du client d'un fil est une
            # perception, pas un tri métier : une sous-liste n'est pas
            # projetée par FOREACH, le programme ne peut pas la lire.
            bodies = [str(m.get("body", "")) for m in conv.get("messages", [])
                      if m.get("author_type") == "customer"]
            conv["body"] = " ".join(bodies)
        return out

    def tag_conversation(conversation_id, tags):
        return raw["reamaze_update_conversation"](
            conversation_id=str(conversation_id), tags=str(tags))

    def route_conversation(conversation_id, tags, assignee_email):
        return raw["reamaze_update_conversation"](
            conversation_id=str(conversation_id), tags=str(tags),
            assignee_email=str(assignee_email))

    def add_row(spreadsheet, worksheet, key1, value1, key2, value2,
                key3, value3, key4, value4):
        cells = {}
        for key, value in ((key1, value1), (key2, value2),
                           (key3, value3), (key4, value4)):
            name = str(key).strip()
            if name:
                cells[name] = str(value)
        return raw["google_sheets_add_row"](
            spreadsheet=str(spreadsheet), worksheet=str(worksheet), cells=cells)

    host.tools["reamaze_get_conversations"] = get_conversations
    host.tools["reamaze_tag_conversation"] = tag_conversation
    host.tools["reamaze_route_conversation"] = route_conversation
    host.tools["google_sheets_add_row"] = add_row
    return host


_ = Symbol  # gardé pour l'homogénéité des hôtes du banc

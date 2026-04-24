# diff_against_stored(conversation, incoming_messages) -> new_turn_messages.
# Three MVP cases (PLAN principle #4): (a) append-only new turn, (b) client
# regeneration replacing last assistant turn, (c) mismatch → raise.
# Implementation + unit tests land in step 1.4.

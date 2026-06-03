#!/usr/bin/env python3
"""One-shot: purge ALL MemPalace collections + clear sync cursors for a FRESH
per-wing remine. Run while the server is STOPPED (so no daemon races the drop).

After this + a restart, the daemons re-mine file wings and re-derive chat wings
from the durable chat DB into fresh per-wing collections — no migration copy, no
old shared collection. The durable chat DB (chats.db) is NOT touched, so chat
history is fully re-derivable.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import brain as _brain  # noqa: E402

mcfg = _brain._load_mempalace_config() or {}
vsp = mcfg.get("venv_site_packages", "")
if vsp and vsp not in sys.path:
    sys.path.insert(0, vsp)
palace = mcfg.get("palace_path", "")
assert palace and os.path.isdir(palace), f"no palace at {palace!r}"

import chromadb  # noqa: E402

client = chromadb.PersistentClient(path=palace)
names = [c.name for c in client.list_collections()]
print(f"collections before: {names}")
dropped = []
for n in names:
    # Drop the old shared collections AND every per-wing copy — full clean slate.
    if n in ("mempalace_drawers", "mempalace_closets") or n.startswith(("wd_", "wc_")):
        try:
            client.delete_collection(n)
            dropped.append(n)
        except Exception as e:
            print(f"  drop {n} failed: {e}")
print(f"dropped: {dropped}")
print(f"collections after: {[c.name for c in client.list_collections()]}")

# Clear sync cursors so chat-sync re-files every turn, closets + KG rebuild.
from server_lib.db import _db_conn  # noqa: E402
with _db_conn() as c:
    for tbl in ("chat_mempalace_sync", "closet_regen_progress",
                "kg_extraction_progress", "kg_extraction_source_state"):
        try:
            c.execute(f"DELETE FROM {tbl}")
            print(f"cleared cursor table: {tbl}")
        except Exception as e:
            print(f"  clear {tbl}: {e}")
    c.commit()

# Mark migration done (nothing to migrate — fresh start) so wing_migrate no-ops.
state_path = os.path.join(palace, "wing_migrate_state.json")
with open(state_path, "w") as f:
    json.dump({"phase": "verified", "note": "fresh-reset", "fresh": True}, f, indent=2)
print(f"wrote migration state: {state_path} (phase=verified, fresh)")
print("DONE — restart the server; daemons will re-mine fresh into per-wing collections.")

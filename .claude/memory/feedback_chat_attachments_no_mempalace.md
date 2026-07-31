---
name: Don't mine chat-attachment content into MemPalace
description: Non-project chat attachments stay metadata-only in MemPalace by design — content lives in /tmp/brain-attachments and is read via read_document; do not propose mining them
type: feedback
originSessionId: d18c7235-4c49-4317-afb6-bf7079758162
---
When non-project chats have file attachments, MemPalace stores only filename + mime + size via the `attachment_metadata_drawer` path. The actual file content is NOT mined into the palace — it lives in `/tmp/brain-attachments/<session>/` and the agent reads it on-demand via `read_document`.

Don't propose adding a content-mining mode for chat attachments unless the user explicitly asks for it.

**Why:** User confirmed (2026-04-28) "no its ok, chat works good at the moment, no need to introduce complxity here." The privacy / blast-radius story is intentional — files in /tmp get cleaned up; mining them would persist arbitrary uploaded content into the palace and make session deletes leakier.

**How to apply:** When discussing search coverage gaps, describe chat-attachment-content as out of scope. Project chats are different — there `ingested/` attachments DO get mined (the user actively chose to add them to the project), and project input folders are similarly intentional persistent content. The line is "did the user add this to a project?" not "is this an attachment?".

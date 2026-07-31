---
name: Chat title approach
description: User prefers manual editable chat titles over LLM-generated summaries; hallucinated summaries were a problem
type: feedback
originSessionId: d2397504-ef78-4132-aec1-32775886b5ad
---
Chat titles should be user-editable in the header, not relying solely on LLM generation. LLM summary generation was hallucinating titles from agent memory context — fixed by removing memory_store injection from _generate_chat_summary. User confirmed the inline-edit approach (click header → text input → Enter/Escape/blur) works well.

**Why:** LLM-generated summaries were including content never said in the conversation, caused by memory context bleeding into the summary prompt.

**How to apply:** For any display text derived from LLM generation, ensure only the relevant source content is passed — never inject memory or other context. User control (manual edit) is preferred over automated naming.

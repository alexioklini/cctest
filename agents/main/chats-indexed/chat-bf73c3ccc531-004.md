---
name: "hallo devstral (part 5/10)"
type: chat_transcript
description: ""Greeting exchange, no task specified.""
session_id: bf73c3ccc531
agent: main
chunk_index: 4
total_chunks: 10
created_at: "2026-04-06T11:56:11.458474"
last_recalled: 2026-04-09
related:
  - file: chats-indexed/chat-bf73c3ccc531-005.md
    type: same_topic
  - file: chats-indexed/chat-bf73c3ccc531-002.md
    type: depends_on
  - file: chats-indexed/chat-bf73c3ccc531-000.md
    type: depends_on
  - file: chats-indexed/chat-41cb5b9401b5-004.md
    type: same_topic
  - file: chats-indexed/chat-bf73c3ccc531-006.md
    type: extends
  - file: chats-indexed/chat-a136c2c10894-003.md
    type: same_topic
  - file: chats-indexed/chat-bf73c3ccc531-001.md
    type: same_topic
  - file: chats-indexed/chat-b0c40a677efb-000.md
    type: depends_on
  - file: chats-indexed/chat-024258860a16-001.md
    type: same_topic
---

### Geschwindigkeit und Leistung

ion, Sicherheit, Compliance.
- **Geschwindigkeit**: Schnell, optimiert für Echtzeit-Moderation.
- **Unterstützte APIs**: Mistral API, Moderations-Tools.
- **Besonderheiten**: Anpassbare Moderation in mehreren Sprachen.

### API-Unterstützung
- **Mistral API**: Vollständige Unterstützung für alle Modelle, inklusive Function Calling, JSON-Modus und Tool-Integration.
- **OpenAI-Kompatibilität**: Einfache Migration von OpenAI zu Mistral.
- **Cloud-Anbieter**: Verfügbar auf AWS Bedrock, Google Cloud Model Garden, Azure AI Studio, IBM, Snowflake, NVIDIA, Outscale.

### Geschwindigkeit und Leistung
- **Schnellste Modelle**: Ministral 3 3B (279 Token/Sekunde), Devstral Small (255 Token/Sekunde).
- **Langsamste Modelle**: Mistral Large 3 (hohe Leistung, aber langsamer).
- **Latenz**: Ministral 3 3B bietet die niedrigste Latenz (0.41s Time-to-First-Token).

Diese Modelle decken ein breites Spektrum an Anwendungsfällen ab, von einfachen Chatbots bis hin zu komplexen multimodalen Analysen. Mistral AI bietet sowohl Cloud-basierte als auch selbstgehostete Lösungen, was Flexibilität und Datenschutz gewährleistet.

**user**: bitte auch genaue Analyse von allen derzeit verfügbaren devstral modellen

**tool**: {
 "query": "Devstral Modelle \u00dcbersicht 2026 Anwendungsgebiete Geschwindigkeit APIs Benchmarks",
 "results": [
  {
   "title": "Mistral AI Models 2026: Magistral, Devstral 2 & Full Lineup Explained",
   "link": "https://serenitiesai.com/articles/mistral-magistral-devstral-2-review-2026",
   "snippet": "Devstral: Mistral's Coding Agent Family - Devstral vs.

SWE-Bench: Crushing the Competition\n- The Full Mistral Model Ecosystem in 2026\n- Pricing and API Access   ...

- Is Devstral 2 better than Claude Code or Codex CLI for coding?\n- Can I run Mistral models locally for free?

...

From the Magistral reasoning family (now at version 1.2) that challenges OpenAI's o3 and o4-mini, to Devstral 2, a   ...

## Magistral 1.2 Benchmarks: How Does It Stack Up?

When Magistral first launched, the Medium variant scored 73.6% on AIME 2024 and an impressive 90% with majority voting   ...

| Model | Parameters | AIME 2024 | License | Status |\n| --- | --- | --- | --- | --- |   ...

| Magistral Small 1.2 | 24B | 70.7%+ (improving) | Apache 2.0 | Current recommended |   ...

On raw AIME math benchmarks, OpenAI's o3 and o4-mini still lead.

But here's what the benchmarks don't tell you: Magistral's 10x faster token throughput through Le Chat's Flash Answers mode means you get practical reasoning at dramatically higher speeds.

For real-world business applications where speed matters as much as accuracy, that's a   ...

## Devstral: Mistral's Coding Agent Family If Magistral is Mistral's brain, Devstral is its hands.

This family of agentic coding models is purpose-built to solve   ...

The Devstral lineup now includes: - Devstral Small \u2014 The original open-source release (Apache 2.0), light enough to run on a single RTX 4090 or a Mac   ...

- Devstral Medium \u2014 A more powerful mid-tier option for enterprise coding workflows.

- Devstral 2 \u2014 The latest flagship, representing the \"larger agentic coding model\" Mistral promised after the initial Devstral release.

### Devstral vs.

SWE-Bench: Crushing the Competition The original Devstral Small achieved 46.8% on SWE-Bench Verified \u2014 a dataset of 500 real-world GitHub issues manually screened for correctness.

That score outperformed all prior open-source models by over 6 percentage points and surpassed GPT-4.1-mini by more than 20%.

| Model | SWE-Bench Verified | Open Source | Local Deployment |\n| --- | --- | --- | --- | | Devstral Small | 46.8% | \u2705 Apache 2.0 | \u2705 RTX 4090 / 32GB Mac | | Devstral 2 | Improved (flagship) | TBD | Enterprise + API | | DeepSeek-V3-0324 | <46.8% (same scaffold) | \u2705 | Requires 671B params |\n| GPT-4.1-mini | ~26% (estimated) | \u274c | \u274c | What makes Devstral special isn't just benchmarks \u2014 it's the practical deployment story.

Built in collaboration with All Hands AI, Devstral runs on coding agent scaffolds like OpenHands and SWE-Agent, giving it a real interface between the model and your codebase.

Compare that to tools like Codex CLI or Claude Code, which require cloud connectivity and subscription fees \u2014 Devstral Small runs entirely locally with zero API costs.

## The Full Mistral Model Ecosystem in 2026 Mistral's 2026 lineup is remarkably comprehensive.

Beyond Magistral and Devstral, here's the complete picture: | Family | Purpose | Models | Open Source?

|\n| --- | --- | --- | --- | | Magistral | Reasoning / thinking | Medium 1.2, Small 1.2 | Small: Apache 2.0 | | Devstral | Agentic coding | Small, Medium, Devstral 2 | Small: Apache 2.0 | | Mistral Large 3 | General flagship | Mistral Large 3 | \u2705 |   ...

| Ministral 3 | Edge / on-device | 3B, 8B, 14B | \u2705 |\n| Codestral | Code completion | Codestral (Jan 2025) | Limited |   ...

Mistral's pricing remains one of its strongest selling points.

While OpenAI charges premium rates for o3 access and   ...

| Model | Input (per M tokens) | Output (per M tokens) | Notes |\n| --- | --- | --- | --- | | Devstral Small | $0.10 | $0.30 | Same as Mistral Small 3.1 |   ...

- Complex reasoning tasks (enterprise) \u2192 Magistral Medium 1.2   ...

- Agentic coding / bug fixing \u2192 Devstral 2 or Devstral Small\n- General-purpose AI \u2192 Mistral Large 3 or Mistral Medium 3   ...

Mistral's 2026 model ecosystem is impressively cohesive.

With Magistral 1.2 handling reasoning, Devstral 2 tackling   ...

Magistral 1.2 is Mistral AI's"
  },
  {
   "title": "Devstral 2: Mistral\u2019s Dedicated Developer Model 2026: Devstral 2",
   "link": "https://yemhub.com/ai-calculator/devstral-2-mistral-developer-model/",
   "snippet": "Devstral 2: Mistral\u2019s Dedicated Developer Model 2026: Devstral 2 \n\n \n\n\ud83e\uddee Use Our AI Calculator # Devstral 2: Mistral\u2019s Dedicated Developer Model 2026\n\nComplete Analysis: 2,000,000 tokens for Devstral 2 Complete analysis of pricing, performance, and use cases for Mistral AI's Devstral 2 model .

...

Model Select Model Mistral Large 3Mistral Small 3Voxtral RealtimeVoxtral AsyncVoxtral Mini Transcribe v2Mistral VibeMistral OCR 3Devstral 2Devstral Small 2Ministral 3MagistralNemotron 3 Super   ...

Select a Model GPT-5.2 Pro GPT-5.3 Codex Spark GPT-5.3 Instant \ud83d\udfe2 GPT-5.4 \ud83d\udfe2 GPT-5.4 Thinking GPT-5.4 Pro o3 Pro o3 Deep   ...

70B Grok 4.1 Grok 4.1 Fast Grok 4 Deep Reason Grok 5 Grok 4.20 Beta Mistral Large 3 Mistral Small 3 Voxtral Realtime Voxtral Async Voxtral Mini Transcribe v2 Mistral Vibe Mistral OCR 3 Devstral 2 Devstral Small 2 Ministral 3 Magistral   ...

##### Devstral 2\n\nMistral AIMax Context: 256,000 tokens\u26a0\ufe0f SURCHARGE ACTIVE\n\n$0.4 / $2 per 1M tokens\n\nInput Tokens   ...

\ud83d\udcb0 Cost\u26a1 Speed\ud83d\udcca Compare\u2139\ufe0f Info\n\n#### Calculate Token Costs\n\n$2.880000 Total Cost\n\n$0.480000 Input Cost   ...

$0.000000 Code Execution\n\n2,000,000Total Tokens\n\n$0.001440Cost per 1K\n\n694,444Tokens per $ ##### \ud83d\udcca Advanced Cost Breakdown\n\n#### Processing Speed\n\n79m 15s Processing Time\n\n450 Tokens/Second 180ms Time to First Token\n\n421 Effective Speed\n\n#### Model Comparison Select a model to see comparisons with competitors.\n\n#### Model Information Select a model to see detailed information.\n\n\ud83d\udd27 Hide Advanced Options\u25b2\n\n#### \ud83d\udd04 Advanced Options\n\n##### \u26a1 Optimization API Mode\n\nUse Tools\n\nTool Session FeeFlat fee per session (e.g., $0.03 for Code Interpreter)   ...

Powered by YemHub AI Calculator v10.5.0\n\nDevstral 2 \u2728 Market Recommendations\n\n## Devstral 2 Mistral AI   ...

\ud83d\udc41\ufe0f\n\nVision/Images\n\n\u2717 Not Available\n\n\ud83c\udfa7\n\nAudio Processing\n\n\u2717 Not Available\n\n\ud83c\udfa5\n\nVideo Analysis\n\n\u2717 Not Available\n\n\ud83d\udd27   ...
---
name: "hallo devstral (part 7/10)"
type: chat_transcript
description: ""Greeting exchange, no task specified.""
session_id: bf73c3ccc531
agent: main
chunk_index: 6
total_chunks: 10
created_at: "2026-04-06T11:56:11.458474"
last_recalled: 2026-04-09
related:
  - file: chats-indexed/chat-bf73c3ccc531-003.md
    type: extends
  - file: chats-indexed/chat-bf73c3ccc531-008.md
    type: depends_on
  - file: chats-indexed/chat-bf73c3ccc531-004.md
    type: depends_on
---

### **2. Devstral Small 2 (24B)**

n\n## What Devstral 2 Is Optimized For Devstral 2 is trained with a very clear goal: end-to-end software engineering, not just code completion.

That means it focuses on: - Multi-file reasoning instead of single-function snippets   ...

- Long-horizon tasks like refactors, bug fixing, and feature additions   ...

This shows up most clearly in how it performs on SWE-bench Verified, where Devstral 2 scores around 72 percent.

That number matters because SWE-bench is not autocomplete-friendly.

You need to understand the bug, navigate the repo,   ...

Most models collapse somewhere in that chain.

Devstral 2 holds.\n\n## Model Sizes and Practical Use ### Devstral 2 (123B) This is the full-strength model meant for cloud or serious on-prem setups.

It competes directly with Claude Sonnet and GPT-class code models, but with a much lower inference cost per task when used efficiently.

Mistral claims up to 7x better cost-efficiency in real-world agent workloads, which tracks if you look at how often Devstral avoids unnecessary   ...

### Devstral Small 2 (24B)\n\nThis is the sleeper hit.

A 24B model that can: - Run locally on high-end consumer GPUs\n- Power internal code review bots   ...

The biggest difference between Devstral 2 and most \u201ccode LLMs\u201d is that it behaves like an agent by default.

...

Devstral 2 is released under a modified MIT license.Devstral Small 2 uses Apache 2.0.

...

- Run it on-prem\n- Modify it\n- Fine-tune it\n- Embed it into commercial products Devstral 2 is also currently free to use via Mistral\u2019s API, which makes benchmarking and prototyping easy before   ...

## How It Compares to Other Code Models\n\nAgainst closed models like Claude Sonnet - Devstral 2 is slightly weaker at stylistic polish\n- Stronger at structured reasoning and persistence   ...

Against open models in the 30B\u201370B range: - Devstral 2 is clearly ahead on repo-scale reasoning\n- Tool use is more reliable\n- Agent loops are more stable   ...

## Who Should Actually Use Devstral 2\n\nThis model is not for casual prompt-and-paste coding.

Devstral 2 makes sense if: - You are building autonomous or semi-autonomous coding agents\n- You need consistent repo-level changes   ...

Devstral 2 is one of the clearest signals that open-source coding models are catching up in the areas that matter most:   ...

It\u2019s not flashy.

It doesn\u2019t try to impress with clever prose.

It just does the work, step by step, without losing the   ...

Programming\n\nCoding\n\n## Published in Data Science in Your Pocket\n\n8.7K followers Last published 5 days ago\n\nYouTube : [https://www"
  },
  {
   "title": "Mistral Devstral 2: Open-Source Coding-AI mit 72.2% SWE-bench Performance | AI-Automation-Engineers.de",
   "link": "https://ai-automation-engineers.de/blog/2026-01-10-mistral-devstral-2-open-source-coding-ai-mit-72-2-swe-bench-performance/",
   "snippet": "Mistral Devstral 2: Open-Source Coding-AI mit 72.2% SWE-bench Performance | AI-Automation-Engineers.de\n\nNews   ...

Mistral launcht Devstral 2 (123B/24B) mit 256K Kontext und Vibe CLI f\u00fcr autonome Terminal-Automatisierung - 7x   ...

TL;DR: Mistral ver\u00f6ffentlicht Devstral 2 (123B/24B Parameter) mit beeindruckenden 72.2% SWE-bench Performance und das   ...

Automatisierungs-Engineers und DevOps-Teams ver\u00f6ffentlicht: Die neue Devstral 2 Modellfamilie mit zwei Varianten (123B und 24B Parameter) erreicht Spitzenwerte in der Code-Generierung und kommt zusammen mit Vibe CLI - einem Open-Source   ...

- \ud83d\udcb0 ROI: Bis zu 7x kosteneffizienter als Claude 4.5 Sonnet bei Real-World-Tasks (wobei Claude 4.5 Sonnet weiterhin   ...

### Technische Details\n\n#### Model-Spezifikationen im Detail\n\nDevstral 2 (123B) - Lizenz: Vollst\u00e4ndig Apache 2.0 - keine Einschr\u00e4nkungen - Hardware: Kann auf Consumer-Hardware lokal betrieben werden (exakte Hardware-Anforderungen variieren je nach Setup) - SWE-bench Score: 68% (immer noch State-of-the-Art f\u00fcr diese Gr\u00f6\u00dfe) - Lizenz: Modified MIT (Details und kommerzielle Einschr\u00e4nkungen siehe offizielle Lizenz-Dokumentation) Devstral Small 2 (24B) - Hardware-Requirements: Hochleistungs-GPU-Infrastruktur erforderlich (exakte Spezifikationen nicht offiziell dokumentiert) - Kontext-L\u00e4nge: Bis zu 256K Tokens (ideal f\u00fcr gro\u00dfe Monorepos) - SWE-bench Verified Score: 72.2% (h\u00f6her als viele Closed-Source Alternativen)   ...

Die Mistral API l\u00e4sst sich direkt in bestehende Automation-Workflows einbinden:   ...

| Deployment-Option | Use-Case | Kosten | Performance |\n| --- | --- | --- | --- |   ...

| Devstral 2 Self-Host | Enterprise, Compliance | GPU-Infrastruktur (~$15k/Monat) | Maximale Performance, volle   ...

## Performance-Vergleich mit der Konkurrenz\n\n### Devstral 2 vs.

etablierte Coding-AIs | Feature | Devstral 2 + Vibe | GitHub Copilot | Cursor | Claude Sonnet |\n| --- | --- | --- | --- | --- | | Multi-File Changes | \u2705 Exzellent | \u274c Limited | \u2705 Gut | \u2705 Gut | | Terminal Automation | \u2705 Native | \u274c Nein | \u26a0\ufe0f Teilweise | \u274c Nein |\n| Self-Hosting | \u2705 M\u00f6glich | \u274c | \u274c | \u274c |   ...

| Mistral positioniert Devstral 2 als bis zu 7x kosteneffizienter als Claude 4.5 Sonnet bei Real-World-Tasks - ein   ...

Mistral macht mit Devstral 2 und Vibe CLI einen gewaltigen Schritt in Richtung demokratisierter AI-Automatisierung.

W\u00e4hrend Closed-Source-Anbieter ihre Modelle hinter APIs verschlie\u00dfen, gibt Mistral Entwicklern die volle Kontrolle -   ...

- \ud83d\udcca SWE-bench Leaderboard\n- \ud83d\udd27 Mistral API Dokumentation   ...

1.

API-Preise: Output-Token-Preis hinzugef\u00fcgt ($2.00/1M)\n2.

GitHub Link: Nicht-existierenden Link entfernt   ...

### Verifizierte Fakten: - \u2705 Release: 9.

Dezember 2025 - best\u00e4tigt\n- \u2705 7x Kosteneffizienz vs Claude 4.5 Sonnet - offiziell dokumentiert - \u2705 Lizenzierung: Modified MIT (123B), Apache 2.0 (24B) - korrekt\n- \u2705 Context Length: 256K Tokens - verifiziert - \u2705 SWE-bench Score: 68.0% (Devstral Small 2 24B) - offiziell best\u00e4tigt - \u2705 SWE-bench Verified Score: 72.2% (Devstral 2 123B) - offiziell best\u00e4tigt   ...

Confidence: HIGH Anzahl Korrekturen: 8 Major Changes - SWE-bench Leaderboard: [https://www.swe"
  }
 ],
 "result_count": 5
}

**assistant**: Hier ist eine detaillierte Analyse der derzeit verfügbaren **Devstral**-Modelle von Mistral AI (Stand 2026):

---

### **1. Devstral 2 (123B)**
- **Anwendungsgebiete**:
  - **Agentische Softwareentwicklung**: Multi-File-Reasoning, Refactoring, Bugfixing, Feature-Additions.
  - **Autonome Coding-Agenten**: Integration in Workflows wie OpenHands oder SWE-Agent.
  - **Unternehmenslösungen**: GDPR-konforme, sichere Entwicklungsumgebungen.
- **Geschwindigkeit & Leistung**:
  - **Output-Geschwindigkeit**: ~79,9 Token/Sekunde (via Mistral API).
  - **Latenz**: 180 ms (Time-to-First-Token).
  - **Benchmark**: 72,2 % auf SWE-Bench Verified (übertrifft viele Closed-Source-Modelle wie GPT-4.1-mini).
  - **Kosteneffizienz**: Bis zu 7x günstiger als Claude 4.5 Sonnet bei realen Aufgaben.
- **API-Unterstützung**:
  - **Mistral API**: Vollständige Integration mit Function Calling, JSON-Modus.
  - **OpenAI-kompatibel**: Einfache Migration.
  - **Self-Hosting**: Möglich (Apache 2.0 Lizenz für Devstral Small 2, Modified MIT für Devstral 2).
- **Hardware-Anforderungen**:
  - **Cloud/On-Prem**: Für ernsthafte Setups (z. B. 2x A100 oder H100 GPUs).
  - **Lokal**: Auf High-End-Consumer-GPUs (z. B. RTX 4090) lauffähig (Devstral Small 2).
- **Besonderheiten**:
  - Optimiert für **langfristige Aufgaben** (z. B. Repository-weite Änderungen).
  - Stabile Tool-Nutzung und Agenten-Schleifen.

---
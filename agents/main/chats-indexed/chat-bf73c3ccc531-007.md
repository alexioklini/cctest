---
name: "hallo devstral (part 8/10)"
type: chat_transcript
description: ""Greeting exchange, no task specified.""
session_id: bf73c3ccc531
agent: main
chunk_index: 7
total_chunks: 10
created_at: "2026-04-06T11:56:11.458474"
last_recalled: 2026-04-09
related:
  - file: chats-indexed/chat-bf73c3ccc531-005.md
    type: same_topic
  - file: chats-indexed/chat-bf73c3ccc531-008.md
    type: depends_on
---

#### **Option 2: vLLM (Hochperformante Inferenz)**

wareentwicklung**: Multi-File-Reasoning, Refactoring, Bugfixing, Feature-Additions.
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

### **2. Devstral Small 2 (24B)**
- **Anwendungsgebiete**:
  - **Lokale Entwicklung**: Ideal für interne Code-Review-Bots oder kleine Projekte.
  - **Kostensensitive Projekte**: Günstigere Alternative zu Devstral 2.
  - **Bildung & Forschung**: Open-Source (Apache 2.0), frei modifizierbar.
- **Geschwindigkeit & Leistung**:
  - **Output-Geschwindigkeit**: ~255 Token/Sekunde (schneller als Devstral 2).
  - **Benchmark**: 68 % auf SWE-Bench (trotz kleinerer Größe beeindruckend).
  - **Kosten**: ~$0.10 (Input) / $0.30 (Output) pro 1M Token.
- **API-Unterstützung**:
  - **Mistral API**: Gleichwertige Features wie Devstral 2.
  - **Self-Hosting**: Einfacher lokal deploybar (z. B. auf RTX 4090 oder M1/M2 Macs).
- **Hardware-Anforderungen**:
  - **Lokal**: Laufbar auf Consumer-Hardware (z. B. 32GB GPU-RAM).
- **Besonderheiten**:
  - **Kompakt, aber leistungsstark**: Übertrifft größere Modelle in bestimmten Nischen (z. B. schnelle Iterationen).
  - **Kostenlos nutzbar**: Via Mistral API für Prototyping.

---

### **3. Devstral Medium**
- **Anwendungsgebiete**:
  - **Unternehmens-Workflows**: Mittelgroße Projekte mit höherer Komplexität als Devstral Small.
  - **Hybride Lösungen**: Kombination aus Cloud und lokaler Verarbeitung.
- **Geschwindigkeit & Leistung**:
  - **Output-Geschwindigkeit**: Zwischen Devstral Small und Devstral 2.
  - **Benchmark**: Keine offiziellen SWE-Bench-Werte, aber besser als Devstral Small.
- **API-Unterstützung**:
  - **Mistral API**: Vollständige Unterstützung.
- **Besonderheiten**:
  - **Balance**: Bietet mehr Leistung als Devstral Small, aber günstiger als Devstral 2.

---

### **Vergleich der Devstral-Modelle**
| Modell          | Parameter | SWE-Bench Score | Output-Geschw. (t/s) | Latenz (ms) | Kosten (Output/1M) | Lizenz          | Self-Hosting |
|-----------------|-----------|------------------|----------------------|-------------|--------------------|-----------------|--------------|
| **Devstral 2**  | 123B      | 72,2 %           | 79,9                 | 180         | ~$2.00             | Modified MIT    | Ja (Enterprise) |
| **Devstral Small 2** | 24B   | 68 %             | 255                  | ~150        | ~$0.30             | Apache 2.0      | Ja (Consumer)   |
| **Devstral Medium** | ~50B* | N/A              | ~150                 | ~200        | ~$1.00             | Proprietär      | Ja (Cloud/On-Prem) |

*Schätzung basierend auf Benchmarks.

---

### **Benchmark-Vergleiche mit Konkurrenz**
- **Devstral 2 vs. Claude Sonnet**:
  - **Stärken**: Bessere strukturierte Reasoning-Fähigkeiten, kosteneffizienter.
  - **Schwächen**: Geringere "stylistische Politur" (z. B. weniger "menschliche" Code-Kommentare).
- **Devstral 2 vs. GitHub Copilot**:
  - **Stärken**: Multi-File-Änderungen, Terminal-Automatisierung (via Vibe CLI).
  - **Schwächen**: Keine native IDE-Integration (noch).
- **Devstral Small 2 vs. Open-Source-Alternativen (z. B. DeepSeek-V3)**:
  - **Stärken**: Höhere SWE-Bench-Performance, bessere Tool-Nutzung.
  - **Schwächen**: Benötigt mehr GPU-RAM als kleinere Modelle (z. B. 7B-Modelle).

---

### **Empfohlene Use Cases**
1. **Devstral 2**:
   - Autonome Coding-Agenten (z. B. Bugfixing in großen Repos).
   - Unternehmenslösungen mit Compliance-Anforderungen (GDPR).
   - Langfristige Projekte mit hohem Reasoning-Bedarf.

2. **Devstral Small 2**:
   - Lokale Entwicklung (z. B. Code-Reviews, kleine Refactorings).
   - Kostensensitive Startups oder Bildungseinrichtungen.
   - Prototyping vor dem Upgrade auf Devstral 2.

3. **Devstral Medium**:
   - Mittelgroße Teams, die eine Balance zwischen Kosten und Leistung suchen.

---

### **API & Tooling**
- **Vibe CLI**: Open-Source-Tool von Mistral für Terminal-Automatisierung (z. B. `vibe run <task>`).
- **Function Calling**: Alle Devstral-Modelle unterstützen strukturierte API-Aufrufe (z. B. Datenbankabfragen).
- **JSON-Modus**: Garantiert valide JSON-Ausgaben für maschinelle Weiterverarbeitung.

---

### **Fazit**
Devstral ist **Mistrals Antwort auf spezialisierte Coding-Modelle** wie GitHub Copilot oder Claude Code. Die Modelle glänzen durch:
- **Agentische Fähigkeiten** (nicht nur Code-Completion).
- **Kosteneffizienz** (besonders Devstral 2 vs. Closed-Source-Alternativen).
- **Flexibilität** (Self-Hosting, Open-Source-Optionen).

Für **Unternehmen** ist Devstral 2 die beste Wahl, während **Einzelentwickler** mit Devstral Small 2 beginnen sollten. Die Integration in bestehende Workflows (z. B. via Mistral API oder Vibe CLI) ist nahtlos möglich.

**user**: welche Möglichkeiten zum lokalen hosten von devstral-small habe ich

**assistant**: Hier sind die Möglichkeiten, **Devstral Small 2** lokal zu hosten, inklusive Hardware-Anforderungen, Software-Stacks und Schritt-für-Schritt-Anleitungen:

---

### **1. Hardware-Anforderungen**
Devstral Small 2 (24B) ist optimiert für **Consumer-Hardware**, aber die Performance hängt stark von der Quantisierung und dem Inferenz-Framework ab.

#### **Minimale Anforderungen**
| Komponente       | Empfohlene Spezifikationen                     | Notizen                                  |
|------------------|-----------------------------------------------|------------------------------------------|
| **GPU**          | NVIDIA RTX 4090 (24GB VRAM) oder besser      | AMD GPUs (z. B. RX 7900 XT) mit ROCm möglich, aber weniger optimiert. |
| **CPU**          | 8+ Kerne (z. B. Ryzen 9, Intel i9)           | Wichtig für Pre/Post-Processing.          |
| **RAM**          | 32GB+                                        | Mehr RAM für größere Kontextfenster (z. B. 256K Token). |
| **Speicher**     | 50GB+ SSD (NVMe empfohlen)                    | Für Modellgewichte und Caches.           |

#### **Alternative Hardware**
- **Apple Silicon (M1/M2/M3)**:
  - Unterstützt via **MLX** (Metal-Beschleunigung).
  - Empfohlen: M2 Ultra oder M3 Max (mind. 64GB unified memory).
- **Cloud-Instanz (falls lokal nicht möglich)**:
  - AWS: `g5.2xlarge` (NVIDIA A10G, 24GB VRAM).
  - Lambda Labs: A100 oder H100 (für bessere Performance).

---

### **2. Software-Stacks zum Hosting**
#### **Option 1: Ollama (Einfachste Lösung)**
- **Vorteile**: Einfache Installation, automatische Modellverwaltung.
- **Nachteile**: Etwas langsamer als native Frameworks.

**Schritte**:
```bash
# 1. Ollama installieren (macOS/Linux)
curl -fsSL https://ollama.com/install.sh | sh

# 2. Devstral Small 2 herunterladen (automatisch)
ollama pull devstral-small-2

# 3. Modell starten (Standard: 4-bit Quantisierung)
ollama run devstral-small-2

# 4. API-Server starten (für externe Anfragen)
ollama serve
```
- **API-Endpunkt**: `http://localhost:11434/api/chat`
- **Quantisierung**: Standardmäßig 4-bit (GGUF-Format).

---
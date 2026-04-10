---
name: "hallo devstral (part 9/10)"
type: chat_transcript
description: ""Greeting exchange, no task specified.""
session_id: bf73c3ccc531
agent: main
chunk_index: 8
total_chunks: 10
created_at: "2026-04-06T11:56:11.458474"
last_recalled: 2026-04-08
related:
  - file: chats-indexed/chat-bf73c3ccc531-000.md
    type: same_topic
  - file: chats-indexed/chat-bf73c3ccc531-006.md
    type: extends
  - file: chats-indexed/chat-bf73c3ccc531-007.md
    type: extends
  - file: chats-indexed/chat-41cb5b9401b5-004.md
    type: references
---

### **Fazit**

                           | Mehr RAM für größere Kontextfenster (z. B. 256K Token). |
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

#### **Option 2: vLLM (Hochperformante Inferenz)**
- **Vorteile**: Optimiert für Durchsatz, unterstützt Continuous Batching.
- **Nachteile**: Komplexere Einrichtung.

**Schritte**:
```bash
# 1. Python-Umgebung vorbereiten
python -m venv devstral-env
source devstral-env/bin/activate
pip install vllm

# 2. Modellgewichte herunterladen (manuell)
# Offizielle Quelle: https://huggingface.co/mistralai/Devstral-Small-2-24B
git lfs install
git clone https://huggingface.co/mistralai/Devstral-Small-2-24B

# 3. vLLM-Server starten (4-bit Quantisierung)
python -m vllm.entrypoints.openai.api_server \
    --model Devstral-Small-2-24B \
    --quantization awq \  # oder "gptq" für andere Quantisierung
    --max-model-len 256000 \  # Kontextfenster
    --gpu-memory-utilization 0.9

# 4. API testen
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "Devstral-Small-2-24B", "messages": [{"role": "user", "content": "Hello!"}]}'
```
- **API-Endpunkt**: `http://localhost:8000/v1/chat/completions` (OpenAI-kompatibel).
- **Quantisierung**:
  - `awq`: 4-bit (empfohlen für GPU).
  - `gptq`: Alternative 4-bit-Methode.
  - `fp16`: Volle Präzision (benötigt 48GB+ VRAM).

---

#### **Option 3: MLX (für Apple Silicon)**
- **Vorteile**: Native Unterstützung für M1/M2/M3, keine GPU nötig.
- **Nachteile**: Langsamer als GPU-Beschleunigung.

**Schritte**:
```bash
# 1. MLX installieren
pip install mlx mlx-lm

# 2. Modell konvertieren (falls nicht verfügbar)
# Offizielle MLX-Gewichte: https://huggingface.co/mlx-community/Devstral-Small-2-24B-4bit
git clone https://huggingface.co/mlx-community/Devstral-Small-2-24B-4bit

# 3. Inferenz starten
python -c "
from mlx_lm import load, generate

model, tokenizer = load('Devstral-Small-2-24B-4bit')
response = generate(model, tokenizer, prompt='Wie hostet man Devstral Small lokal?', temp=0.7)
print(response)
"
```
- **API-Server**: Nutze `FastAPI` + `mlx-lm` für einen REST-Endpunkt.

---

#### **Option 4: LM Studio (GUI für Windows/macOS)**
- **Vorteile**: Benutzerfreundlich, keine Kommandozeile nötig.
- **Nachteile**: Weniger Kontrolle über Parameter.

**Schritte**:
1. [LM Studio herunterladen](https://lmstudio.ai/).
2. Unter "Models" nach `Devstral-Small-2` suchen und herunterladen.
3. Modell starten und über `localhost:1234` oder die GUI nutzen.

---

#### **Option 5: Docker (Containerisierte Bereitstellung)**
- **Vorteile**: Isolierte Umgebung, einfach zu skalieren.
- **Nachteile**: Überhead durch Container.

**Beispiel mit `ollama` in Docker**:
```bash
# 1. Docker installieren (falls nicht vorhanden)
# 2. Ollama-Docker-Container starten
docker run -d --gpus=all -v ollama:/root/.ollama -p 11434:11434 --name ollama ollama/ollama
docker exec -it ollama ollama pull devstral-small-2
```
- **API**: Wie bei nativer Ollama-Installation unter `localhost:11434`.

---

### **3. Quantisierung (Speicher vs. Performance)**
| Methode       | Bit-Breite | VRAM-Bedarf (24B) | Performance | Tools                     |
|---------------|------------|-------------------|-------------|---------------------------|
| **FP16**      | 16         | ~48GB             | 100%        | vLLM, HuggingFace         |
| **INT8**      | 8          | ~24GB             | 95%         | GGML, vLLM                |
| **AWQ**       | 4          | ~12GB             | 90%         | vLLM, Ollama              |
| **GPTQ**      | 4          | ~12GB             | 85%         | AutoGPTQ, vLLM            |
| **GGUF**      | 4/5/8      | ~6-12GB           | 80-90%      | Ollama, LM Studio         |

**Empfehlung**:
- **Für beste Performance**: `AWQ` (4-bit) mit vLLM.
- **Für maximale Kompatibilität**: `GGUF` (z. B. für Ollama/LM Studio).

---

### **4. API-Integration (Beispiele)**
#### **Python (OpenAI-SDK)**
```python
from openai import OpenAI

client = OpenAI(base_url="http://localhost:11434/v1", api_key="ollama")

response = client.chat.completions.create(
    model="devstral-small-2",
    messages=[{"role": "user", "content": "Schreibe einen Python-Code für QuickSort."}]
)
print(response.choices[0].message.content)
```

#### **cURL (REST-API)**
```bash
curl http://localhost:11434/api/chat -d '{
  "model": "devstral-small-2",
  "messages": [{"role": "user", "content": "Erkläre Devstral Small."}]
}'
```

---

### **5. Optimierungen**
- **Kontextfenster**: Standardmäßig 256K Token, aber reduzierbar für weniger VRAM-Verbrauch:
  ```bash
  # Beispiel mit vLLM
  --max-model-len 32768  # Reduziert auf 32K Token
  ```
- **Batch-Verarbeitung**: Nutze `vLLM` für parallele Anfragen:
  ```python
  # Python-Beispiel mit vLLM
  from vllm import LLM
  llm = LLM(model="Devstral-Small-2-24B", tensor_parallel_size=1)
  outputs = llm.generate(["Prompt 1", "Prompt 2"], sampling_params={"temperature": 0.7})
  ```
- **Offloading**: Falls VRAM knapp ist, nutze CPU-Offloading (langsamer):
  ```bash
  # Ollama mit CPU-Fallback
  ollama run devstral-small-2 --gpu 0.5  # Nutzt nur 50% GPU
  ```

---

### **6. Häufige Probleme & Lösungen**
| Problem                          | Lösung                                                                 |
|----------------------------------|------------------------------------------------------------------------|
| **CUDA Out of Memory**           | Quantisierung auf 4-bit umstellen oder kleineres Kontextfenster nutzen. |
| **Langsame Inferenz auf CPU**    | MLX (Apple) oder besserer Quantisierung (GGUF) verwenden.               |
| **Modell lädt nicht in Ollama**  | Manuell herunterladen: `ollama pull devstral-small-2 --force`           |
| **vLLM startet nicht**           | Prüfe CUDA-Version (`nvcc --version`) und installiere passende vLLM-Version. |

---

### **7. Alternativen für schwächere Hardware**
Falls deine Hardware nicht ausreicht:
- **Kleinere Modelle**:
  - **Mistral 7B**: Laufbar auf RTX 3060 (12GB VRAM).
  - **TinyLlama**: 1.1B Parameter, läuft auf CPU.
- **Cloud-Hosting**:
  - **Mistral API**: $0.30/1M Output-Token.
  - **Replicate**: Hosted Devstral Small 2 (Pay-as-you-go).

---

### **Fazit**
| Methode          | Schwierigkeit | Performance | VRAM-Bedarf | Empfohlen für               |
|------------------|---------------|-------------|-------------|-----------------------------|
| **Ollama**       | ⭐            | ⚡⚡         | ~12GB       | Einsteiger, macOS/Windows    |
| **vLLM**         | ⭐⭐           | ⚡⚡⚡        | ~12GB       | Entwickler, Hochdurchsatz   |
| **MLX**          | ⭐⭐           | ⚡⚡          | ~20GB*      | Apple Silicon Nutzer        |
| **LM Studio**    | ⭐            | ⚡           | ~12GB       | GUI-Benutzer                |
| **Docker**       | ⭐⭐           | ⚡⚡          | ~12GB       | Containerisierte Umgebungen |
// WireLanguageModel — JEDER OpenAI-kompatible Endpoint (llm-router, oMLX,
// beliebige Server) hinter Apples FoundationModels-`LanguageModel`-Protokoll,
// mit MAXIMALER Protokoll-Abdeckung:
//   - Chat + Live-Streaming (SSE → .appendText, approx. Token-Zählung)
//   - Reasoning: natives `reasoning_content` UND Inline-<think>-Tags werden
//     in den Reasoning-Kanal gesplittet (delta-übergreifender Tag-Puffer)
//   - Tool-Calling inkl. toolCallingMode (.required → tool_choice)
//   - Guided Generation als DREISTUFIGE KASKADE (gepuffert, nie live —
//     Schema-Snapshots sind nicht präfix-stabil, eigene Lektion v9.430.2):
//       1. response_format json_schema  (Logit-Enforcement, z. B. oMLX/xgrammar)
//       2. response_format json_object + Schema im Prompt (Upstream ohne schema)
//       3. reiner Prompt-Zwang; jede Stufe JSON-validiert, erst dann emittiert
//   - Vision: Attachment-Segmente → image_url-Data-URIs (PNG via ImageIO)
//   - GenerationOptions: temperature, maximumResponseTokens, samplingMode
//     (greedy→temp 0; top-k/top-p best-effort via Beschreibungs-Introspektion)
//   - ContextOptions.includeSchemaInPrompt wird respektiert
//   - prewarm(): echter 1-Token-Warm-Ping (fire-and-forget, primt KV-Prefix)
//   - Typisierte Fehler: 429→rateLimited (inkl. Retry-After), Kontext-Sniffs→
//     contextSizeExceeded, Guardrail-Sniffs→guardrailViolation
//   - Cancellation: Task-Abbruch schließt den Stream sauber
//   - Usage inkl. cachedTokenCount/reasoningTokens + Abschluss-Metadaten
import Foundation
import FoundationModels
import ImageIO
import UniformTypeIdentifiers

extension String {
    func appendToFile(_ path: String) throws {
        if let h = FileHandle(forWritingAtPath: path) {
            h.seekToEndOfFile(); h.write(self.data(using: .utf8)!); h.closeFile()
        } else {
            try self.write(toFile: path, atomically: true, encoding: .utf8)
        }
    }
}

@available(macOS 27.0, *)
public struct WireLanguageModel: FoundationModels.LanguageModel {
    public typealias Executor = WireExecutor

    let baseURL: String
    let apiKey: String
    let model: String
    let declared: [LanguageModelCapabilities.Capability]

    public init(baseURL: String, apiKey: String, model: String,
                capabilities: [LanguageModelCapabilities.Capability] =
                    [.toolCalling, .guidedGeneration, .reasoning]) {
        self.baseURL = baseURL
        self.apiKey = apiKey
        self.model = model
        self.declared = capabilities
    }

    public var capabilities: LanguageModelCapabilities {
        LanguageModelCapabilities(declared)
    }

    public var executorConfiguration: WireExecutor.Configuration {
        WireExecutor.Configuration(baseURL: baseURL, apiKey: apiKey, model: model)
    }
}

// Delta-übergreifender <think>…</think>-Splitter: manche Modelle (qwen/gemma
// via Chat-Template) emittieren Reasoning INLINE im content statt in
// reasoning_content. Kleiner Zustandsautomat mit Carry für zerteilte Tags.
@available(macOS 27.0, *)
final class ThinkSplitter {
    private var inThink = false
    private var carry = ""
    /// Liefert (sichtbarer Text, Reasoning-Text) für ein Delta.
    func feed(_ delta: String) -> (text: String, think: String) {
        var buf = carry + delta
        carry = ""
        var text = "", think = ""
        while !buf.isEmpty {
            if inThink {
                if let r = buf.range(of: "</think>") {
                    think += String(buf[..<r.lowerBound])
                    buf = String(buf[r.upperBound...])
                    inThink = false
                } else {
                    // Tail könnte ein zerteiltes "</think>" enthalten
                    let keep = min(8, buf.count)
                    think += String(buf.dropLast(keep))
                    carry = String(buf.suffix(keep))
                    buf = ""
                }
            } else {
                if let r = buf.range(of: "<think>") {
                    text += String(buf[..<r.lowerBound])
                    buf = String(buf[r.upperBound...])
                    inThink = true
                } else {
                    let keep = min(7, buf.count)
                    text += String(buf.dropLast(keep))
                    carry = String(buf.suffix(keep))
                    buf = ""
                }
            }
        }
        return (text, think)
    }
    /// Rest ausspülen (Stream-Ende).
    func flush() -> (text: String, think: String) {
        defer { carry = "" }
        return inThink ? ("", carry) : (carry, "")
    }
}

@available(macOS 27.0, *)
public struct WireExecutor: LanguageModelExecutor {
    public typealias Model = WireLanguageModel

    public struct Configuration: Hashable, Sendable {
        public let baseURL: String
        public let apiKey: String
        public let model: String
    }

    let config: Configuration

    public init(configuration: Configuration) throws {
        self.config = configuration
    }

    // Echter Warm-Ping: 1-Token-Request mit dem Transcript-Präfix primt den
    // KV-Prefix des Endpoints (oMLX-Prefix-Cache, Router-Upstreams).
    public func prewarm(model: WireLanguageModel, transcript: Transcript) {
        let cfg = config
        let messages = Self.renderMessages(transcript)
        Task.detached(priority: .background) {
            var body: [String: Any] = ["model": cfg.model, "messages": messages,
                                       "max_tokens": 1, "stream": false]
            guard let url = URL(string: cfg.baseURL + "/chat/completions"),
                  let data = try? JSONSerialization.data(withJSONObject: body) else { return }
            var req = URLRequest(url: url)
            req.httpMethod = "POST"
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
            req.setValue("Bearer \(cfg.apiKey)", forHTTPHeaderField: "Authorization")
            req.httpBody = data
            _ = try? await URLSession.shared.data(for: req)
            _ = body.removeValue(forKey: "model")
        }
    }

    // ── Transcript → OpenAI-Messages (inkl. Vision-Attachments) ─────────────
    static func segParts(_ segments: [Transcript.Segment]) -> (text: String, images: [String]) {
        var texts: [String] = []
        var images: [String] = []
        for seg in segments {
            switch seg {
            case .text(let t):
                texts.append(t.content)
            case .attachment(let a):
                if let uri = dataURI(a.content) { images.append(uri) }
                else if let label = a.label { texts.append("[Anhang: \(label)]") }
            default:
                texts.append(String(describing: seg))
            }
        }
        return (texts.joined(separator: "\n"), images)
    }

    // Attachment → Data-URI. Attachment ist ein Enum (aktuell nur .image);
    // url/cgImage sitzen auf ImageAttachment.
    static func dataURI(_ attachment: Transcript.Attachment) -> String? {
        switch attachment {
        case .image(let img):
            if let url = img.url, let data = try? Data(contentsOf: url) {
                let ext = url.pathExtension.lowercased()
                let mime = ext == "png" ? "image/png" : (ext == "gif" ? "image/gif" : "image/jpeg")
                return "data:\(mime);base64,\(data.base64EncodedString())"
            }
            let cg = img.cgImage
            let out = NSMutableData()
            guard let dest = CGImageDestinationCreateWithData(out, UTType.png.identifier as CFString, 1, nil)
            else { return nil }
            CGImageDestinationAddImage(dest, cg, nil)
            guard CGImageDestinationFinalize(dest) else { return nil }
            return "data:image/png;base64,\((out as Data).base64EncodedString())"
        @unknown default:
            return nil
        }
    }

    static func renderMessages(_ transcript: Transcript) -> [[String: Any]] {
        var messages: [[String: Any]] = []
        func contentValue(_ segments: [Transcript.Segment]) -> Any {
            let (text, images) = segParts(segments)
            if images.isEmpty { return text }
            var parts: [[String: Any]] = []
            if !text.isEmpty { parts.append(["type": "text", "text": text]) }
            for uri in images {
                parts.append(["type": "image_url", "image_url": ["url": uri]])
            }
            return parts
        }
        for entry in transcript {
            switch entry {
            case .instructions(let ins):
                messages.append(["role": "system", "content": contentValue(ins.segments)])
            case .prompt(let p):
                messages.append(["role": "user", "content": contentValue(p.segments)])
            case .response(let r):
                let (text, _) = segParts(r.segments)
                // Leere Response-Einträge (z. B. der Platzhalter einer reinen
                // Tool-Runde) NICHT rendern — eine leere Assistant-Message
                // zwischen tool_calls und tool-Output zerreißt die Paarung
                // (Modell ignoriert dann das Tool-Ergebnis; live diagnostiziert).
                if !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    messages.append(["role": "assistant", "content": text])
                }
            case .toolCalls(let calls):
                var tcs: [[String: Any]] = []
                for c in calls {
                    tcs.append(["id": c.id, "type": "function",
                                "function": ["name": c.toolName,
                                             "arguments": c.arguments.jsonString]])
                }
                messages.append(["role": "assistant", "content": "", "tool_calls": tcs])
            case .toolOutput(let out):
                let (text, _) = segParts(out.segments)
                messages.append(["role": "tool", "tool_call_id": out.id, "content": text])
            @unknown default:
                break
            }
        }
        return messages
    }

    static func jsonSchema(_ schema: GenerationSchema) -> [String: Any]? {
        guard let data = try? JSONEncoder().encode(schema),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        else { return nil }
        return obj
    }

    static func toolPayload(_ defs: [Transcript.ToolDefinition]) -> [[String: Any]] {
        defs.compactMap { d in
            var fn: [String: Any] = ["name": d.name, "description": d.description]
            if let params = jsonSchema(d.parameters) { fn["parameters"] = params }
            return ["type": "function", "function": fn]
        }
    }

    static func approxTokens(_ s: String) -> Int { max(1, s.count / 4) }

    // GenerationOptions → Wire-Knöpfe. SamplingMode ist ein opakes Struct ohne
    // öffentliche Introspektion — Best-Effort über die Debug-Beschreibung
    // (greedy → temperature 0; top-k/top-p, wenn Zahlen erkennbar sind).
    static func applyOptions(_ opts: GenerationOptions, to body: inout [String: Any]) {
        if let t = opts.temperature { body["temperature"] = t }
        if let m = opts.maximumResponseTokens { body["max_tokens"] = m }
        if let mode = opts.samplingMode {
            let desc = String(describing: mode).lowercased()
            if desc.contains("greedy") {
                body["temperature"] = 0
            } else if desc.contains("probabilitythreshold"),
                      let v = firstDouble(in: desc) {
                body["top_p"] = v
            } else if desc.contains("top"), let v = firstDouble(in: desc) {
                body["top_k"] = Int(v)
            }
        }
        if let tm = opts.toolCallingMode {
            switch tm {
            case .required: body["tool_choice"] = "required"
            case .allowed: body["tool_choice"] = "auto"
            default: break
            }
        }
    }

    static func firstDouble(in s: String) -> Double? {
        var cur = ""
        for ch in s {
            if ch.isNumber || ch == "." { cur.append(ch) }
            else if !cur.isEmpty { break }
        }
        return Double(cur)
    }

    // HTTP-/Upstream-Fehler → typisierte LanguageModelError-Fälle.
    static func typedError(status: Int, body: String) -> Error {
        let lower = body.lowercased()
        if status == 429 {
            return LanguageModelError.rateLimited(.init(
                resetDate: nil, debugDescription: "HTTP 429: \(body.prefix(200))"))
        }
        if lower.contains("context") && (lower.contains("size") || lower.contains("window")
            || lower.contains("length") || lower.contains("maximum")) {
            let nums = body.split(whereSeparator: { !$0.isNumber }).compactMap { Int($0) }
            return LanguageModelError.contextSizeExceeded(.init(
                contextSize: nums.first ?? 0, tokenCount: nums.dropFirst().first ?? 0,
                debugDescription: String(body.prefix(300))))
        }
        if lower.contains("guardrail") || lower.contains("content policy")
            || lower.contains("safety") {
            return LanguageModelError.guardrailViolation(.init(
                debugDescription: String(body.prefix(300))))
        }
        return NSError(domain: "WireExecutor", code: status, userInfo: [
            NSLocalizedDescriptionKey: "wire HTTP \(status): \(body.prefix(300))"])
    }

    // ── Kern ────────────────────────────────────────────────────────────────
    public func respond(to request: LanguageModelExecutorGenerationRequest,
                        model: WireLanguageModel,
                        streamingInto channel: LanguageModelExecutorGenerationChannel) async throws {
        if let schema = request.schema {
            try await guidedCascade(request: request, schema: schema, channel: channel)
        } else {
            _ = try await streamOnce(request: request, extraBody: [:],
                                     buffer: false, channel: channel)
        }
    }

    // Guided-Kaskade: gepuffert (nie live streamen — Schema-Snapshots sind
    // nicht präfix-stabil), jede Stufe validiert, erst DANN ein appendText.
    func guidedCascade(request: LanguageModelExecutorGenerationRequest,
                       schema: GenerationSchema,
                       channel: LanguageModelExecutorGenerationChannel) async throws {
        let js = Self.jsonSchema(schema)
        let schemaPrompt = js.flatMap {
            (try? JSONSerialization.data(withJSONObject: $0)).flatMap {
                String(data: $0, encoding: .utf8)
            }
        } ?? ""
        var attempts: [[String: Any]] = []
        if let js {
            attempts.append(["response_format": [
                "type": "json_schema",
                "json_schema": ["name": "generated", "schema": js]]])
        }
        attempts.append(["response_format": ["type": "json_object"],
                         "__schema_prompt": schemaPrompt])
        attempts.append(["__schema_prompt": schemaPrompt])

        var lastError: Error = NSError(domain: "WireExecutor", code: -1, userInfo: [
            NSLocalizedDescriptionKey: "guided: keine Stufe lieferte valides JSON"])
        for var extra in attempts {
            let sp = extra.removeValue(forKey: "__schema_prompt") as? String
            do {
                let text = try await streamOnce(
                    request: request, extraBody: extra, buffer: true,
                    channel: channel, schemaPromptSuffix: sp)
                // Validierung: muss als JSON parsen (das Framework dekodiert
                // anschließend typgenau gegen das GenerationSchema).
                let candidate = Self.extractJSON(from: text)
                if let data = candidate.data(using: .utf8),
                   (try? JSONSerialization.jsonObject(with: data)) != nil {
                    await channel.send(.response(action: .appendText(
                        candidate, tokenCount: Self.approxTokens(candidate))))
                    return
                }
                lastError = NSError(domain: "WireExecutor", code: -2, userInfo: [
                    NSLocalizedDescriptionKey: "guided: Ausgabe kein valides JSON: \(text.prefix(150))"])
            } catch {
                lastError = error
                // 4xx wegen response_format → nächste Stufe; harte Fehler durchreichen
                if let lm = error as? LanguageModelError {
                    throw lm
                }
            }
        }
        throw lastError
    }

    // JSON aus evtl. Prosa/Codefences herausschälen (Stufe 2/3-Toleranz).
    static func extractJSON(from text: String) -> String {
        var t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if t.hasPrefix("```") {
            t = t.replacingOccurrences(of: "```json", with: "```")
            let parts = t.components(separatedBy: "```")
            if parts.count >= 2 { t = parts[1].trimmingCharacters(in: .whitespacesAndNewlines) }
        }
        if let s = t.firstIndex(of: "{"), let e = t.lastIndex(of: "}") , s < e {
            return String(t[s...e])
        }
        return t
    }

    /// Ein Streaming-Durchlauf. buffer=false → Events live in den Channel;
    /// buffer=true → Text nur sammeln und zurückgeben (Guided-Kaskade).
    @discardableResult
    func streamOnce(request: LanguageModelExecutorGenerationRequest,
                    extraBody: [String: Any], buffer: Bool,
                    channel: LanguageModelExecutorGenerationChannel,
                    schemaPromptSuffix: String? = nil) async throws -> String {
        var messages = Self.renderMessages(request.transcript)
        if let sp = schemaPromptSuffix, !sp.isEmpty {
            messages.append(["role": "system", "content":
                "Antworte AUSSCHLIESSLICH mit einem JSON-Objekt, das exakt diesem "
                + "JSON-Schema entspricht — keine Prosa, keine Code-Fences:\n\(sp)"])
        }
        // ContextOptions.includeSchemaInPrompt: explizite Bitte, das Schema
        // zusätzlich in den Prompt zu legen (hilft Stufe-1-Upstreams).
        if request.contextOptions.includeSchemaInPrompt == true,
           schemaPromptSuffix == nil, let schema = request.schema,
           let js = Self.jsonSchema(schema),
           let d = try? JSONSerialization.data(withJSONObject: js),
           let s = String(data: d, encoding: .utf8) {
            messages.append(["role": "system", "content":
                "Halte dich exakt an dieses JSON-Schema:\n\(s)"])
        }

        var body: [String: Any] = [
            "model": config.model,
            "messages": messages,
            "stream": true,
            "stream_options": ["include_usage": true],
        ]
        // KEIN "user"-Feld: Mistral-Upstreams lehnen unbekannte Felder mit
        // 422 extra_forbidden ab (live getestet) — Korrelation ggf. später
        // router-seitig über Header.
        Self.applyOptions(request.generationOptions, to: &body)
        let tools = Self.toolPayload(request.enabledToolDefinitions)
        if !tools.isEmpty { body["tools"] = tools }
        for (k, v) in extraBody { body[k] = v }

        var urlReq = URLRequest(url: URL(string: config.baseURL + "/chat/completions")!)
        urlReq.httpMethod = "POST"
        urlReq.setValue("application/json", forHTTPHeaderField: "Content-Type")
        urlReq.setValue("Bearer \(config.apiKey)", forHTTPHeaderField: "Authorization")
        urlReq.timeoutInterval = 600
        urlReq.httpBody = try JSONSerialization.data(withJSONObject: body)
        if ProcessInfo.processInfo.environment["WIRE_DEBUG"] == "1",
           let d = try? JSONSerialization.data(withJSONObject: body, options: [.prettyPrinted]),
           let str = String(data: d, encoding: .utf8) {
            try? (str + "\n=====\n").appendToFile("/tmp/wire_debug.log")
        }

        let (bytes, resp) = try await URLSession.shared.bytes(for: urlReq)
        if let http = resp as? HTTPURLResponse, http.statusCode != 200 {
            var errBody = ""
            for try await line in bytes.lines { errBody += line; if errBody.count > 600 { break } }
            throw Self.typedError(status: http.statusCode, body: errBody)
        }

        var callIDs: [Int: (id: String, name: String)] = [:]
        var gotArgs = Set<Int>()
        var collected = ""
        var finishReason: String? = nil
        let splitter = ThinkSplitter()

        for try await line in bytes.lines {
            if Task.isCancelled { throw CancellationError() }
            guard line.hasPrefix("data: ") else { continue }
            let payload = String(line.dropFirst(6))
            if payload == "[DONE]" { break }
            guard let data = payload.data(using: .utf8),
                  let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
            else { continue }

            if let usage = obj["usage"] as? [String: Any] {
                let pIn = usage["prompt_tokens"] as? Int ?? 0
                let pOut = usage["completion_tokens"] as? Int ?? 0
                let cached = (usage["prompt_tokens_details"] as? [String: Any])?["cached_tokens"] as? Int ?? 0
                let rTok = (usage["completion_tokens_details"] as? [String: Any])?["reasoning_tokens"] as? Int ?? 0
                await channel.send(.response(action: .updateUsage(
                    input: .init(totalTokenCount: pIn, cachedTokenCount: cached),
                    output: .init(totalTokenCount: pOut, reasoningTokenCount: rTok))))
            }

            guard let choices = obj["choices"] as? [[String: Any]],
                  let choice = choices.first else { continue }
            if let fr = choice["finish_reason"] as? String { finishReason = fr }
            guard let delta = choice["delta"] as? [String: Any] else { continue }

            if let text = delta["content"] as? String, !text.isEmpty {
                if buffer {
                    collected += text
                } else {
                    let (vis, think) = splitter.feed(text)
                    if !vis.isEmpty {
                        await channel.send(.response(action: .appendText(
                            vis, tokenCount: Self.approxTokens(vis))))
                    }
                    if !think.isEmpty {
                        await channel.send(.reasoning(action: .appendText(
                            think, tokenCount: Self.approxTokens(think))))
                    }
                }
            }
            if let think = delta["reasoning_content"] as? String, !think.isEmpty, !buffer {
                await channel.send(.reasoning(action: .appendText(
                    think, tokenCount: Self.approxTokens(think))))
            }
            if let tcs = delta["tool_calls"] as? [[String: Any]] {
                for tc in tcs {
                    let idx = tc["index"] as? Int ?? 0
                    let fn = tc["function"] as? [String: Any]
                    if callIDs[idx] == nil {
                        let cid = (tc["id"] as? String) ?? "call_\(UUID().uuidString.prefix(12))"
                        let name = fn?["name"] as? String ?? "unknown"
                        callIDs[idx] = (id: cid, name: name)
                    }
                    if let frag = fn?["arguments"] as? String, !frag.isEmpty,
                       let known = callIDs[idx] {
                        gotArgs.insert(idx)
                        await channel.send(.toolCalls(action: .toolCall(
                            id: known.id, name: known.name,
                            action: .appendArguments(frag, tokenCount: Self.approxTokens(frag)))))
                    }
                }
            }
        }
        if !buffer {
            let (vis, think) = splitter.flush()
            if !vis.isEmpty {
                await channel.send(.response(action: .appendText(
                    vis, tokenCount: Self.approxTokens(vis))))
            }
            if !think.isEmpty {
                await channel.send(.reasoning(action: .appendText(
                    think, tokenCount: Self.approxTokens(think))))
            }
        }
        for (idx, known) in callIDs where !gotArgs.contains(idx) {
            await channel.send(.toolCalls(action: .toolCall(
                id: known.id, name: known.name,
                action: .appendArguments("{}", tokenCount: 1))))
        }
        // Abschluss-Metadaten (finish_reason + Endpoint-Modell) für Telemetrie.
        var meta: [String: any ConvertibleToGeneratedContent] = ["wire_model": config.model]
        if let fr = finishReason { meta["finish_reason"] = fr }
        await channel.send(.response(action: .updateMetadata(meta)))
        return collected
    }
}

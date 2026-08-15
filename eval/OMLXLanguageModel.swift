// OMLXLanguageModel — oMLX-gehostete Modelle hinter Apples FoundationModels-
// `LanguageModel`-Protokoll: oMLX bleibt der Inferencer (Modelle, Quants,
// KV-Prefix-Cache, SSD-Offload), aber LanguageModelSession/Tools/@Generable/
// SpotlightSearchTool funktionieren unverändert. VOLLE Protokoll-Unterstützung:
//   - Chat + Streaming     (SSE-Deltas → .response(.appendText))
//   - Tool-Calling         (OpenAI tool_calls-Deltas → .toolCalls(.appendArguments))
//   - Reasoning            (reasoning_content → .reasoning(.appendText))
//   - Guided Generation    (request.schema → response_format json_schema —
//                           oMLX erzwingt via xgrammar auf LOGIT-Ebene,
//                           live verifiziert 15.08.2026)
//   - Usage                (prompt/completion + cached_tokens → .updateUsage)
// Referenz-Implementierungen: Apples CoreAILanguageModel (apple/coreai-models)
// und ml-explores MLXLanguageModel (mlx-swift-lm).
import Foundation
import FoundationModels

@available(macOS 27.0, *)
public struct OMLXLanguageModel: FoundationModels.LanguageModel {
    public typealias Executor = OMLXExecutor

    let baseURL: String     // z. B. http://localhost:8000/v1
    let apiKey: String
    let model: String       // oMLX-Modell-ID, z. B. gemma-4-12B-it-qat-oQ4-fp16
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

    public var executorConfiguration: OMLXExecutor.Configuration {
        OMLXExecutor.Configuration(baseURL: baseURL, apiKey: apiKey, model: model)
    }
}

@available(macOS 27.0, *)
public struct OMLXExecutor: LanguageModelExecutor {
    public typealias Model = OMLXLanguageModel

    public struct Configuration: Hashable, Sendable {
        public let baseURL: String
        public let apiKey: String
        public let model: String
    }

    let config: Configuration

    public init(configuration: Configuration) throws {
        self.config = configuration
    }

    // Optionaler Warm-Ping: oMLX primt den KV-Prefix ohnehin bei echter
    // Nutzung; bewusster No-Op (kein Payload-Drift-Risiko).
    public func prewarm(model: OMLXLanguageModel, transcript: Transcript) {}

    // ── Transcript → OpenAI-Messages ────────────────────────────────────────
    static func segText(_ segments: [Transcript.Segment]) -> String {
        var out: [String] = []
        for seg in segments {
            if case .text(let t) = seg { out.append(t.content) }
            else { out.append(String(describing: seg)) }
        }
        return out.joined(separator: "\n")
    }

    static func renderMessages(_ transcript: Transcript) -> [[String: Any]] {
        var messages: [[String: Any]] = []
        for entry in transcript {
            switch entry {
            case .instructions(let ins):
                messages.append(["role": "system", "content": segText(ins.segments)])
            case .prompt(let p):
                messages.append(["role": "user", "content": segText(p.segments)])
            case .response(let r):
                messages.append(["role": "assistant", "content": segText(r.segments)])
            case .toolCalls(let calls):
                var tcs: [[String: Any]] = []
                for c in calls {
                    tcs.append([
                        "id": c.id,
                        "type": "function",
                        "function": ["name": c.toolName,
                                     "arguments": c.arguments.jsonString],
                    ])
                }
                messages.append(["role": "assistant", "content": "", "tool_calls": tcs])
            case .toolOutput(let out):
                messages.append(["role": "tool",
                                 "tool_call_id": out.id,
                                 "content": segText(out.segments)])
            @unknown default:
                break
            }
        }
        return messages
    }

    // GenerationSchema → JSON-Schema-Dict (GenerationSchema ist Codable und
    // serialisiert ALS JSON Schema — fm-agent nutzt dieselbe Äquivalenz in
    // Gegenrichtung seit v9.425).
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

    // ── Der Kern: eine Generierung gegen oMLX, gestreamt in den Channel ─────
    public func respond(to request: LanguageModelExecutorGenerationRequest,
                        model: OMLXLanguageModel,
                        streamingInto channel: LanguageModelExecutorGenerationChannel) async throws {
        var body: [String: Any] = [
            "model": config.model,
            "messages": Self.renderMessages(request.transcript),
            "stream": true,
            "stream_options": ["include_usage": true],
        ]
        if let t = request.generationOptions.temperature { body["temperature"] = t }
        if let m = request.generationOptions.maximumResponseTokens { body["max_tokens"] = m }
        let tools = Self.toolPayload(request.enabledToolDefinitions)
        if !tools.isEmpty { body["tools"] = tools }
        if let schema = request.schema, let js = Self.jsonSchema(schema) {
            body["response_format"] = [
                "type": "json_schema",
                "json_schema": ["name": "generated", "schema": js],
            ]
        }

        var urlReq = URLRequest(url: URL(string: config.baseURL + "/chat/completions")!)
        urlReq.httpMethod = "POST"
        urlReq.setValue("application/json", forHTTPHeaderField: "Content-Type")
        urlReq.setValue("Bearer \(config.apiKey)", forHTTPHeaderField: "Authorization")
        urlReq.timeoutInterval = 600
        urlReq.httpBody = try JSONSerialization.data(withJSONObject: body)

        let (bytes, resp) = try await URLSession.shared.bytes(for: urlReq)
        if let http = resp as? HTTPURLResponse, http.statusCode != 200 {
            var errBody = ""
            for try await line in bytes.lines { errBody += line; if errBody.count > 500 { break } }
            throw NSError(domain: "OMLXExecutor", code: http.statusCode, userInfo: [
                NSLocalizedDescriptionKey: "oMLX HTTP \(http.statusCode): \(errBody.prefix(300))"])
        }

        // Tool-Call-Zustand über die Delta-Fragmente (index → id/name)
        var callIDs: [Int: (id: String, name: String)] = [:]
        var gotArgs = Set<Int>()

        for try await line in bytes.lines {
            guard line.hasPrefix("data: ") else { continue }
            let payload = String(line.dropFirst(6))
            if payload == "[DONE]" { break }
            guard let data = payload.data(using: .utf8),
                  let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
            else { continue }

            // Usage (finaler Chunk via include_usage)
            if let usage = obj["usage"] as? [String: Any] {
                let pIn = usage["prompt_tokens"] as? Int ?? 0
                let pOut = usage["completion_tokens"] as? Int ?? 0
                let details = usage["prompt_tokens_details"] as? [String: Any]
                let cached = details?["cached_tokens"] as? Int ?? 0
                let rDetails = usage["completion_tokens_details"] as? [String: Any]
                let rTok = rDetails?["reasoning_tokens"] as? Int ?? 0
                await channel.send(.response(action: .updateUsage(
                    input: .init(totalTokenCount: pIn, cachedTokenCount: cached),
                    output: .init(totalTokenCount: pOut, reasoningTokenCount: rTok))))
            }

            guard let choices = obj["choices"] as? [[String: Any]],
                  let choice = choices.first,
                  let delta = choice["delta"] as? [String: Any] else { continue }

            if let text = delta["content"] as? String, !text.isEmpty {
                await channel.send(.response(action: .appendText(
                    text, tokenCount: Self.approxTokens(text))))
            }
            if let think = delta["reasoning_content"] as? String, !think.isEmpty {
                await channel.send(.reasoning(action: .appendText(
                    think, tokenCount: Self.approxTokens(think))))
            }
            if let tcs = delta["tool_calls"] as? [[String: Any]] {
                for tc in tcs {
                    let idx = tc["index"] as? Int ?? 0
                    let fn = tc["function"] as? [String: Any]
                    if callIDs[idx] == nil {
                        let cid = (tc["id"] as? String)
                            ?? "call_\(UUID().uuidString.prefix(12))"
                        let name = fn?["name"] as? String ?? "unknown"
                        callIDs[idx] = (id: cid, name: name)
                    }
                    if let frag = fn?["arguments"] as? String, !frag.isEmpty,
                       let known = callIDs[idx] {
                        gotArgs.insert(idx)
                        await channel.send(.toolCalls(action: .toolCall(
                            id: known.id, name: known.name,
                            action: .appendArguments(frag,
                                tokenCount: Self.approxTokens(frag)))))
                    }
                }
            }
        }
        // Parameterlose Tool-Aufrufe (kein einziges Argument-Fragment im
        // Stream): mindestens "{}" liefern, damit der Call vollständig ist.
        for (idx, known) in callIDs where !gotArgs.contains(idx) {
            await channel.send(.toolCalls(action: .toolCall(
                id: known.id, name: known.name,
                action: .appendArguments("{}", tokenCount: 1))))
        }
    }
}

// fm_agent_server.swift — OpenAI-kompatibler Chat-Completions-Server um Apples
// FoundationModels (macOS 26+) MIT agentischem Tool-Calling über die Wire,
// Bildeingabe, Vision-OCR und Modell-Varianten (Guardrails/UseCase).
//
// WARUM (14.08.2026): `fm serve` (Apples CLI-Bridge, :8005) kann keine Wire-
// Tool-Calls — tools+tool_choice → 500 "An unsupported generation guide was
// used" (Beta-5-Regression des tool_calls_override-Assets), und selbst wenn
// das Asset heilt, ist die Bridge ein Hack: FoundationModels führt Tools
// framework-intern aus (Tool-Protokoll, call() in-process) und kennt kein
// "Call an den API-Client zurückgeben". DIESER Server macht das sauber:
//
//   • tools aus dem Request → dynamische Proxy-Tools (Arguments =
//     GeneratedContent, parameters = GenerationSchema — dessen Codable-Form
//     ist JSON-Schema mit Pflichtfeldern title/x-order/required/
//     additionalProperties auf jedem Objekt-Level; normalizeSchema rüstet
//     nach, `fm schema` erzeugt exakt diese Form).
//   • Proxy-call() CAPTURED name+arguments und wirft ab → die Generierung
//     stoppt, der Server emittiert ein OpenAI tool_calls-Delta +
//     finish_reason "tool_calls". Das Tool läuft NIE hier — der Client
//     (Brains llm_loop) dispatcht selbst.
//   • Folge-Request mit role:"tool"-Messages → Transcript-Rekonstruktion
//     (Instructions/Prompt/Response/ToolCalls/ToolOutput sind öffentliche
//     Transcript-Entries) → die Session generiert nahtlos weiter.
//   • tool_choice: "auto"→.allowed, "required"/{name}→.required (bei {name}
//     wird das Toolset auf das genannte Tool gefiltert = forced-Semantik),
//     "none"→.disallowed. response_format json_schema → Guided Generation.
//   • BILDER (macOS 27): OpenAI content-parts {"type":"image_url"} mit
//     data:-URI → Transcript.ImageAttachment (AFM 3 Core Advanced ist
//     multimodal). NUR data:-URIs — keine Remote-Fetches (SSRF/Datenschutz).
//   • MODELL-VARIANTEN über den OpenAI-Modellnamen (so wählt der Caller
//     auch künftig PCC o. Ä.):
//       apple-fm-agentic        System-Modell, Standard-Guardrails (Default)
//       apple-fm-permissive     Guardrails .permissiveContentTransformations
//                               (Übersetzung/Transformation; Bank-Test hatte
//                               ~1% Guardrail-Blocks mit .default)
//       apple-fm-contenttagging UseCase .contentTagging (Tags/Schlagworte)
//       apple-vision-ocr        KEIN LLM: Vision RecognizeDocumentsRequest,
//                               Bild → Markdown (Absätze/Tabellen/Listen)
//   • USAGE: echte Token-Zahlen aus dem Snapshot (macOS 27, inkl.
//     prompt_tokens_details.cached_tokens); Fallback chars/4 ("estimated").
//   • FEHLERMAPPING: GenerationError → OpenAI-Fehlercodes
//     (context_length_exceeded 400, content_filter 400, model_not_ready 503,
//     invalid_schema 400, unsupported_language 400).
//
// Kein externes Paket (Network.framework, handgeschriebenes HTTP/1.1 wie der
// WS-Handshake im speech_stream_server). Antworten sind SSE (stream:true)
// oder ein JSON-Objekt; immer Connection: close (EOF-terminiert, httpx-ok).
//
// Build (M4 — 27er-SDK verlangt den CLT-Swift, Xcodes 6.2.3 kann die
// Interfaces nicht; voller Pfad + explizites -sdk, sonst 'unable to load
// standard library'):
//   /Library/Developer/CommandLineTools/usr/bin/swiftc -O \
//     -sdk /Library/Developer/CommandLineTools/SDKs/MacOSX27.sdk \
//     fm_agent_server.swift -o fm_agent_server
// launchd: com.brain-agent.fm-agent (Port 8007). Log: /tmp/fm-agent.log
//
// Grenzen (v1, bewusst): parallele Tool-Calls einer Runde werden nur so weit
// gecaptured, wie das Framework sie vor dem Abbruch noch aufruft (Brain fährt
// ohnehin disable_parallel für forced-Pfade); eine Fortsetzung nach
// Tool-Outputs bzw. einem Bild-Prompt hängt einen leeren Prompt-Eintrag an
// (empirisch unauffällig, siehe Abnahmetests im Commit).

import CoreGraphics
import Foundation
import FoundationModels
import ImageIO
import Network
import Vision

let arguments = CommandLine.arguments
var port: UInt16 = 8007
if let i = arguments.firstIndex(of: "--port"), i + 1 < arguments.count,
   let p = UInt16(arguments[i + 1]) { port = p }

func log(_ s: String) {
    FileHandle.standardError.write(("[fm-agent] " + s + "\n").data(using: .utf8)!)
}

// ── Modell-Varianten ───────────────────────────────────────────────────────
let DEFAULT_MODEL = "apple-fm-agentic"
let OCR_MODEL = "apple-vision-ocr"
let FM_VARIANTS = [DEFAULT_MODEL, "apple-fm-permissive", "apple-fm-contenttagging"]

@available(macOS 26.0, *)
func resolveSystemModel(_ id: String) -> SystemLanguageModel? {
    switch id {
    case "apple-fm-permissive":
        return SystemLanguageModel(guardrails: .permissiveContentTransformations)
    case "apple-fm-contenttagging":
        return SystemLanguageModel(useCase: .contentTagging)
    case DEFAULT_MODEL, "", "system", "apple-fm-system":
        return SystemLanguageModel.default
    default:
        return nil
    }
}

// ── Tool-Call-Capture ──────────────────────────────────────────────────────
struct AbortAfterCapture: Error {}

final class CallCapture: @unchecked Sendable {
    private let lock = NSLock()
    private var calls: [(id: String, name: String, argsJSON: String)] = []
    func add(name: String, args: GeneratedContent) {
        lock.lock(); defer { lock.unlock() }
        let cid = "call_" + UUID().uuidString.replacingOccurrences(of: "-", with: "").prefix(24)
        calls.append((id: String(cid), name: name, argsJSON: args.jsonString))
    }
    var snapshot: [(id: String, name: String, argsJSON: String)] {
        lock.lock(); defer { lock.unlock() }
        return calls
    }
}

@available(macOS 26.0, *)
struct ProxyTool: Tool {
    typealias Arguments = GeneratedContent
    typealias Output = String
    let name: String
    let description: String
    let parameters: GenerationSchema
    let capture: CallCapture
    var includesSchemaInInstructions: Bool { true }
    func call(arguments: GeneratedContent) async throws -> String {
        capture.add(name: name, args: arguments)
        throw AbortAfterCapture()
    }
}

// ── Schema-Normalisierung ──────────────────────────────────────────────────
// GenerationSchemas Codable-Form ist JSON-Schema MIT Pflichtfeldern, die
// OpenAI-Clients üblicherweise weglassen: jedes Objekt-Level verlangt
// "title", "x-order" (Property-Reihenfolge), "required" und
// "additionalProperties" (per Decode-Probe auf dem 27er-SDK ermittelt;
// `fm schema` erzeugt exakt diese Form). Rekursiv nachrüsten, vorhandene
// Angaben gewinnen.
func normalizeSchema(_ any: Any, title: String) -> Any {
    guard var d = any as? [String: Any] else { return any }
    if (d["type"] as? String) == "object" || d["properties"] != nil {
        var np: [String: Any] = [:]
        for (k, v) in (d["properties"] as? [String: Any]) ?? [:] {
            np[k] = normalizeSchema(v, title: k.prefix(1).uppercased() + k.dropFirst())
        }
        d["properties"] = np
        let keys = np.keys.sorted()
        if d["title"] == nil { d["title"] = title }
        if d["x-order"] == nil { d["x-order"] = keys }
        if d["required"] == nil { d["required"] = keys }
        if d["additionalProperties"] == nil { d["additionalProperties"] = false }
    }
    if let items = d["items"] { d["items"] = normalizeSchema(items, title: title + "Item") }
    for key in ["anyOf", "oneOf", "allOf"] {
        if let arr = d[key] as? [Any] {
            d[key] = arr.map { normalizeSchema($0, title: title) }
        }
    }
    return d
}

@available(macOS 26.0, *)
func decodeSchema(_ params: Any?, title: String) -> GenerationSchema? {
    // Leere/fehlende parameters → leeres Objekt-Schema.
    var obj: Any = params ?? ["type": "object", "properties": [String: Any]()]
    if let d = obj as? [String: Any], d.isEmpty {
        obj = ["type": "object", "properties": [String: Any]()]
    }
    obj = normalizeSchema(obj, title: title)
    guard let data = try? JSONSerialization.data(withJSONObject: obj) else { return nil }
    return try? JSONDecoder().decode(GenerationSchema.self, from: data)
}

// ── Bilder ─────────────────────────────────────────────────────────────────
// NUR data:-URIs (base64) — bewusst keine Remote-Fetches.
func decodeImageDataURI(_ url: String) -> Data? {
    guard url.hasPrefix("data:"), let comma = url.firstIndex(of: ",") else { return nil }
    let b64 = String(url[url.index(after: comma)...])
    return Data(base64Encoded: b64, options: .ignoreUnknownCharacters)
}

func cgImage(from data: Data) -> CGImage? {
    guard let src = CGImageSourceCreateWithData(data as CFData, nil) else { return nil }
    return CGImageSourceCreateImageAtIndex(src, 0, nil)
}

// ── OpenAI-Request → Transcript ────────────────────────────────────────────
@available(macOS 26.0, *)
struct WireRequest {
    var modelID = DEFAULT_MODEL
    var systemText = ""
    var entries: [Transcript.Entry] = []       // Historie OHNE finalen Text-Prompt
    var promptText: String? = nil              // letzter user-Text (nil = Fortsetzung: Tool-Outputs/Bild-Prompt im Transcript)
    var tools: [ProxyTool] = []
    var toolDefs: [Transcript.ToolDefinition] = []
    var toolMode: GenerationOptions.ToolCallingMode? = nil
    var responseSchema: GenerationSchema? = nil
    var temperature: Double? = nil
    var maxTokens: Int? = nil
    var stream = false
    var promptCharCount = 0
    var ocrImages: [Data] = []                 // nur für apple-vision-ocr
}

@available(macOS 26.0, *)
func parseRequest(_ body: [String: Any], capture: CallCapture) -> (WireRequest?, String?) {
    var rq = WireRequest()
    rq.modelID = ((body["model"] as? String) ?? DEFAULT_MODEL)
    rq.stream = (body["stream"] as? Bool) ?? false
    rq.temperature = body["temperature"] as? Double
    rq.maxTokens = (body["max_tokens"] as? Int) ?? (body["max_completion_tokens"] as? Int)
    if rq.modelID != OCR_MODEL, resolveSystemModel(rq.modelID) == nil {
        return (nil, "unbekanntes Modell '\(rq.modelID)' — verfügbar: \(FM_VARIANTS + [OCR_MODEL])")
    }

    // tools → Proxys + Definitionen
    var toolFilter: String? = nil
    if let tc = body["tool_choice"] as? String {
        switch tc {
        case "required": rq.toolMode = .required
        case "none": rq.toolMode = .disallowed
        default: rq.toolMode = .allowed
        }
    } else if let tc = body["tool_choice"] as? [String: Any],
              let fn = tc["function"] as? [String: Any],
              let n = fn["name"] as? String {
        rq.toolMode = .required
        toolFilter = n   // forced-Semantik: Toolset auf das genannte Tool eindampfen
    }
    if let tools = body["tools"] as? [[String: Any]] {
        for t in tools {
            guard let fn = t["function"] as? [String: Any],
                  let name = fn["name"] as? String else { continue }
            if let f = toolFilter, name != f { continue }
            guard let schema = decodeSchema(fn["parameters"],
                                            title: name.prefix(1).uppercased() + name.dropFirst()) else {
                return (nil, "tool '\(name)': parameters nicht als GenerationSchema dekodierbar")
            }
            let desc = (fn["description"] as? String) ?? ""
            rq.tools.append(ProxyTool(name: name, description: desc,
                                      parameters: schema, capture: capture))
            rq.toolDefs.append(Transcript.ToolDefinition(
                name: name, description: desc, parameters: schema))
        }
        if let f = toolFilter, rq.tools.isEmpty {
            return (nil, "tool_choice nennt unbekanntes Tool '\(f)'")
        }
    }

    // response_format json_schema → Guided Generation
    if let rf = body["response_format"] as? [String: Any],
       (rf["type"] as? String) == "json_schema",
       let js = rf["json_schema"] as? [String: Any] {
        let rfName = (js["name"] as? String) ?? "Response"
        guard let schema = decodeSchema(js["schema"],
                                        title: rfName.prefix(1).uppercased() + rfName.dropFirst()) else {
            return (nil, "response_format.json_schema.schema nicht dekodierbar")
        }
        rq.responseSchema = schema
    }

    // messages → Transcript-Einträge; der LETZTE reine Text-user wird zum Prompt.
    guard let messages = body["messages"] as? [[String: Any]] else {
        return (nil, "messages fehlt")
    }
    // content: String ODER parts [{type:text|image_url}]. Bilder nur als data:-URI.
    func partsOf(_ m: [String: Any]) -> (text: String, images: [Data]) {
        if let s = m["content"] as? String { return (s, []) }
        var text: [String] = []
        var images: [Data] = []
        if let parts = m["content"] as? [[String: Any]] {
            for p in parts {
                if let t = p["text"] as? String { text.append(t) }
                if (p["type"] as? String) == "image_url",
                   let iu = p["image_url"] as? [String: Any],
                   let u = iu["url"] as? String {
                    if let d = decodeImageDataURI(u) { images.append(d) }
                }
            }
        }
        return (text.joined(separator: "\n"), images)
    }
    var pending: [Transcript.Entry] = []
    var lastPromptHadImages = false
    for m in messages {
        let role = (m["role"] as? String) ?? ""
        let (text, images) = partsOf(m)
        rq.promptCharCount += text.count
        switch role {
        case "system", "developer":
            rq.systemText += (rq.systemText.isEmpty ? "" : "\n") + text
        case "user":
            rq.ocrImages.append(contentsOf: images)
            var segs: [Transcript.Segment] = []
            if !text.isEmpty { segs.append(.text(Transcript.TextSegment(content: text))) }
            var badImage = false
            if #available(macOS 27.0, *) {
                for d in images {
                    guard let cg = cgImage(from: d) else { badImage = true; continue }
                    segs.append(.attachment(Transcript.AttachmentSegment(
                        content: .image(Transcript.ImageAttachment(cg)))))
                }
            } else if !images.isEmpty {
                return (nil, "Bildeingabe benötigt macOS 27")
            }
            if badImage { return (nil, "image_url: data:-URI nicht dekodierbar (nur base64-Bilddaten)") }
            pending.append(.prompt(Transcript.Prompt(segments: segs)))
            lastPromptHadImages = !images.isEmpty
        case "assistant":
            lastPromptHadImages = false
            if let tcs = m["tool_calls"] as? [[String: Any]], !tcs.isEmpty {
                var calls: [Transcript.ToolCall] = []
                for tc in tcs {
                    guard let fn = tc["function"] as? [String: Any],
                          let n = fn["name"] as? String else { continue }
                    let cid = (tc["id"] as? String) ?? ("call_" + UUID().uuidString.prefix(8))
                    let argsRaw = (fn["arguments"] as? String) ?? "{}"
                    let args = (try? GeneratedContent(json: argsRaw))
                        ?? GeneratedContent(properties: [:])
                    calls.append(Transcript.ToolCall(id: String(cid), toolName: n, arguments: args))
                }
                if !text.isEmpty {
                    pending.append(.response(Transcript.Response(
                        assetIDs: [],
                        segments: [.text(Transcript.TextSegment(content: text))])))
                }
                pending.append(.toolCalls(Transcript.ToolCalls(calls)))
            } else {
                pending.append(.response(Transcript.Response(
                    assetIDs: [],
                    segments: [.text(Transcript.TextSegment(content: text))])))
            }
        case "tool":
            lastPromptHadImages = false
            let cid = (m["tool_call_id"] as? String) ?? UUID().uuidString
            let tname = (m["name"] as? String) ?? "tool"
            pending.append(.toolOutput(Transcript.ToolOutput(
                id: cid, toolName: tname,
                segments: [.text(Transcript.TextSegment(content: text))])))
        default:
            continue
        }
    }
    // Endet die Historie mit einem REINEN Text-user-Prompt, wird er als Prompt
    // an respond() gereicht (die Session hängt den Eintrag selbst an); endet
    // sie mit Tool-Outputs oder einem Bild-Prompt, bleibt alles im Transcript
    // und die Generierung wird mit einem leeren Prompt fortgesetzt.
    if !lastPromptHadImages, case .prompt(let p)? = pending.last {
        rq.promptText = p.segments.compactMap {
            if case .text(let t) = $0 { return t.content } else { return nil }
        }.joined(separator: "\n")
        pending.removeLast()
    }
    rq.entries = pending
    return (rq, nil)
}

// ── Generierung ────────────────────────────────────────────────────────────
@available(macOS 26.0, *)
func buildSession(_ rq: WireRequest) -> LanguageModelSession {
    var entries: [Transcript.Entry] = []
    if !rq.systemText.isEmpty || !rq.toolDefs.isEmpty {
        entries.append(.instructions(Transcript.Instructions(
            segments: rq.systemText.isEmpty ? [] :
                [.text(Transcript.TextSegment(content: rq.systemText))],
            toolDefinitions: rq.toolDefs)))
    }
    entries.append(contentsOf: rq.entries)
    let model = resolveSystemModel(rq.modelID) ?? SystemLanguageModel.default
    return LanguageModelSession(
        model: model, tools: rq.tools, transcript: Transcript(entries: entries))
}

@available(macOS 26.0, *)
func generationOptions(_ rq: WireRequest) -> GenerationOptions {
    var opts = GenerationOptions(temperature: rq.temperature,
                                 maximumResponseTokens: rq.maxTokens)
    if let m = rq.toolMode { opts.toolCallingMode = m }
    return opts
}

// ── Fehlermapping ──────────────────────────────────────────────────────────
@available(macOS 26.0, *)
func mapGenerationError(_ error: Error) -> (status: Int, code: String, message: String) {
    if let g = error as? LanguageModelSession.GenerationError {
        switch g {
        case .exceededContextWindowSize:
            return (400, "context_length_exceeded",
                    "Kontextfenster überschritten (AFM ~4k Tokens)")
        case .guardrailViolation:
            return (400, "content_filter",
                    "Apple-Guardrail hat die Anfrage blockiert (ggf. Modell apple-fm-permissive nutzen)")
        case .assetsUnavailable:
            return (503, "model_not_ready",
                    "Modell-Assets nicht verfügbar (Apple Intelligence lädt ggf. noch)")
        case .unsupportedGuide:
            return (400, "invalid_schema",
                    "Guided-Generation-Schema wird nicht unterstützt")
        case .unsupportedLanguageOrLocale:
            return (400, "unsupported_language",
                    "Sprache/Locale wird vom Modell nicht unterstützt")
        default:
            break
        }
    }
    // Fallback per Message-Sniffing: nicht jeder Fehler kommt als matchbarer
    // GenerationError-Case an (der 8k-Overflow z. B. traf beim Test den
    // default-Zweig, Meldung "exceeds the maximum allowed context size").
    let msg = (error as? LocalizedError)?.errorDescription ?? "\(error)"
    // Beide beobachteten Varianten: "…exceeds the maximum allowed context
    // size of 8192" (Prompt-Validierung) und "The session's transcript
    // exceeded the model's context size." (Transcript-Pfad).
    if msg.contains("context size") || msg.contains("context window") {
        return (400, "context_length_exceeded", msg)
    }
    if msg.lowercased().contains("guardrail") || msg.lowercased().contains("safety") {
        return (400, "content_filter", msg)
    }
    return (500, "server_error", msg)
}

// ── OpenAI-Antwortbau ──────────────────────────────────────────────────────
func jsonData(_ obj: Any) -> Data {
    (try? JSONSerialization.data(withJSONObject: obj)) ?? Data("{}".utf8)
}

func chunkObj(_ rid: String, _ model: String, _ delta: [String: Any],
              finish: String? = nil, usage: [String: Any]? = nil) -> [String: Any] {
    var choice: [String: Any] = ["index": 0, "delta": delta]
    choice["finish_reason"] = finish ?? NSNull()
    var o: [String: Any] = [
        "id": rid, "object": "chat.completion.chunk",
        "created": Int(Date().timeIntervalSince1970),
        "model": model, "choices": [choice],
    ]
    if let u = usage { o["usage"] = u }
    return o
}

func estimatedUsage(promptChars: Int, completionChars: Int) -> [String: Any] {
    let p = max(1, promptChars / 4), c = max(0, completionChars / 4)
    return ["prompt_tokens": p, "completion_tokens": c,
            "total_tokens": p + c, "estimated": true]
}

func toolCallsJSON(_ calls: [(id: String, name: String, argsJSON: String)]) -> [[String: Any]] {
    calls.enumerated().map { (i, c) in
        ["index": i, "id": c.id, "type": "function",
         "function": ["name": c.name, "arguments": c.argsJSON]]
    }
}

// ── Vision-OCR (Port von apple_ocr_cli.swift) ──────────────────────────────
func ocrCellText(_ container: DocumentObservation.Container) -> String {
    container.text.transcript
        .replacingOccurrences(of: "\n", with: " ")
        .replacingOccurrences(of: "|", with: "\\|")
        .trimmingCharacters(in: .whitespaces)
}

func ocrMarkdown(from document: DocumentObservation.Container) -> String {
    var out: [String] = []
    for paragraph in document.paragraphs {
        let t = paragraph.transcript.trimmingCharacters(in: .whitespacesAndNewlines)
        if !t.isEmpty { out.append(t) }
    }
    for table in document.tables {
        var lines: [String] = []
        for (i, row) in table.rows.enumerated() {
            let cells = row.map { ocrCellText($0.content) }
            lines.append("| " + cells.joined(separator: " | ") + " |")
            if i == 0 {
                lines.append("|" + Array(repeating: " --- |", count: cells.count).joined())
            }
        }
        if !lines.isEmpty { out.append(lines.joined(separator: "\n")) }
    }
    for list in document.lists {
        let items = list.items.map { "- " + $0.itemString.trimmingCharacters(in: .whitespacesAndNewlines) }
        if !items.isEmpty { out.append(items.joined(separator: "\n")) }
    }
    if out.isEmpty {
        let t = document.text.transcript.trimmingCharacters(in: .whitespacesAndNewlines)
        if !t.isEmpty { out.append(t) }
    }
    return out.joined(separator: "\n\n")
}

func runOCR(_ images: [Data]) async throws -> String {
    var parts: [String] = []
    for data in images {
        let request = RecognizeDocumentsRequest()
        let observations = try await request.perform(on: data)
        for observation in observations {
            parts.append(ocrMarkdown(from: observation.document))
        }
    }
    return parts.joined(separator: "\n\n---\n\n")
}

// ── HTTP-Schicht ───────────────────────────────────────────────────────────
final class ConnectionHandler: @unchecked Sendable {
    let conn: NWConnection
    let id: Int
    var buffer = Data()
    static var registry: [Int: ConnectionHandler] = [:]   // globaler Retain
    static let registryLock = NSLock()
    static var nextID = 0

    init(_ c: NWConnection) {
        conn = c
        ConnectionHandler.registryLock.lock()
        ConnectionHandler.nextID += 1
        id = ConnectionHandler.nextID
        ConnectionHandler.registry[id] = self
        ConnectionHandler.registryLock.unlock()
    }

    func start() {
        conn.stateUpdateHandler = { [weak self] st in
            if case .failed = st { self?.close() }
            if case .cancelled = st { self?.unregister() }
        }
        conn.start(queue: .global())
        receive()
    }

    func receive() {
        conn.receive(minimumIncompleteLength: 1, maximumLength: 1 << 22) {
            [weak self] data, _, complete, err in
            guard let self else { return }
            if let d = data { self.buffer.append(d) }
            if err != nil || complete { self.close(); return }
            if self.tryHandle() { return }   // Antwort läuft; Verbindung endet danach
            self.receive()
        }
    }

    // true = Request vollständig, Handling gestartet
    func tryHandle() -> Bool {
        guard let headerEnd = buffer.range(of: Data("\r\n\r\n".utf8)) else { return false }
        let head = String(data: buffer[..<headerEnd.lowerBound], encoding: .utf8) ?? ""
        let lines = head.components(separatedBy: "\r\n")
        guard let reqLine = lines.first else { return false }
        let parts = reqLine.components(separatedBy: " ")
        guard parts.count >= 2 else { self.close(); return true }
        let method = parts[0], path = parts[1]
        var contentLength = 0
        for l in lines.dropFirst() {
            let kv = l.split(separator: ":", maxSplits: 1)
            if kv.count == 2, kv[0].lowercased() == "content-length" {
                contentLength = Int(kv[1].trimmingCharacters(in: .whitespaces)) ?? 0
            }
        }
        let bodyStart = headerEnd.upperBound
        guard buffer.count - bodyStart >= contentLength else { return false }
        let body = buffer.subdata(in: bodyStart..<(bodyStart + contentLength))
        route(method: method, path: path, body: body)
        return true
    }

    func route(method: String, path: String, body: Data) {
        switch (method, path) {
        case ("GET", "/health"):
            sendJSON(200, ["status": "ok", "models": FM_VARIANTS + [OCR_MODEL]])
        case ("GET", "/v1/models"):
            let now = Int(Date().timeIntervalSince1970)
            sendJSON(200, ["object": "list", "data":
                (FM_VARIANTS + [OCR_MODEL]).map {
                    ["id": $0, "object": "model", "owned_by": "Apple", "created": now]
                }])
        case ("POST", "/v1/chat/completions"):
            guard #available(macOS 26.0, *) else {
                sendError(500, "FoundationModels benötigt macOS 26+"); return
            }
            guard let obj = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any] else {
                sendError(400, "Body ist kein JSON-Objekt"); return
            }
            handleChat(obj)
        default:
            sendError(404, "not found")
        }
    }

    // ── Chat ───────────────────────────────────────────────────────────────
    @available(macOS 26.0, *)
    func handleChat(_ body: [String: Any]) {
        let capture = CallCapture()
        let (rqOpt, perr) = parseRequest(body, capture: capture)
        guard let rq = rqOpt else { sendError(400, perr ?? "parse error"); return }
        let rid = "chatcmpl-" + UUID().uuidString

        // OCR-Modell: kein LLM — Vision direkt.
        if rq.modelID == OCR_MODEL {
            guard !rq.ocrImages.isEmpty else {
                sendError(400, "apple-vision-ocr erwartet mindestens ein Bild (image_url, data:-URI)")
                return
            }
            Task {
                if rq.stream {
                    self.sendSSEHeader()
                    self.sendSSE(chunkObj(rid, rq.modelID, ["role": "assistant"]))
                }
                do {
                    let md = try await runOCR(rq.ocrImages)
                    if rq.stream { self.sendSSE(chunkObj(rid, rq.modelID, ["content": md])) }
                    self.finishText(rid: rid, rq: rq, text: md, realUsage: nil)
                } catch {
                    self.failGeneration(rid: rid, rq: rq,
                                        status: 500, code: "ocr_error", message: "\(error)")
                }
            }
            return
        }

        let session = buildSession(rq)
        let opts = generationOptions(rq)
        let promptText = rq.promptText ?? ""   // leer = Fortsetzung (Tool-Outputs/Bild-Prompt im Transcript)

        Task {
            if rq.stream {
                self.sendSSEHeader()
                self.sendSSE(chunkObj(rid, rq.modelID, ["role": "assistant"]))
            }
            var emitted = ""
            var realUsage: [String: Any]? = nil
            do {
                if let schema = rq.responseSchema {
                    // Guided Generation — Stream liefert kumulative Snapshots.
                    let stream = session.streamResponse(
                        to: Prompt(promptText), schema: schema,
                        includeSchemaInPrompt: true, options: opts)
                    var lastJSON = ""
                    for try await snap in stream {
                        lastJSON = snap.content.jsonString
                        realUsage = self.usageJSON(snap.usage)
                        if rq.stream {
                            let delta = String(lastJSON.dropFirst(
                                emitted.commonPrefix(with: lastJSON).count))
                            if !delta.isEmpty {
                                self.sendSSE(chunkObj(rid, rq.modelID, ["content": delta]))
                                emitted = lastJSON
                            }
                        }
                    }
                    emitted = lastJSON
                } else {
                    let stream = session.streamResponse(to: Prompt(promptText), options: opts)
                    for try await snap in stream {
                        let cum = snap.content
                        realUsage = self.usageJSON(snap.usage)
                        if rq.stream {
                            let delta = String(cum.dropFirst(
                                emitted.commonPrefix(with: cum).count))
                            if !delta.isEmpty { self.sendSSE(chunkObj(rid, rq.modelID, ["content": delta])) }
                        }
                        emitted = cum
                    }
                }
                self.finishText(rid: rid, rq: rq, text: emitted, realUsage: realUsage)
            } catch {
                let calls = capture.snapshot
                if !calls.isEmpty {
                    self.finishToolCalls(rid: rid, rq: rq, calls: calls, realUsage: realUsage)
                } else {
                    let (status, code, message) = mapGenerationError(error)
                    log("generation error [\(code)]: \(message)")
                    self.failGeneration(rid: rid, rq: rq,
                                        status: status, code: code, message: message)
                }
            }
        }
    }

    // Echte Token-Zahlen aus dem Snapshot (macOS 27); nil → Schätzung greift.
    @available(macOS 26.0, *)
    func usageJSON(_ usage: LanguageModelSession.Usage) -> [String: Any] {
        return [
            "prompt_tokens": usage.input.totalTokenCount,
            "completion_tokens": usage.output.totalTokenCount,
            "total_tokens": usage.input.totalTokenCount + usage.output.totalTokenCount,
            "prompt_tokens_details": ["cached_tokens": usage.input.cachedTokenCount],
        ]
    }

    @available(macOS 26.0, *)
    func finishText(rid: String, rq: WireRequest, text: String, realUsage: [String: Any]?) {
        let usage = realUsage ?? estimatedUsage(promptChars: rq.promptCharCount,
                                               completionChars: text.count)
        if rq.stream {
            sendSSE(chunkObj(rid, rq.modelID, [:], finish: "stop", usage: usage))
            sendSSEDone()
        } else {
            sendJSON(200, [
                "id": rid, "object": "chat.completion",
                "created": Int(Date().timeIntervalSince1970), "model": rq.modelID,
                "choices": [["index": 0, "finish_reason": "stop",
                             "message": ["role": "assistant", "content": text]]],
                "usage": usage])
        }
    }

    @available(macOS 26.0, *)
    func finishToolCalls(rid: String, rq: WireRequest,
                         calls: [(id: String, name: String, argsJSON: String)],
                         realUsage: [String: Any]?) {
        let tcs = toolCallsJSON(calls)
        let usage = realUsage ?? estimatedUsage(
            promptChars: rq.promptCharCount,
            completionChars: calls.reduce(0) { $0 + $1.argsJSON.count })
        if rq.stream {
            sendSSE(chunkObj(rid, rq.modelID, ["tool_calls": tcs]))
            sendSSE(chunkObj(rid, rq.modelID, [:], finish: "tool_calls", usage: usage))
            sendSSEDone()
        } else {
            sendJSON(200, [
                "id": rid, "object": "chat.completion",
                "created": Int(Date().timeIntervalSince1970), "model": rq.modelID,
                "choices": [["index": 0, "finish_reason": "tool_calls",
                             "message": ["role": "assistant", "content": NSNull(),
                                         "tool_calls": tcs]]],
                "usage": usage])
        }
    }

    @available(macOS 26.0, *)
    func failGeneration(rid: String, rq: WireRequest, status: Int, code: String, message: String) {
        if rq.stream {
            sendSSE(["error": ["message": message, "type": status >= 500 ? "server_error" : "invalid_request_error",
                               "code": code]], event: "error")
            sendSSEDone()
        } else {
            sendJSON(status, ["error": ["message": message,
                                        "type": status >= 500 ? "server_error" : "invalid_request_error",
                                        "code": code]])
        }
    }

    // ── Senden ─────────────────────────────────────────────────────────────
    func rawSend(_ d: Data, close: Bool = false) {
        conn.send(content: d, completion: .contentProcessed { [weak self] _ in
            if close { self?.close() }
        })
    }

    func sendJSON(_ status: Int, _ obj: Any) {
        let body = jsonData(obj)
        let head = "HTTP/1.1 \(status) \(status == 200 ? "OK" : "Error")\r\n"
            + "Content-Type: application/json\r\nContent-Length: \(body.count)\r\n"
            + "Connection: close\r\n\r\n"
        rawSend(Data(head.utf8) + body, close: true)
    }

    func sendError(_ status: Int, _ message: String) {
        sendJSON(status, ["error": ["message": message,
                                    "type": status >= 500 ? "server_error" : "invalid_request_error",
                                    "code": "\(status)"]])
    }

    func sendSSEHeader() {
        rawSend(Data(("HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
            + "Cache-Control: no-cache\r\nConnection: close\r\n\r\n").utf8))
    }

    func sendSSE(_ obj: Any, event: String? = nil) {
        var s = ""
        if let e = event { s += "event: \(e)\n" }
        s += "data: " + (String(data: jsonData(obj), encoding: .utf8) ?? "{}") + "\n\n"
        rawSend(Data(s.utf8))
    }

    func sendSSEDone() {
        rawSend(Data("data: [DONE]\n\n".utf8), close: true)
    }

    func close() { conn.cancel() }

    func unregister() {
        ConnectionHandler.registryLock.lock()
        ConnectionHandler.registry.removeValue(forKey: id)
        ConnectionHandler.registryLock.unlock()
    }
}

// ── Listener ───────────────────────────────────────────────────────────────
let params = NWParameters.tcp
params.allowLocalEndpointReuse = true
let listener = try NWListener(using: params, on: NWEndpoint.Port(rawValue: port)!)
listener.newConnectionHandler = { conn in
    ConnectionHandler(conn).start()
}
listener.stateUpdateHandler = { st in
    if case .ready = st { log("bereit auf Port \(port) — Modelle: \(FM_VARIANTS + [OCR_MODEL])") }
    if case .failed(let e) = st { log("Listener-Fehler: \(e)"); exit(1) }
}
listener.start(queue: .global())

// ANE-Warmstart: drückt die Erste-Antwort-Latenz nach Serverstart.
if #available(macOS 26.0, *) {
    Task {
        LanguageModelSession(model: .default).prewarm(promptPrefix: nil)
        log("prewarm angestoßen")
    }
}

dispatchMain()

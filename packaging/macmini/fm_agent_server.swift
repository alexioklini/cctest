// fm_agent_server.swift — OpenAI-kompatibler Chat-Completions-Server um Apples
// FoundationModels (macOS 26+) MIT agentischem Tool-Calling über die Wire.
//
// WARUM (14.08.2026): `fm serve` (Apples CLI-Bridge, :8005) kann keine Wire-
// Tool-Calls — tools+tool_choice → 500 "An unsupported generation guide was
// used" (Beta-5-Regression des tool_calls_override-Assets), und selbst wenn
// das Asset heilt, ist die Bridge ein Hack: FoundationModels führt Tools
// framework-intern aus (Tool-Protokoll, call() in-process) und kennt kein
// "Call an den API-Client zurückgeben". DIESER Server macht das sauber:
//
//   • tools aus dem Request → dynamische Proxy-Tools (Arguments =
//     GeneratedContent, parameters = GenerationSchema via JSONDecoder — die
//     GenerationSchema-Codable-Form IST Standard-JSON-Schema, dieselbe, die
//     `fm respond --schema` lädt).
//   • Proxy-call() CAPTURED name+arguments und wirft ab → die Generierung
//     stoppt, der Server emittiert ein OpenAI tool_calls-Delta +
//     finish_reason "tool_calls". Das Tool läuft NIE hier — der Client
//     (Brains llm_loop) dispatcht selbst.
//   • Folge-Request mit role:"tool"-Messages → Transcript-Rekonstruktion
//     (Instructions/Prompt/Response/ToolCalls/ToolOutput sind öffentliche
//     Transcript-Entries) → die Session generiert nahtlos weiter.
//   • tool_choice: "auto"→.allowed, "required"/{name}→.required (bei {name}
//     wird das Toolset auf das genannte Tool gefiltert = forced-Semantik),
//     "none"→.disallowed. response_format json_schema → Guided Generation
//     (Transcript.ResponseFormat) wird ebenfalls unterstützt.
//
// Kein externes Paket (Network.framework, handgeschriebenes HTTP/1.1 wie der
// WS-Handshake im speech_stream_server). Antworten sind SSE (stream:true)
// oder ein JSON-Objekt; immer Connection: close (EOF-terminiert, httpx-ok).
//
// Build (M4 — 27er-SDK verlangt den CLT-Swift, Xcodes 6.2.3 kann die
// Interfaces nicht; voller Pfad, siehe Beta-5-Lektionen):
//   /Library/Developer/CommandLineTools/usr/bin/swiftc -O fm_agent_server.swift -o fm_agent_server
// launchd: com.brain-agent.fm-agent (Port 8007). Log: /tmp/fm-agent.log
//
// Grenzen (v1, bewusst): parallele Tool-Calls einer Runde werden nur so weit
// gecaptured, wie das Framework sie vor dem Abbruch noch aufruft (Brain fährt
// ohnehin disable_parallel für forced-Pfade); Token-Zahlen sind Schätzwerte
// (chars/4 — AFM meldet keine Usage nach außen); eine Fortsetzung nach
// Tool-Outputs hängt einen leeren Prompt-Eintrag an (empirisch unauffällig,
// siehe Abnahmetests im Commit).

import Foundation
import FoundationModels
import Network

let arguments = CommandLine.arguments
var port: UInt16 = 8007
if let i = arguments.firstIndex(of: "--port"), i + 1 < arguments.count,
   let p = UInt16(arguments[i + 1]) { port = p }

func log(_ s: String) {
    FileHandle.standardError.write(("[fm-agent] " + s + "\n").data(using: .utf8)!)
}

let SERVED_MODEL = "apple-fm-agentic"

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

// ── OpenAI-Request → Transcript ────────────────────────────────────────────
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

@available(macOS 26.0, *)
struct WireRequest {
    var systemText = ""
    var entries: [Transcript.Entry] = []       // Historie OHNE finalen Prompt
    var promptText: String? = nil              // letzter user-Text (nil = Fortsetzung nach Tool-Outputs)
    var tools: [ProxyTool] = []
    var toolDefs: [Transcript.ToolDefinition] = []
    var toolMode: GenerationOptions.ToolCallingMode? = nil
    var responseSchema: GenerationSchema? = nil
    var temperature: Double? = nil
    var maxTokens: Int? = nil
    var stream = false
    var promptCharCount = 0
}

@available(macOS 26.0, *)
func parseRequest(_ body: [String: Any], capture: CallCapture) -> (WireRequest?, String?) {
    var rq = WireRequest()
    rq.stream = (body["stream"] as? Bool) ?? false
    rq.temperature = body["temperature"] as? Double
    rq.maxTokens = (body["max_tokens"] as? Int) ?? (body["max_completion_tokens"] as? Int)

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

    // messages → Transcript-Einträge; der LETZTE user-Text wird zum Prompt.
    guard let messages = body["messages"] as? [[String: Any]] else {
        return (nil, "messages fehlt")
    }
    func textOf(_ m: [String: Any]) -> String {
        if let s = m["content"] as? String { return s }
        if let parts = m["content"] as? [[String: Any]] {
            return parts.compactMap { ($0["text"] as? String) }.joined(separator: "\n")
        }
        return ""
    }
    var pending: [Transcript.Entry] = []
    for m in messages {
        let role = (m["role"] as? String) ?? ""
        let text = textOf(m)
        rq.promptCharCount += text.count
        switch role {
        case "system", "developer":
            rq.systemText += (rq.systemText.isEmpty ? "" : "\n") + text
        case "user":
            pending.append(.prompt(Transcript.Prompt(
                segments: [.text(Transcript.TextSegment(content: text))])))
        case "assistant":
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
                    segments: [.text(Transcript.TextSegment(content: text))])))
            }
        case "tool":
            let cid = (m["tool_call_id"] as? String) ?? UUID().uuidString
            let tname = (m["name"] as? String) ?? "tool"
            pending.append(.toolOutput(Transcript.ToolOutput(
                id: cid, toolName: tname,
                segments: [.text(Transcript.TextSegment(content: text))])))
        default:
            continue
        }
    }
    // Endet die Historie mit einem user-Prompt, wird er als Prompt an
    // respond() gereicht (die Session hängt den Eintrag selbst an); endet sie
    // mit Tool-Outputs, bleibt alles im Transcript und die Generierung wird
    // mit einem leeren Prompt fortgesetzt.
    if case .prompt(let p)? = pending.last {
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
    return LanguageModelSession(
        model: .default, tools: rq.tools, transcript: Transcript(entries: entries))
}

@available(macOS 26.0, *)
func generationOptions(_ rq: WireRequest) -> GenerationOptions {
    var opts = GenerationOptions(temperature: rq.temperature,
                                 maximumResponseTokens: rq.maxTokens)
    if let m = rq.toolMode { opts.toolCallingMode = m }
    return opts
}

// ── OpenAI-Antwortbau ──────────────────────────────────────────────────────
func jsonData(_ obj: Any) -> Data {
    (try? JSONSerialization.data(withJSONObject: obj)) ?? Data("{}".utf8)
}

func chunkObj(_ rid: String, _ delta: [String: Any], finish: String? = nil,
              usage: [String: Any]? = nil) -> [String: Any] {
    var choice: [String: Any] = ["index": 0, "delta": delta]
    choice["finish_reason"] = finish ?? NSNull()
    var o: [String: Any] = [
        "id": rid, "object": "chat.completion.chunk",
        "created": Int(Date().timeIntervalSince1970),
        "model": SERVED_MODEL, "choices": [choice],
    ]
    if let u = usage { o["usage"] = u }
    return o
}

func usageObj(promptChars: Int, completionChars: Int) -> [String: Any] {
    // AFM liefert keine Token-Zahlen nach außen — grobe chars/4-Schätzung,
    // als solche markiert (Brain bucht is_local ohnehin mit 0 €).
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
        conn.receive(minimumIncompleteLength: 1, maximumLength: 1 << 20) {
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
            sendJSON(200, ["status": "ok", "model": SERVED_MODEL])
        case ("GET", "/v1/models"):
            sendJSON(200, ["object": "list", "data": [
                ["id": SERVED_MODEL, "object": "model", "owned_by": "Apple",
                 "created": Int(Date().timeIntervalSince1970)]]])
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
        let session = buildSession(rq)
        let opts = generationOptions(rq)
        let promptText = rq.promptText ?? ""   // leer = Fortsetzung nach Tool-Outputs

        Task {
            if rq.stream {
                self.sendSSEHeader()
                self.sendSSE(chunkObj(rid, ["role": "assistant"]))
            }
            var emitted = ""
            do {
                if let schema = rq.responseSchema {
                    // Guided Generation — Stream liefert kumulative Snapshots.
                    let stream = session.streamResponse(
                        to: Prompt(promptText), schema: schema,
                        includeSchemaInPrompt: true, options: opts)
                    var lastJSON = ""
                    for try await snap in stream {
                        lastJSON = snap.content.jsonString
                        if rq.stream {
                            let delta = String(lastJSON.dropFirst(
                                emitted.commonPrefix(with: lastJSON).count))
                            if !delta.isEmpty {
                                self.sendSSE(chunkObj(rid, ["content": delta]))
                                emitted = lastJSON
                            }
                        }
                    }
                    emitted = lastJSON
                } else {
                    let stream = session.streamResponse(to: Prompt(promptText), options: opts)
                    for try await snap in stream {
                        let cum = snap.content
                        if rq.stream {
                            let delta = String(cum.dropFirst(
                                emitted.commonPrefix(with: cum).count))
                            if !delta.isEmpty { self.sendSSE(chunkObj(rid, ["content": delta])) }
                        }
                        emitted = cum
                    }
                }
                self.finishText(rid: rid, rq: rq, text: emitted)
            } catch {
                let calls = capture.snapshot
                if !calls.isEmpty {
                    self.finishToolCalls(rid: rid, rq: rq, calls: calls)
                } else {
                    let msg = "\(error)"
                    log("generation error: \(msg)")
                    if rq.stream {
                        self.sendSSE(["error": ["message": msg, "type": "server_error",
                                                "code": "500"]], event: "error")
                        self.sendSSEDone()
                    } else {
                        self.sendError(500, msg)
                    }
                }
            }
        }
    }

    func finishText(rid: String, rq: WireRequest, text: String) {
        let usage = usageObj(promptChars: rq.promptCharCount, completionChars: text.count)
        if rq.stream {
            sendSSE(chunkObj(rid, [:], finish: "stop", usage: usage))
            sendSSEDone()
        } else {
            sendJSON(200, [
                "id": rid, "object": "chat.completion",
                "created": Int(Date().timeIntervalSince1970), "model": SERVED_MODEL,
                "choices": [["index": 0, "finish_reason": "stop",
                             "message": ["role": "assistant", "content": text]]],
                "usage": usage])
        }
    }

    func finishToolCalls(rid: String, rq: WireRequest,
                         calls: [(id: String, name: String, argsJSON: String)]) {
        let tcs = toolCallsJSON(calls)
        let usage = usageObj(promptChars: rq.promptCharCount,
                             completionChars: calls.reduce(0) { $0 + $1.argsJSON.count })
        if rq.stream {
            sendSSE(chunkObj(rid, ["tool_calls": tcs]))
            sendSSE(chunkObj(rid, [:], finish: "tool_calls", usage: usage))
            sendSSEDone()
        } else {
            sendJSON(200, [
                "id": rid, "object": "chat.completion",
                "created": Int(Date().timeIntervalSince1970), "model": SERVED_MODEL,
                "choices": [["index": 0, "finish_reason": "tool_calls",
                             "message": ["role": "assistant", "content": NSNull(),
                                         "tool_calls": tcs]]],
                "usage": usage])
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
    if case .ready = st { log("bereit auf Port \(port) (Modell \(SERVED_MODEL))") }
    if case .failed(let e) = st { log("Listener-Fehler: \(e)"); exit(1) }
}
listener.start(queue: .global())
dispatchMain()

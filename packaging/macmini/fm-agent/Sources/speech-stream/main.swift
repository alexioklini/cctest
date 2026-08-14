// speech_stream_server.swift — WebSocket-Streaming-Transkription über Apples
// SpeechAnalyzer/SpeechTranscriber (macOS 26+ Speech-Framework) auf dem M4.
//
// Protokoll IDENTISCH zur Voxtral-Streaming-Lane (ROUTERSTREAMINGSPEC):
//   1. Client → Text-Frame (JSON-Config): {"model":…, "sample_rate":16000, "language":""}
//   2. Client → Binär-Frames: PCM16-LE, mono, 16 kHz
//   3. Server → {"type":"partial","text":"<kumulativ>"} (finalisierte Segmente
//      + aktuelle volatile Hypothese)
//   4. Client → {"type":"end"} → Server → {"type":"final","text":…}; der CLIENT
//      schließt (Server-Grace 2s — Close-Semantik wie der Voxtral-1006-Fix).
//   Fehler: {"type":"error","message":…}. 30s ohne Audio → final + Close.
//
// Läuft auf der ANE/CPU — die M4-GPU (oMLX) bleibt unberührt. Eine Session
// gleichzeitig (Symmetrie zur Voxtral-Lane; busy → error-Frame).
//
// Build (M4, Command Line Tools reichen — den VORHANDENEN Prebuilt-Modul-Cache
// nutzen, KEIN frischer -module-cache-path: die 27er-SDK-Interfaces verlangen
// einen neueren Swift-Compiler, der Prebuilt-Cache trägt die 26er-Speech-API):
//   swiftc -O speech_stream_server.swift -o speech_stream_server
// launchd: com.brain-agent.speech-stream (Port 8004).

import AVFoundation
import Foundation
import NaturalLanguage
import Network
import Speech

let arguments = CommandLine.arguments
var port: UInt16 = 8004
if let i = arguments.firstIndex(of: "--port"), i + 1 < arguments.count,
   let p = UInt16(arguments[i + 1]) { port = p }

func log(_ s: String) {
    FileHandle.standardError.write(("[speech-stream] " + s + "\n").data(using: .utf8)!)
}

// ── Locale-Auflösung ───────────────────────────────────────────────────────
// Config-"language": ""  → DUAL-LANE de_DE + en_US (SpeechTranscriber kann
// keine Sprache erkennen — Session ist locale-fest; zwei Transcriber am
// selben Analyzer hören dasselbe Audio, ein NL-Selektor kürt die Gewinner-
// Spur und liefert deren Sprache ins Frame; WBP-language-Erweiterung).
// "de"/"en"/BCP-47 → eine Spur (Schnellpfad, Sprache = Vorgabe).
func resolveLanes(_ language: String) -> [(Locale, String)] {
    switch language.lowercased() {
    case "":
        return [(Locale(identifier: "de_DE"), "de"), (Locale(identifier: "en_US"), "en")]
    case "de", "de-de", "de_de": return [(Locale(identifier: "de_DE"), "de")]
    case "en", "en-us", "en_us": return [(Locale(identifier: "en_US"), "en")]
    default:
        let ident = language.replacingOccurrences(of: "-", with: "_")
        return [(Locale(identifier: ident), String(language.prefix(2)).lowercased())]
    }
}

// ── Eine Transkriptions-Spur (Locale-fest) ─────────────────────────────────
@available(macOS 26.0, *)
final class TranscribeLane {
    let code: String  // ISO-639-1 fürs language-Feld ("de"/"en")
    let transcriber: SpeechTranscriber
    var confirmed = ""
    var volatileText = ""

    var cumulative: String {
        (confirmed + " " + volatileText).trimmingCharacters(in: .whitespaces)
    }

    init(locale: Locale, code: String) {
        self.code = code
        transcriber = SpeechTranscriber(
            locale: locale,
            transcriptionOptions: [],
            // fastResults ist der Realtime-Hebel: ohne ihn verarbeitet der
            // Transcriber in ~10s-Fenstern (Partials kamen erst am Ende).
            reportingOptions: [.volatileResults, .fastResults],
            attributeOptions: [])
    }
}

// ── Eine Transkriptions-Session (ein Client = eine Wortmeldung) ────────────
// 1 Spur bei explizitem language, 2 Spuren (de+en) bei Auto: beide Module
// hängen am SELBEN Analyzer und hören dasselbe Audio; der Selektor kürt die
// Spur, deren Text laut NLLanguageRecognizer zu ihrer eigenen Locale passt
// (Denglisch → Mehrheitssprache gewinnt). Frames tragen die Gewinner-Sprache.
@available(macOS 26.0, *)
final class TranscribeSession {
    private let lanes: [TranscribeLane]
    private let analyzer: SpeechAnalyzer
    private let inputContinuation: AsyncStream<AnalyzerInput>.Continuation
    private let converter: AVAudioConverter
    private let inputFormat: AVAudioFormat
    private let analyzerFormat: AVAudioFormat
    private var resultsTasks: [Task<Void, Never>] = []
    var audioSeconds: Double = 0
    // Beide Lane-Tasks mutieren State und lesen im Selektor die JEWEILS
    // ANDERE Lane — unsynchronisiert war das ein Data-Race (SIGSEGV beim
    // Release). Alle State-Zugriffe laufen über diese serielle Queue.
    private let stateQueue = DispatchQueue(label: "speech-session-state")

    func winner() -> (text: String, language: String?) {
        stateQueue.sync { winnerLocked() }
    }

    private func winnerLocked() -> (text: String, language: String?) {
        if lanes.count == 1 {
            let t = lanes[0].cumulative
            return (t, t.isEmpty ? nil : lanes[0].code)
        }
        var best: (lane: TranscribeLane, score: Double)?
        for lane in lanes {
            let t = lane.cumulative
            if t.isEmpty { continue }
            var score = Double(t.count)
            let recognizer = NLLanguageRecognizer()
            recognizer.processString(t)
            if let dominant = recognizer.dominantLanguage?.rawValue.prefix(2),
               dominant == lane.code {
                score *= 2.0
            }
            if best == nil || score > best!.score { best = (lane, score) }
        }
        guard let b = best else { return ("", nil) }
        // Sprach-Stabilität: unter 12 Zeichen ist NL-Erkennung Rauschen
        // ("G" → en) — Feld weglassen, App nutzt solange ihre Heuristik.
        let language = b.lane.cumulative.count >= 12 ? b.lane.code : nil
        return (b.lane.cumulative, language)
    }

    init(lanes laneSpecs: [(Locale, String)],
         onUpdate: @escaping (String, String?) -> Void) async throws {
        lanes = laneSpecs.map { TranscribeLane(locale: $0.0, code: $0.1) }
        for lane in lanes {
            if let request = try await AssetInventory.assetInstallationRequest(
                    supporting: [lane.transcriber]) {
                log("lade Speech-Assets für \(lane.code) …")
                try await request.downloadAndInstall()
            }
        }
        analyzer = SpeechAnalyzer(modules: lanes.map { $0.transcriber })
        guard let best = await SpeechAnalyzer.bestAvailableAudioFormat(
                compatibleWith: lanes.map { $0.transcriber }) else {
            throw NSError(domain: "speech-stream", code: 1, userInfo: [
                NSLocalizedDescriptionKey: "no analyzer audio format"])
        }
        analyzerFormat = best
        guard let inFmt = AVAudioFormat(
                commonFormat: .pcmFormatInt16, sampleRate: 16000,
                channels: 1, interleaved: true),
              let conv = AVAudioConverter(from: inFmt, to: best) else {
            throw NSError(domain: "speech-stream", code: 2, userInfo: [
                NSLocalizedDescriptionKey: "audio converter init failed"])
        }
        inputFormat = inFmt
        converter = conv

        var continuation: AsyncStream<AnalyzerInput>.Continuation!
        let stream = AsyncStream<AnalyzerInput> { continuation = $0 }
        inputContinuation = continuation

        // Je Spur: volatile ersetzt die Hypothese, final wandert in confirmed;
        // nach jedem Update entscheidet der Selektor, was emittiert wird.
        for lane in lanes {
            resultsTasks.append(Task { [weak self] in
                guard let self else { return }
                do {
                    for try await result in lane.transcriber.results {
                        let text = String(result.text.characters)
                            .trimmingCharacters(in: .whitespaces)
                        self.stateQueue.sync {
                            if result.isFinal {
                                if !text.isEmpty {
                                    lane.confirmed = (lane.confirmed + " " + text)
                                        .trimmingCharacters(in: .whitespaces)
                                }
                                lane.volatileText = ""
                            } else {
                                lane.volatileText = text
                            }
                            let w = self.winnerLocked()
                            onUpdate(w.text, w.language)
                        }
                    }
                } catch {
                    log("results stream (\(lane.code)) ended: \(error)")
                }
            })
        }
        try await analyzer.start(inputSequence: stream)
    }

    func feed(pcm16: Data) throws {
        let frames = AVAudioFrameCount(pcm16.count / 2)
        guard frames > 0,
              let inBuf = AVAudioPCMBuffer(pcmFormat: inputFormat,
                                           frameCapacity: frames) else { return }
        inBuf.frameLength = frames
        _ = pcm16.withUnsafeBytes { raw in
            memcpy(inBuf.int16ChannelData![0], raw.baseAddress!, pcm16.count)
        }
        audioSeconds += Double(frames) / 16000.0

        let ratio = analyzerFormat.sampleRate / inputFormat.sampleRate
        let outCap = AVAudioFrameCount(Double(frames) * ratio) + 64
        guard let outBuf = AVAudioPCMBuffer(pcmFormat: analyzerFormat,
                                            frameCapacity: outCap) else { return }
        var fed = false
        var convError: NSError?
        converter.convert(to: outBuf, error: &convError) { _, status in
            if fed { status.pointee = .noDataNow; return nil }
            fed = true
            status.pointee = .haveData
            return inBuf
        }
        if let convError { throw convError }
        if outBuf.frameLength > 0 {
            inputContinuation.yield(AnalyzerInput(buffer: outBuf))
        }
    }

    private var finished = false
    private var finalResult: (text: String, language: String?) = ("", nil)

    func finish() async -> (text: String, language: String?) {
        // Idempotent: der Teardown ruft finish() nach dem final-Pfad erneut —
        // ein zweites finalizeAndFinish auf dem beendeten Analyzer crasht den
        // Prozess (KeepAlive-Restart riss dann die NÄCHSTE Verbindung ab).
        if finished { return finalResult }
        finished = true
        inputContinuation.finish()
        try? await analyzer.finalizeAndFinishThroughEndOfInput()
        for task in resultsTasks { task.cancel() }
        // Nach finalize sind alle Segmente final — Selektor entscheidet.
        finalResult = winner()
        return finalResult
    }
}

// ── WebSocket-Server (Network.framework, kein externes Paket) ──────────────
let sessionSlot = DispatchSemaphore(value: 1)

func wsSend(_ connection: NWConnection, json: [String: Any]) {
    guard let data = try? JSONSerialization.data(withJSONObject: json) else { return }
    let metadata = NWProtocolWebSocket.Metadata(opcode: .text)
    let context = NWConnection.ContentContext(identifier: "text", metadata: [metadata])
    connection.send(content: data, contentContext: context,
                    completion: .contentProcessed { _ in })
}

@available(macOS 26.0, *)
// ── Status-Datei für SparkDash ─────────────────────────────────────────────
// Der Port spricht ausschließlich WebSocket (NWProtocolWebSocket im Stack) —
// ein HTTP-/status auf demselben Port ist nicht möglich. Monitoring liest
// stattdessen ~/speech-stream-status.json (atomar geschrieben) + TCP-Check.
let STATUS_FILE = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent("speech-stream-status.json")
let PROCESS_START = Date()

final class SpeechStats {
    static let shared = SpeechStats()
    private let lock = NSLock()
    private var sessionsTotal = 0
    private var aborts = 0
    private var busy = false
    private var lastLanguage: String?
    private var lastFinalChars = 0
    private var lastSessionEnd: Date?

    func sessionStarted() {
        lock.lock(); sessionsTotal += 1; busy = true; lock.unlock()
        write()
    }
    func sessionEnded(language: String?, finalChars: Int) {
        lock.lock()
        busy = false
        if let l = language { lastLanguage = l }
        lastFinalChars = finalChars
        lastSessionEnd = Date()
        lock.unlock()
        write()
    }
    /// Teardown ohne final (Client weg/Fehler) — no-op, wenn die Session
    /// bereits sauber beendet wurde (busy schon false).
    func sessionAborted() {
        lock.lock()
        let wasBusy = busy
        if wasBusy { busy = false; aborts += 1; lastSessionEnd = Date() }
        lock.unlock()
        if wasBusy { write() }
    }
    func write() {
        lock.lock()
        var obj: [String: Any] = [
            "uptime_s": Int(Date().timeIntervalSince(PROCESS_START)),
            "busy": busy,
            "sessions_total": sessionsTotal,
            "aborts": aborts,
            "last_final_chars": lastFinalChars,
        ]
        if let l = lastLanguage { obj["last_language"] = l }
        if let t = lastSessionEnd { obj["last_session_ts"] = Int(t.timeIntervalSince1970) }
        lock.unlock()
        if let d = try? JSONSerialization.data(withJSONObject: obj) {
            try? d.write(to: STATUS_FILE, options: .atomic)
        }
    }
}

final class ConnectionHandler {
    let connection: NWConnection
    var session: TranscribeSession?
    var lastEmit = ""
    var gotConfig = false
    var holdsSlot = false
    var closed = false
    var idleTimer: DispatchWorkItem?
    var onClosed: (() -> Void)?
    // Audio, das VOR Session-Bereitschaft eintrifft (Init ist async — Assets,
    // Analyzer-Start), wird gepuffert und nachgefüttert statt verworfen —
    // sonst fehlt der Anfang der Wortmeldung ("Guten Morgen,"-Bug).
    var pendingAudio: [Data] = []
    var pendingEnd = false

    init(_ connection: NWConnection) { self.connection = connection }

    func start() {
        connection.stateUpdateHandler = { [weak self] state in
            if case .failed = state { self?.teardown() }
            if case .cancelled = state { self?.teardown() }
        }
        connection.start(queue: .global())
        receiveLoop()
        armIdleTimer()
    }

    private func armIdleTimer() {
        idleTimer?.cancel()
        let work = DispatchWorkItem { [weak self] in
            guard let self, !self.closed else { return }
            Task { await self.sendFinalAndClose() }  // 30s ohne Frames
        }
        idleTimer = work
        DispatchQueue.global().asyncAfter(deadline: .now() + 30, execute: work)
    }

    private func receiveLoop() {
        connection.receiveMessage { [weak self] data, context, _, error in
            guard let self, !self.closed else { return }
            if error != nil { self.teardown(); return }
            defer { if !self.closed { self.receiveLoop() } }
            guard let data, let context else { return }
            self.armIdleTimer()
            let meta = context.protocolMetadata(
                definition: NWProtocolWebSocket.definition) as? NWProtocolWebSocket.Metadata
            switch meta?.opcode {
            case .text:
                self.handleText(data)
            case .binary:
                if let s = self.session {
                    try? s.feed(pcm16: data)
                } else {
                    self.pendingAudio.append(data)
                }
            case .close:
                self.teardown()
            default:
                break
            }
        }
    }

    private func handleText(_ data: Data) {
        guard let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return }
        if !gotConfig {
            gotConfig = true
            let language = (obj["language"] as? String) ?? ""
            if sessionSlot.wait(timeout: .now()) != .success {
                wsSend(connection, json: ["type": "error",
                    "message": "busy: a streaming session is already active"])
                teardown()
                return
            }
            holdsSlot = true
            SpeechStats.shared.sessionStarted()
            Task { [weak self] in
                guard let self else { return }
                do {
                    let s = try await TranscribeSession(
                        lanes: resolveLanes(language)) { [weak self] text, lang in
                        guard let self, !self.closed else { return }
                        let emitKey = (lang ?? "") + "|" + text
                        if !text.isEmpty && emitKey != self.lastEmit {
                            self.lastEmit = emitKey
                            var frame: [String: Any] = ["type": "partial", "text": text]
                            if let lang { frame["language"] = lang }
                            wsSend(self.connection, json: frame)
                        }
                    }
                    // Gepuffertes Frühaudio nachfüttern, dann normal weiter.
                    for chunk in self.pendingAudio { try? s.feed(pcm16: chunk) }
                    self.pendingAudio = []
                    self.session = s
                    if self.pendingEnd { await self.sendFinalAndClose() }
                } catch {
                    wsSend(self.connection, json: ["type": "error",
                        "message": "session init failed: \(error.localizedDescription)"])
                    self.teardown()
                }
            }
            return
        }
        if (obj["type"] as? String) == "end" {
            if session == nil { pendingEnd = true; return }  // Init läuft noch
            Task { await sendFinalAndClose() }
        }
    }

    private func sendFinalAndClose() async {
        guard !closed else { return }
        idleTimer?.cancel()
        let result = await session?.finish() ?? ("", nil)
        SpeechStats.shared.sessionEnded(language: result.1, finalChars: result.0.count)
        var frame: [String: Any] = ["type": "final", "text": result.0]
        if let lang = result.1 { frame["language"] = lang }
        wsSend(connection, json: frame)
        // Grace: der Client schließt nach Empfang des final; wir räumen nach 2s.
        DispatchQueue.global().asyncAfter(deadline: .now() + 2) { [weak self] in
            self?.teardown()
        }
    }

    private func teardown() {
        guard !closed else { return }
        closed = true
        idleTimer?.cancel()
        if holdsSlot { sessionSlot.signal(); holdsSlot = false; SpeechStats.shared.sessionAborted() }
        if let s = session { Task { _ = await s.finish() } }
        session = nil
        connection.cancel()
        onClosed?()
    }
}

// ── Listener ───────────────────────────────────────────────────────────────
guard #available(macOS 26.0, *) else {
    log("braucht macOS 26+")
    exit(1)
}

let parameters = NWParameters(tls: nil)
let wsOptions = NWProtocolWebSocket.Options()
wsOptions.autoReplyPing = true
wsOptions.maximumMessageSize = 8 * 1024 * 1024
parameters.defaultProtocolStack.applicationProtocols.insert(wsOptions, at: 0)

// Aktive Handler retainen — ohne das würde der Handler sofort deallokiert
// (weak-self-Callbacks liefen ins Leere) und die Verbindung stürbe still.
var activeHandlers: [ObjectIdentifier: AnyObject] = [:]
let handlersLock = NSLock()

let listener = try NWListener(using: parameters, on: NWEndpoint.Port(rawValue: port)!)
listener.newConnectionHandler = { connection in
    if #available(macOS 26.0, *) {
        let handler = ConnectionHandler(connection)
        let key = ObjectIdentifier(handler)
        handlersLock.lock(); activeHandlers[key] = handler; handlersLock.unlock()
        handler.onClosed = {
            handlersLock.lock(); activeHandlers.removeValue(forKey: key); handlersLock.unlock()
        }
        handler.start()
    }
}
listener.start(queue: .global())
SpeechStats.shared.write()   // Status-Datei sofort anlegen (busy=false)
log("streaming lane on ws://0.0.0.0:\(port) (SpeechAnalyzer)")
RunLoop.main.run()

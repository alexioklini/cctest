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
// Config-"language": ""/"de" → de_DE, "en" → en_US, sonst 1:1 als Identifier.
func resolveLocale(_ language: String) -> Locale {
    switch language.lowercased() {
    case "", "de", "de-de", "de_de": return Locale(identifier: "de_DE")
    case "en", "en-us", "en_us": return Locale(identifier: "en_US")
    default: return Locale(identifier: language.replacingOccurrences(of: "-", with: "_"))
    }
}

// ── Eine Transkriptions-Session (ein Client = eine Wortmeldung) ────────────
@available(macOS 26.0, *)
final class TranscribeSession {
    private let transcriber: SpeechTranscriber
    private let analyzer: SpeechAnalyzer
    private let inputContinuation: AsyncStream<AnalyzerInput>.Continuation
    private let converter: AVAudioConverter
    private let inputFormat: AVAudioFormat
    private let analyzerFormat: AVAudioFormat
    private var resultsTask: Task<Void, Never>?

    // Kumulativer Stand: finalisierte Segmente + aktuelle volatile Hypothese.
    private(set) var confirmed: String = ""
    private(set) var volatileText: String = ""
    var audioSeconds: Double = 0

    var cumulative: String {
        (confirmed + " " + volatileText).trimmingCharacters(in: .whitespaces)
    }

    init(locale: Locale, onUpdate: @escaping (String) -> Void) async throws {
        transcriber = SpeechTranscriber(
            locale: locale,
            transcriptionOptions: [],
            // fastResults ist der Realtime-Hebel: ohne ihn verarbeitet der
            // Transcriber in ~10s-Fenstern (Partials kamen erst am Ende).
            reportingOptions: [.volatileResults, .fastResults],
            attributeOptions: [])
        // Modell-Assets bei Bedarf nachladen (einmalig pro Locale).
        if let request = try await AssetInventory.assetInstallationRequest(
                supporting: [transcriber]) {
            log("lade Speech-Assets für \(locale.identifier) …")
            try await request.downloadAndInstall()
        }
        analyzer = SpeechAnalyzer(modules: [transcriber])
        guard let best = await SpeechAnalyzer.bestAvailableAudioFormat(
                compatibleWith: [transcriber]) else {
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

        // Ergebnisse: volatile ersetzt die Hypothese, final wandert in confirmed.
        resultsTask = Task { [weak self] in
            guard let self else { return }
            do {
                for try await result in self.transcriber.results {
                    let text = String(result.text.characters)
                        .trimmingCharacters(in: .whitespaces)
                    if result.isFinal {
                        if !text.isEmpty {
                            self.confirmed = (self.confirmed + " " + text)
                                .trimmingCharacters(in: .whitespaces)
                        }
                        self.volatileText = ""
                    } else {
                        self.volatileText = text
                    }
                    onUpdate(self.cumulative)
                }
            } catch {
                log("results stream ended: \(error)")
            }
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

    func finish() async -> String {
        inputContinuation.finish()
        try? await analyzer.finalizeAndFinishThroughEndOfInput()
        resultsTask?.cancel()
        // Nach finalize sind alle Segmente final — volatile Rest einrechnen.
        return cumulative
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
            Task { [weak self] in
                guard let self else { return }
                do {
                    let s = try await TranscribeSession(
                        locale: resolveLocale(language)) { [weak self] text in
                        guard let self, !self.closed else { return }
                        if !text.isEmpty && text != self.lastEmit {
                            self.lastEmit = text
                            wsSend(self.connection, json: ["type": "partial", "text": text])
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
        let text = await session?.finish() ?? ""
        wsSend(connection, json: ["type": "final", "text": text])
        // Grace: der Client schließt nach Empfang des final; wir räumen nach 2s.
        DispatchQueue.global().asyncAfter(deadline: .now() + 2) { [weak self] in
            self?.teardown()
        }
    }

    private func teardown() {
        guard !closed else { return }
        closed = true
        idleTimer?.cancel()
        if holdsSlot { sessionSlot.signal(); holdsSlot = false }
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
log("streaming lane on ws://0.0.0.0:\(port) (SpeechAnalyzer)")
RunLoop.main.run()

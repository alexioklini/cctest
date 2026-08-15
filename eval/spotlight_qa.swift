// spotlight-qa — Core-Spotlight-RAG, alles Apple/ml-explore-Standard:
// Stufe 1 Dokument-Discovery (natives SpotlightSearchTool + mdfind-Zweitquelle),
// Stufe 2 Antwort aus Companion-Markdown-Auszügen — beides in EINER
// LanguageModelSession-Familie mit austauschbarem Backend (MODEL-Env):
//   afm | coreai:<pfad> | mlx:<hf-id> | omlx:<id> | router:<router-modell-id>
// Modi:  spotlight-qa "<Frage>" [corpus]           (Einzelfrage)
//        spotlight-qa --serve [corpus]             (Modell RESIDENT; Fragen
//          zeilenweise via stdin, je Antwort EINE Zeile "@@RESULT@@{json}")
import Foundation
import CoreSpotlight
import FoundationModels
import CoreAILanguageModels
import WireLanguageModel
import HuggingFace
import MLXFoundationModels
import MLXHuggingFace
import MLXLLM
import MLXLMCommon
import Tokenizers

// Capabilities eines Router-Modells aus GET /v1/models (capabilities-Feld).
@available(macOS 27.0, *)
func routerCapabilities(base: String, key: String, model: String) async
    -> [LanguageModelCapabilities.Capability]? {
    guard let url = URL(string: base + "/models") else { return nil }
    var req = URLRequest(url: url)
    req.timeoutInterval = 5
    req.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
    guard let (data, _) = try? await URLSession.shared.data(for: req),
          let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          let list = obj["data"] as? [[String: Any]],
          let entry = list.first(where: { ($0["id"] as? String) == model }),
          let strs = entry["capabilities"] as? [String] else { return nil }
    var caps: [LanguageModelCapabilities.Capability] = []
    for c in strs {
        switch c {
        case "tools": caps.append(.toolCalling)
        case "guided": caps.append(.guidedGeneration)
        case "reasoning": caps.append(.reasoning)
        case "vision": caps.append(.vision)
        default: break
        }
    }
    return caps.isEmpty ? nil : caps
}

@available(macOS 27.0, *)
func makeModel(_ spec: String) async throws -> any FoundationModels.LanguageModel {
    if spec.hasPrefix("coreai:") {
        let path = String(spec.dropFirst("coreai:".count))
        let url = URL(fileURLWithPath: NSString(string: path).expandingTildeInPath,
                      isDirectory: true)
        return try await CoreAILanguageModel(resourcesAt: url)
    }
    if spec.hasPrefix("mlx:") {
        let id = String(spec.dropFirst("mlx:".count))
        let cfg = ModelConfiguration(id: id)
        return MLXLanguageModel(
            configuration: cfg,
            capabilities: [],
            weightsLocation: { rid in
                let cache = HubCache.default
                guard let repo = Repo.ID(rawValue: rid) else { return cache.cacheDirectory }
                if let commit = cache.resolveRevision(repo: repo, kind: .model, ref: "main"),
                   let snapshot = try? cache.snapshotPath(repo: repo, kind: .model, commitHash: commit) {
                    return snapshot
                }
                return cache.repoDirectory(repo: repo, kind: .model)
            },
            load: { configuration, progressHandler in
                try await loadModelContainer(
                    from: #hubDownloader(),
                    using: #huggingFaceTokenizerLoader(),
                    configuration: configuration,
                    progressHandler: progressHandler)
            })
    }
    if spec.hasPrefix("omlx:") {
        let id = String(spec.dropFirst("omlx:".count))
        let base = ProcessInfo.processInfo.environment["OMLX_URL"] ?? "http://localhost:8000/v1"
        let key = ProcessInfo.processInfo.environment["OMLX_KEY"] ?? "brain"
        return WireLanguageModel(baseURL: base, apiKey: key, model: id)
    }
    if spec.hasPrefix("router:") {
        let id = String(spec.dropFirst("router:".count))
        let base = ProcessInfo.processInfo.environment["ROUTER_URL"] ?? "http://localhost:8424/v1"
        let key = ProcessInfo.processInfo.environment["ROUTER_KEY"] ?? ""
        var caps: [LanguageModelCapabilities.Capability] = [.toolCalling, .guidedGeneration]
        if let capsCSV = ProcessInfo.processInfo.environment["ROUTER_CAPS"] {
            // Expliziter Override (Debug/Experimente)
            caps = []
            for c in capsCSV.split(separator: ",") {
                switch c.trimmingCharacters(in: .whitespaces) {
                case "tool", "tools": caps.append(.toolCalling)
                case "guided": caps.append(.guidedGeneration)
                case "reasoning": caps.append(.reasoning)
                case "vision": caps.append(.vision)
                default: break
                }
            }
        } else if let fetched = await routerCapabilities(base: base, key: key, model: id) {
            // Single Source of Truth: der Router weist die Capabilities pro
            // Modell in GET /v1/models aus (berechnete Defaults ∪ extras).
            caps = fetched
        }
        return WireLanguageModel(baseURL: base, apiKey: key, model: id, capabilities: caps)
    }
    return SystemLanguageModel(guardrails: .permissiveContentTransformations)
}

// Echo-Tool für den Tool-Calling-E2E-Test (--probe; File-Scope — attached
// macros sind auf lokalen Typen verboten).
@available(macOS 27.0, *)
struct WetterTool: FoundationModels.Tool {
    let name = "wetter"
    let description = "Liefert das aktuelle Wetter für einen Ort."
    @Generable
    struct Arguments {
        @Guide(description: "Ortsname")
        var ort: String
    }
    func call(arguments: Arguments) async throws -> String {
        return "Wetter in \(arguments.ort): sonnig, 25 Grad"
    }
}

// Probe-Zieltyp für den Guided-Generation-E2E-Test (--probe).
@available(macOS 27.0, *)
@Generable
struct ProbeStadt {
    @Guide(description: "Name der Stadt")
    var stadt: String
    @Guide(description: "Ungefähre Einwohnerzahl")
    var einwohner: Int
}

@main
struct SpotlightQA {
    static func main() async {
        guard #available(macOS 27.0, *) else {
            print("{\"error\": \"braucht macOS 27\"}"); exit(3)
        }
        await run()
    }

    // --probe: die drei Protokoll-Säulen des gewählten Backends nacheinander
    // testen — Chat, Guided Generation (@Generable), Tool-Calling.
    @available(macOS 27.0, *)
    static func probe(model: any FoundationModels.LanguageModel) async {
        var out: [String: Any] = [:]
        // 1) Chat
        do {
            let s = LanguageModelSession(model: model)
            let r = try await s.respond(to: "Antworte mit genau einem Wort: OK")
            out["chat"] = r.content
        } catch { out["chat_error"] = "\(error)" }
        // 2) Guided Generation
        do {
            let s = LanguageModelSession(model: model)
            let r = try await s.respond(
                to: "Nenne eine Stadt in Österreich mit Einwohnerzahl.",
                generating: ProbeStadt.self)
            out["guided"] = ["stadt": r.content.stadt,
                             "einwohner": r.content.einwohner]
        } catch { out["guided_error"] = "\(error)" }
        // 3) Tool-Calling (triviales Echo-Tool, File-Scope wegen Macro)
        do {
            let s = LanguageModelSession(model: model, tools: [WetterTool()])
            let r = try await s.respond(to: "Wie ist das Wetter in Wien? Nutze das Wetter-Werkzeug.")
            var calls = 0
            for e in s.transcript { if case .toolCalls(let c) = e { calls += c.count } }
            out["tool_calls"] = calls
            out["tool_answer"] = String(r.content.prefix(120))
        } catch { out["tool_error"] = "\(error)" }
        if let data = try? JSONSerialization.data(withJSONObject: out, options: [.prettyPrinted]),
           let s = String(data: data, encoding: .utf8) { print(s) }
    }

    @available(macOS 27.0, *)
    static func run() async {
        let args = CommandLine.arguments
        guard args.count >= 2 else {
            FileHandle.standardError.write("usage: [MODEL=…] spotlight-qa \"<frage>\"|--serve [corpus-dir]\n".data(using: .utf8)!)
            exit(2)
        }
        let serve = (args[1] == "--serve")
        let corpus = args.count >= 3 ? args[2]
            : NSString(string: "~/spotlight-eval/corpus").expandingTildeInPath
        let modelSpec = ProcessInfo.processInfo.environment["MODEL"] ?? "afm"

        let model: any FoundationModels.LanguageModel
        do {
            model = try await makeModel(modelSpec)
        } catch {
            print("{\"error\": \"model-load: \(String(describing: error).replacingOccurrences(of: "\"", with: "'"))\"}")
            exit(1)
        }

        if args[1] == "--probe" {
            await probe(model: model)
            return
        }

        // Companion-Map einmal bauen (serve: bleibt über alle Fragen stehen).
        var mdByBase: [String: String] = [:]
        if let en = FileManager.default.enumerator(atPath: corpus) {
            var mdRels: [String] = []
            while let rel = en.nextObject() as? String {
                if rel.hasSuffix(".md") { mdRels.append(rel) }
            }
            for rel in mdRels {
                let base = (rel as NSString).lastPathComponent
                    .replacingOccurrences(of: ".md", with: "")
                mdByBase[base] = corpus + "/" + rel
            }
        }

        if serve {
            FileHandle.standardError.write("bereit (\(modelSpec))\n".data(using: .utf8)!)
            while let line = readLine(strippingNewline: true) {
                let q = line.trimmingCharacters(in: .whitespaces)
                if q.isEmpty { continue }
                if q == "@@QUIT@@" { break }
                let out = await answerOne(question: q, corpus: corpus,
                                          model: model, mdByBase: mdByBase)
                emitResult(out, marker: true)
            }
        } else {
            let out = await answerOne(question: args[1], corpus: corpus,
                                      model: model, mdByBase: mdByBase)
            emitResult(out, marker: false)
        }
    }

    @available(macOS 27.0, *)
    static func emitResult(_ out: [String: Any], marker: Bool) {
        let opts: JSONSerialization.WritingOptions = marker ? [] : [.prettyPrinted]
        if let data = try? JSONSerialization.data(withJSONObject: out, options: opts),
           let s = String(data: data, encoding: .utf8) {
            print((marker ? "@@RESULT@@" : "") + s)
        } else {
            print((marker ? "@@RESULT@@" : "") + "{\"error\": \"json serialization failed\"}")
        }
        fflush(stdout)
    }

    @available(macOS 27.0, *)
    static func answerOne(question: String, corpus: String,
                          model: any FoundationModels.LanguageModel,
                          mdByBase: [String: String]) async -> [String: Any] {
        // ── Stufe 1: Discovery via natives SpotlightSearchTool ──────────────
        var fileSource = FileSource(fetchAttributes: [.title, .path, .contentURL, .displayName])
        fileSource.scopes = [URL(fileURLWithPath: corpus, isDirectory: true)]
        // Beta-Befund: scopes wird NICHT respektiert → harter Korpus-Filter
        // unten über die Companion-Map; 8 Slots gegen Junk-Verdrängung.
        fileSource.maximumResultCount = 8
        var config = SpotlightSearchTool.Configuration(sources: [.files(fileSource)])
        config.guide = SpotlightSearchTool.Guide(
            level: .focused(.documents), format: .compact)
        let tool = SpotlightSearchTool(configuration: config)

        let searchSession = LanguageModelSession(model: model, tools: [tool], instructions: """
            Finde die für die Frage relevanten Dokumente. Du MUSST das \
            Spotlight-Suchwerkzeug aufrufen. Suche mit 1-2 Kernbegriffen (alle \
            Begriffe müssen im Dokument vorkommen — weniger ist mehr); bei 0 \
            Treffern suche SOFORT erneut mit nur EINEM Begriff. Antworte danach \
            nur mit den gefundenen Dateinamen.
            """)

        final class ReplyBox: @unchecked Sendable { var paths: [String] = [] }
        let box = ReplyBox()
        let collector = Task {
            for await reply in tool.searchResults {
                var items: [CoreSpotlight.SearchableItem] = []
                switch reply.content {
                case .items(let arr): items = arr
                case .scoredItems(let arr): items = arr.map { $0.item }
                default: break
                }
                for si in items {
                    // path/contentURL sind im Datei-Index-Reply nil (gemessen)
                    // — displayName trägt den Dateinamen.
                    if let d = si.item.attributeSet.displayName { box.paths.append(d) }
                }
            }
        }

        let t0 = Date()
        var searchNote = ""
        do {
            let r = try await searchSession.respond(to: question)
            searchNote = r.content
        } catch { searchNote = "search-error: \(error)" }
        try? await Task.sleep(nanoseconds: 300_000_000)
        collector.cancel()

        var toolCalls: [[String: String]] = []
        for entry in searchSession.transcript {
            if case .toolCalls(let calls) = entry {
                for c in calls {
                    toolCalls.append(["tool": c.toolName,
                                      "args": String(describing: c.arguments).prefix(200).description])
                }
            }
        }

        // Deterministische Zweitquelle: mdfind -onlyin corpus pro Frage-Begriff
        // (scopes-Bug + UND-Verknüpfung der Tool-Keywords).
        let stopEarly: Set<String> = ["welche", "werden", "müssen", "wie", "wird",
                                      "sind", "eine", "einen", "gilt", "oft", "wer",
                                      "was", "ist", "von", "bei", "für", "und",
                                      "oder", "nicht", "auch", "wurde", "durch"]
        let qTerms = question.lowercased()
            .components(separatedBy: CharacterSet.alphanumerics.inverted)
            .filter { $0.count > 3 && !stopEarly.contains($0) }
        var hitCount: [String: Int] = [:]
        for term in qTerms.prefix(6) {
            let proc = Process()
            proc.executableURL = URL(fileURLWithPath: "/usr/bin/mdfind")
            proc.arguments = ["-onlyin", corpus, term]
            let pipe = Pipe()
            proc.standardOutput = pipe
            proc.standardError = Pipe()
            try? proc.run()
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            proc.waitUntilExit()
            for line in (String(data: data, encoding: .utf8) ?? "").split(separator: "\n") {
                let p = String(line)
                guard !p.contains("/.brain-extracted/") else { continue }
                hitCount[p, default: 0] += 1
            }
        }
        let mdfindRanked = hitCount.sorted { ($0.value, $0.key) > ($1.value, $1.key) }
            .map { ($0.key as NSString).lastPathComponent }

        // Kandidaten-Union, HART auf den Korpus gefiltert (Companion-Map).
        var docTexts: [(name: String, text: String)] = []
        var seen = Set<String>()
        for p in box.paths + mdfindRanked {
            let base = (p as NSString).lastPathComponent
            let name = base.hasSuffix(".md")
                ? base.replacingOccurrences(of: ".md", with: "") : base
            guard let mp = mdByBase[name], !seen.contains(name) else { continue }
            seen.insert(name)
            if let t = try? String(contentsOfFile: mp, encoding: .utf8) {
                docTexts.append((name, t))
            }
            if docTexts.count >= 3 { break }
        }

        // Kontext-Fenster: Absätze nach Frage-Begriffen ranken, je Doc kappen.
        func windows(_ text: String, budget: Int) -> String {
            let paras = text.components(separatedBy: "\n\n")
            let scored = paras.enumerated().map { (i, p) -> (Int, Int, String) in
                let lp = p.lowercased()
                let s = qTerms.reduce(0) { $0 + (lp.contains($1) ? 1 : 0) }
                return (s, i, p)
            }.sorted { ($0.0, -$0.1) > ($1.0, -$1.1) }
            var out: [(Int, String)] = []
            var used = 0
            for (s, i, p) in scored where s > 0 || used == 0 {
                if used + p.count > budget { continue }
                out.append((i, p)); used += p.count
                if used > budget - 200 { break }
            }
            return out.sorted { $0.0 < $1.0 }.map { $0.1 }.joined(separator: "\n\n")
        }
        let totalBudget = Int(ProcessInfo.processInfo.environment["CTX_BUDGET"] ?? "") ?? 7000
        let perDoc = docTexts.isEmpty ? 0 : totalBudget / docTexts.count
        var context = ""
        for (name, text) in docTexts {
            context += "=== \(name) ===\n" + windows(text, budget: perDoc) + "\n\n"
        }

        if ProcessInfo.processInfo.environment["RETRIEVE_ONLY"] == "1" {
            return [
                "docs": docTexts.map { $0.name },
                "context": context,
                "tool_calls": toolCalls,
                "duration_s": Double(Int(Date().timeIntervalSince(t0) * 10)) / 10,
            ]
        }

        // ── Stufe 2: Antwort aus dem echten Inhalt ──────────────────────────
        var answer = ""
        var errorText: String? = nil
        if context.isEmpty {
            answer = "Die Dokumente enthalten keine Antwort auf diese Frage (keine relevanten Treffer im Regelwerk)."
        } else {
            let answerSession = LanguageModelSession(model: model, instructions: """
                Beantworte die Frage präzise auf Deutsch, NUR auf Basis der \
                Dokumentauszüge. Belege JEDE Aussage in der Form \
                [Quelle: <Dateiname> — "wörtliches Zitat aus dem Auszug"]. \
                Erfinde keine Werte: was nicht im Auszug steht, ist "nicht \
                spezifiziert". Steht die Antwort gar nicht in den Auszügen, \
                sage das ausdrücklich.
                """)
            do {
                let r = try await answerSession.respond(to: "Frage: \(question)\n\nDokumentauszüge:\n\(context)")
                answer = r.content
            } catch { errorText = "\(error)" }
        }
        let dur = Date().timeIntervalSince(t0)

        var out: [String: Any] = [
            "answer": answer,
            "search_note": searchNote,
            "tool_calls": toolCalls,
            "docs": docTexts.map { $0.name },
            "context_chars": context.count,
            "duration_s": Double(Int(dur * 10)) / 10,
        ]
        if let e = errorText { out["error"] = e }
        return out
    }
}

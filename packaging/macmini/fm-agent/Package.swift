// swift-tools-version: 6.0
// fm-agent — OpenAI-kompatibler Inferenz-Server um Apples FoundationModels
// (AFM on-device, Vision-OCR) PLUS CoreAI-Zoo-Modelle via CoreAIKit.
//
// SUPPLY-CHAIN: coreai-kit ist auf den auditierten Commit GEPINNT (Review
// 14.08.2026: Netzwerk ausschließlich huggingface.co mit revisions-gepinnten
// Downloads via BuiltinPins, keine Prozess-Spawns, kein dlopen, keine
// Telemetrie; Deps: john-rocky/coreai-models exact 0.2.1-zoo — gepatchter
// Fork von apple/coreai-models — und huggingface/swift-transformers).
// Beim Pin-Update: Diff erneut reviewen.
//
// Build (M4): SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX27.sdk \
//   /Library/Developer/CommandLineTools/usr/bin/swift build -c release
// Deploy: cp .build/release/fm-agent ~/.omlx/fm_agent_server (Plist-Pfad bleibt)
import PackageDescription

let package = Package(
    name: "fm-agent",
    platforms: [.macOS("27.0")],
    dependencies: [
        .package(url: "https://github.com/john-rocky/coreai-kit",
                 revision: "7df23bb1f763b31a4d8d67b4ff55e54e962aa0c8"),
    ],
    targets: [
        .executableTarget(
            name: "fm-agent",
            dependencies: [
                .product(name: "CoreAIKit", package: "coreai-kit"),
            ],
            // Der Server-Code stammt aus der Single-File-swiftc-Ära (v9.426);
            // Swift-5-Modus erspart die strict-concurrency-Umschreibung.
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
        // ZWEITES Binary, EIGENER Prozess (launchd com.brain-agent.speech-stream,
        // :8004): die SpeechAnalyzer-WS-Transkription der Bank-App. Bewusst NICHT
        // in den fm-agent-Prozess gezogen — CoreAI-Runtime-Fehler sind fatale
        // Traps (14.08. live gesehen) und dürfen keine laufende Kundencall-
        // Transkription reißen; fm-agent wird zudem deutlich öfter deployt.
        // EIN Package = ein Build/Deploy/Review-Stand, ZWEI Prozesse = Isolation.
        .executableTarget(
            name: "speech-stream",
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
    ]
)

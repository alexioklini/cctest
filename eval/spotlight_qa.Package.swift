// swift-tools-version: 6.0
// spotlight-qa — Core-Spotlight-RAG-Testharness, ALLES Apple-Standard:
// CoreSpotlight (SpotlightSearchTool) + FoundationModels (LanguageModelSession)
// + apple/coreai-models (CoreAILanguageModel — Apples eigener FoundationModels-
// Adapter für CoreAI-Bundles, BSD-3). Lokale Pfad-Abhängigkeit auf die bereits
// beta-drift-gepatchte Kopie aus dem fm-agent-Build (gepinnter Apple-Fork).
// Build (M4): SDKROOT=/Library/Developer/CommandLineTools/SDKs/MacOSX27.sdk \
//   /Library/Developer/CommandLineTools/usr/bin/swift build -c release
import PackageDescription

let package = Package(
    name: "spotlight-qa",
    platforms: [.macOS("27.0")],
    dependencies: [
        .package(path: "../fm-agent/Packages/coreai-models"),
        // Offizieller MLX-Adapter (ml-explore): MLXLanguageModel conforming
        // to FoundationModels.LanguageModel.
        .package(url: "https://github.com/ml-explore/mlx-swift-lm",
                 branch: "main"),
    ],
    targets: [
        .executableTarget(
            name: "spotlight-qa",
            dependencies: [
                .product(name: "CoreAILM", package: "coreai-models"),
                .product(name: "MLXFoundationModels", package: "mlx-swift-lm"),
                .product(name: "MLXLLM", package: "mlx-swift-lm"),
                .product(name: "MLXLMCommon", package: "mlx-swift-lm"),
                .product(name: "MLXHuggingFace", package: "mlx-swift-lm"),
            ],
            swiftSettings: [.swiftLanguageMode(.v5)]
        ),
    ]
)

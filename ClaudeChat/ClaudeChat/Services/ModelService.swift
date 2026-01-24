import Foundation

actor ModelService {
    static let shared = ModelService()

    private let session: URLSession
    private let decoder = JSONDecoder()

    // Cache for available models
    private var cachedModels: [String] = []
    private var lastFetchTime: Date?
    private let cacheExpirationInterval: TimeInterval = 300 // 5 minutes

    private init() {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 30
        self.session = URLSession(configuration: config)
    }

    func fetchAvailableModels(settings: AppSettings, forceRefresh: Bool = false) async -> [String] {
        // Return cached if valid
        if !forceRefresh,
           let lastFetch = lastFetchTime,
           Date().timeIntervalSince(lastFetch) < cacheExpirationInterval,
           !cachedModels.isEmpty {
            return cachedModels
        }

        guard let url = URL(string: "\(settings.baseURL)/models") else {
            return cachedModels
        }

        guard !settings.apiKey.isEmpty else {
            return cachedModels
        }

        var request = URLRequest(url: url)
        request.httpMethod = "GET"
        request.setValue(settings.apiKey, forHTTPHeaderField: "x-api-key")
        request.setValue("2023-06-01", forHTTPHeaderField: "anthropic-version")

        do {
            let (data, response) = try await session.data(for: request)

            guard let httpResponse = response as? HTTPURLResponse,
                  (200...299).contains(httpResponse.statusCode) else {
                return cachedModels
            }

            let modelsResponse = try decoder.decode(ModelsResponse.self, from: data)
            cachedModels = modelsResponse.data.map { $0.id }
            lastFetchTime = Date()

            return cachedModels
        } catch {
            print("Error fetching models: \(error)")
            return cachedModels
        }
    }

    func findFallbackModel(
        excluding model: String,
        settings: AppSettings
    ) async -> String? {
        let models = await fetchAvailableModels(settings: settings, forceRefresh: true)

        // Prefer claude models in this order
        let preferredPrefixes = [
            "claude-opus",
            "claude-sonnet",
            "claude-3",
            "claude"
        ]

        for prefix in preferredPrefixes {
            if let match = models.first(where: { $0.hasPrefix(prefix) && $0 != model }) {
                return match
            }
        }

        // Return any model that isn't the excluded one
        return models.first { $0 != model }
    }

    func clearCache() {
        cachedModels = []
        lastFetchTime = nil
    }
}

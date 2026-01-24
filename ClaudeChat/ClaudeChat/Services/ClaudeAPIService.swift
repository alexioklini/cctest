import Foundation

enum APIServiceError: LocalizedError {
    case invalidURL
    case noAPIKey
    case invalidResponse
    case httpError(Int, String)
    case modelNotAvailable
    case decodingError(Error)
    case pdfTooLarge(Int)
    case invalidPDF

    var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "Invalid API URL"
        case .noAPIKey:
            return "No API key configured"
        case .invalidResponse:
            return "Invalid response from server"
        case .httpError(let code, let message):
            return "HTTP \(code): \(message)"
        case .modelNotAvailable:
            return "Model not available"
        case .decodingError(let error):
            return "Decoding error: \(error.localizedDescription)"
        case .pdfTooLarge(let sizeMB):
            return "PDF too large (\(sizeMB)MB). Maximum size is 32MB."
        case .invalidPDF:
            return "Invalid PDF file"
        }
    }

    /// Maximum PDF size in bytes (32MB Anthropic limit)
    static let maxPDFSize = 32 * 1024 * 1024
}

actor ClaudeAPIService {
    static let shared = ClaudeAPIService()

    private let session: URLSession
    private let decoder = JSONDecoder()
    private let encoder = JSONEncoder()

    private init() {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 120
        config.timeoutIntervalForResource = 300
        self.session = URLSession(configuration: config)
    }

    // MARK: - Streaming Messages

    func sendMessageStream(
        messages: [Message],
        model: String,
        settings: AppSettings
    ) -> AsyncThrowingStream<String, Error> {
        AsyncThrowingStream { continuation in
            Task {
                do {
                    // Validate PDF sizes before sending
                    for message in messages {
                        for attachment in message.attachments {
                            if attachment.data.count > APIServiceError.maxPDFSize {
                                let sizeMB = attachment.data.count / (1024 * 1024)
                                throw APIServiceError.pdfTooLarge(sizeMB)
                            }
                        }
                    }

                    guard let url = URL(string: "\(settings.baseURL)/messages") else {
                        throw APIServiceError.invalidURL
                    }

                    guard !settings.apiKey.isEmpty else {
                        throw APIServiceError.noAPIKey
                    }

                    var request = URLRequest(url: url)
                    request.httpMethod = "POST"
                    request.setValue("application/json", forHTTPHeaderField: "Content-Type")
                    request.setValue(settings.apiKey, forHTTPHeaderField: "x-api-key")
                    request.setValue("2023-06-01", forHTTPHeaderField: "anthropic-version")

                    // Convert Message objects to API format
                    let apiMessages = messages.map { $0.toAPIMessage(extractPDFText: settings.extractPDFText) }

                    let messagesRequest = MessagesRequest(
                        model: model,
                        maxTokens: settings.maxTokens,
                        messages: apiMessages,
                        stream: true
                    )

                    request.httpBody = try self.encoder.encode(messagesRequest)

                    let (bytes, response) = try await self.session.bytes(for: request)

                    guard let httpResponse = response as? HTTPURLResponse else {
                        throw APIServiceError.invalidResponse
                    }

                    if httpResponse.statusCode == 400 {
                        throw APIServiceError.modelNotAvailable
                    }

                    guard (200...299).contains(httpResponse.statusCode) else {
                        throw APIServiceError.httpError(httpResponse.statusCode, "Request failed")
                    }

                    var currentEvent: String?

                    for try await line in bytes.lines {
                        if line.hasPrefix("event: ") {
                            currentEvent = String(line.dropFirst(7))
                        } else if line.hasPrefix("data: ") {
                            if currentEvent == "message_stop" {
                                break
                            }

                            let jsonString = String(line.dropFirst(6))
                            guard let jsonData = jsonString.data(using: .utf8) else { continue }

                            do {
                                let event = try self.decoder.decode(StreamEvent.self, from: jsonData)

                                if event.type == "content_block_delta",
                                   let delta = event.delta,
                                   delta.type == "text_delta",
                                   let text = delta.text {
                                    continuation.yield(text)
                                }
                            } catch {
                                // Skip malformed events
                                continue
                            }
                        }
                    }

                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
        }
    }

    // MARK: - Non-Streaming (for simple requests)

    func sendMessage(
        content: String,
        model: String,
        settings: AppSettings
    ) async throws -> String {
        guard let url = URL(string: "\(settings.baseURL)/messages") else {
            throw APIServiceError.invalidURL
        }

        guard !settings.apiKey.isEmpty else {
            throw APIServiceError.noAPIKey
        }

        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue(settings.apiKey, forHTTPHeaderField: "x-api-key")
        request.setValue("2023-06-01", forHTTPHeaderField: "anthropic-version")

        let messagesRequest = MessagesRequest(
            model: model,
            maxTokens: settings.maxTokens,
            messages: [APIMessage(role: "user", content: .text(content))],
            stream: false
        )

        request.httpBody = try encoder.encode(messagesRequest)

        let (data, response) = try await session.data(for: request)

        guard let httpResponse = response as? HTTPURLResponse else {
            throw APIServiceError.invalidResponse
        }

        if httpResponse.statusCode == 400 {
            throw APIServiceError.modelNotAvailable
        }

        guard (200...299).contains(httpResponse.statusCode) else {
            let errorMessage = String(data: data, encoding: .utf8) ?? "Unknown error"
            throw APIServiceError.httpError(httpResponse.statusCode, errorMessage)
        }

        // Parse non-streaming response
        struct NonStreamResponse: Decodable {
            let content: [ContentItem]
            struct ContentItem: Decodable {
                let type: String
                let text: String?
            }
        }

        let responseObj = try decoder.decode(NonStreamResponse.self, from: data)
        return responseObj.content.compactMap { $0.text }.joined()
    }
}

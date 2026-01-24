import Foundation

// MARK: - Request Models

struct MessagesRequest: Encodable {
    let model: String
    let maxTokens: Int
    let messages: [APIMessage]
    let stream: Bool

    enum CodingKeys: String, CodingKey {
        case model
        case maxTokens = "max_tokens"
        case messages
        case stream
    }
}

struct APIMessage: Codable {
    let role: String
    let content: String
}

// MARK: - Response Models

struct ModelsResponse: Decodable {
    let data: [ModelInfo]
}

struct ModelInfo: Decodable, Identifiable {
    let id: String
    let displayName: String?
    let createdAt: Int?

    enum CodingKeys: String, CodingKey {
        case id
        case displayName = "display_name"
        case createdAt = "created_at"
    }
}

// MARK: - Streaming Event Models

struct StreamEvent: Decodable {
    let type: String
    let index: Int?
    let delta: ContentDelta?
    let contentBlock: ContentBlock?
    let message: StreamMessage?

    enum CodingKeys: String, CodingKey {
        case type
        case index
        case delta
        case contentBlock = "content_block"
        case message
    }
}

struct ContentDelta: Decodable {
    let type: String?
    let text: String?
}

struct ContentBlock: Decodable {
    let type: String
    let text: String?
}

struct StreamMessage: Decodable {
    let id: String
    let type: String
    let role: String
    let model: String
}

// MARK: - Error Models

struct APIError: Decodable {
    let type: String
    let error: ErrorDetails
}

struct ErrorDetails: Decodable {
    let type: String
    let message: String
}

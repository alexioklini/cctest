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

struct APIMessage: Encodable {
    let role: String
    let content: MessageContent
}

// MARK: - Multimodal Content Types

/// Represents message content that can be either a simple string or an array of content items
enum MessageContent: Encodable {
    case text(String)
    case multimodal([ContentItem])

    func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .text(let string):
            try container.encode(string)
        case .multimodal(let items):
            try container.encode(items)
        }
    }
}

/// A content item within a multimodal message
enum ContentItem: Encodable {
    case text(String)
    case document(DocumentContent)

    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        switch self {
        case .text(let text):
            try container.encode("text", forKey: .type)
            try container.encode(text, forKey: .text)
        case .document(let doc):
            try container.encode("document", forKey: .type)
            try container.encode(doc.source, forKey: .source)
        }
    }

    private enum CodingKeys: String, CodingKey {
        case type
        case text
        case source
    }
}

/// Document content for PDF attachments
struct DocumentContent {
    let source: DocumentSource
}

/// Source of a document (base64-encoded)
struct DocumentSource: Encodable {
    let type: String = "base64"
    let mediaType: String
    let data: String

    enum CodingKeys: String, CodingKey {
        case type
        case mediaType = "media_type"
        case data
    }
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

import Foundation

enum MessageRole: String, Codable {
    case user
    case assistant
}

// MARK: - PDF Attachment

struct PDFAttachment: Identifiable, Equatable {
    let id: UUID
    let filename: String
    let data: Data
    let pageCount: Int

    init(id: UUID = UUID(), filename: String, data: Data, pageCount: Int) {
        self.id = id
        self.filename = filename
        self.data = data
        self.pageCount = pageCount
    }

    var formattedFileSize: String {
        let bytes = Double(data.count)
        if bytes < 1024 {
            return "\(Int(bytes)) B"
        } else if bytes < 1024 * 1024 {
            return String(format: "%.1f KB", bytes / 1024)
        } else {
            return String(format: "%.1f MB", bytes / (1024 * 1024))
        }
    }
}

// MARK: - Message

struct Message: Identifiable, Equatable {
    let id: UUID
    let role: MessageRole
    var content: String
    let timestamp: Date
    var isStreaming: Bool
    let attachments: [PDFAttachment]

    init(
        id: UUID = UUID(),
        role: MessageRole,
        content: String,
        timestamp: Date = Date(),
        isStreaming: Bool = false,
        attachments: [PDFAttachment] = []
    ) {
        self.id = id
        self.role = role
        self.content = content
        self.timestamp = timestamp
        self.isStreaming = isStreaming
        self.attachments = attachments
    }

    static func userMessage(_ content: String, attachments: [PDFAttachment] = []) -> Message {
        Message(role: .user, content: content, attachments: attachments)
    }

    static func assistantMessage(_ content: String = "", isStreaming: Bool = false) -> Message {
        Message(role: .assistant, content: content, isStreaming: isStreaming)
    }

    /// Converts this message to an API message format for the Anthropic API
    func toAPIMessage() -> APIMessage {
        let apiRole = role == .user ? "user" : "assistant"

        // If there are no attachments, use simple text content
        if attachments.isEmpty {
            return APIMessage(role: apiRole, content: .text(content))
        }

        // Build multimodal content with documents first, then text
        var items: [ContentItem] = []

        for attachment in attachments {
            let base64Data = attachment.data.base64EncodedString()
            let source = DocumentSource(mediaType: "application/pdf", data: base64Data)
            let doc = DocumentContent(source: source)
            items.append(.document(doc))
        }

        // Add text content if not empty
        let trimmedContent = content.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmedContent.isEmpty {
            items.append(.text(trimmedContent))
        } else {
            // If no text provided with PDF, add a default prompt
            items.append(.text("Please analyze this document."))
        }

        return APIMessage(role: apiRole, content: .multimodal(items))
    }
}

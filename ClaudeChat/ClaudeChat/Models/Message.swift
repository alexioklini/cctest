import Foundation

enum MessageRole: String, Codable {
    case user
    case assistant
}

struct Message: Identifiable, Equatable {
    let id: UUID
    let role: MessageRole
    var content: String
    let timestamp: Date
    var isStreaming: Bool

    init(
        id: UUID = UUID(),
        role: MessageRole,
        content: String,
        timestamp: Date = Date(),
        isStreaming: Bool = false
    ) {
        self.id = id
        self.role = role
        self.content = content
        self.timestamp = timestamp
        self.isStreaming = isStreaming
    }

    static func userMessage(_ content: String) -> Message {
        Message(role: .user, content: content)
    }

    static func assistantMessage(_ content: String = "", isStreaming: Bool = false) -> Message {
        Message(role: .assistant, content: content, isStreaming: isStreaming)
    }
}

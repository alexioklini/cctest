import Foundation
import PDFKit

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

    /// Extracts text content from the PDF using PDFKit
    func extractText() -> String {
        guard let pdfDocument = PDFDocument(data: data) else {
            return "[PDF konnte nicht gelesen werden]"
        }

        var extractedText = ""
        for pageIndex in 0..<pdfDocument.pageCount {
            if let page = pdfDocument.page(at: pageIndex) {
                if let pageText = page.string {
                    if !extractedText.isEmpty {
                        extractedText += "\n\n"
                    }
                    extractedText += "--- Seite \(pageIndex + 1) ---\n"
                    extractedText += pageText
                }
            }
        }

        if extractedText.isEmpty {
            return "[Kein Text im PDF gefunden - möglicherweise gescanntes Dokument]"
        }

        return extractedText
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
    /// - Parameter extractPDFText: If true, extracts text from PDFs instead of sending as base64 documents
    func toAPIMessage(extractPDFText: Bool = false) -> APIMessage {
        let apiRole = role == .user ? "user" : "assistant"

        // If there are no attachments, use simple text content
        if attachments.isEmpty {
            return APIMessage(role: apiRole, content: .text(content))
        }

        // Text extraction mode: Convert PDFs to text and prepend to message
        if extractPDFText {
            var fullText = ""

            for attachment in attachments {
                let extractedText = attachment.extractText()
                fullText += "=== Dokument: \(attachment.filename) (\(attachment.pageCount) Seiten) ===\n"
                fullText += extractedText
                fullText += "\n\n"
            }

            // Add user's message text
            let trimmedContent = content.trimmingCharacters(in: .whitespacesAndNewlines)
            if !trimmedContent.isEmpty {
                fullText += "=== Anfrage ===\n"
                fullText += trimmedContent
            } else {
                fullText += "=== Anfrage ===\n"
                fullText += "Bitte analysiere dieses Dokument."
            }

            return APIMessage(role: apiRole, content: .text(fullText))
        }

        // Native PDF mode: Send as base64 documents (Anthropic format)
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

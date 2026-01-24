import SwiftUI

struct MessageBubble: View {
    let message: Message

    private var isUser: Bool {
        message.role == .user
    }

    private var backgroundColor: Color {
        isUser ? Color.accentColor : Color(nsColor: .controlBackgroundColor)
    }

    private var textColor: Color {
        isUser ? .white : .primary
    }

    var body: some View {
        HStack {
            if isUser { Spacer(minLength: 60) }

            VStack(alignment: isUser ? .trailing : .leading, spacing: 4) {
                // PDF attachment badges (for user messages)
                if isUser && !message.attachments.isEmpty {
                    ForEach(message.attachments) { attachment in
                        PDFAttachmentBadge(attachment: attachment)
                    }
                }

                // Message content
                if !message.content.isEmpty {
                    Text(message.content)
                        .textSelection(.enabled)
                        .foregroundColor(textColor)
                        .padding(.horizontal, 12)
                        .padding(.vertical, 8)
                        .background(backgroundColor)
                        .clipShape(RoundedRectangle(cornerRadius: 16))
                }

                if message.isStreaming {
                    HStack(spacing: 4) {
                        ProgressView()
                            .scaleEffect(0.5)
                            .frame(width: 12, height: 12)
                        Text("Generating...")
                            .font(.caption2)
                            .foregroundColor(.secondary)
                    }
                    .padding(.horizontal, 4)
                }
            }

            if !isUser { Spacer(minLength: 60) }
        }
        .padding(.horizontal)
        .padding(.vertical, 4)
    }
}

// MARK: - PDF Attachment Badge

struct PDFAttachmentBadge: View {
    let attachment: PDFAttachment

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: "doc.fill")
                .foregroundColor(.white.opacity(0.9))
                .font(.caption)

            VStack(alignment: .leading, spacing: 1) {
                Text(attachment.filename)
                    .font(.caption)
                    .fontWeight(.medium)
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .foregroundColor(.white)

                Text("\(attachment.formattedFileSize) • \(attachment.pageCount) page\(attachment.pageCount == 1 ? "" : "s")")
                    .font(.caption2)
                    .foregroundColor(.white.opacity(0.8))
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(Color.accentColor.opacity(0.8))
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }
}

struct MessageBubble_Previews: PreviewProvider {
    static var previews: some View {
        VStack {
            MessageBubble(message: .userMessage("Hello, how are you?"))
            MessageBubble(message: .assistantMessage("I'm doing well, thank you for asking! How can I help you today?"))
            MessageBubble(message: .assistantMessage("Thinking...", isStreaming: true))

            // Message with PDF attachment
            MessageBubble(message: Message(
                role: .user,
                content: "What's in this document?",
                attachments: [
                    PDFAttachment(
                        filename: "example.pdf",
                        data: Data(),
                        pageCount: 5
                    )
                ]
            ))
        }
        .frame(width: 400)
        .padding()
    }
}

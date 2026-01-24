import SwiftUI

struct InputBar: View {
    @Binding var text: String
    @Binding var pendingAttachments: [PDFAttachment]
    let isLoading: Bool
    let onSend: () -> Void
    let onCancel: () -> Void
    let onAttachTapped: () -> Void
    let onRemoveAttachment: (PDFAttachment) -> Void

    @FocusState private var isFocused: Bool

    private var canSend: Bool {
        let hasText = !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        let hasAttachments = !pendingAttachments.isEmpty
        return (hasText || hasAttachments) && !isLoading
    }

    var body: some View {
        VStack(spacing: 0) {
            // Attachment strip
            if !pendingAttachments.isEmpty {
                attachmentStrip
            }

            // Input row
            HStack(alignment: .bottom, spacing: 12) {
                // Paperclip button
                Button(action: onAttachTapped) {
                    Image(systemName: "paperclip")
                        .font(.title2)
                        .foregroundColor(.secondary)
                }
                .buttonStyle(.plain)
                .help("Attach PDF")
                .disabled(isLoading)

                TextEditor(text: $text)
                    .font(.body)
                    .frame(minHeight: 36, maxHeight: 120)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 4)
                    .background(Color(nsColor: .textBackgroundColor))
                    .clipShape(RoundedRectangle(cornerRadius: 8))
                    .overlay(
                        RoundedRectangle(cornerRadius: 8)
                            .stroke(Color(nsColor: .separatorColor), lineWidth: 1)
                    )
                    .focused($isFocused)
                    .onSubmit {
                        if NSEvent.modifierFlags.contains(.command) {
                            onSend()
                        }
                    }

                if isLoading {
                    Button(action: onCancel) {
                        Image(systemName: "stop.circle.fill")
                            .font(.title2)
                            .foregroundColor(.red)
                    }
                    .buttonStyle(.plain)
                    .help("Stop generating")
                } else {
                    Button(action: onSend) {
                        Image(systemName: "paperplane.fill")
                            .font(.title2)
                            .foregroundColor(canSend ? .accentColor : .secondary)
                    }
                    .buttonStyle(.plain)
                    .disabled(!canSend)
                    .keyboardShortcut(.return, modifiers: .command)
                    .help("Send message (⌘+Return)")
                }
            }
            .padding()
        }
        .background(Color(nsColor: .windowBackgroundColor))
    }

    private var attachmentStrip: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(pendingAttachments) { attachment in
                    AttachmentChip(
                        attachment: attachment,
                        onRemove: { onRemoveAttachment(attachment) }
                    )
                }
            }
            .padding(.horizontal)
            .padding(.vertical, 8)
        }
        .background(Color(nsColor: .controlBackgroundColor))
    }
}

// MARK: - Attachment Chip

struct AttachmentChip: View {
    let attachment: PDFAttachment
    let onRemove: () -> Void

    var body: some View {
        HStack(spacing: 6) {
            Image(systemName: "doc.fill")
                .foregroundColor(.red)
                .font(.caption)

            VStack(alignment: .leading, spacing: 1) {
                Text(attachment.filename)
                    .font(.caption)
                    .lineLimit(1)
                    .truncationMode(.middle)

                Text("\(attachment.formattedFileSize) • \(attachment.pageCount) page\(attachment.pageCount == 1 ? "" : "s")")
                    .font(.caption2)
                    .foregroundColor(.secondary)
            }

            Button(action: onRemove) {
                Image(systemName: "xmark.circle.fill")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(Color(nsColor: .textBackgroundColor))
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay(
            RoundedRectangle(cornerRadius: 8)
                .stroke(Color(nsColor: .separatorColor), lineWidth: 1)
        )
    }
}

struct InputBar_Previews: PreviewProvider {
    static var previews: some View {
        VStack {
            InputBar(
                text: .constant("Hello"),
                pendingAttachments: .constant([]),
                isLoading: false,
                onSend: {},
                onCancel: {},
                onAttachTapped: {},
                onRemoveAttachment: { _ in }
            )

            InputBar(
                text: .constant("Generating..."),
                pendingAttachments: .constant([]),
                isLoading: true,
                onSend: {},
                onCancel: {},
                onAttachTapped: {},
                onRemoveAttachment: { _ in }
            )
        }
        .frame(width: 400)
    }
}

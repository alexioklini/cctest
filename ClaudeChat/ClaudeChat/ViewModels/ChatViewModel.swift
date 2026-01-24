import Foundation
import SwiftUI
import PDFKit

@MainActor
class ChatViewModel: ObservableObject {
    @Published var messages: [Message] = []
    @Published var currentInput: String = ""
    @Published var isLoading: Bool = false
    @Published var error: String?
    @Published var availableModels: [String] = []
    @Published var showSettings: Bool = false
    @Published var usedFallbackModel: String?
    @Published var pendingAttachments: [PDFAttachment] = []
    @Published var showFileImporter: Bool = false

    let settings: AppSettings

    private var currentStreamingMessageId: UUID?
    private var streamTask: Task<Void, Never>?

    init(settings: AppSettings = .shared) {
        self.settings = settings

        // Show settings on first launch if no API key
        if !settings.hasAPIKey {
            showSettings = true
        }
    }

    // MARK: - Public Methods

    func sendMessage() {
        let content = currentInput.trimmingCharacters(in: .whitespacesAndNewlines)
        let hasAttachments = !pendingAttachments.isEmpty

        // Allow sending if there's text OR attachments
        guard !content.isEmpty || hasAttachments else { return }
        guard !isLoading else { return }

        // Capture and clear input immediately
        let attachments = pendingAttachments
        currentInput = ""
        pendingAttachments = []
        error = nil
        usedFallbackModel = nil

        // Add user message with attachments
        let userMessage = Message.userMessage(content, attachments: attachments)
        messages.append(userMessage)

        // Start streaming response
        sendWithFallback(userMessage: userMessage, model: settings.selectedModel)
    }

    // MARK: - PDF Attachment Methods

    func addPDFAttachment(from url: URL) {
        // Start security-scoped access
        guard url.startAccessingSecurityScopedResource() else {
            error = "Cannot access file"
            return
        }
        defer { url.stopAccessingSecurityScopedResource() }

        do {
            let data = try Data(contentsOf: url)

            // Validate PDF magic bytes (%PDF)
            guard data.count >= 4,
                  let header = String(data: data.prefix(4), encoding: .ascii),
                  header == "%PDF" else {
                error = "Invalid PDF file"
                return
            }

            // Check file size
            if data.count > APIServiceError.maxPDFSize {
                let sizeMB = data.count / (1024 * 1024)
                error = "PDF too large (\(sizeMB)MB). Maximum size is 32MB."
                return
            }

            // Get page count using PDFKit
            let pageCount: Int
            if let pdfDocument = PDFDocument(data: data) {
                pageCount = pdfDocument.pageCount
            } else {
                pageCount = 0
            }

            let filename = url.lastPathComponent
            let attachment = PDFAttachment(
                filename: filename,
                data: data,
                pageCount: pageCount
            )
            pendingAttachments.append(attachment)
        } catch {
            self.error = "Failed to read PDF: \(error.localizedDescription)"
        }
    }

    func removePendingAttachment(_ attachment: PDFAttachment) {
        pendingAttachments.removeAll { $0.id == attachment.id }
    }

    func clearChat() {
        cancelStream()
        messages = []
        error = nil
        usedFallbackModel = nil
    }

    func cancelStream() {
        streamTask?.cancel()
        streamTask = nil

        if let id = currentStreamingMessageId,
           let index = messages.firstIndex(where: { $0.id == id }) {
            messages[index].isStreaming = false
        }
        currentStreamingMessageId = nil
        isLoading = false
    }

    func refreshModels() async {
        let models = await ModelService.shared.fetchAvailableModels(
            settings: settings,
            forceRefresh: true
        )
        availableModels = models
    }

    // MARK: - Private Methods

    private func sendWithFallback(userMessage: Message, model: String, isRetry: Bool = false) {
        isLoading = true

        // Create assistant message placeholder
        let assistantMessage = Message.assistantMessage(isStreaming: true)
        currentStreamingMessageId = assistantMessage.id
        messages.append(assistantMessage)

        streamTask = Task {
            do {
                // Build conversation history (all messages except the streaming assistant message)
                let conversationMessages = messages.filter { $0.id != assistantMessage.id }

                let stream = await ClaudeAPIService.shared.sendMessageStream(
                    messages: conversationMessages,
                    model: model,
                    settings: settings
                )

                for try await text in stream {
                    if Task.isCancelled { break }

                    if let index = messages.firstIndex(where: { $0.id == currentStreamingMessageId }) {
                        messages[index].content += text
                    }
                }

                // Mark as complete
                if let index = messages.firstIndex(where: { $0.id == currentStreamingMessageId }) {
                    messages[index].isStreaming = false
                }

            } catch let apiError as APIServiceError {
                await handleAPIError(apiError, userMessage: userMessage, attemptedModel: model, isRetry: isRetry)
            } catch {
                await handleError(error)
            }

            currentStreamingMessageId = nil
            isLoading = false
        }
    }

    private func handleAPIError(
        _ error: APIServiceError,
        userMessage: Message,
        attemptedModel: String,
        isRetry: Bool
    ) async {
        // If model not available and not already a retry, try fallback
        if case .modelNotAvailable = error, !isRetry {
            // Remove the failed assistant message
            if let id = currentStreamingMessageId {
                messages.removeAll { $0.id == id }
            }

            // Try to find a fallback model
            if let fallbackModel = await ModelService.shared.findFallbackModel(
                excluding: attemptedModel,
                settings: settings
            ) {
                usedFallbackModel = fallbackModel
                sendWithFallback(userMessage: userMessage, model: fallbackModel, isRetry: true)
                return
            }
        }

        await handleError(error)
    }

    private func handleError(_ error: Error) {
        self.error = error.localizedDescription

        // Remove incomplete assistant message
        if let id = currentStreamingMessageId {
            messages.removeAll { $0.id == id }
        }
    }
}

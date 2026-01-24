import Foundation
import SwiftUI

@MainActor
class ChatViewModel: ObservableObject {
    @Published var messages: [Message] = []
    @Published var currentInput: String = ""
    @Published var isLoading: Bool = false
    @Published var error: String?
    @Published var availableModels: [String] = []
    @Published var showSettings: Bool = false
    @Published var usedFallbackModel: String?

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
        guard !content.isEmpty else { return }
        guard !isLoading else { return }

        // Clear input immediately
        currentInput = ""
        error = nil
        usedFallbackModel = nil

        // Add user message
        let userMessage = Message.userMessage(content)
        messages.append(userMessage)

        // Start streaming response
        sendWithFallback(content: content, model: settings.selectedModel)
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

    private func sendWithFallback(content: String, model: String, isRetry: Bool = false) {
        isLoading = true

        // Create assistant message placeholder
        let assistantMessage = Message.assistantMessage(isStreaming: true)
        currentStreamingMessageId = assistantMessage.id
        messages.append(assistantMessage)

        streamTask = Task {
            do {
                let stream = await ClaudeAPIService.shared.sendMessageStream(
                    content: content,
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
                await handleAPIError(apiError, content: content, attemptedModel: model, isRetry: isRetry)
            } catch {
                await handleError(error)
            }

            currentStreamingMessageId = nil
            isLoading = false
        }
    }

    private func handleAPIError(
        _ error: APIServiceError,
        content: String,
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
                sendWithFallback(content: content, model: fallbackModel, isRetry: true)
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

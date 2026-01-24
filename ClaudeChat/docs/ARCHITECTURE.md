# Architecture

## Overview

ClaudeChat follows the **Model-View-ViewModel (MVVM)** architectural pattern, leveraging SwiftUI's reactive data binding and Swift's modern concurrency features.

```
┌─────────────────────────────────────────────────────────────┐
│                        Views (SwiftUI)                       │
│  ContentView → ChatView → MessageBubble                     │
│             → InputBar                                       │
│             → SettingsView                                   │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ @ObservedObject / @StateObject
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                      ViewModel Layer                         │
│                      ChatViewModel                           │
│  - Messages state    - Streaming control                    │
│  - Error handling    - Model fallback logic                 │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ async/await
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                      Service Layer (Actors)                  │
│  ClaudeAPIService          ModelService                     │
│  - HTTP requests           - Model discovery                │
│  - SSE streaming           - Fallback selection             │
│  - Response parsing        - Cache management               │
│                                                              │
│  KeychainService           AppSettings                      │
│  - Secure storage          - UserDefaults wrapper           │
└─────────────────────────────────────────────────────────────┘
```

## Component Details

### Views

#### ContentView
The root view that composes the main interface:
- Toolbar with settings button, model picker, and clear button
- Error/fallback banners (conditional)
- ChatView for message display
- InputBar for text entry

Handles app-level concerns like showing the settings sheet and refreshing models on appear.

#### ChatView
Displays the message list using `ScrollViewReader` and `LazyVStack`:
- Empty state when no messages
- Auto-scrolls to bottom on new messages or content updates
- Uses message ID for scroll targeting

#### MessageBubble
Renders individual messages with role-based styling:
- User messages: accent color background, right-aligned
- Assistant messages: control background color, left-aligned
- Shows streaming indicator during generation

#### InputBar
Text input with send/cancel functionality:
- Multi-line TextEditor with height constraints
- Send button (or stop button when loading)
- Cmd+Return keyboard shortcut

#### SettingsView
Modal configuration dialog:
- Secure API key entry
- Base URL configuration
- Model selection dropdown
- Connection test with result feedback
- Max tokens stepper

### ViewModel

#### ChatViewModel
Central state manager marked with `@MainActor`:

**Published Properties:**
- `messages: [Message]` - Chat history
- `currentInput: String` - Text field binding
- `isLoading: Bool` - Streaming state
- `error: String?` - Error message display
- `availableModels: [String]` - Model list for picker
- `showSettings: Bool` - Settings sheet control
- `usedFallbackModel: String?` - Fallback notification

**Key Methods:**
- `sendMessage()` - Initiates message send with streaming
- `clearChat()` - Resets conversation
- `cancelStream()` - Stops active generation
- `refreshModels()` - Fetches available models

**Fallback Logic:**
When `sendWithFallback()` receives a model unavailable error:
1. Removes the failed assistant message placeholder
2. Queries `ModelService` for alternative model
3. Retries with fallback model (marked as retry to prevent infinite loop)
4. Displays banner showing which model was used

### Services

#### ClaudeAPIService (Actor)
Singleton actor for thread-safe API communication.

**Streaming Implementation:**
```swift
func sendMessageStream(...) -> AsyncThrowingStream<String, Error>
```
1. Constructs POST request to `/messages` endpoint
2. Sets required headers (Content-Type, x-api-key, anthropic-version)
3. Uses `URLSession.bytes(for:)` for streaming
4. Parses Server-Sent Events line by line
5. Yields text deltas via `AsyncThrowingStream` continuation
6. Handles `content_block_delta` events with `text_delta` type

**SSE Event Types:**
- `message_start` - Response begins
- `content_block_start` - Content block begins
- `content_block_delta` - Text chunk (yielded to stream)
- `content_block_stop` - Content block ends
- `message_stop` - Response complete

#### ModelService (Actor)
Manages model discovery and fallback selection.

**Caching:**
- Caches model list for 5 minutes
- Force refresh available via parameter

**Fallback Priority:**
1. `claude-opus-*`
2. `claude-sonnet-*`
3. `claude-3-*`
4. `claude-*`
5. Any available model

#### KeychainService
Wrapper around Security framework for API key storage:
- Service: `com.claudechat.api`
- Account: `apiKey`
- Accessibility: `kSecAttrAccessibleWhenUnlocked`

### Models

#### Message
```swift
struct Message: Identifiable, Equatable {
    let id: UUID
    let role: MessageRole  // .user or .assistant
    var content: String    // Mutable for streaming
    let timestamp: Date
    var isStreaming: Bool
}
```

#### AppSettings
Observable singleton combining:
- `@AppStorage` for UserDefaults (baseURL, selectedModel, maxTokens)
- `@Published` for Keychain-backed apiKey

## Data Flow

### Send Message Flow
```
User types → InputBar.text binding → ChatViewModel.currentInput
     │
User sends (Cmd+Return)
     │
     ▼
ChatViewModel.sendMessage()
     │
     ├── Append user Message to messages[]
     ├── Create assistant Message placeholder (isStreaming: true)
     │
     ▼
ClaudeAPIService.sendMessageStream()
     │
     ▼
AsyncThrowingStream<String, Error>
     │
     ├── for await text in stream
     │       └── Append text to assistant message content
     │
     ▼
Stream complete → isStreaming = false
```

### Model Fallback Flow
```
API returns HTTP 400 (model unavailable)
     │
     ▼
ChatViewModel.handleAPIError()
     │
     ├── Remove failed assistant message
     ├── Call ModelService.findFallbackModel()
     │
     ▼
Found fallback? ──Yes──► sendWithFallback(isRetry: true)
     │                         │
     No                        └── Set usedFallbackModel for banner
     │
     ▼
Display error to user
```

## Concurrency Model

- **Main Actor**: `ChatViewModel` and all UI updates
- **Actor Isolation**: `ClaudeAPIService` and `ModelService` are actors
- **Structured Concurrency**: Tasks tied to view lifecycle
- **Cancellation**: `streamTask` stored for explicit cancellation support

## Error Handling

Errors are categorized in `APIServiceError`:
- `invalidURL` - Malformed base URL
- `noAPIKey` - Missing API key
- `invalidResponse` - Non-HTTP response
- `httpError(Int, String)` - Server errors
- `modelNotAvailable` - HTTP 400, triggers fallback
- `decodingError(Error)` - JSON parsing failures

All errors surface to `ChatViewModel.error` for display in the error banner.

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ClaudeChat is a native macOS SwiftUI application for chatting with the Claude API. It features streaming responses, model fallback when the selected model is unavailable, and secure API key storage via Keychain.

## Building and Running

Open `ClaudeChat.xcodeproj` in Xcode and run with ⌘R. The app requires macOS and uses SwiftUI with no external dependencies.

## Architecture

The app follows MVVM architecture:

- **Models/** - Data types (`Message`, `APIModels`, `AppSettings`)
- **Views/** - SwiftUI views (`ContentView`, `ChatView`, `SettingsView`, `MessageBubble`, `InputBar`)
- **ViewModels/** - `ChatViewModel` manages chat state and coordinates API calls
- **Services/** - Network and storage services (`ClaudeAPIService`, `ModelService`, `KeychainService`)

### Key Patterns

**Actor-based Services**: `ClaudeAPIService` and `ModelService` are Swift actors (singletons) providing thread-safe API access.

**Streaming**: `ClaudeAPIService.sendMessageStream()` returns an `AsyncThrowingStream<String, Error>` that yields text deltas from Server-Sent Events. The ViewModel appends these to the current message in real-time.

**Model Fallback**: When a model returns HTTP 400 (not available), `ChatViewModel` automatically retries with a fallback model from `ModelService.findFallbackModel()`, which prefers claude-opus > claude-sonnet > claude-3 > any available.

**Settings Storage**: User preferences use `@AppStorage` (UserDefaults), but the API key is stored in Keychain via `KeychainService`.

### API Integration

The app uses Anthropic's Messages API (`/messages` endpoint) with:
- Header `x-api-key` for authentication
- Header `anthropic-version: 2023-06-01`
- SSE streaming for responses
- Default base URL: `http://localhost:8317/v1` (configurable in settings)

### Keyboard Shortcuts

- ⌘N: New chat
- ⌘,: Settings
- ⌘Return: Send message

# Project Overview

## Introduction

ClaudeChat is a native macOS desktop application that provides a clean, focused interface for conversing with Claude AI models. Built entirely with SwiftUI, it offers real-time streaming responses and seamless model switching.

## Features

### Core Functionality
- **Real-time Streaming**: Responses appear word-by-word as they're generated, providing immediate feedback
- **Model Selection**: Choose from available Claude models via a dropdown in the toolbar
- **Automatic Model Fallback**: If the selected model is unavailable, the app automatically tries alternative models
- **Conversation Management**: Clear chat history with a single click

### Security
- **Keychain Storage**: API keys are stored securely in macOS Keychain, not in plaintext
- **No Data Persistence**: Chat history is kept in memory only and cleared when the app closes

### User Experience
- **Native macOS Design**: Uses standard macOS UI patterns and system colors
- **Keyboard Shortcuts**: Full keyboard navigation (Cmd+N for new chat, Cmd+Return to send)
- **Connection Testing**: Verify API connectivity before saving settings
- **Error Handling**: Clear error banners with dismissible notifications

## Requirements

- macOS (SwiftUI-based, requires macOS 12.0+)
- Xcode 14.0+ for building
- Claude API access (API key required)
- API server endpoint (default: `http://localhost:8317/v1`)

## Getting Started

1. Open `ClaudeChat.xcodeproj` in Xcode
2. Build and run (Cmd+R)
3. On first launch, the Settings dialog opens automatically
4. Enter your API key and base URL
5. Click "Test Connection" to verify
6. Save and start chatting

## Configuration

### Settings (Cmd+,)
- **API Key**: Your Anthropic API key (stored in Keychain)
- **Base URL**: API endpoint (default: `http://localhost:8317/v1`)
- **Model**: Select from available models
- **Max Tokens**: Maximum response length (256-32768)

## Project Structure

```
ClaudeChat/
├── ClaudeChatApp.swift      # App entry point and menu commands
├── Models/
│   ├── Message.swift        # Chat message model
│   ├── APIModels.swift      # API request/response types
│   └── AppSettings.swift    # User preferences
├── Views/
│   ├── ContentView.swift    # Main window layout
│   ├── ChatView.swift       # Message list
│   ├── MessageBubble.swift  # Individual message display
│   ├── InputBar.swift       # Text input area
│   └── SettingsView.swift   # Settings dialog
├── ViewModels/
│   └── ChatViewModel.swift  # Chat state management
├── Services/
│   ├── ClaudeAPIService.swift  # API communication
│   ├── ModelService.swift      # Model discovery/fallback
│   └── KeychainService.swift   # Secure storage
└── Resources/
    └── Assets.xcassets/     # App icons and colors
```

## Technology Stack

- **Language**: Swift 5
- **UI Framework**: SwiftUI
- **Networking**: URLSession with async/await
- **Storage**: UserDefaults (@AppStorage) + Keychain
- **Dependencies**: None (stdlib only)

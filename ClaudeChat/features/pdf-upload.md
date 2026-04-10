# PDF Upload Feature Implementation Plan

## Overview
Add PDF upload functionality to ClaudeChat, allowing users to attach PDF files and ask the LLM questions about their content.

## Files to Modify

### 1. `ClaudeChat/Models/APIModels.swift`
Add multimodal content types for Anthropic API:
- `ContentItem` enum (text or document cases)
- `DocumentSource` struct (base64, media_type, data)
- `MessageContent` enum wrapper (handles String or [ContentItem])
- Update `APIMessage.content` from `String` to `MessageContent`

### 2. `ClaudeChat/Models/Message.swift`
Add PDF attachment support:
- New `PDFAttachment` struct (id, filename, data, pageCount, formattedFileSize)
- Add `attachments: [PDFAttachment]` to `Message`
- Add `toAPIMessage()` method for conversion

### 3. `ClaudeChat/Services/ClaudeAPIService.swift`
Update API service:
- Add `pdfTooLarge` and `invalidPDF` error cases
- Change `sendMessageStream(content:)` to `sendMessageStream(messages:)`
- Validate PDF size (32MB limit) before sending
- Encode multimodal content correctly

### 4. `ClaudeChat/ViewModels/ChatViewModel.swift`
Add PDF state management:
- `@Published var pendingAttachments: [PDFAttachment]`
- `@Published var showFileImporter: Bool`
- `addPDFAttachment(from: URL)` - read, validate, add to pending
- `removePendingAttachment()` - remove from pending
- Update `sendMessage()` to include attachments
- Add `import PDFKit` for page count extraction

### 5. `ClaudeChat/Views/InputBar.swift`
Add attachment UI:
- Paperclip button to trigger file picker
- Horizontal scroll strip showing pending attachments
- `AttachmentChip` component (filename, size, remove button)
- Update `canSend` to allow PDF-only sends

### 6. `ClaudeChat/Views/MessageBubble.swift`
Display attachments in messages:
- `PDFAttachmentBadge` component showing filename, size, page count
- Render badges above text content for user messages

### 7. `ClaudeChat/Views/ContentView.swift`
Wire up file importer:
- Add `.fileImporter()` modifier for PDF selection
- Pass new bindings to `InputBar`

## Implementation Order
1. APIModels.swift (new types)
2. Message.swift (PDFAttachment + attachments field)
3. ClaudeAPIService.swift (multimodal encoding)
4. ChatViewModel.swift (state + file handling)
5. InputBar.swift (UI + bindings)
6. MessageBubble.swift (attachment display)
7. ContentView.swift (fileImporter wiring)

## Key Design Decisions
- **Store PDF as `Data`**: Security-scoped URLs expire; storing data ensures PDF is available at send time
- **32MB limit**: Anthropic API limit for document uploads
- **Validate PDF magic bytes**: Check `%PDF` header to reject non-PDFs early
- **Allow PDF-only sends**: User can send a PDF without text (e.g., "summarize this")

## Edge Cases
| Case | Handling |
|------|----------|
| PDF > 32MB | Show error with file size |
| Invalid PDF | Check magic bytes, reject early |
| Multiple PDFs | Support via array in attachments |
| PDF-only (no text) | Allow - API accepts document-only content |
| File permission denied | Use security-scoped resource access |
| Password-protected PDF | Let API handle; may return error |

## Verification
1. Build and run the app
2. Click paperclip icon → select a PDF → verify it appears in attachment strip
3. Send message with PDF attached → verify request includes document content
4. Check assistant response references PDF content
5. Test edge cases: large PDF (>32MB shows error), invalid file, multiple PDFs

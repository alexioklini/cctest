# PDF Upload Feature - Design Document

## 1. System Architecture

### 1.1 Component Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         View Layer                               │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────────────┐ │
│  │ ContentView │  │  InputBar    │  │    MessageBubble        │ │
│  │             │  │ +paperclip   │  │    +PDFAttachmentBadge  │ │
│  │ +fileImporter│ │ +AttachmentChip│ │                        │ │
│  └─────────────┘  └──────────────┘  └─────────────────────────┘ │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                       ViewModel Layer                            │
│  ┌─────────────────────────────────────────────────────────────┐│
│  │                    ChatViewModel                             ││
│  │  @Published pendingAttachments: [PDFAttachment]             ││
│  │  @Published showFileImporter: Bool                          ││
│  │  addPDFAttachment(from: URL)                                ││
│  │  removePendingAttachment(_:)                                ││
│  └─────────────────────────────────────────────────────────────┘│
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                       Service Layer                              │
│  ┌──────────────────────────┐  ┌──────────────────────────────┐ │
│  │    ClaudeAPIService      │  │         PDFKit              │ │
│  │    (actor)               │  │    (System Framework)        │ │
│  │  - Multimodal encoding   │  │  - Page count extraction     │ │
│  │  - Size validation       │  │  - PDF validation            │ │
│  └──────────────────────────┘  └──────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                       Model Layer                                │
│  ┌──────────────────┐  ┌──────────────────┐  ┌────────────────┐ │
│  │  PDFAttachment   │  │    Message       │  │   APIMessage   │ │
│  │  - filename      │  │  + attachments   │  │ + MessageContent│ │
│  │  - data          │  │  + toAPIMessage()│  │ + ContentItem  │ │
│  │  - pageCount     │  │                  │  │ + DocumentSource│ │
│  └──────────────────┘  └──────────────────┘  └────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 Data Flow

```
User selects PDF via fileImporter
         │
         ▼
ContentView receives [URL] from .fileImporter result
         │
         ▼
ChatViewModel.addPDFAttachment(from: url)
         │
         ├── Access security-scoped resource
         ├── Read file data
         ├── Validate PDF (magic bytes %PDF)
         ├── Check size limit (≤32MB)
         ├── Extract page count via PDFKit
         │
         ▼
Create PDFAttachment, append to pendingAttachments
         │
         ▼
InputBar displays AttachmentChip in preview strip
         │
         ▼
User clicks Send (or Cmd+Return)
         │
         ▼
ChatViewModel.sendMessage()
         │
         ├── Create Message with attachments
         ├── Clear pendingAttachments
         │
         ▼
Message.toAPIMessage()
         │
         ├── If no attachments → APIMessage with text content
         ├── If attachments → APIMessage with [ContentItem]
         │
         ▼
ClaudeAPIService.sendMessageStream(messages:)
         │
         ├── Encode multimodal request body
         ├── POST to /messages endpoint
         │
         ▼
Stream response back to UI
```

## 2. Data Models

### 2.1 PDFAttachment

```swift
struct PDFAttachment: Identifiable, Equatable {
    let id: UUID
    let filename: String      // Original filename
    let data: Data            // Raw PDF bytes
    let pageCount: Int?       // Extracted via PDFKit

    var base64String: String  // Computed for API
    var fileSize: Int         // data.count
    var formattedFileSize: String  // "1.2 MB"
}
```

**Design Rationale:**
- Store `Data` not `URL` because security-scoped URLs expire
- `pageCount` is optional (may fail to extract for some PDFs)
- Base64 computed on-demand to avoid memory duplication

### 2.2 API Content Types

```swift
enum ContentItem: Codable {
    case text(String)
    case document(DocumentSource)
}

struct DocumentSource: Codable {
    let type: String        // "base64"
    let mediaType: String   // "application/pdf"
    let data: String        // Base64-encoded PDF
}

enum MessageContent: Codable {
    case text(String)
    case multimodal([ContentItem])
}
```

**Design Rationale:**
- `MessageContent` wrapper allows backward-compatible encoding
- Text-only messages encode as plain string (existing behavior)
- Multimodal messages encode as content array (new behavior)

### 2.3 API Request Format

**Text-only (existing):**
```json
{
  "messages": [
    {"role": "user", "content": "Hello"}
  ]
}
```

**With PDF (new):**
```json
{
  "messages": [
    {
      "role": "user",
      "content": [
        {"type": "text", "text": "Summarize this document"},
        {
          "type": "document",
          "source": {
            "type": "base64",
            "media_type": "application/pdf",
            "data": "JVBERi0xLjQK..."
          }
        }
      ]
    }
  ]
}
```

## 3. UI Components

### 3.1 InputBar Modifications

```
┌────────────────────────────────────────────────────────────────┐
│ ┌────────────────────────────────────────────────────────────┐ │
│ │ [doc] report.pdf (2.1 MB) [×]  [doc] data.pdf (500 KB) [×] │ │  ← Attachment strip
│ └────────────────────────────────────────────────────────────┘ │
│ ┌──┐ ┌──────────────────────────────────────────────────┐ ┌──┐│
│ │📎│ │ Ask a question about the PDFs...                 │ │▶ ││  ← Input row
│ └──┘ └──────────────────────────────────────────────────┘ └──┘│
└────────────────────────────────────────────────────────────────┘
```

### 3.2 AttachmentChip Component

```
┌─────────────────────────────────┐
│ [doc]  filename.pdf    [×]     │
│        1.2 MB                   │
└─────────────────────────────────┘
```

- Red document icon
- Filename (truncated if long)
- File size in secondary text
- Remove button (×)

### 3.3 PDFAttachmentBadge (in MessageBubble)

```
┌─────────────────────────────────┐
│ [doc]  document.pdf             │
│        2.5 MB · 12 pages        │
└─────────────────────────────────┘
```

- Appears above text content in user messages
- Shows filename, size, and page count
- Styled to match message bubble (accent for user, neutral for display)

## 4. Validation Rules

| Rule | Limit | Error Message |
|------|-------|---------------|
| File size | ≤ 32MB | "PDF is too large (X MB). Maximum allowed is 32MB." |
| File type | PDF only | "The selected file is not a valid PDF." |
| Magic bytes | %PDF header | "The selected file is not a valid PDF." |

## 5. Security Considerations

### 5.1 File Access
- Use `startAccessingSecurityScopedResource()` when reading files from picker
- Always call `stopAccessingSecurityScopedResource()` in defer block
- Store file data immediately; don't retain URL references

### 5.2 Memory Management
- Large PDFs (up to 32MB) may consume significant memory
- Base64 encoding increases size by ~33%
- Consider clearing pending attachments if app goes to background

### 5.3 Data Handling
- PDF data is stored in memory only (not persisted)
- Data is cleared when message is sent
- No caching of PDF content between sessions

## 6. Error Handling

| Scenario | Handling |
|----------|----------|
| File read failure | Show error banner, don't add attachment |
| Invalid PDF | Show error banner, don't add attachment |
| Size exceeded | Show error banner with size info |
| API error during upload | Standard API error handling applies |
| Network timeout | Existing timeout handling (120s request, 300s resource) |

## 7. Future Considerations

Not in scope for initial implementation, but possible enhancements:
- PDF thumbnail previews
- Drag-and-drop PDF attachment
- Multiple file type support (images, text files)
- PDF text extraction for local preview
- Attachment history/reuse

# Feature Proposal: Multimodal Support (Image Upload & Display)

**Status:** Proposed
**Author:** Brain Agent Team
**Date:** 2026-03-20
**Effort:** ~7 days
**Priority:** High

---

## Problem Statement

Brain Agent currently handles text-only conversations. Claude and other modern LLMs
support vision (image input) and can describe, analyze, and reason about images. Some
providers also support image generation or structured diagram output. But Brain Agent's
chat pipeline — from Web UI to server to engine — has no mechanism to:

1. Accept image uploads from users
2. Encode and transmit images to vision-capable LLM APIs
3. Display images (uploaded or generated) in chat responses
4. Handle image-bearing messages in Telegram (where photo messages are native)

Users cannot share screenshots of errors, architecture diagrams, photos of whiteboards,
or any visual content with their agents. This is a significant gap for a platform that
aims to be a comprehensive AI assistant.

---

## Proposed Solution

Add end-to-end image support across all three frontends (Web UI, TUI, Telegram) and the
server/engine pipeline. Images are transmitted as base64-encoded content blocks in the
existing message format, following the Anthropic messages API content block structure.

### Supported Formats

| Format | MIME Type       | Max Size | Notes                        |
|--------|-----------------|----------|------------------------------|
| JPEG   | image/jpeg      | 20 MB    | Most common, photos          |
| PNG    | image/png       | 20 MB    | Screenshots, diagrams        |
| GIF    | image/gif       | 10 MB    | Animated supported           |
| WebP   | image/webp      | 20 MB    | Modern web format            |
| SVG    | image/svg+xml   | 5 MB     | Vector diagrams (text-based) |

### Context Window Impact

Images consume significant tokens when sent to vision models:

| Resolution       | Approx Tokens | Notes                            |
|------------------|---------------|----------------------------------|
| 100x100          | ~200          | Tiny thumbnail                   |
| 512x512          | ~1,600        | Small image                      |
| 1024x1024        | ~6,400        | Standard screenshot              |
| 2048x2048        | ~25,600       | High-res photo                   |
| 4096x4096        | ~100,000+     | Very large — auto-resize needed  |

Auto-resize images larger than 2048px on the longest edge to keep token usage manageable.
Display a token estimate badge next to the image preview before sending.

### Provider Compatibility

| Provider      | Vision Support | Image Generation | Notes                        |
|---------------|----------------|------------------|------------------------------|
| Anthropic     | Yes (Claude 3+)| No               | Native content blocks        |
| OpenAI        | Yes (GPT-4V+)  | Yes (DALL-E)     | image_url content type       |
| oMLX (local)  | No             | No               | Crow-4B is text-only         |
| CLIProxyAPI   | Yes (proxied)  | No               | Passes through to Claude     |
| MiniMax       | Varies         | No               | Check model capabilities     |

---

## Architecture

```
                         Image Flow
                         ==========

  Web UI                    Server                    LLM API
  ======                    ======                    =======

  User drops/pastes    POST /v1/chat
  image into chat  --> {messages: [{              --> messages API
  |                      role: "user",                with content
  |  [base64 encode]     content: [                   blocks
  |  [resize if >2048]     {type:"image",
  |  [show preview]          source: {
  |                            type: "base64",
  |                            media_type: "image/png",
  |                            data: "iVBOR..."
  |                          }},
  |                        {type:"text",
  |                          text: "What's this?"}
  |                      ]
  |                    }]}
  |
  |  SSE response <--- streaming text response <--- model analyzes
  |  with optional      (may include markdown        image + text
  |  image blocks       image refs or mermaid)
  v
  Display inline
```

### Message Content Format

Currently, messages use a simple string for content:

```json
{"role": "user", "content": "Hello"}
```

With multimodal support, content becomes an array of blocks:

```json
{
  "role": "user",
  "content": [
    {
      "type": "image",
      "source": {
        "type": "base64",
        "media_type": "image/png",
        "data": "iVBORw0KGgoAAAANSUhEUgAA..."
      }
    },
    {
      "type": "text",
      "text": "What's the error in this screenshot?"
    }
  ]
}
```

The engine must handle both formats (string and array) for backward compatibility.

### Storage

Images are NOT stored in SQLite chat history (too large). Instead:

```
agents/main/uploads/
  img_20260320_143052_a7b3.png    <- original (resized if needed)
  img_20260320_143052_a7b3.thumb.png  <- 200px thumbnail for history
```

Messages in chats.db store a reference:

```json
{
  "type": "image",
  "source": {
    "type": "file",
    "path": "uploads/img_20260320_143052_a7b3.png",
    "media_type": "image/png",
    "thumbnail": "uploads/img_20260320_143052_a7b3.thumb.png"
  }
}
```

When sending to the API, the engine reads the file and converts to base64 on the fly.
When loading chat history, the Web UI fetches thumbnails for display.

---

## Web UI Mockups

### Chat Input with Image Attach Button

```
+--------------------------------------------------------------------------+
|  Brain Agent  [main]  claude-opus-4-6            [sun/moon] [settings]   |
+--------------------------------------------------------------------------+
|                                                                          |
|  ... chat messages ...                                                   |
|                                                                          |
+--------------------------------------------------------------------------+
|                                                                          |
|  +--------------------------------------------------------------------+  |
|  |                                                                    |  |
|  |  Type a message...                                          [Send] |  |
|  |                                                                    |  |
|  +--------------------------------------------------------------------+  |
|  [clip]  [image preview area - hidden until image attached]              |
|                                                                          |
+--------------------------------------------------------------------------+

[clip] = paperclip button that opens file picker (accept: image/*)
         Also serves as drag-and-drop indicator
```

### Drag-and-Drop Zone (Active)

```
+--------------------------------------------------------------------------+
|                                                                          |
|  +--------------------------------------------------------------------+  |
|  |                                                                    |  |
|  |              . . . . . . . . . . . . . . . . . .                  |  |
|  |              .                                  .                  |  |
|  |              .    Drop image here to attach     .                  |  |
|  |              .                                  .                  |  |
|  |              .    JPEG, PNG, GIF, WebP, SVG     .                  |  |
|  |              .    Max 20 MB                     .                  |  |
|  |              .                                  .                  |  |
|  |              . . . . . . . . . . . . . . . . . .                  |  |
|  |                                                                    |  |
|  +--------------------------------------------------------------------+  |
|                                                                          |
+--------------------------------------------------------------------------+
```

### Image Preview Before Send

```
+--------------------------------------------------------------------------+
|                                                                          |
|  +--------------------------------------------------------------------+  |
|  |  What's the error in this screenshot?                       [Send] |  |
|  +--------------------------------------------------------------------+  |
|  [clip]                                                                  |
|                                                                          |
|  +------------------+                                                    |
|  | +--------------+ |                                                    |
|  | |  screenshot  | |  screenshot.png                                    |
|  | |   preview    | |  1024 x 768  |  342 KB  |  ~6,400 tokens          |
|  | |  (thumbnail) | |  [x remove]                                       |
|  | +--------------+ |                                                    |
|  +------------------+                                                    |
|                                                                          |
+--------------------------------------------------------------------------+

- Thumbnail preview (max 200px tall)
- Filename, dimensions, file size
- Estimated token cost badge
- [x] button to remove before sending
- Multiple images supported (horizontal scroll)
```

### Chat Message with Uploaded Image

```
+--------------------------------------------------------------------------+
|                                                                          |
|  +-- user -----------------------------------------------+               |
|  |                                                       |               |
|  |  +---------------------+                              |               |
|  |  |                     |                              |               |
|  |  |   [screenshot of    |                              |               |
|  |  |    terminal with    |                              |               |
|  |  |    error message]   |                              |               |
|  |  |                     |                              |               |
|  |  +---------------------+                              |               |
|  |  screenshot.png  1024x768                             |               |
|  |                                                       |               |
|  |  What's the error in this screenshot?                 |               |
|  |                                                       |               |
|  +-------------------------------------------------------+               |
|                                                                          |
|  +-- assistant -------------------------------------------+              |
|  |                                                        |              |
|  |  I can see a Python traceback in your terminal. The    |              |
|  |  error is a `KeyError: 'provider'` on line 142 of     |              |
|  |  server.py. This happens because...                    |              |
|  |                                                        |              |
|  +--------------------------------------------------------+              |
|                                                                          |
+--------------------------------------------------------------------------+

- Image displayed inline above the text, max 400px wide
- Click to view full size in a lightbox overlay
- Images in assistant messages rendered the same way
```

### Assistant Response with Generated Diagram

```
+--------------------------------------------------------------------------+
|                                                                          |
|  +-- user -----------------------------------------------+               |
|  |  Explain the architecture of the scheduler system     |               |
|  +-------------------------------------------------------+               |
|                                                                          |
|  +-- assistant -------------------------------------------+              |
|  |                                                        |              |
|  |  Here's the scheduler architecture:                    |              |
|  |                                                        |              |
|  |  +--------------------------------------------------+  |              |
|  |  |            Mermaid Diagram (rendered)             |  |              |
|  |  |                                                  |  |              |
|  |  |  [scheduler_loop] --> [check_due_tasks]          |  |              |
|  |  |       |                     |                    |  |              |
|  |  |       v                     v                    |  |              |
|  |  |  [sleep 60s]         [spawn_thread]              |  |              |
|  |  |                           |                      |  |              |
|  |  |                           v                      |  |              |
|  |  |                    [execute_task]                 |  |              |
|  |  |                           |                      |  |              |
|  |  |                           v                      |  |              |
|  |  |                    [store_result]                 |  |              |
|  |  |                                                  |  |              |
|  |  +--------------------------------------------------+  |              |
|  |                                                        |              |
|  |  The scheduler runs a continuous loop that checks      |              |
|  |  for due tasks every 60 seconds...                     |              |
|  |                                                        |              |
|  +--------------------------------------------------------+              |
|                                                                          |
+--------------------------------------------------------------------------+

- Mermaid code blocks (```mermaid) rendered as SVG diagrams
- Fallback to code block if rendering fails
- "Copy as code" button on rendered diagrams
```

---

## Telegram Mockups

### User Sends Photo to Bot

```
+--------------------------------------+
|           Brain Agent Bot            |
+--------------------------------------+
|                                      |
|  User sends a photo with caption:    |
|                                      |
|  +----------------------------+      |
|  |                            |      |
|  |   [photo of whiteboard    |      |
|  |    with architecture      |      |
|  |    diagram]               |      |
|  |                            |      |
|  +----------------------------+      |
|  "Can you transcribe this            |
|   whiteboard diagram?"               |
|                                      |
|  --------------------------------    |
|                                      |
|  Bot responds:                       |
|                                      |
|  I can see a system architecture     |
|  diagram on the whiteboard with      |
|  the following components:           |
|                                      |
|  <b>Services:</b>                    |
|  - API Gateway (top)                 |
|  - Auth Service (left)              |
|  - User DB (bottom-left)            |
|  - Cache (right)                     |
|                                      |
|  <b>Connections:</b>                 |
|  - Gateway -> Auth (arrows both     |
|    directions)                       |
|  - Auth -> User DB (one-way)        |
|  - Gateway -> Cache (one-way)       |
|                                      |
+--------------------------------------+
```

### Telegram Implementation

```python
# telegram.py changes

# Handle photo messages (Telegram sends multiple sizes)
if "photo" in update["message"]:
    # Get largest photo size
    photo = update["message"]["photo"][-1]
    file_id = photo["file_id"]

    # Download via Telegram Bot API
    file_info = bot.get_file(file_id)
    image_bytes = bot.download_file(file_info["file_path"])

    # Encode as base64
    b64 = base64.b64encode(image_bytes).decode()

    # Build content blocks
    content = [
        {"type": "image", "source": {"type": "base64",
         "media_type": "image/jpeg", "data": b64}},
    ]
    if caption := update["message"].get("caption"):
        content.append({"type": "text", "text": caption})
    else:
        content.append({"type": "text",
                        "text": "What can you see in this image?"})
```

---

## Workflows

### Workflow 1: Screenshot Analysis

1. User drags `error-screenshot.png` into the Web UI chat input
2. Web UI resizes if >2048px, generates thumbnail, shows preview
3. Token estimate badge shows "~6,400 tokens"
4. User types "What's the error in this screenshot?" and clicks Send
5. Web UI base64-encodes the image, sends POST /v1/chat with content blocks
6. Server passes content blocks to engine
7. Engine sends to Claude API with vision content
8. Claude analyzes the screenshot and responds with error explanation
9. Response streams back via SSE, displayed in chat

### Workflow 2: Architecture Diagram Analysis

1. User pastes (Ctrl+V) an architecture diagram into chat
2. Paste handler detects image data in clipboard, creates preview
3. User types "Explain this architecture" and sends
4. Agent describes all components, connections, and data flows

### Workflow 3: Mermaid Diagram Generation

1. User asks "Draw a flowchart of the scheduler system"
2. Agent generates a Mermaid code block in its response
3. Web UI detects ```mermaid fence, renders as SVG inline
4. User sees interactive diagram (zoom, pan)
5. "Copy code" button lets user grab the Mermaid source

### Workflow 4: Telegram Photo Analysis

1. User sends a photo to the Telegram bot with caption "What plant is this?"
2. Telegram bot downloads the photo via Bot API
3. Bot base64-encodes and builds content blocks
4. Engine sends to vision-capable model
5. Bot responds with plant identification and care tips

---

## Implementation Plan

### Day 1-2: Server & Engine Pipeline

- Modify `/v1/chat` to accept content block arrays in messages
- Update `claude_cli.py` to pass content blocks to provider APIs
- Handle format conversion between Anthropic and OpenAI image formats
- Add image storage under `agents/<name>/uploads/`
- Add thumbnail generation (Pillow or built-in)
- Add `GET /v1/uploads/<path>` endpoint for serving stored images
- Graceful fallback: if model doesn't support vision, extract text-only content

### Day 3-4: Web UI

- Add paperclip button to chat input area
- Implement drag-and-drop with visual drop zone
- Implement paste handler for clipboard images
- Client-side resize for images >2048px (canvas API)
- Image preview with metadata (size, dimensions, token estimate)
- Base64 encoding before send
- Render image blocks in chat messages (user and assistant)
- Lightbox overlay for full-size viewing
- Mermaid diagram rendering (mermaid.js library)

### Day 5: Telegram

- Handle photo messages in update handler
- Download photos via Telegram Bot API
- Base64 encode and build content blocks
- Support photo captions as text content
- Handle document messages (for PNG/SVG sent as files)

### Day 6: Chat History & Storage

- Store image references in chats.db (not raw base64)
- Thumbnail generation for history display
- Load images from file when replaying chat history
- Handle missing images gracefully (show placeholder)
- Context window token counting for images

### Day 7: Testing & Polish

- Test with all supported formats (JPEG, PNG, GIF, WebP, SVG)
- Test with multiple providers (Anthropic, OpenAI, oMLX fallback)
- Test large images (auto-resize)
- Test multiple images in one message
- Error handling: unsupported format, file too large, vision not available
- TUI: display image path/info (terminal can't show images inline)

---

## API Changes

### POST /v1/chat (modified)

```json
{
  "session_id": "abc-123",
  "message": {
    "role": "user",
    "content": [
      {
        "type": "image",
        "source": {
          "type": "base64",
          "media_type": "image/png",
          "data": "iVBORw0KGgoAAAANSUhEUgAA..."
        }
      },
      {
        "type": "text",
        "text": "What's in this image?"
      }
    ]
  }
}
```

Backward compatible: `"content": "plain text"` still works.

### GET /v1/uploads/{agent}/{filename}

Returns stored image file. Used by Web UI to display images in chat history.

### GET /v1/sessions/{id}/messages (modified)

Messages now may contain content block arrays. Client must handle both formats.

---

## Benefits

1. **Unlocks vision capabilities** of Claude, GPT-4V, and future models
2. **Screenshot debugging** — paste error screenshots, get instant analysis
3. **Diagram understanding** — share architecture diagrams, flowcharts, ERDs
4. **Telegram native** — photo messages are the most natural Telegram interaction
5. **Mermaid rendering** — agents can generate visual diagrams in responses
6. **Future-proof** — content block format supports audio/video when models add support

---

## Risks & Mitigations

| Risk                              | Mitigation                                          |
|-----------------------------------|-----------------------------------------------------|
| Large images bloat context window | Auto-resize >2048px, show token estimate            |
| Base64 increases payload size     | Compress before encode, file storage for history     |
| Not all models support vision     | Graceful fallback: strip images, warn user           |
| Storage growth from uploads       | Configurable retention, cleanup old uploads          |
| Slow upload on mobile/tunnel      | Progress indicator, chunked upload for large files   |
| SVG security (XSS)               | Sanitize SVG before display, CSP headers             |

---

## File Changes Summary

| File              | Changes                                                    |
|-------------------|------------------------------------------------------------|
| `server.py`       | Accept content blocks in /v1/chat, serve uploads endpoint  |
| `claude_cli.py`   | Handle content blocks, format per provider, store images   |
| `web/index.html`  | Attach button, drag-drop, paste, preview, image display    |
| `telegram.py`     | Handle photo messages, download, encode                    |
| `tui.py`          | Show image info (path, size) — no inline display           |
| `tools.md`        | Document image support in tool descriptions                |

---

## Open Questions

1. Should we support multi-image messages? (Yes, up to 5 images per message)
2. Should images be included in context compaction? (Strip images first during compaction)
3. Should we support image generation tools (DALL-E)? (Phase 2)
4. Maximum total upload storage per agent? (Configurable, default 500 MB)
5. Should the TUI support kitty/iTerm2 inline image protocol? (Nice-to-have, Phase 2)

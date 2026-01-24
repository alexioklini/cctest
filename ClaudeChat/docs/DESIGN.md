# Design Document

## Design Philosophy

ClaudeChat prioritizes **simplicity** and **native feel**. The app should feel like a natural part of macOS, using system conventions and avoiding unnecessary complexity.

### Guiding Principles

1. **Native First**: Use system colors, fonts, and UI patterns
2. **Minimal Chrome**: Let the conversation be the focus
3. **Immediate Feedback**: Show streaming responses and loading states
4. **Non-Blocking Errors**: Display errors without interrupting flow
5. **Keyboard-Centric**: Support power users with shortcuts

## Visual Design

### Color System

The app uses semantic system colors for automatic light/dark mode support:

| Element | Color |
|---------|-------|
| Window background | `NSColor.windowBackgroundColor` |
| Input field | `NSColor.textBackgroundColor` |
| Input border | `NSColor.separatorColor` |
| User bubble | `Color.accentColor` |
| Assistant bubble | `NSColor.controlBackgroundColor` |
| User text | `.white` |
| Assistant text | `.primary` |
| Error banner | `Color.red.opacity(0.1)` |
| Fallback banner | `Color.blue.opacity(0.1)` |

### Typography

- Message text: System `.body` font
- Streaming indicator: `.caption2`
- Error/banner text: `.caption`
- Empty state title: `.title2`
- Empty state subtitle: `.subheadline`

### Spacing

- Message bubbles: 12px horizontal padding, 8px vertical padding
- Message list: 4px vertical spacing between bubbles
- Toolbar: 8px vertical padding
- Input bar: 16px padding
- Minimum bubble spacing from edge: 60px (creates visual asymmetry)

### Corner Radii

- Message bubbles: 16px (pill-like)
- Input field: 8px
- Buttons: Default system styling

## Layout Structure

```
┌────────────────────────────────────────────────┐
│ [⚙]        [Model Selector ▼]           [🗑]  │  ← Toolbar (gear, picker, trash)
├────────────────────────────────────────────────┤
│ ⚠ Error message                          [×]  │  ← Error banner (conditional)
├────────────────────────────────────────────────┤
│ ℹ Using fallback model: xyz              [×]  │  ← Fallback banner (conditional)
├────────────────────────────────────────────────┤
│                                                │
│                    [Chat area]                 │  ← ScrollView with messages
│                                                │
│     ┌────────────────────────────────┐        │
│     │ User message                   │        │  ← Right-aligned, accent color
│     └────────────────────────────────┘        │
│                                                │
│  ┌────────────────────────────────┐           │
│  │ Assistant message              │           │  ← Left-aligned, neutral color
│  └────────────────────────────────┘           │
│                                                │
├────────────────────────────────────────────────┤
│ ┌──────────────────────────────────────┐ [▶] │  ← Input bar
│ │ Type a message...                    │      │
│ └──────────────────────────────────────┘      │
└────────────────────────────────────────────────┘
```

### Window Constraints

- Minimum size: 500 x 400 points
- Settings sheet: 450 x 350 points (fixed)
- Input field height: 36-120 points (auto-expanding)

## Interaction Design

### Message Input

**Text Entry:**
- Multi-line TextEditor allows natural paragraph entry
- Auto-expands up to 120px, then scrolls
- Focused by default

**Send Action:**
- Primary: Cmd+Return (keyboard shortcut)
- Secondary: Click send button
- Disabled when: empty input or loading

**Cancel Action:**
- Stop button replaces send during generation
- Click or view dismissal cancels stream

### Streaming Feedback

During response generation:
1. Assistant message bubble appears immediately (empty)
2. Text appends character-by-character
3. Streaming indicator shows below bubble:
   - Small progress spinner
   - "Generating..." text
4. Indicator disappears when complete

### Error States

**Error Banner:**
- Red-tinted background
- Warning icon + error message
- Dismiss button (×)
- Auto-dismisses on next successful action

**Fallback Notice:**
- Blue-tinted background
- Info icon + model name
- Dismiss button (×)
- Informational, not an error

### Empty State

When no messages exist:
- Centered vertically
- Chat bubble icon (48pt)
- "Start a conversation" heading
- "Type a message below to begin" subtitle
- Secondary color for all elements

### Settings Dialog

**Connection Testing:**
1. User enters API key and base URL
2. Clicks "Test Connection"
3. Loading spinner appears
4. Result shows:
   - Success: green checkmark + "X models available"
   - Failure: red X + error message
5. On success, model picker populates

**Validation:**
- Test/Refresh buttons disabled without API key
- Save enabled regardless (allows clearing settings)

## Keyboard Navigation

| Shortcut | Action |
|----------|--------|
| ⌘N | Clear chat (new conversation) |
| ⌘, | Open settings |
| ⌘Return | Send message |
| Escape | Close settings dialog |
| Return | Save settings (in dialog) |

## State Transitions

### Chat States

```
Empty ──(send)──► Loading ──(stream complete)──► Has Messages
  ▲                  │                              │
  │                  │(error)                       │
  │                  ▼                              │
  │              Error Shown ◄───(error)────────────┤
  │                  │                              │
  └──(clear)─────────┴──────────(clear)─────────────┘
```

### Loading States

```
Idle ──(send)──► Streaming ──(complete)──► Idle
                    │
                    │(cancel/error)
                    ▼
                  Idle + (error banner if error)
```

## Accessibility Considerations

- All interactive elements have `.help()` tooltips
- Text selection enabled on message bubbles
- System colors ensure contrast compliance
- Keyboard shortcuts for all primary actions
- Focus states use system defaults

## Future Design Considerations

Areas identified for potential enhancement:
- Conversation history persistence
- Multiple conversation threads
- Message editing/regeneration
- Code syntax highlighting
- Image/file attachments
- Export functionality

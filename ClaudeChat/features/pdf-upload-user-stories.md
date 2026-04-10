# PDF Upload Feature - User Stories

## Epic: PDF Document Analysis

As a ClaudeChat user, I want to upload PDF documents so that I can ask Claude questions about their content without manually copying text.

---

## User Stories

### US-1: Attach PDF to Message

**As a** user
**I want to** attach a PDF file to my message
**So that** Claude can analyze its contents when answering my question

**Acceptance Criteria:**
- [ ] Paperclip icon button appears in the input bar
- [ ] Clicking the button opens a macOS file picker
- [ ] File picker filters to show only PDF files
- [ ] Selected PDF appears as a chip in the attachment preview area
- [ ] Multiple PDFs can be attached to a single message

**Notes:**
- Use SwiftUI's `.fileImporter` modifier
- Filter using `UTType.pdf`

---

### US-2: View Attachment Details

**As a** user
**I want to** see details about my attached PDF before sending
**So that** I can confirm I selected the correct file

**Acceptance Criteria:**
- [ ] Attachment chip shows the filename
- [ ] Attachment chip shows the file size (human-readable, e.g., "2.1 MB")
- [ ] Multiple attachments display in a horizontal scrollable strip
- [ ] Chips are visually distinct from the text input area

---

### US-3: Remove Attachment

**As a** user
**I want to** remove an attached PDF before sending
**So that** I can correct mistakes without starting over

**Acceptance Criteria:**
- [ ] Each attachment chip has a remove (×) button
- [ ] Clicking remove immediately removes the attachment
- [ ] Remaining attachments reflow in the preview area
- [ ] No confirmation dialog is shown (action is easily undoable by re-attaching)

---

### US-4: Send Message with PDF

**As a** user
**I want to** send a message with attached PDFs
**So that** Claude can read and respond about the document content

**Acceptance Criteria:**
- [ ] Send button is enabled when: (text OR attachments) AND not loading
- [ ] Sending clears both the text input and attachment preview
- [ ] User message appears in chat showing the attached PDF(s)
- [ ] Claude's response references content from the PDF
- [ ] Cmd+Return keyboard shortcut works with attachments

---

### US-5: View Sent Attachments in Chat

**As a** user
**I want to** see which PDFs were attached to my sent messages
**So that** I can reference what documents I asked about

**Acceptance Criteria:**
- [ ] User message bubble shows PDF attachment badge(s)
- [ ] Badge displays filename and file size
- [ ] Badge displays page count (if available)
- [ ] Badge styling matches the user message bubble (accent color)
- [ ] Attachments appear above the text content

---

### US-6: Handle Large PDF Error

**As a** user
**I want to** receive a clear error when my PDF is too large
**So that** I understand why the file wasn't attached

**Acceptance Criteria:**
- [ ] Error appears if PDF exceeds 32MB
- [ ] Error message includes the actual file size
- [ ] Error message states the 32MB limit
- [ ] Error is displayed in the error banner (dismissible)
- [ ] File is not added to pending attachments

**Example Error:**
> "PDF is too large (45.2 MB). Maximum allowed is 32MB."

---

### US-7: Handle Invalid File Error

**As a** user
**I want to** receive a clear error when my file isn't a valid PDF
**So that** I know to select a different file

**Acceptance Criteria:**
- [ ] Error appears if file doesn't have valid PDF header
- [ ] Error message clearly states the file is invalid
- [ ] File picker already filters to PDFs, so this is a fallback check
- [ ] Error is displayed in the error banner (dismissible)

**Example Error:**
> "The selected file is not a valid PDF."

---

### US-8: Send PDF Without Text

**As a** user
**I want to** send a PDF with just a simple prompt or no prompt
**So that** I can get a summary without typing a detailed question

**Acceptance Criteria:**
- [ ] Send button is enabled with attachments only (no text required)
- [ ] Empty text input with PDF attachment sends successfully
- [ ] Claude responds appropriately (e.g., provides summary if no specific question)

---

## Non-Functional Requirements

### NFR-1: Performance

- PDF selection and validation completes in < 1 second for files up to 32MB
- UI remains responsive while reading large PDFs
- Memory usage stays reasonable (PDF data + base64 encoding)

### NFR-2: Accessibility

- Paperclip button has help tooltip ("Attach PDF")
- Attachment chips are keyboard navigable
- Remove buttons have accessible labels

### NFR-3: Consistency

- Attachment UI follows existing app styling (system colors, corner radii)
- Error handling matches existing error banner pattern
- Keyboard shortcuts remain consistent (Cmd+Return to send)

---

## Out of Scope

The following are explicitly not included in this feature:
- Drag-and-drop file attachment
- Image file attachments
- PDF thumbnail previews
- PDF text extraction preview
- Attachment persistence between sessions
- Batch file selection via Finder
- File type conversion (e.g., Word to PDF)

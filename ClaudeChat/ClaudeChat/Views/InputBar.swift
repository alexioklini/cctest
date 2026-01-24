import SwiftUI

struct InputBar: View {
    @Binding var text: String
    let isLoading: Bool
    let onSend: () -> Void
    let onCancel: () -> Void

    @FocusState private var isFocused: Bool

    private var canSend: Bool {
        !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && !isLoading
    }

    var body: some View {
        HStack(alignment: .bottom, spacing: 12) {
            TextEditor(text: $text)
                .font(.body)
                .frame(minHeight: 36, maxHeight: 120)
                .padding(.horizontal, 8)
                .padding(.vertical, 4)
                .background(Color(nsColor: .textBackgroundColor))
                .clipShape(RoundedRectangle(cornerRadius: 8))
                .overlay(
                    RoundedRectangle(cornerRadius: 8)
                        .stroke(Color(nsColor: .separatorColor), lineWidth: 1)
                )
                .focused($isFocused)
                .onSubmit {
                    if NSEvent.modifierFlags.contains(.command) {
                        onSend()
                    }
                }

            if isLoading {
                Button(action: onCancel) {
                    Image(systemName: "stop.circle.fill")
                        .font(.title2)
                        .foregroundColor(.red)
                }
                .buttonStyle(.plain)
                .help("Stop generating")
            } else {
                Button(action: onSend) {
                    Image(systemName: "paperplane.fill")
                        .font(.title2)
                        .foregroundColor(canSend ? .accentColor : .secondary)
                }
                .buttonStyle(.plain)
                .disabled(!canSend)
                .keyboardShortcut(.return, modifiers: .command)
                .help("Send message (⌘+Return)")
            }
        }
        .padding()
        .background(Color(nsColor: .windowBackgroundColor))
    }
}

struct InputBar_Previews: PreviewProvider {
    static var previews: some View {
        VStack {
            InputBar(
                text: .constant("Hello"),
                isLoading: false,
                onSend: {},
                onCancel: {}
            )

            InputBar(
                text: .constant("Generating..."),
                isLoading: true,
                onSend: {},
                onCancel: {}
            )
        }
        .frame(width: 400)
    }
}

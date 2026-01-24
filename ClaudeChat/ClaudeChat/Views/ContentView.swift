import SwiftUI
import UniformTypeIdentifiers

struct ContentView: View {
    @StateObject private var viewModel = ChatViewModel()
    @ObservedObject private var settings = AppSettings.shared

    var body: some View {
        VStack(spacing: 0) {
            // Toolbar area
            toolbarView

            Divider()

            // Error banner
            if let error = viewModel.error {
                errorBanner(error)
            }

            // Fallback model notice
            if let fallbackModel = viewModel.usedFallbackModel {
                fallbackBanner(fallbackModel)
            }

            // Chat area
            ChatView(viewModel: viewModel)

            Divider()

            // Input area
            InputBar(
                text: $viewModel.currentInput,
                pendingAttachments: $viewModel.pendingAttachments,
                isLoading: viewModel.isLoading,
                onSend: viewModel.sendMessage,
                onCancel: viewModel.cancelStream,
                onAttachTapped: { viewModel.showFileImporter = true },
                onRemoveAttachment: viewModel.removePendingAttachment
            )
        }
        .frame(minWidth: 500, minHeight: 400)
        .sheet(isPresented: $viewModel.showSettings) {
            SettingsView(settings: settings, viewModel: viewModel)
        }
        .fileImporter(
            isPresented: $viewModel.showFileImporter,
            allowedContentTypes: [UTType.pdf],
            allowsMultipleSelection: true
        ) { result in
            switch result {
            case .success(let urls):
                for url in urls {
                    viewModel.addPDFAttachment(from: url)
                }
            case .failure(let error):
                viewModel.error = error.localizedDescription
            }
        }
        .task {
            await viewModel.refreshModels()
        }
    }

    private var toolbarView: some View {
        HStack {
            Button(action: { viewModel.showSettings = true }) {
                Image(systemName: "gear")
            }
            .buttonStyle(.plain)
            .help("Settings")

            Spacer()

            // Model selector
            Picker("Model", selection: $settings.selectedModel) {
                if viewModel.availableModels.isEmpty {
                    Text(settings.selectedModel).tag(settings.selectedModel)
                } else {
                    ForEach(viewModel.availableModels, id: \.self) { model in
                        Text(model).tag(model)
                    }
                }
            }
            .pickerStyle(.menu)
            .frame(maxWidth: 250)

            Spacer()

            Button(action: viewModel.clearChat) {
                Image(systemName: "trash")
            }
            .buttonStyle(.plain)
            .disabled(viewModel.messages.isEmpty)
            .help("Clear chat")
        }
        .padding(.horizontal)
        .padding(.vertical, 8)
        .background(Color(nsColor: .windowBackgroundColor))
    }

    private func errorBanner(_ error: String) -> some View {
        HStack {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundColor(.yellow)

            Text(error)
                .font(.caption)
                .lineLimit(2)

            Spacer()

            Button(action: { viewModel.error = nil }) {
                Image(systemName: "xmark")
                    .font(.caption)
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal)
        .padding(.vertical, 8)
        .background(Color.red.opacity(0.1))
    }

    private func fallbackBanner(_ model: String) -> some View {
        HStack {
            Image(systemName: "info.circle.fill")
                .foregroundColor(.blue)

            Text("Using fallback model: \(model)")
                .font(.caption)

            Spacer()

            Button(action: { viewModel.usedFallbackModel = nil }) {
                Image(systemName: "xmark")
                    .font(.caption)
            }
            .buttonStyle(.plain)
        }
        .padding(.horizontal)
        .padding(.vertical, 6)
        .background(Color.blue.opacity(0.1))
    }
}

struct ContentView_Previews: PreviewProvider {
    static var previews: some View {
        ContentView()
    }
}

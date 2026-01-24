import SwiftUI

struct SettingsView: View {
    @ObservedObject var settings: AppSettings
    @ObservedObject var viewModel: ChatViewModel
    @Environment(\.dismiss) private var dismiss

    @State private var apiKeyInput: String = ""
    @State private var baseURLInput: String = ""
    @State private var isTestingConnection: Bool = false
    @State private var connectionTestResult: ConnectionTestResult?

    enum ConnectionTestResult {
        case success(Int)
        case failure(String)
    }

    var body: some View {
        VStack(spacing: 0) {
            Form {
                Section {
                    SecureField("API Key", text: $apiKeyInput)
                        .textFieldStyle(.roundedBorder)

                    TextField("Base URL", text: $baseURLInput)
                        .textFieldStyle(.roundedBorder)

                    Picker("Model", selection: $settings.selectedModel) {
                        if viewModel.availableModels.isEmpty {
                            Text(settings.selectedModel).tag(settings.selectedModel)
                        } else {
                            ForEach(viewModel.availableModels, id: \.self) { model in
                                Text(model).tag(model)
                            }
                        }
                    }

                    HStack {
                        Button("Test Connection") {
                            testConnection()
                        }
                        .disabled(isTestingConnection || apiKeyInput.isEmpty)

                        if isTestingConnection {
                            ProgressView()
                                .scaleEffect(0.7)
                        }

                        if let result = connectionTestResult {
                            connectionResultView(result)
                        }

                        Spacer()

                        Button("Refresh Models") {
                            Task {
                                await viewModel.refreshModels()
                            }
                        }
                        .disabled(apiKeyInput.isEmpty)
                    }
                } header: {
                    Text("API Configuration")
                }

                Section {
                    Stepper(
                        "Max Tokens: \(settings.maxTokens)",
                        value: $settings.maxTokens,
                        in: 256...32768,
                        step: 256
                    )
                } header: {
                    Text("Generation Settings")
                }
            }
            .formStyle(.grouped)
            .padding()

            Divider()

            HStack {
                Spacer()

                Button("Cancel") {
                    dismiss()
                }
                .keyboardShortcut(.escape)

                Button("Save") {
                    saveSettings()
                    dismiss()
                }
                .keyboardShortcut(.return)
                .buttonStyle(.borderedProminent)
            }
            .padding()
        }
        .frame(width: 450, height: 350)
        .onAppear {
            apiKeyInput = settings.apiKey
            baseURLInput = settings.baseURL
        }
    }

    @ViewBuilder
    private func connectionResultView(_ result: ConnectionTestResult) -> some View {
        switch result {
        case .success(let modelCount):
            HStack(spacing: 4) {
                Image(systemName: "checkmark.circle.fill")
                    .foregroundColor(.green)
                Text("\(modelCount) models available")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
        case .failure(let error):
            HStack(spacing: 4) {
                Image(systemName: "xmark.circle.fill")
                    .foregroundColor(.red)
                Text(error)
                    .font(.caption)
                    .foregroundColor(.secondary)
                    .lineLimit(1)
            }
        }
    }

    private func testConnection() {
        isTestingConnection = true
        connectionTestResult = nil

        // Temporarily apply settings for test
        let tempSettings = AppSettings.shared
        tempSettings.baseURL = baseURLInput
        tempSettings.apiKey = apiKeyInput

        Task {
            let models = await ModelService.shared.fetchAvailableModels(
                settings: tempSettings,
                forceRefresh: true
            )

            await MainActor.run {
                isTestingConnection = false
                if models.isEmpty {
                    connectionTestResult = .failure("No models found")
                } else {
                    connectionTestResult = .success(models.count)
                    viewModel.availableModels = models
                }
            }
        }
    }

    private func saveSettings() {
        settings.baseURL = baseURLInput
        settings.saveAPIKey(apiKeyInput)

        Task {
            await viewModel.refreshModels()
        }
    }
}

struct SettingsView_Previews: PreviewProvider {
    static var previews: some View {
        SettingsView(settings: .shared, viewModel: ChatViewModel())
    }
}

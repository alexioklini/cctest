import Foundation
import SwiftUI

class AppSettings: ObservableObject {
    static let shared = AppSettings()

    @AppStorage("baseURL") var baseURL: String = "http://localhost:8317/v1"
    @AppStorage("selectedModel") var selectedModel: String = "claude-opus-4-5-20251101"
    @AppStorage("maxTokens") var maxTokens: Int = 4096
    @AppStorage("extractPDFText") var extractPDFText: Bool = false

    // API key is stored in Keychain, not AppStorage
    @Published var apiKey: String = ""

    private init() {
        // Load API key from Keychain on init
        loadAPIKey()
    }

    func loadAPIKey() {
        apiKey = KeychainService.shared.getAPIKey() ?? ""
    }

    func saveAPIKey(_ key: String) {
        apiKey = key
        if key.isEmpty {
            KeychainService.shared.deleteAPIKey()
        } else {
            KeychainService.shared.saveAPIKey(key)
        }
    }

    var hasAPIKey: Bool {
        !apiKey.isEmpty
    }
}

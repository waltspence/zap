import FeatherweightCore
import SwiftUI

struct LicenseView: View {
    @ObservedObject var license: LicenseManager
    @State private var keyInput = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Image(systemName: "checkmark.seal.fill")
                    .foregroundStyle(.linearGradient(colors: [.blue, .purple], startPoint: .topLeading, endPoint: .bottomTrailing))
                Text("Featherweight Pro")
                    .font(.headline)
            }
            Text(license.statusText)
                .font(.caption)
                .foregroundColor(.secondary)

            if license.isPro {
                if let masked = license.maskedKey {
                    Text("Key: \(masked)")
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundColor(.secondary)
                }
                Button("Deactivate This Mac") {
                    Task { await license.deactivate() }
                }
                .disabled(license.isBusy)
            } else {
                TextField("XXXX-XXXX-XXXX-XXXX", text: $keyInput)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(size: 12, design: .monospaced))

                HStack {
                    if let url = LicenseManager.purchaseURL {
                        Link("Buy Featherweight Pro", destination: url)
                            .font(.caption)
                    }
                    Spacer()
                    Button(license.isBusy ? "Activating…" : "Activate") {
                        Task { await license.activate(key: keyInput) }
                    }
                    .keyboardShortcut(.defaultAction)
                    .disabled(keyInput.trimmingCharacters(in: .whitespaces).isEmpty || license.isBusy)
                }

                if LicenseManager.polarOrganizationID.isEmpty {
                    Text("Store not configured yet — set the Polar organization ID and checkout URL in LicenseManager.swift before launch.")
                        .font(.system(size: 10))
                        .foregroundColor(.orange)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            if let error = license.lastError {
                Text(error)
                    .font(.caption2)
                    .foregroundColor(.red)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .padding(16)
        .frame(width: 380)
    }
}

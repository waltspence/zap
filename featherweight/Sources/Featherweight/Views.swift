import AppKit
import FeatherweightCore
import SwiftUI

extension RiskLevel {
    var color: Color {
        switch self {
        case .safe: .green; case .warning: .orange; case .danger: .red
        }
    }
}

// MARK: - Content View

struct ContentView: View {
    @ObservedObject var scanner: Scanner
    @ObservedObject var license: LicenseManager
    let delegate: AppDelegate

    var body: some View {
        VStack(spacing: 0) {
            HeaderView(scanner: scanner, license: license, delegate: delegate)
            Divider()
            SummaryBar(scanner: scanner)
            Divider()
            if scanner.isScanning { ScanningProgressView(scanner: scanner) }
            CategoryListView(scanner: scanner)
            Divider()
            FooterView(scanner: scanner)
        }
        .frame(width: 400)
        .background(Color(NSColor.windowBackgroundColor))
    }
}

// MARK: - Header

struct HeaderView: View {
    @ObservedObject var scanner: Scanner
    @ObservedObject var license: LicenseManager
    let delegate: AppDelegate

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: "bubbles.and.sparkles.fill")
                .font(.title3)
                .foregroundStyle(.linearGradient(colors: [.blue, .purple], startPoint: .topLeading, endPoint: .bottomTrailing))
            Text("Featherweight")
                .font(.system(size: 15, weight: .bold))

            if license.isPro {
                Text("PRO")
                    .font(.system(size: 8, weight: .bold))
                    .padding(.horizontal, 5)
                    .padding(.vertical, 2)
                    .background(Capsule().fill(Color.blue.opacity(0.15)))
                    .foregroundColor(.blue)
            }

            Spacer()

            if let date = scanner.lastCleanedDate {
                Text("Last clean: \(date.formatted(.relative(presentation: .numeric)))")
                    .font(.system(size: 9))
                    .foregroundColor(.secondary)
            }

            // Filter
            HStack(spacing: 4) {
                Image(systemName: "text.magnifyingglass")
                    .font(.caption2).foregroundColor(.secondary)
                TextField("Filter", text: $scanner.searchText)
                    .textFieldStyle(.plain).font(.system(size: 11)).frame(width: 80)
                if !scanner.searchText.isEmpty {
                    Button(action: { scanner.searchText = "" }) {
                        Image(systemName: "xmark.circle.fill").font(.caption2)
                    }.buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 6).padding(.vertical, 3)
            .background(Color(NSColor.controlBackgroundColor))
            .clipShape(RoundedRectangle(cornerRadius: 5))

            // Menu
            Menu {
                Button(action: { delegate.showWindow() }) {
                    Label("Show Window", systemImage: "macwindow")
                }
                Button(action: { delegate.showSettings() }) {
                    Label("Project Folders…", systemImage: "folder.badge.gearshape")
                }
                Button(action: { delegate.openCleanLog() }) {
                    Label("Open Clean Log", systemImage: "doc.text")
                }
                Button(action: { delegate.showLicense() }) {
                    Label(license.isPro ? "License" : "Upgrade to Pro…", systemImage: "checkmark.seal")
                }
                Divider()
                Button(action: { delegate.showAbout() }) {
                    Label("About Featherweight", systemImage: "info.circle")
                }
                Button(action: { NSApp.terminate(nil) }) {
                    Label("Quit", systemImage: "power")
                }
            } label: {
                Image(systemName: "ellipsis.circle")
                    .font(.system(size: 16))
                    .foregroundColor(.secondary)
            }
            .menuStyle(.borderlessButton)
            .frame(width: 24)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
    }
}

// MARK: - Summary Bar

struct SummaryBar: View {
    @ObservedObject var scanner: Scanner
    @State private var showCleanConfirmation = false

    var body: some View {
        HStack(spacing: 12) {
            // Scan button
            Button(action: {
                if scanner.isScanning { scanner.cancelScan() }
                else { scanner.scan() }
            }) {
                Label(
                    scanner.isScanning ? "Stop" : "Scan",
                    systemImage: scanner.isScanning ? "stop.fill" : "magnifyingglass"
                )
                .frame(width: 80)
            }
            .buttonStyle(.borderedProminent)
            .tint(scanner.isScanning ? .orange : .blue)

            if scanner.totalReclaimable > 0 {
                VStack(alignment: .leading, spacing: 1) {
                    Text("Found")
                        .font(.system(size: 9))
                        .foregroundColor(.secondary)
                    Text(ByteCountFormatter().string(fromByteCount: Int64(scanner.totalReclaimable)))
                        .font(.system(size: 14, weight: .semibold, design: .monospaced))
                }
            }

            Spacer()

            if scanner.totalSelected > 0 {
                Button(action: { showCleanConfirmation = true }) {
                    Label(
                        ByteCountFormatter().string(fromByteCount: Int64(scanner.totalSelected)),
                        systemImage: scanner.isCleaning ? "hourglass" : "trash.fill"
                    )
                }
                .buttonStyle(.borderedProminent)
                .tint(.red)
                .disabled(scanner.isCleaning)
                .confirmationDialog(
                    "Clean \(ByteCountFormatter().string(fromByteCount: Int64(scanner.totalSelected)))?",
                    isPresented: $showCleanConfirmation,
                    titleVisibility: .visible
                ) {
                    Button("Clean", role: .destructive) {
                        Task { await scanner.clean() }
                    }
                    Button("Cancel", role: .cancel) {}
                } message: {
                    Text("This will clean: \(scanner.selectedNames.joined(separator: ", ")). Most items are moved to the Trash; caches and snapshots may be deleted directly. Everything is recorded in the clean log.")
                }
            }

            if scanner.lastCleanedBytes > 0 {
                Text("✓ \(ByteCountFormatter().string(fromByteCount: Int64(scanner.lastCleanedBytes))) freed")
                    .font(.system(size: 10))
                    .foregroundColor(.green)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
    }
}

// MARK: - Scanning Progress

struct ScanningProgressView: View {
    @ObservedObject var scanner: Scanner

    var scanned: Int { scanner.results.filter { $0.status == .done }.count }
    var total: Int { scanner.results.count }
    // Matches Scanner.updateTotals: danger categories are reference-only
    var foundBytes: UInt64 { scanner.results.filter { $0.category.risk != .danger }.reduce(0) { $0 + $1.sizeBytes } }

    var body: some View {
        VStack(spacing: 4) {
            ProgressView(value: scanner.scanProgress)
                .tint(.blue)
            Text("Scanning \(scanned)/\(total) — \(ByteCountFormatter().string(fromByteCount: Int64(foundBytes))) so far")
                .font(.system(size: 10))
                .foregroundColor(.secondary)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 6)
    }
}

// MARK: - Category List

struct CategoryListView: View {
    @ObservedObject var scanner: Scanner

    // Section order follows first appearance in categories.json
    var groupOrder: [String] {
        var seen = Set<String>()
        return scanner.results.compactMap { result in
            seen.insert(result.category.group).inserted ? result.category.group : nil
        }
    }

    var body: some View {
        ScrollView {
            LazyVStack(spacing: 0) {
                if let error = scanner.loadError {
                    errorState(error)
                } else if scanner.filteredResults.isEmpty && !scanner.isScanning {
                    emptyState
                } else {
                    let sorted = scanner.filteredResults.sorted { $0.sizeBytes > $1.sizeBytes }
                    ForEach(groupOrder, id: \.self) { group in
                        let items = sorted.filter { $0.category.group == group }
                        if !items.isEmpty {
                            sectionHeader(group)
                            ForEach(items) { ScanRowView(result: $0, scanner: scanner) }
                        }
                    }
                }
            }
            .padding(.vertical, 6)
        }
    }

    func sectionHeader(_ title: String) -> some View {
        Text(title)
            .font(.system(size: 10, weight: .semibold))
            .foregroundColor(.secondary)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 14)
            .padding(.top, 12)
            .padding(.bottom, 2)
    }

    func errorState(_ message: String) -> some View {
        VStack(spacing: 12) {
            Image(systemName: "exclamationmark.triangle.fill")
                .font(.system(size: 32))
                .foregroundColor(.orange)
            Text(message)
                .font(.system(size: 12))
                .foregroundColor(.secondary)
                .multilineTextAlignment(.center)
                .padding(.horizontal, 20)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 80)
    }

    var emptyState: some View {
        VStack(spacing: 12) {
            if scanner.isFirstLaunch {
                Image(systemName: "eyes")
                    .font(.system(size: 32))
                    .foregroundColor(.secondary.opacity(0.3))
                Text("Detecting your tools…")
                    .font(.system(size: 13))
                    .foregroundColor(.secondary)
            } else {
                Image(systemName: "magnifyingglass")
                    .font(.system(size: 32))
                    .foregroundColor(.secondary.opacity(0.3))
                Text("Press Scan to find reclaimable space")
                    .font(.system(size: 13))
                    .foregroundColor(.secondary)
                Text("Only shows tools actually on your system")
                    .font(.system(size: 10))
                    .foregroundColor(.secondary)
                    .multilineTextAlignment(.center)
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 80)
    }
}

// MARK: - Scan Row

struct ScanRowView: View {
    let result: ScanResult
    @ObservedObject var scanner: Scanner
    @State private var isHovered = false
    @State private var showDetails = false

    var body: some View {
        HStack(spacing: 8) {
            // Checkbox
            Button(action: { scanner.toggle(result.id) }) {
                Image(systemName: result.selected ? "checkmark.circle.fill" : "circle")
                    .font(.system(size: 15))
                    .foregroundColor(result.selected ? .accentColor : .secondary.opacity(0.4))
            }
            .buttonStyle(.plain)
            .disabled(result.category.risk == .danger)

            // Icon
            Image(systemName: result.category.icon)
                .frame(width: 20)
                .font(.system(size: 12))
                .foregroundColor(result.status == .done && result.sizeBytes > 0 ? .primary : .secondary)

            // Name + risk
            VStack(alignment: .leading, spacing: 1) {
                Text(result.category.name)
                    .font(.system(size: 12.5, weight: .medium))
                    .lineLimit(1)
                HStack(spacing: 4) {
                    Circle().fill(result.category.risk.color).frame(width: 5, height: 5)
                    Text(result.category.risk.label)
                        .font(.system(size: 9))
                        .foregroundColor(.secondary)
                }
            }

            // What is this?
            if let details = result.category.details {
                Button(action: { showDetails.toggle() }) {
                    Image(systemName: "info.circle")
                        .font(.system(size: 10))
                        .foregroundColor(.secondary.opacity(isHovered || showDetails ? 0.9 : 0.35))
                }
                .buttonStyle(.plain)
                .popover(isPresented: $showDetails, arrowEdge: .bottom) {
                    VStack(alignment: .leading, spacing: 6) {
                        Text(result.category.name)
                            .font(.system(size: 12, weight: .semibold))
                        Text(details)
                            .font(.system(size: 11))
                            .foregroundColor(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .padding(12)
                    .frame(width: 280)
                }
                .help(details)
            }

            Spacer()

            // Size
            switch result.status {
            case .idle:      Text("—").font(.system(size: 12, design: .monospaced)).foregroundColor(.secondary.opacity(0.4))
            case .scanning:  ProgressView().scaleEffect(0.5).frame(width: 12, height: 12)
            case .done:      Text(result.formatted).font(.system(size: 12, design: .monospaced)).foregroundColor(result.sizeBytes > 0 ? .primary : .secondary)
            case .error(let message):
                Button(action: openPrivacySettings) {
                    HStack(spacing: 3) {
                        Image(systemName: "lock.fill").font(.caption2)
                        Text("No access").font(.system(size: 10))
                    }
                    .foregroundColor(.orange)
                }
                .buttonStyle(.plain)
                .help("\(message) — click to open System Settings → Privacy & Security → Full Disk Access")
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 7)
        .background(isHovered ? Color.primary.opacity(0.04) : Color.clear)
        .onHover { isHovered = $0 }
    }

    private func openPrivacySettings() {
        if let url = URL(string: "x-apple.systempreference:com.apple.preference.security?Privacy_AllFiles") {
            NSWorkspace.shared.open(url)
        }
    }
}

// MARK: - Footer

struct FooterView: View {
    @ObservedObject var scanner: Scanner

    var body: some View {
        HStack {
            Button("Safe") { scanner.selectAllSafe() }
                .font(.caption2).buttonStyle(.plain).foregroundColor(.secondary)
            Button("None") { scanner.deselectAll() }
                .font(.caption2).buttonStyle(.plain).foregroundColor(.secondary)

            if scanner.hiddenCount > 0 {
                Button(scanner.showAll ? "Hide empty (\(scanner.hiddenCount))" : "Show all (\(scanner.hiddenCount) hidden)") {
                    scanner.showAll.toggle()
                }
                .font(.caption2).buttonStyle(.plain).foregroundColor(.accentColor)
            }

            Spacer()

            HStack(spacing: 8) {
                HStack(spacing: 3) { Circle().fill(.green).frame(width: 6, height: 6); Text("Safe").font(.system(size: 9)) }
                HStack(spacing: 3) { Circle().fill(.orange).frame(width: 6, height: 6); Text("Careful").font(.system(size: 9)) }
                HStack(spacing: 3) { Circle().fill(.red).frame(width: 6, height: 6); Text("Danger").font(.system(size: 9)) }
            }
            .foregroundColor(.secondary)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 7)
    }
}

// MARK: - Settings

struct SettingsView: View {
    @ObservedObject var scanner: Scanner
    @State private var rootsText = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("Project folders")
                .font(.headline)
            Text("Folders searched for node_modules, virtualenvs, and other project artifacts. One per line; ~ is allowed. Missing folders are skipped.")
                .font(.caption)
                .foregroundColor(.secondary)
                .fixedSize(horizontal: false, vertical: true)

            TextEditor(text: $rootsText)
                .font(.system(size: 12, design: .monospaced))
                .frame(height: 110)
                .overlay(RoundedRectangle(cornerRadius: 4).stroke(Color.secondary.opacity(0.3)))

            HStack {
                Button("Reset to Defaults") {
                    rootsText = Scanner.defaultRoots.joined(separator: "\n")
                }
                Spacer()
                Button("Save") {
                    scanner.projectRoots = rootsText
                        .split(separator: "\n")
                        .map { $0.trimmingCharacters(in: .whitespaces) }
                        .filter { !$0.isEmpty }
                    NSApp.keyWindow?.close()
                }
                .keyboardShortcut(.defaultAction)
            }
        }
        .padding(16)
        .frame(width: 400)
        .onAppear { rootsText = scanner.projectRoots.joined(separator: "\n") }
    }
}

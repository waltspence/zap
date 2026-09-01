import AppKit
import FeatherweightClean
import FeatherweightCore
import SwiftUI
import ZapKit

extension RiskLevel {
    var color: Color {
        switch self {
        case .safe: .green
        case .warning: .orange
        case .danger: .red
        }
    }
}

/// The Reclaim Space detail pane: scan the category catalog, review what was
/// found, and clean the selection. Mirrors the app-uninstall table's layout.
struct ReclaimView: View {
    @ObservedObject var scanner: ReclaimScanner
    @State private var confirmClean = false
    @State private var editingRoots = false
    @State private var rootsText = ""

    private var rows: [ScanResult] {
        scanner.filteredResults.sorted { $0.sizeBytes > $1.sizeBytes }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            header
            if scanner.isScanning {
                ProgressView(value: scanner.scanProgress).padding(.horizontal)
            }
            if let error = scanner.loadError {
                ContentUnavailableView(error, systemImage: "exclamationmark.triangle.fill")
            } else {
                table
            }
        }
        .safeAreaInset(edge: .bottom) { footer }
        .alert("Clean \(humanBytes(Int64(scanner.totalSelected)))?", isPresented: $confirmClean) {
            Button("Clean", role: .destructive) { Task { await scanner.clean() } }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This will clean: \(scanner.selectedNames.joined(separator: ", ")). "
                + "Most items are moved to the Trash; caches and snapshots may be deleted directly. "
                + "Everything is recorded in the clean log.")
        }
    }

    private var header: some View {
        HStack(alignment: .firstTextBaseline) {
            Text("Reclaim Space").font(.title2.bold())
            if scanner.totalReclaimable > 0 {
                Text("\(humanBytes(Int64(scanner.totalReclaimable))) found").foregroundStyle(.secondary)
            }
            if scanner.lastCleanedBytes > 0, scanner.lastCleanedDate != nil {
                Text("· \(humanBytes(Int64(scanner.lastCleanedBytes))) freed last clean")
                    .foregroundStyle(.green)
            }
            Spacer()
            TextField("Filter", text: $scanner.searchText).frame(width: 140)
            Button(scanner.isScanning ? "Stop" : "Scan") {
                if scanner.isScanning { scanner.cancelScan() } else { scanner.scan() }
            }
        }
        .padding([.horizontal, .top])
    }

    private var table: some View {
        Table(rows) {
            TableColumn("") { r in
                Toggle("", isOn: Binding(
                    get: { r.selected },
                    set: { _ in scanner.toggle(r.id) }
                ))
                .labelsHidden()
                .disabled(r.category.risk == .danger)
            }.width(24)
            TableColumn("Size") { r in
                switch r.status {
                case .idle: Text("—").foregroundStyle(.secondary)
                case .scanning: ProgressView().controlSize(.small)
                case .done: Text(r.formatted).monospacedDigit()
                        .foregroundStyle(r.sizeBytes > 0 ? .primary : .secondary)
                case .error: Label("No access", systemImage: "lock.fill")
                        .foregroundStyle(.orange)
                        .help("Needs Full Disk Access")
                }
            }.width(90)
            TableColumn("Risk") { r in
                HStack(spacing: 4) {
                    Circle().fill(r.category.risk.color).frame(width: 6, height: 6)
                    Text(r.category.risk.label).foregroundStyle(.secondary)
                }
            }.width(min: 100, ideal: 130)
            TableColumn("Group") { r in Text(r.category.group).foregroundStyle(.secondary) }.width(min: 80, ideal: 100)
            TableColumn("Category") { r in
                HStack(spacing: 4) {
                    Text(r.category.name)
                    if let details = r.category.details {
                        Image(systemName: "info.circle").foregroundStyle(.secondary).help(details)
                    }
                }
            }
        }
    }

    private var footer: some View {
        HStack {
            Button("Safe") { scanner.selectAllSafe() }
            Button("None") { scanner.deselectAll() }
            if scanner.hiddenCount > 0 {
                Button(scanner.showAll ? "Hide empty (\(scanner.hiddenCount))" : "Show all (\(scanner.hiddenCount) hidden)") {
                    scanner.showAll.toggle()
                }
            }
            Button("Folders…") {
                rootsText = scanner.projectRoots.joined(separator: "\n")
                editingRoots = true
            }
            .help("Folders searched for node_modules, virtualenvs, and other project artifacts")
            .popover(isPresented: $editingRoots) { rootsEditor }
            Button("Clean Log") {
                CleanLogger.ensureLogExists()
                NSWorkspace.shared.open(CleanLogger.logFileURL)
            }
            Spacer()
            if scanner.isCleaning { ProgressView().controlSize(.small) }
            Button("Clean \(humanBytes(Int64(scanner.totalSelected)))") { confirmClean = true }
                .buttonStyle(.borderedProminent).tint(.red)
                .disabled(scanner.isCleaning || scanner.isScanning || scanner.totalSelected == 0)
        }
        .controlSize(.small)
        .padding(.horizontal).padding(.vertical, 8)
        .background(.bar)
    }

    private var rootsEditor: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Project folders").font(.headline)
            Text("One per line; ~ is allowed. Missing folders are skipped.")
                .font(.caption).foregroundStyle(.secondary)
            TextEditor(text: $rootsText)
                .font(.system(size: 12, design: .monospaced))
                .frame(width: 300, height: 100)
            HStack {
                Button("Reset to Defaults") { rootsText = ReclaimScanner.defaultRoots.joined(separator: "\n") }
                Spacer()
                Button("Save") {
                    scanner.projectRoots = rootsText
                        .split(separator: "\n")
                        .map { $0.trimmingCharacters(in: .whitespaces) }
                        .filter { !$0.isEmpty }
                    editingRoots = false
                }
                .keyboardShortcut(.defaultAction)
            }
        }
        .padding(12)
    }
}

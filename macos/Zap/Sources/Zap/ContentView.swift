import SwiftUI
import ZapKit

struct ContentView: View {
    @EnvironmentObject var m: Model

    var body: some View {
        NavigationSplitView {
            List(selection: Binding(get: { m.sidebarSelection }, set: { m.select($0) })) {
                Section("Tools") {
                    Label("Reclaim Space", systemImage: "internaldrive")
                        .tag(SidebarItem.reclaim)
                }
                Section("Apps") {
                    ForEach(m.visibleApps) { app in
                        VStack(alignment: .leading, spacing: 1) {
                            Text(app.title)
                            Text(app.bundleId ?? app.identifier).font(.caption).foregroundStyle(.secondary).lineLimit(1)
                        }
                        .tag(SidebarItem.app(app.id))
                    }
                }
            }
            .searchable(text: $m.filter, placement: .sidebar, prompt: "Filter apps")
            .navigationSplitViewColumnWidth(min: 200, ideal: 240)
        } detail: {
            detail
        }
        .toolbar {
            ToolbarItemGroup {
                Toggle("Aggressive", isOn: $m.aggressive).help("Also select derived matches (id-prefix, helpers)")
                Toggle("Name-only", isOn: $m.includeLow).help("Also select matches found only by app name")
                Button { m.showHistory() } label: { Label("Undo…", systemImage: "arrow.uturn.backward") }.disabled(m.busy)
                Button { m.refresh() } label: { Label("Refresh", systemImage: "arrow.clockwise") }.disabled(m.busy)
            }
        }
        .safeAreaInset(edge: .bottom) { if m.sidebarSelection != .reclaim { footer } }
        .sheet(isPresented: Binding(get: { m.operations != nil }, set: { if !$0 { m.operations = nil } })) { UndoSheet() }
        .sheet(isPresented: $m.fdaMissing) { AccessGate().interactiveDismissDisabled() }
        .alert("Move to Trash?", isPresented: $m.confirmTrash) {
            Button("Move to Trash", role: .destructive) { m.trash() }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("\(m.selectedArtifacts.count) items of \(m.plan?.app.title ?? "") (\(humanBytes(m.selectedBytes))). You can restore them later with Undo.")
        }
        .alert(m.updateReady ? "Update available" : "Frankly Zapped", isPresented: Binding(get: { m.updateInfo != nil }, set: { if !$0 { m.updateInfo = nil } })) {
            if m.updateReady {
                Button("Update Now") { Updater.updateNowAndQuit() }
                Button("Later", role: .cancel) {}
            } else {
                Button("OK", role: .cancel) {}
            }
        } message: {
            Text((m.updateInfo ?? "") + (m.updateReady ? "\n\nUpdate pulls the repo, rebuilds, and relaunches the app." : ""))
        }
        .alert("Permission needed", isPresented: Binding(get: { !m.blockers.isEmpty }, set: { if !$0 { m.blockers = [] } })) {
            Button("Open Settings") { NSWorkspace.shared.open(FullDiskAccess.settingsURL); m.blockers = [] }
            Button("OK", role: .cancel) {}
        } message: {
            Text(m.blockers.map { b in b.message + "\n" + b.paths.prefix(4).map { "  • \($0)" }.joined(separator: "\n") }
                .joined(separator: "\n\n") + "\n\nNothing was removed.")
        }
        .alert("Some items were not removed", isPresented: Binding(get: { !m.attention.isEmpty }, set: { if !$0 { m.attention = [] } })) {
            Button("OK") {}
        } message: {
            Text(m.attention.prefix(8).map { "\($0.status): \($0.path)" + ($0.reason.map { "\n  \($0)" } ?? "") }.joined(separator: "\n"))
        }
    }

    @ViewBuilder private var detail: some View {
        if m.sidebarSelection == .reclaim {
            ReclaimView(scanner: m.reclaim)
        } else if let plan = m.plan {
            VStack(alignment: .leading, spacing: 6) {
                HStack(alignment: .firstTextBaseline) {
                    Text(plan.app.title).font(.title2.bold())
                    Text("\(plan.app.bundleId ?? plan.app.identifier)  v\(plan.app.version ?? "?")").foregroundStyle(.secondary)
                }.padding([.horizontal, .top])
                Table(plan.artifacts) {
                    TableColumn("") { a in
                        Toggle("", isOn: Binding(get: { m.checked.contains(a.id) }, set: { _ in m.toggle(a.id) })).labelsHidden()
                    }.width(24)
                    TableColumn("Size") { a in Text(humanBytes(a.sizeBytes)).monospacedDigit() }.width(70)
                    TableColumn("Kind") { a in Text(a.category.replacingOccurrences(of: "_", with: " ")) }.width(min: 90, ideal: 130)
                    TableColumn("Match") { a in
                        Text(a.tier).foregroundStyle(a.tier == "exact" ? .primary : .secondary)
                    }.width(80)
                    TableColumn("Path") { a in
                        HStack(spacing: 4) {
                            if a.risky { Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange).help(a.requiresRoot ? "Needs administrator" : "Shared with other apps") }
                            Text(a.path).lineLimit(1).truncationMode(.middle)
                                .foregroundStyle(m.checked.contains(a.id) ? .primary : .secondary)
                        }
                    }
                }
            }
        } else {
            ContentUnavailableView(m.selectedApp == nil ? "Select an app" : (m.busy ? "Scanning…" : m.status),
                                   systemImage: "bolt.fill")
        }
    }

    private var footer: some View {
        HStack {
            if m.busy { ProgressView().controlSize(.small) }
            Text(m.status).foregroundStyle(.secondary).lineLimit(1)
            Spacer()
            Button("Move to Trash") { m.confirmTrash = true }
                .keyboardShortcut(.delete, modifiers: .command)
                .buttonStyle(.borderedProminent).tint(.red)
                .disabled(!m.canTrash)
        }
        .padding(.horizontal).padding(.vertical, 8)
        .background(.bar)
    }
}

struct AccessGate: View {
    @EnvironmentObject var m: Model

    var body: some View {
        VStack(spacing: 14) {
            Image(systemName: "lock.shield").font(.system(size: 40)).foregroundStyle(.orange)
            Text("Full Disk Access required").font(.title2.bold())
            Text("""
            macOS protects app data (Containers, Mail, Safari…) even from you. \
            Without Full Disk Access, removals will fail halfway. Grant it first:

            1. Open System Settings → Privacy & Security → Full Disk Access
            2. Enable Frankly Zapped
            3. Reopen the app
            """).frame(width: 400).fixedSize(horizontal: false, vertical: true)
            HStack {
                Button("Quit") { NSApp.terminate(nil) }
                Spacer()
                Button("Check Again") { m.recheckAccess() }
                Button("Open System Settings") { NSWorkspace.shared.open(FullDiskAccess.settingsURL) }
                    .keyboardShortcut(.defaultAction)
            }.frame(width: 400)
        }
        .padding(24)
    }
}

struct UndoSheet: View {
    @EnvironmentObject var m: Model
    @State private var pick: UndoOperation.ID?

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Undo").font(.title2.bold())
            Table(m.operations ?? [], selection: $pick) {
                TableColumn("When") { op in Text(op.createdAt.prefix(16).replacingOccurrences(of: "T", with: " ")).monospacedDigit() }.width(130)
                TableColumn("App", value: \.appName)
                TableColumn("Items") { op in Text("\(op.reversibleCount)/\(op.itemCount) restorable") }.width(120)
                TableColumn("Method", value: \.method).width(60)
            }.frame(minHeight: 200)
            HStack {
                Spacer()
                Button("Cancel") { m.operations = nil }.keyboardShortcut(.cancelAction)
                Button("Restore") { if let op = m.operations?.first(where: { $0.id == pick }) { m.restore(op) } }
                    .keyboardShortcut(.defaultAction).disabled(pick == nil)
            }
        }
        .padding().frame(width: 620)
    }
}

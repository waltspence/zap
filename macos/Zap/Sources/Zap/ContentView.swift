import SwiftUI
import ZapKit

struct ContentView: View {
    @EnvironmentObject var m: Model

    var body: some View {
        NavigationSplitView {
            List(m.visibleApps, selection: Binding(get: { m.selectedApp }, set: { m.select($0) })) { app in
                VStack(alignment: .leading, spacing: 1) {
                    Text(app.title)
                    Text(app.bundleId ?? app.identifier).font(.caption).foregroundStyle(.secondary).lineLimit(1)
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
        .safeAreaInset(edge: .bottom) { footer }
        .sheet(isPresented: Binding(get: { m.operations != nil }, set: { if !$0 { m.operations = nil } })) { UndoSheet() }
        .alert("Move to Trash?", isPresented: $m.confirmTrash) {
            Button("Move to Trash", role: .destructive) { m.trash() }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("\(m.selectedArtifacts.count) items of \(m.plan?.app.title ?? "") (\(humanBytes(m.selectedBytes))). You can restore them later with Undo.")
        }
        .alert("Some items were not removed", isPresented: Binding(get: { !m.attention.isEmpty }, set: { if !$0 { m.attention = [] } })) {
            Button("OK") {}
        } message: {
            Text(m.attention.prefix(8).map { "\($0.status): \($0.path)" + ($0.reason.map { "\n  \($0)" } ?? "") }.joined(separator: "\n"))
        }
    }

    @ViewBuilder private var detail: some View {
        if let plan = m.plan {
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

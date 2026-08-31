import SwiftUI
import ZapKit

@main
struct ZapApp: App {
    @StateObject private var model = Model()

    var body: some Scene {
        WindowGroup("Frankly Zapped") {
            ContentView().environmentObject(model)
                .frame(minWidth: 820, minHeight: 480)
        }
        .windowResizability(.contentSize)
        .commands {
            CommandGroup(replacing: .newItem) {}
            CommandGroup(after: .appInfo) {
                Button("Check for Updates…") { model.checkForUpdates() }
            }
        }
    }
}

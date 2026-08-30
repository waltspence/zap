import SwiftUI
import ZapKit

@main
struct ZapApp: App {
    @StateObject private var model = Model()

    var body: some Scene {
        WindowGroup("Zap") {
            ContentView().environmentObject(model)
                .frame(minWidth: 820, minHeight: 480)
        }
        .windowResizability(.contentSize)
        .commands { CommandGroup(replacing: .newItem) {} }
    }
}

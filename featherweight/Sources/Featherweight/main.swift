import AppKit

// Entry point only — all logic lives in AppDelegate/Scanner/Views.

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
_ = delegate // keep strong reference
app.run()

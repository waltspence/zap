import AppKit
import ServiceManagement
import SwiftUI

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private var statusItem: NSStatusItem!
    private var popover: NSPopover!
    private var scanner: Scanner!
    private var license: LicenseManager!
    private var window: NSWindow?
    private var settingsWindow: NSWindow?
    private var licenseWindow: NSWindow?
    private var rightClickMenu: NSMenu!
    private var launchAtLoginItem: NSMenuItem!

    func applicationDidFinishLaunching(_ notification: Notification) {
        scanner = Scanner()
        license = LicenseManager()

        // Status bar item
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = statusItem.button {
            button.image = NSImage(systemSymbolName: "bubbles.and.sparkles.fill", accessibilityDescription: "Featherweight")
            button.action = #selector(togglePopover)
            button.target = self
            button.sendAction(on: [.leftMouseUp, .rightMouseUp])
        }

        // Right-click menu
        rightClickMenu = NSMenu()
        rightClickMenu.delegate = self
        rightClickMenu.addItem(NSMenuItem(title: "About Featherweight", action: #selector(showAbout), keyEquivalent: ""))
        rightClickMenu.addItem(NSMenuItem.separator())
        rightClickMenu.addItem(NSMenuItem(title: "Show Window", action: #selector(showWindow), keyEquivalent: "w"))
        rightClickMenu.addItem(NSMenuItem(title: "Project Folders…", action: #selector(showSettings), keyEquivalent: ","))
        rightClickMenu.addItem(NSMenuItem(title: "Open Clean Log", action: #selector(openCleanLog), keyEquivalent: ""))
        rightClickMenu.addItem(NSMenuItem(title: "License…", action: #selector(showLicense), keyEquivalent: ""))
        launchAtLoginItem = NSMenuItem(title: "Launch at Login", action: #selector(toggleLaunchAtLogin), keyEquivalent: "")
        rightClickMenu.addItem(launchAtLoginItem)
        rightClickMenu.addItem(NSMenuItem.separator())
        rightClickMenu.addItem(NSMenuItem(title: "Quit Featherweight", action: #selector(quitApp), keyEquivalent: "q"))

        // Popover
        popover = NSPopover()
        popover.contentSize = NSSize(width: 400, height: 640)
        popover.behavior = .transient
        popover.contentViewController = NSHostingController(rootView: ContentView(scanner: scanner, license: license, delegate: self))

        NSEvent.addGlobalMonitorForEvents(matching: [.leftMouseDown, .rightMouseDown]) { [weak self] _ in
            guard let self, self.popover.isShown else { return }
            self.popover.performClose(nil)
        }
    }

    func menuWillOpen(_ menu: NSMenu) {
        launchAtLoginItem.state = SMAppService.mainApp.status == .enabled ? .on : .off
    }

    @objc @MainActor private func togglePopover() {
        guard let event = NSApp.currentEvent else { return }
        if event.type == .rightMouseUp {
            rightClickMenu.popUp(positioning: nil, at: .zero, in: statusItem.button)
            return
        }
        if popover.isShown { popover.performClose(nil) }
        else if let button = statusItem.button {
            popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
            popover.contentViewController?.view.window?.makeKey()
        }
    }

    @objc @MainActor func showWindow() {
        popover.performClose(nil)
        if window == nil {
            window = NSWindow(
                contentRect: NSRect(x: 0, y: 0, width: 420, height: 680),
                styleMask: [.titled, .closable, .miniaturizable, .resizable],
                backing: .buffered, defer: false
            )
            window?.title = "Featherweight"
            window?.center()
            window?.contentViewController = NSHostingController(rootView: ContentView(scanner: scanner, license: license, delegate: self))
            window?.isReleasedWhenClosed = false
        }
        window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc @MainActor func showSettings() {
        popover.performClose(nil)
        if settingsWindow == nil {
            settingsWindow = NSWindow(
                contentRect: NSRect(x: 0, y: 0, width: 400, height: 260),
                styleMask: [.titled, .closable],
                backing: .buffered, defer: false
            )
            settingsWindow?.title = "Featherweight Settings"
            settingsWindow?.center()
            settingsWindow?.contentViewController = NSHostingController(rootView: SettingsView(scanner: scanner))
            settingsWindow?.isReleasedWhenClosed = false
        }
        settingsWindow?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc @MainActor func showLicense() {
        popover.performClose(nil)
        if licenseWindow == nil {
            licenseWindow = NSWindow(
                contentRect: NSRect(x: 0, y: 0, width: 380, height: 200),
                styleMask: [.titled, .closable],
                backing: .buffered, defer: false
            )
            licenseWindow?.title = "Featherweight Pro"
            licenseWindow?.center()
            licenseWindow?.contentViewController = NSHostingController(rootView: LicenseView(license: license))
            licenseWindow?.isReleasedWhenClosed = false
        }
        licenseWindow?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc @MainActor func openCleanLog() {
        CleanLogger.ensureLogExists()
        NSWorkspace.shared.open(CleanLogger.logFileURL)
    }

    @objc @MainActor func toggleLaunchAtLogin() {
        let service = SMAppService.mainApp
        do {
            if service.status == .enabled {
                try service.unregister()
            } else {
                try service.register()
            }
        } catch {
            NSLog("Featherweight: launch-at-login toggle failed: \(error.localizedDescription)")
        }
    }

    @objc @MainActor func showAbout() {
        NSApp.orderFrontStandardAboutPanel(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    @objc @MainActor func quitApp() {
        NSApp.terminate(nil)
    }
}

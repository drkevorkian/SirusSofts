import Cocoa

// Optional native control surface. The launchd worker is independent of this app.
final class MacNodeMenu: NSObject, NSApplicationDelegate {
    private let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let label = "com.sirussofts.mac-node"
    private var service = "gui/\(getuid())/com.sirussofts.mac-node"
    private var plist: String {
        NSHomeDirectory() + "/Library/LaunchAgents/" + label + ".plist"
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        item.button?.title = "Sirus"
        item.button?.toolTip = "SirusSofts Mac Research Node"
        let menu = NSMenu()
        let status = NSMenuItem(title: "Refresh service state", action: #selector(showStatus), keyEquivalent: "")
        status.target = self
        menu.addItem(status)
        menu.addItem(.separator())
        let start = NSMenuItem(title: "Start worker", action: #selector(startService), keyEquivalent: "")
        start.target = self
        menu.addItem(start)
        let stop = NSMenuItem(title: "Stop worker", action: #selector(stopService), keyEquivalent: "")
        stop.target = self
        menu.addItem(stop)
        let dashboard = NSMenuItem(title: "Open Mac dashboard", action: #selector(openDashboard), keyEquivalent: "")
        dashboard.target = self
        menu.addItem(dashboard)
        let config = NSMenuItem(title: "Open configuration", action: #selector(openConfig), keyEquivalent: "")
        config.target = self
        menu.addItem(config)
        menu.addItem(.separator())
        let quit = NSMenuItem(title: "Quit menu app (worker keeps running)", action: #selector(quitUI), keyEquivalent: "q")
        quit.target = self
        menu.addItem(quit)
        item.menu = menu
    }

    @discardableResult
    private func launchctl(_ arguments: [String]) -> Int32 {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/launchctl")
        process.arguments = arguments
        do {
            try process.run()
            process.waitUntilExit()
            return process.terminationStatus
        } catch {
            return -1
        }
    }

    private func alert(_ message: String) {
        let window = NSAlert()
        window.messageText = "SirusSofts Mac Node"
        window.informativeText = message
        window.runModal()
    }

    @objc private func showStatus() {
        let state = launchctl(["print", service]) == 0 ? "Loaded in launchd" : "Not loaded"
        alert(state + ". This does not establish that the coordinator is online; check /mac for the last authenticated heartbeat.")
    }

    @objc private func startService() {
        guard FileManager.default.fileExists(atPath: plist) else {
            alert("Install the Mac agent and enroll the node key first.")
            return
        }
        let rc = launchctl(["bootstrap", "gui/\(getuid())", plist])
        if rc != 0 && launchctl(["print", service]) != 0 {
            alert("Unable to load worker. Review launchd and stderr.log.")
            return
        }
        if launchctl(["kickstart", "-k", service]) != 0 {
            alert("Unable to start worker. Review stderr.log.")
        }
    }

    @objc private func stopService() {
        if launchctl(["bootout", service]) != 0 {
            alert("Worker is already unloaded or could not be stopped.")
        }
    }

    @objc private func openDashboard() {
        if let url = URL(string: "https://sirussofts.com/mac") {
            NSWorkspace.shared.open(url)
        }
    }

    @objc private func openConfig() {
        let location = NSHomeDirectory() + "/.config/sirus-mac-node/config.json"
        NSWorkspace.shared.open(URL(fileURLWithPath: location))
    }

    @objc private func quitUI() { NSApp.terminate(nil) }
}

let app = NSApplication.shared
let delegate = MacNodeMenu()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()

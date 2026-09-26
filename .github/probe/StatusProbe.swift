// Control for the CI: the smallest native menu bar app. If this gets no menu bar space either,
// the runner (not Account Switcher) is what keeps menu bar icons from showing.
import AppKit

class Delegate: NSObject, NSApplicationDelegate {
    var item: NSStatusItem?
    func applicationDidFinishLaunching(_ note: Notification) {
        item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item?.button?.title = "PROBE"
        DispatchQueue.main.asyncAfter(deadline: .now() + 3) {
            let f = self.item?.button?.window?.frame ?? .zero
            print("probe frame:", f.origin.x, f.origin.y, f.size.width, f.size.height)
            fflush(stdout)
        }
    }
}

let app = NSApplication.shared
let delegate = Delegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()

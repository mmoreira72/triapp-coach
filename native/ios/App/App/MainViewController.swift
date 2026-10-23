import UIKit
import Capacitor

/// Bridge view controller that registers the app's own plugins (they live in this target, not in npm packages).
class MainViewController: CAPBridgeViewController {
    override open func capacitorDidLoad() {
        bridge?.registerPluginInstance(HealthPlugin())
        bridge?.registerPluginInstance(LocalLLMPlugin())
        bridge?.registerPluginInstance(WidgetBridgePlugin())
    }
}

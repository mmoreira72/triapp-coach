import Foundation
import Capacitor
import WidgetKit

/// Hands the widget its data: the page calls Capacitor.Plugins.WidgetBridge.setData({json}) and the widget reloads.
@objc(WidgetBridgePlugin)
public class WidgetBridgePlugin: CAPPlugin, CAPBridgedPlugin {
    public let identifier = "WidgetBridgePlugin"
    public let jsName = "WidgetBridge"
    public let pluginMethods: [CAPPluginMethod] = [CAPPluginMethod(name: "setData", returnType: CAPPluginReturnPromise)]

    @objc func setData(_ call: CAPPluginCall) {
        guard let json = call.getString("json"), let defaults = UserDefaults(suiteName: "group.ai.laloop.trisquad") else {
            call.reject("sem dados ou sem App Group")
            return
        }
        defaults.set(json, forKey: "widget")
        WidgetCenter.shared.reloadAllTimelines()
        call.resolve()
    }
}

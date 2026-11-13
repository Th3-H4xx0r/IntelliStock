import SwiftUI

@main
struct IntelliStockApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    /// Built once at launch; reads the keychain synchronously so the first
    /// frame already shows the right gate and lock state.
    @State private var services = AppServices()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(services)
                // Dark only, the off-white tint, Inter: the web app's look.
                .dsAppearance()
                .onAppear {
                    appDelegate.attach(push: services.push)
                    appDelegate.attach(updates: services.appUpdates)
                }
        }
    }
}

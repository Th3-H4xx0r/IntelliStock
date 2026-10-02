import UIKit
import UserNotifications

/// The push half of `mobile/ios/Runner/AppDelegate.swift`: makes the app the
/// notification-center delegate at launch, hands the APNs device token to
/// `PushService`, and shows banners while the app is in the foreground.
final class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    private var push: PushService?
    /// A token that arrived before `attach(push:)`, forwarded on attach.
    private var pendingToken: Data?

    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        return true
    }

    /// Connects the delegate to the app's push service.
    func attach(push: PushService) {
        self.push = push
        if let token = pendingToken {
            pendingToken = nil
            Task { await push.didRegister(deviceToken: token) }
        }
    }

    // APNs handed us a device token — forward its hex form to the backend.
    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        guard let push else {
            pendingToken = deviceToken
            return
        }
        Task { await push.didRegister(deviceToken: deviceToken) }
    }

    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: any Error) {
        NSLog("APNs registration failed: \(error.localizedDescription)")
    }

    // Show the banner even when the app is in the foreground.
    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        [.banner, .sound, .badge]
    }
}

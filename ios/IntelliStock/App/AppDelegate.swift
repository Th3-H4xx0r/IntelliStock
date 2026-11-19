import UIKit
import UserNotifications

/// The push half of `mobile/ios/Runner/AppDelegate.swift`: makes the app the
/// notification-center delegate at launch, hands the APNs device token to
/// `PushService`, and shows banners while the app is in the foreground. The
/// "update available" push goes to `AppUpdateCenter` instead, and a tapped
/// MCP_APPROVAL push opens its approval.
final class AppDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    private var push: PushService?
    /// A token that arrived before `attach(push:)`, forwarded on attach.
    private var pendingToken: Data?
    private weak var updates: AppUpdateCenter?
    /// An update push tapped before `attach(updates:)` (a cold launch from it).
    private var pendingUpdateTap = false
    /// Opens an approval by id (`AppServices.openApproval`).
    private var openApproval: ((String) -> Void)?
    /// An approval push tapped before `attach(openApproval:)` (a cold launch from it).
    private var pendingApprovalId: String?

    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        UNUserNotificationCenter.current().delegate = self
        // The web palette on the system bars, before any bar exists.
        DSSystemBars.configure()
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

    /// Connects the delegate to the over-the-air update center.
    func attach(updates: AppUpdateCenter) {
        self.updates = updates
        if pendingUpdateTap {
            pendingUpdateTap = false
            updates.openFromNotification()
        }
    }

    /// Connects the delegate to the approval screen, opening an approval push
    /// tapped before it.
    func attach(openApproval: @escaping (String) -> Void) {
        self.openApproval = openApproval
        if let id = pendingApprovalId {
            pendingApprovalId = nil
            openApproval(id)
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

    // Show the banner even when the app is in the foreground — except the update
    // push while the sheet can show it, which shows the sheet instead.
    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        if AppUpdateRules.isUpdatePush(notification.request.content.userInfo), await updatePushArrived() {
            return []
        }
        return [.banner, .sound, .badge]
    }

    // A tapped update push opens the app onto the sheet, even after Later; a
    // tapped approval push opens that approval. Only the Sendable values read
    // here cross to the main actor.
    nonisolated func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse
    ) async {
        let info = response.notification.request.content.userInfo
        let isUpdate = AppUpdateRules.isUpdatePush(info)
        let id = ApprovalPush.approvalId(from: info)
        await routeTap(isUpdate: isUpdate, approvalId: id)
    }

    /// Where a tapped push goes: the update sheet, or the approval it names —
    /// held until `attach(openApproval:)` when nothing is attached yet.
    func routeTap(isUpdate: Bool, approvalId: String?) {
        if isUpdate {
            updatePushTapped()
        } else if let approvalId {
            if let openApproval {
                openApproval(approvalId)
            } else {
                pendingApprovalId = approvalId
            }
        }
    }

    private func updatePushArrived() -> Bool {
        updates?.pushArrived() ?? false
    }

    private func updatePushTapped() {
        if let updates {
            updates.openFromNotification()
        } else {
            pendingUpdateTap = true
        }
    }
}

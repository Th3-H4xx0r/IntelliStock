import Foundation
import UIKit
import UserNotifications

/// The system calls `PushService` needs, so tests can stand in for APNs.
protocol PushRegistering: AnyObject {
    /// Asks for alert, badge and sound permission; true when granted.
    func requestAuthorization() async -> Bool
    func registerForRemoteNotifications()
}

/// `UNUserNotificationCenter` + `UIApplication` — what the Flutter
/// `AppDelegate`'s `requestAndRegister` did on the `registerPush` call.
final class SystemPushRegistrar: PushRegistering {
    func requestAuthorization() async -> Bool {
        (try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .badge, .sound])) ?? false
    }

    func registerForRemoteNotifications() {
        UIApplication.shared.registerForRemoteNotifications()
    }
}

/// Bridges APNs registration to the backend — `PushService` in
/// `push_service.dart` plus the native half of the old MethodChannel.
///
/// `enable()` asks for permission and registers for remote notifications;
/// `AppDelegate` hands the device token back through `didRegister(deviceToken:)`,
/// which forwards it to `POST /push/devices`. Best-effort throughout: iOS
/// re-delivers the token on the next launch.
final class PushService {
    /// Debug builds talk to the APNs sandbox; release builds to production.
    static var defaultEnv: String {
        #if DEBUG
        "sandbox"
        #else
        "prod"
        #endif
    }

    static var bundleVersion: String? {
        Bundle.main.infoDictionary?["CFBundleShortVersionString"] as? String
    }

    let env: String
    let appVersion: String?

    private let repository: () -> PushRepository
    private let registrar: any PushRegistering
    private let shouldRegister: () -> Bool

    /// `repository` is read per call, so a server change is picked up.
    /// `shouldRegister` gates every registration — `AppServices` passes
    /// "signed in and not locked", since Dart sent nothing from behind the
    /// lock screen.
    init(
        repository: @escaping () -> PushRepository,
        registrar: any PushRegistering = SystemPushRegistrar(),
        env: String = PushService.defaultEnv,
        appVersion: String? = PushService.bundleVersion,
        shouldRegister: @escaping () -> Bool = { true }
    ) {
        self.repository = repository
        self.registrar = registrar
        self.env = env
        self.appVersion = appVersion
        self.shouldRegister = shouldRegister
    }

    /// Asks iOS to register for push. Safe to call on every sign-in: once the
    /// person has answered, the system does not prompt again. Does nothing
    /// while `shouldRegister` is false.
    func enable() async {
        guard shouldRegister() else { return }
        guard await registrar.requestAuthorization() else { return }
        registrar.registerForRemoteNotifications()
    }

    /// APNs handed over a device token: forward its hex form.
    func didRegister(deviceToken: Data) async {
        await register(token: Self.hex(deviceToken))
    }

    func register(token: String) async {
        guard !token.isEmpty, shouldRegister() else { return }
        try? await repository().registerToken(token, env: env, appVersion: appVersion)
    }

    static func hex(_ data: Data) -> String {
        data.map { String(format: "%02x", $0) }.joined()
    }
}

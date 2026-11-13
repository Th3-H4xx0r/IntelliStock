import Foundation
import Observation
import SwiftUI
import UIKit

/// A newer build of this app the server is offering (`GET /app-updates/latest`).
/// Ported from JarvisCopilot's `AppUpdateCenter.swift`.
nonisolated struct AppUpdateOffer: Equatable, Identifiable, Sendable {
    let build: Int
    let version: String
    let notes: String
    let installURL: URL
    /// Bytes to download; 0 when the server didn't say.
    var size: Int64 = 0

    /// One sheet per build: a fresh link for the same build keeps the sheet up.
    var id: Int { build }

    /// The link's token, which names it to `/app-updates/install-status`: the
    /// manifest URL inside `itms-services://?…&url=…/ota/m/<token>.plist`.
    var installToken: String? {
        guard let inner = URLComponents(url: installURL, resolvingAgainstBaseURL: false)?
                .queryItems?.first(where: { $0.name == "url" })?.value,
              let name = URL(string: inner)?.lastPathComponent, name.hasSuffix(".plist") else { return nil }
        return String(name.dropLast(".plist".count))
    }

    /// The notes one line per item, with the "•" / "-" / "*" the publisher typed
    /// taken off.
    var noteItems: [String] {
        notes.split(whereSeparator: \.isNewline).map {
            var line = $0.trimmingCharacters(in: .whitespaces)
            if let first = line.first, "•-*".contains(first) { line.removeFirst() }
            return line.trimmingCharacters(in: .whitespaces)
        }.filter { !$0.isEmpty }
    }

    /// "IntelliStock 1.0.0 · Build 263161830 · 12.3 MB" — the build as digits,
    /// never "263,161,830".
    var subtitle: String {
        var parts = [version.isEmpty ? "IntelliStock" : "IntelliStock \(version)", "Build \(String(build))"]
        if size > 0 {
            parts.append(ByteCountFormatter.string(fromByteCount: size, countStyle: .file))
        }
        return parts.joined(separator: " · ")
    }

    /// The server's answer, or nil when it offers nothing newer than `installed`
    /// (or offers it without an install link).
    static func decode(_ json: JSON, installed: Int) -> AppUpdateOffer? {
        guard json["available"].bool,
              let build = json["build"].int, build > installed,
              let link = json["install_url"].string, !link.isEmpty,
              let url = URL(string: link), url.scheme != nil
        else { return nil }
        return AppUpdateOffer(
            build: build,
            version: json["version"].string ?? "",
            notes: (json["notes"].string ?? "").trimmingCharacters(in: .whitespacesAndNewlines),
            installURL: url,
            size: Int64(json["size"].int ?? 0)
        )
    }
}

nonisolated enum AppUpdateRules {
    /// The `kind` (and APNs `category`) the API's "update available" push carries.
    static let pushKind = "app_update"

    /// The API's update push. `apns_sender` lays the data out at the top level of
    /// the payload: `{"aps": …, "kind": "app_update", "build": N, "category": "app_update"}`.
    static func isUpdatePush(_ userInfo: [AnyHashable: Any]) -> Bool {
        (userInfo["kind"] as? String) == pushKind || (userInfo["category"] as? String) == pushKind
    }

    /// Up only in front, and not for the build the user said Later to (until the
    /// app is opened again).
    static func shouldShow(_ offer: AppUpdateOffer?, foreground: Bool, laterBuild: Int?) -> Bool {
        guard let offer, foreground else { return false }
        return offer.build != laterBuild
    }

    /// This build's number (`CFBundleVersion`, stamped by `ios/scripts/release.sh`;
    /// 0 when unreadable). The server never offers an update to build 0 or 1.
    static func installedBuild(_ info: [String: Any]? = Bundle.main.infoDictionary) -> Int {
        Int(info?["CFBundleVersion"] as? String ?? "") ?? 0
    }
}

/// What the last check found, for the Settings row.
nonisolated enum AppUpdateCheckStatus: Equatable, Sendable {
    case idle
    case checking
    case upToDate
    case available(Int)
    /// The server has a newer build but can't hand out an install link (why).
    case unavailable(String)
    case failed(String)
}

/// `GET /app-updates/latest` and `GET /app-updates/install-status`.
nonisolated struct AppUpdateRepository: Sendable {
    let client: ApiClient

    func latest(installedBuild: Int) async throws -> JSON {
        try await client.get("/app-updates/latest", query: ["platform": "ios", "build": .string(String(installedBuild))])
    }

    /// True once iOS has started downloading the package behind `token`.
    func installStarted(token: String) async throws -> Bool {
        try await client.get("/app-updates/install-status", query: ["token": .string(token)])["started"].bool
    }
}

/// The "Update Available" sheet's brain. `scripts/publish-app-update.sh` puts a
/// build on the server; the app asks for it at launch and on every return to the
/// foreground, a push ("IntelliStock update available") brings it up at once, and
/// Install Now hands iOS a short-lived `itms-services` link. iOS confirms, the app
/// steps aside, and iOS installs over it keeping its data.
@Observable
final class AppUpdateCenter {
    private(set) var offer: AppUpdateOffer?
    private(set) var installing = false
    private(set) var error: String?
    private(set) var status: AppUpdateCheckStatus = .idle
    private(set) var foreground = false
    private(set) var laterBuild: Int?

    let installedBuild: Int

    /// The offer the sheet shows, or nil when it must be down.
    var presented: AppUpdateOffer? {
        AppUpdateRules.shouldShow(offer, foreground: foreground, laterBuild: laterBuild) ? offer : nil
    }

    @ObservationIgnored private let repository: () -> AppUpdateRepository
    @ObservationIgnored private let canCheck: () -> Bool
    @ObservationIgnored private let openURL: (URL) async -> Bool
    @ObservationIgnored private let quit: @MainActor () -> Void
    @ObservationIgnored private let sleep: (Duration) async throws -> Void
    @ObservationIgnored private var wasBackgrounded = true
    @ObservationIgnored private var inFlight: Task<Bool, Never>?
    @ObservationIgnored private var installWatch: Task<Void, Never>?

    /// `repository` is read per call, so a server change is picked up.
    /// `canCheck` gates every check — `AppServices` passes "signed in and not
    /// locked".
    init(
        repository: @escaping () -> AppUpdateRepository,
        installedBuild: Int = AppUpdateRules.installedBuild(),
        canCheck: @escaping () -> Bool = { true },
        openURL: @escaping (URL) async -> Bool = { await UIApplication.shared.open($0) },
        quit: @escaping @MainActor () -> Void = AppUpdateCenter.quitForInstall,
        sleep: @escaping (Duration) async throws -> Void = { try await Task.sleep(for: $0) }
    ) {
        self.repository = repository
        self.installedBuild = installedBuild
        self.canCheck = canCheck
        self.openURL = openURL
        self.quit = quit
        self.sleep = sleep
    }

    /// Every `scenePhase` change the signed-in shell sees (`MainTabView`).
    func scenePhaseChanged(to phase: ScenePhase) {
        switch phase {
        case .active:
            foreground = true
            // A fresh open brings back an offer the user put off with Later.
            if wasBackgrounded { laterBuild = nil }
            wasBackgrounded = false
            Task { await check() }
        case .background:
            foreground = false
            wasBackgrounded = true
        default:
            // `.inactive` is iOS's own install confirm (or Control Center): the
            // sheet stays up behind it.
            break
        }
    }

    /// The signed-in shell left the screen (the lock rose, or the session
    /// ended). Counts as leaving, and drops the offer: the next shell, perhaps
    /// for another server, asks again.
    func shellDisappeared() {
        foreground = false
        wasBackgrounded = true
        offer = nil
        error = nil
    }

    /// The update push was tapped: show the sheet even if Later hid it.
    func openFromNotification() {
        laterBuild = nil
        Task { await check() }
    }

    /// Settings › Check for Updates: brings the sheet back even after Later.
    func checkNow() async {
        laterBuild = nil
        await check()
    }

    /// An update push arrived with the app open. True when the sheet can show
    /// it, so the banner is not needed.
    func pushArrived() -> Bool {
        guard foreground, canCheck() else { return false }
        Task { await check() }
        return true
    }

    /// Ask the server whether there's something newer than this build. False when
    /// it couldn't be asked (the offer in hand is then left as it was). Calls that
    /// overlap share one request.
    @discardableResult
    func check() async -> Bool {
        if let inFlight { return await inFlight.value }
        let task = Task { await self.performCheck() }
        inFlight = task
        let asked = await task.value
        if inFlight == task { inFlight = nil }
        return asked
    }

    private func performCheck() async -> Bool {
        guard canCheck() else { return false }
        status = .checking
        do {
            let answer = try await repository().latest(installedBuild: installedBuild)
            let fresh = AppUpdateOffer.decode(answer, installed: installedBuild)
            offer = fresh
            if let fresh {
                status = .available(fresh.build)
            } else if answer["available"].bool, let why = answer["error"].string, !why.isEmpty {
                status = .unavailable(why)
            } else {
                status = .upToDate
            }
            return true
        } catch is CancellationError {
            status = .idle
            return false
        } catch {
            status = .failed((error as? ApiError)?.message ?? error.localizedDescription)
            return false
        }
    }

    func later() {
        laterBuild = offer?.build
    }

    /// The sheet went away without a button (a swipe down): the same as Later.
    func sheetDismissed() {
        if foreground, presented != nil { later() }
    }

    func install() async {
        guard !installing else { return }
        installing = true
        error = nil
        defer { installing = false }
        // The link in hand may be minutes old and links expire, so only a fresh one goes.
        guard await check() else {
            error = "Couldn't reach the server to start the install. Try again."
            return
        }
        guard let offer else { return }
        // iOS takes it from here: its own confirm, then the install. The sheet
        // stays, so a cancelled confirm can be tried again.
        if await openURL(offer.installURL) {
            if let token = offer.installToken { quitOnceInstalling(token) }
        } else {
            error = "iOS couldn't open the install link."
        }
    }

    /// iOS won't install over an app that's still running, and it never closes it
    /// itself. It fetches the package only after the user taps Install on its
    /// confirm, so once the server has seen that fetch the app steps out to the
    /// Home Screen (where the icon shows the install) and quits. A Cancel never
    /// fetches, so the sheet just stays up.
    private func quitOnceInstalling(_ token: String) {
        installWatch?.cancel()
        let repository = repository()
        installWatch = Task { [weak self] in
            for _ in 0..<180 {
                guard let self else { return }
                try? await self.sleep(.seconds(1))
                guard !Task.isCancelled else { return }
                if (try? await repository.installStarted(token: token)) == true {
                    self.quit()
                    return
                }
            }
        }
    }

    /// Home first, so it reads as stepping aside rather than a crash; then gone.
    static func quitForInstall() {
        let app = UIApplication.shared
        let task = app.beginBackgroundTask(withName: "app-update-quit")
        UIControl().sendAction(#selector(URLSessionTask.suspend), to: app, for: nil)
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) {
            _ = task
            exit(0)
        }
    }
}

import Foundation
import SwiftUI
import Testing
import UIKit
@testable import IntelliStock

/// The "Update Available" sheet: what counts as an offer, and when it may show.
/// Ported from JarvisCopilot's AppUpdateCenterTests.
struct AppUpdateOfferTests {
    /// What `GET /app-updates/latest` answers, with `overrides` laid over it.
    static func makeAnswer(_ overrides: [String: JSON] = [:]) -> JSON {
        var fields: [String: JSON] = [
            "available": true, "build": 263161830, "version": "1.0.0",
            "notes": "\n• Order history\n- Faster charts\n\n",
            "size": 12_345_678,
            "install_url": "itms-services://?action=download-manifest&url=https%3A%2F%2Fapi.example.dev%2Fota%2Fm%2Fios.263161830.99.abc.plist",
        ]
        for (key, value) in overrides { fields[key] = value }
        return .object(fields)
    }

    static let answer = makeAnswer()

    @Test func aNewerBuildIsAnOffer() throws {
        let offer = try #require(AppUpdateOffer.decode(Self.answer, installed: 2))
        #expect(offer.build == 263161830)
        #expect(offer.version == "1.0.0")
        #expect(offer.notes == "• Order history\n- Faster charts")
        #expect(offer.noteItems == ["Order history", "Faster charts"])
        #expect(offer.size == 12_345_678)
        #expect(offer.installURL.scheme == "itms-services")
        #expect(offer.installToken == "ios.263161830.99.abc", "the token that names the link to install-status")
        #expect(offer.id == offer.build)
    }

    @Test func theSubtitleShowsTheBuildAsDigits() throws {
        let offer = try #require(AppUpdateOffer.decode(Self.answer, installed: 2))
        #expect(offer.subtitle.hasPrefix("IntelliStock 1.0.0 · Build 263161830 · "))
        #expect(!offer.subtitle.contains("263,161,830"))
    }

    @Test func theSameOrAnOlderBuildOrNoLinkIsNoOffer() {
        #expect(AppUpdateOffer.decode(Self.answer, installed: 263161830) == nil)
        #expect(AppUpdateOffer.decode(Self.answer, installed: 263161900) == nil)
        #expect(AppUpdateOffer.decode(Self.makeAnswer(["available": false]), installed: 2) == nil)
        #expect(AppUpdateOffer.decode(Self.makeAnswer(["install_url": ""]), installed: 2) == nil)
        #expect(AppUpdateOffer.decode(Self.makeAnswer(["install_url": .null]), installed: 2) == nil)
        #expect(AppUpdateOffer.decode(.null, installed: 2) == nil)
    }

    @Test func theUpdatePushIsRecognisedAsTheApiSendsIt() {
        // apns_sender._build_payload: data keys at the top level, plus `category`.
        let push: [AnyHashable: Any] = [
            "aps": ["alert": ["title": "IntelliStock update available", "body": "Build 263161830 — Order history"]],
            "kind": "app_update", "platform": "ios", "build": 263161830, "category": "app_update",
        ]
        #expect(AppUpdateRules.isUpdatePush(push))
        #expect(AppUpdateRules.isUpdatePush(["category": "app_update"]))
        #expect(!AppUpdateRules.isUpdatePush(["category": "test", "notif_key": "fill"]))
        #expect(!AppUpdateRules.isUpdatePush([:]))
    }

    @Test func theSheetWaitsForTheForegroundAndRespectsLater() throws {
        let offer = try #require(AppUpdateOffer.decode(Self.answer, installed: 2))
        #expect(AppUpdateRules.shouldShow(offer, foreground: true, laterBuild: nil))
        #expect(!AppUpdateRules.shouldShow(offer, foreground: false, laterBuild: nil))
        #expect(!AppUpdateRules.shouldShow(offer, foreground: true, laterBuild: offer.build))
        // Later on an older offer doesn't hide a newer one.
        #expect(AppUpdateRules.shouldShow(offer, foreground: true, laterBuild: 5))
        #expect(!AppUpdateRules.shouldShow(nil, foreground: true, laterBuild: nil))
    }

    @Test func theInstalledBuildIsCFBundleVersion() {
        #expect(AppUpdateRules.installedBuild(["CFBundleVersion": "263161830"]) == 263161830)
        #expect(AppUpdateRules.installedBuild(["CFBundleVersion": "1.2"]) == 0)
        #expect(AppUpdateRules.installedBuild(nil) == 0)
        #expect(AppUpdateRules.installedBuild() > 0)
    }

    @Test func theSettingsRowSaysWhatTheLastCheckFound() {
        #expect(appUpdateSubtitle(.upToDate) == "IntelliStock is up to date")
        #expect(appUpdateSubtitle(.available(263161830)) == "Build 263161830 is available")
        #expect(appUpdateSubtitle(.failed("Cannot reach the server.")).hasSuffix("Cannot reach the server."))
    }
}

/// The center against a stubbed server.
@MainActor
struct AppUpdateCenterTests {
    private let stub = DataStub()

    private static var offerJSON: String {
        let data = try! AppUpdateOfferTests.answer.data()
        return String(decoding: data, as: UTF8.self)
    }

    private final class Probe {
        var opened: [URL] = []
        var openResult = true
        var quits = 0
        var canCheck = true
    }

    private func center(installed: Int = 2, probe: Probe = Probe()) -> AppUpdateCenter {
        let client = stub.client
        return AppUpdateCenter(
            repository: { AppUpdateRepository(client: client) },
            installedBuild: installed,
            canCheck: { probe.canCheck },
            openURL: { url in probe.opened.append(url); return probe.openResult },
            quit: { probe.quits += 1 },
            sleep: { _ in await Task.yield() }
        )
    }

    @Test func aCheckAsksTheServerAboutThisBuild() async throws {
        stub.respond(json: Self.offerJSON)
        let center = center(installed: 2)
        #expect(await center.check())
        let req = try #require(stub.requests.first)
        #expect(req.httpMethod == "GET")
        #expect(req.url?.path == "/app-updates/latest")
        #expect(req.url?.query == "build=2&platform=ios")
        #expect(center.offer?.build == 263161830)
        #expect(center.status == .available(263161830))
    }

    @Test func theSheetIsUpOnlyInTheForeground() async {
        stub.respond(json: Self.offerJSON)
        let center = center()
        await center.check()
        #expect(center.presented == nil, "not in front yet")
        center.scenePhaseChanged(to: .active)
        #expect(center.presented?.build == 263161830)
        center.scenePhaseChanged(to: .inactive)
        #expect(center.presented != nil, "iOS's own install confirm leaves it up")
        center.scenePhaseChanged(to: .background)
        #expect(center.presented == nil)
    }

    @Test func laterHidesTheOfferUntilTheNextFreshOpen() async {
        stub.respond(json: Self.offerJSON)
        let center = center()
        center.scenePhaseChanged(to: .active)
        await center.check()
        center.later()
        #expect(center.presented == nil)
        // A foreground without a background in between (an inactive blip) keeps Later.
        center.scenePhaseChanged(to: .active)
        await center.check()
        #expect(center.presented == nil)
        center.scenePhaseChanged(to: .background)
        center.scenePhaseChanged(to: .active)
        await center.check()
        #expect(center.presented?.build == 263161830)
    }

    @Test func aSwipeDownIsLater() async {
        stub.respond(json: Self.offerJSON)
        let center = center()
        center.scenePhaseChanged(to: .active)
        await center.check()
        center.sheetDismissed()
        #expect(center.laterBuild == 263161830)
        #expect(center.presented == nil)
    }

    @Test func aTappedPushOrSettingsBringsBackAnOfferPutOffWithLater() async {
        stub.respond(json: Self.offerJSON)
        let center = center()
        center.scenePhaseChanged(to: .active)
        await center.check()
        center.later()
        await center.checkNow()
        #expect(center.presented != nil)
        center.later()
        center.openFromNotification()
        #expect(center.presented != nil)
    }

    @Test func aPushWithTheAppOpenShowsTheSheetInsteadOfABanner() async {
        stub.respond(json: Self.offerJSON)
        let probe = Probe()
        let center = center(probe: probe)
        #expect(!center.pushArrived(), "in the background the banner is needed")
        center.scenePhaseChanged(to: .active)
        #expect(center.pushArrived())
        probe.canCheck = false
        #expect(!center.pushArrived(), "behind the lock the banner is needed")
    }

    @Test func noSessionMeansNoCheck() async {
        let probe = Probe()
        probe.canCheck = false
        let center = center(probe: probe)
        #expect(await !center.check())
        #expect(stub.requests.isEmpty)
    }

    @Test func overlappingChecksShareOneRequest() async {
        stub.respond(json: Self.offerJSON)
        let center = center()
        let first = Task { await center.check() }
        let second = Task { await center.check() }
        let a = await first.value
        let b = await second.value
        #expect(a && b)
        #expect(stub.requests.count == 1)
    }

    @Test func anUpToDateAppHasNoOffer() async {
        stub.respond(json: #"{"available": false, "platform": "ios", "build": 2}"#)
        let center = center()
        await center.check()
        #expect(center.offer == nil)
        #expect(center.status == .upToDate)
    }

    @Test func aServerThatCannotLinkSaysWhy() async {
        stub.respond(json: #"{"available": true, "build": 9, "install_url": "", "error": "Install links need https."}"#)
        let center = center()
        await center.check()
        #expect(center.offer == nil)
        #expect(center.status == .unavailable("Install links need https."))
    }

    @Test func aFailedCheckKeepsTheOfferInHand() async {
        stub.respond(json: Self.offerJSON)
        let center = center()
        await center.check()
        stub.respond(status: 500)
        #expect(await !center.check())
        #expect(center.offer?.build == 263161830)
        if case .failed = center.status {} else { Issue.record("status \(center.status)") }
    }

    @Test func theShellLeavingDropsTheOffer() async {
        stub.respond(json: Self.offerJSON)
        let center = center()
        center.scenePhaseChanged(to: .active)
        await center.check()
        center.shellDisappeared()
        #expect(center.offer == nil)
        #expect(center.presented == nil)
    }

    @Test func installOpensAFreshLinkAndQuitsOnceIOSFetchesThePackage() async throws {
        let offer = Self.offerJSON
        stub.handler = { req in
            req.url?.path == "/app-updates/install-status"
                ? (200, #"{"started": true}"#)
                : (200, offer)
        }
        let probe = Probe()
        let center = center(probe: probe)
        center.scenePhaseChanged(to: .active)
        await center.check()
        let before = stub.requests.count
        await center.install()
        #expect(probe.opened.map(\.scheme) == ["itms-services"])
        #expect(stub.requests.count > before, "a fresh link, not the one in hand")
        #expect(center.error == nil)
        #expect(await eventually { probe.quits == 1 })
        let poll = try #require(stub.requests.last { $0.url?.path == "/app-updates/install-status" })
        #expect(poll.url?.query == "token=ios.263161830.99.abc")
    }

    @Test func aCancelledConfirmNeverQuits() async {
        let offer = Self.offerJSON
        stub.handler = { req in
            req.url?.path == "/app-updates/install-status"
                ? (200, #"{"started": false}"#)
                : (200, offer)
        }
        let probe = Probe()
        let center = center(probe: probe)
        await center.install()
        try? await Task.sleep(for: .milliseconds(200))
        #expect(probe.opened.count == 1)
        #expect(probe.quits == 0)
    }

    @Test func installSaysWhenTheServerIsUnreachable() async {
        stub.fail(.notConnectedToInternet)
        let probe = Probe()
        let center = center(probe: probe)
        await center.install()
        #expect(probe.opened.isEmpty)
        #expect(center.error == "Couldn't reach the server to start the install. Try again.")
        #expect(!center.installing)
    }

    @Test func installSaysWhenIOSRefusesTheLink() async {
        stub.respond(json: Self.offerJSON)
        let probe = Probe()
        probe.openResult = false
        let center = center(probe: probe)
        await center.install()
        #expect(center.error == "iOS couldn't open the install link.")
        #expect(probe.quits == 0)
    }

    @Test func theServicesCheckOnlyWhileSignedIn() async {
        let services = AppServices(apiClient: stub.client)
        stub.respond(json: Self.offerJSON)
        #expect(await !services.appUpdates.check(), "signed out")
        #expect(stub.requests.isEmpty)
    }
}

/// The sheet draws (ScrollView can't go through ImageRenderer, so its two parts
/// are drawn stacked). Set TEST_RUNNER_APP_UPDATE_SNAPSHOT_DIR to keep the PNGs.
@MainActor
struct AppUpdateSheetRenderTests {
    @Test(arguments: [ColorScheme.light, .dark])
    func theSheetRenders(scheme: ColorScheme) throws {
        let offer = AppUpdateOffer(
            build: 263161830, version: "1.0.0",
            notes: "• Open orders and order history on the dashboard\n• Over-the-air updates: new builds arrive without a cable\n• Faster charts",
            installURL: URL(string: "itms-services://x")!, size: 12_345_678)
        for (name, error, installing) in [("idle", nil as String?, false), ("error", "iOS couldn't open the install link.", true)] {
            let view = VStack(spacing: 0) {
                AppUpdateSheetContent(offer: offer).padding(.horizontal, 24).padding(.top, 32).padding(.bottom, 16)
                AppUpdateSheetActions(installing: installing, error: error).padding(.horizontal, 24).padding(.bottom, 8)
            }
            .frame(width: 402)
            .background(Color(uiColor: .systemBackground))
            .environment(\.colorScheme, scheme)
            let renderer = ImageRenderer(content: view)
            renderer.scale = 2
            let image = try #require(renderer.uiImage)
            #expect(image.size.width == 402)
            #expect(image.size.height > 300)
            if let dir = ProcessInfo.processInfo.environment["APP_UPDATE_SNAPSHOT_DIR"], !dir.isEmpty {
                let url = URL(fileURLWithPath: dir, isDirectory: true)
                try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
                try image.pngData()?.write(to: url.appendingPathComponent("app-update-\(name)-\(scheme == .dark ? "dark" : "light").png"))
            }
        }
    }
}

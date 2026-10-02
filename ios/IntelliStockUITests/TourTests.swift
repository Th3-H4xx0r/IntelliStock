import XCTest

/// A read-only screenshot tour of every screen against a live backend.
///
/// Run with the `IntelliStockTour` scheme (never part of the unit-test
/// scheme). xcodebuild hands `TEST_RUNNER_<NAME>` variables to this process as
/// `<NAME>`:
///
///     TEST_RUNNER_IS_URL=https://… TEST_RUNNER_IS_USER=… TEST_RUNNER_IS_PASS=…
///     TEST_RUNNER_IS_DETAILS="instance=/instances/x,backtest=/backtests/y,…"
///
/// The tour only navigates. It never taps start, stop, trade, delete, approve
/// or confirm, and it declines the notification prompt so the simulator never
/// registers for push. Every stop is a kept screenshot attachment; export them
/// with `xcrun xcresulttool export attachments`.
final class TourTests: XCTestCase {
    private let app = XCUIApplication()
    private let springboard = XCUIApplication(bundleIdentifier: "com.apple.springboard")
    private var env: [String: String] { ProcessInfo.processInfo.environment }

    override func setUp() {
        continueAfterFailure = true
    }

    func testTour() throws {
        let url = try XCTUnwrap(env["IS_URL"], "set TEST_RUNNER_IS_URL")
        let user = try XCTUnwrap(env["IS_USER"], "set TEST_RUNNER_IS_USER")
        let pass = try XCTUnwrap(env["IS_PASS"], "set TEST_RUNNER_IS_PASS")

        app.launch()
        shot("00-launch")
        signIn(url: url, user: user, pass: pass)

        // Tab roots.
        for (n, tab) in ["Dashboard", "Kalshi", "Instances", "Strategies"].enumerated() {
            tapTab(tab)
            settle(5)
            shot("1\(n)-tab-\(tab)")
            for page in 1...2 {
                app.swipeUp()
                settle(1)
                shot("1\(n)-tab-\(tab)-scroll\(page)")
            }
        }

        // More destinations (more_sheet.dart order).
        tapTab("More")
        settle(1)
        shot("20-tab-More")
        let more = ["Crypto", "Backtests", "Brokerages", "Agent Runs", "Nexus Graph",
                    "Learning", "Models", "Token Usage", "Settings"]
        for (n, row) in more.enumerated() {
            tapTab("More")
            guard tapRow(row) else { continue }
            settle(5)
            shot("2\(n + 1)-more-\(row)")
            app.swipeUp()
            settle(1)
            shot("2\(n + 1)-more-\(row)-scroll")
            if row == "Settings", tapRow("Notifications") {
                settle(3)
                shot("2\(n + 1)-more-Settings-Notifications")
                back()
            }
            back()
        }

        // Detail routes via deep links: "name=/path,name=/path".
        let details = (env["IS_DETAILS"] ?? "").split(separator: ",").compactMap { pair -> (String, String)? in
            let parts = pair.split(separator: "=", maxSplits: 1).map(String.init)
            return parts.count == 2 ? (parts[0], parts[1]) : nil
        }
        for (n, (name, path)) in details.enumerated() {
            tapTab("Dashboard")
            openDeepLink(path)
            settle(6)
            shot("3\(n)-detail-\(name)")
            app.swipeUp()
            settle(1)
            shot("3\(n)-detail-\(name)-scroll")
        }

        // Chat entry, if the floating button is present.
        tapTab("Dashboard")
        let chat = app.buttons.matching(NSPredicate(format: "label CONTAINS[c] 'chat' OR label CONTAINS[c] 'assistant'")).firstMatch
        if chat.waitForExistence(timeout: 3) {
            chat.tap()
            settle(3)
            shot("40-chat")
        }
    }

    // MARK: Steps

    private func signIn(url: String, user: String, pass: String) {
        // Connect: the only text field on screen. Its button reads "Test & Connect".
        if app.staticTexts["Connect to your instance"].waitForExistence(timeout: 8) {
            let urlField = app.textFields.firstMatch
            urlField.tap()
            urlField.typeText(url)
            shot("01-connect")
            let connect = app.buttons.matching(NSPredicate(format: "label CONTAINS[c] 'Connect' OR label IN {'Continue', 'Save'}")).firstMatch
            connect.tap()
            settle(5)
            let saveAnyway = app.buttons.matching(NSPredicate(format: "label BEGINSWITH[c] 'Save anyway'")).firstMatch
            if saveAnyway.exists { saveAnyway.tap() }
        }
        // Login: a username text field and a password secure field.
        let signIn = app.buttons["Sign In"]
        if signIn.waitForExistence(timeout: 15) {
            settle(2)
            shot("02-login")
            let username = app.textFields.firstMatch
            username.tap()
            username.typeText(user)
            let password = app.secureTextFields.firstMatch.exists ? app.secureTextFields.firstMatch : app.textFields.element(boundBy: 1)
            password.tap()
            password.typeText(pass)
            signIn.tap()
        }
        declineNotificationsIfAsked()
        XCTAssertTrue(app.tabBars.buttons["Dashboard"].waitForExistence(timeout: 30), "never reached the signed-in tabs")
        declineNotificationsIfAsked()
    }

    private func declineNotificationsIfAsked() {
        for label in ["Don’t Allow", "Don't Allow"] {
            let button = springboard.buttons[label]
            if button.waitForExistence(timeout: 3) {
                button.tap()
                return
            }
        }
    }

    private func tapTab(_ name: String) {
        let tab = app.tabBars.buttons[name]
        if tab.waitForExistence(timeout: 5) { tab.tap() }
        // A second tap on the selected tab pops its stack to the root.
        if name == "More", app.navigationBars.buttons.count > 0, !app.staticTexts["Crypto"].exists {
            tab.tap()
            settle(1)
        }
    }

    private func tapRow(_ label: String) -> Bool {
        let candidates = [app.buttons[label], app.cells.staticTexts[label], app.staticTexts[label]]
        for element in candidates where element.waitForExistence(timeout: 3) {
            element.tap()
            return true
        }
        XCTFail("row not found: \(label)")
        return false
    }

    private func back() {
        let backButton = app.navigationBars.buttons.element(boundBy: 0)
        if backButton.exists { backButton.tap() }
        settle(1)
    }

    private func openDeepLink(_ path: String) {
        guard let link = URL(string: "intellistock:/" + path) else { return }
        XCUIDevice.shared.system.open(link)
        let open = springboard.buttons["Open"]
        if open.waitForExistence(timeout: 3) { open.tap() }
    }

    private func settle(_ seconds: UInt32) {
        sleep(seconds)
    }

    private func shot(_ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }
}

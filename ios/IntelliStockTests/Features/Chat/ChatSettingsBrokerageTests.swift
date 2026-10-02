import Foundation
import Testing
@testable import IntelliStock

/// `test/features/settings/notification_prefs_controller_test.dart` plus the
/// screen's behaviour (`notification_settings_screen_test.dart`).
@MainActor
@Suite struct ChatNotificationPrefsTests {
    private static let seed = #"{"categories":{"order_fill":{"discord":true,"push":false}}}"#

    private func make(_ getJSON: String = seed) -> (NotificationPrefsModel, DataStub, ChatStubRoutes) {
        let stub = DataStub()
        let routes = ChatStubRoutes()
        routes.set("GET /notification-preferences", getJSON)
        routes.stub = stub
        stub.handler = { request in
            // Save echoes the body back, like the fake repository.
            if request.method == "PUT" {
                return (200, String(decoding: request.httpBody ?? Data("{}".utf8), as: UTF8.self))
            }
            return routes.answer(request)
        }
        let client = stub.client
        return (NotificationPrefsModel(repository: { NotificationPrefsRepository(client: client) }), stub, routes)
    }

    @Test func loadsPreferencesFromTheRepository() async throws {
        let (model, stub, _) = make()
        await model.load()
        let path = try #require(stub.last?.path)
        #expect(path.hasPrefix("/"))
        let prefs = try #require(model.prefs.value)
        #expect(prefs.routeFor("order_fill").discord)
        #expect(!prefs.routeFor("order_fill").push)
    }

    @Test func toggleOptimisticallyUpdatesAndPersists() async throws {
        let (model, stub, _) = make()
        await model.load()
        let before = stub.requests.count
        let error = await model.toggle("order_fill", .push, true)
        #expect(error == nil)
        #expect(stub.requests.count == before + 1)
        #expect(stub.last?.method == "PUT")
        let prefs = try #require(model.prefs.value)
        #expect(prefs.routeFor("order_fill").push)
        #expect(prefs.routeFor("order_fill").discord)
    }

    @Test func toggleRevertsOnSaveFailureAndReturnsAnError() async throws {
        let (model, stub, routes) = make()
        await model.load()
        stub.handler = { routes.answer($0) }   // PUT now 404s
        let error = await model.toggle("order_fill", .push, true)
        #expect(error != nil)
        #expect(try #require(model.prefs.value).routeFor("order_fill").push == false)
    }

    @Test func toggleBeforeLoadIsRefused() async {
        let (model, _, _) = make()
        #expect(await model.toggle("order_fill", .push, true) == "Preferences not loaded yet")
    }

    @Test func groupedTypesFallBackToTheNineteenCategories() throws {
        let prefs = NotificationPrefs(json: try JSON(data: Data(Self.seed.utf8)))
        let groups = NotificationPrefsModel.groupedTypes(prefs)
        #expect(groups.map(\.group) == ["Notifications"])
        #expect(groups[0].types.count == 19)
        #expect(groups[0].types.contains { $0.label == "Order filled" })
        #expect(groups[0].types.contains { $0.label == "Crash loop" })
        #expect(groups[0].types.contains { $0.label == "Approved order refused or unconfirmed" })
    }

    @Test func groupedTypesFollowTheApiTaxonomy() throws {
        let prefs = NotificationPrefs(json: [
            "categories": ["order_fill": ["discord": true, "push": false]],
            "types": [
                ["key": "order_fill", "group": "Trading", "label": "Order filled", "desc": "An order filled"],
                ["key": "halt", "group": "Risk & Halts", "label": "Halt", "desc": "Trading halted"],
                ["key": "order_submit", "group": "Trading", "label": "Submitted", "desc": "Sent"],
            ],
        ])
        let groups = NotificationPrefsModel.groupedTypes(prefs)
        #expect(groups.map(\.group) == ["Trading", "Risk & Halts"])
        #expect(groups[0].types.map(\.key) == ["order_fill", "order_submit"])
    }

    @Test func testOutcomeMessages() {
        #expect(NotificationTestOutcome(channel: .discord, result: ["ok": true]) == NotificationTestOutcome(message: "Discord test sent ✓", ok: true))
        #expect(NotificationTestOutcome(channel: .discord, result: ["ok": false]).message == "Discord test could not be sent")
        #expect(NotificationTestOutcome(channel: .push, result: ["ok": true]).message == "iOS push test sent ✓")
        #expect(NotificationTestOutcome(channel: .push, result: ["ok": false]).message
            == "No iOS device registered yet — tap \"Enable push on this device\".")
        #expect(NotificationTestOutcome(channel: .push, result: ["ok": false, "devices": 0]).message
            == "No iOS device registered yet — tap \"Enable push on this device\".")
        #expect(NotificationTestOutcome(channel: .push, result: ["ok": false, "devices": 1, "errors": [["reason": "BadDeviceToken"]]]).message
            == "Push failed: BadDeviceToken")
        #expect(NotificationTestOutcome(channel: .push, result: ["ok": false, "devices": 2]).message
            == "Push not delivered — check APNs setup.")
        #expect(NotificationTestOutcome(channel: .discord, error: ApiError(message: "boom", statusCode: 500)).message
            == "Discord test failed: boom")
    }

    @Test func deviceSubtitleAndVersion() {
        let device = PushDevice(json: ["device_token": "0123456789abcdef", "platform": "ios", "env": "sandbox", "last_seen": "2026-06-11T00:00:00Z"])
        #expect(device.tokenSuffix == "…89abcdef")
        #expect(pushDeviceSubtitle(device) == "iOS · sandbox · seen 2026-06-11")
        #expect(appVersionString(["CFBundleShortVersionString": "1.2.0", "CFBundleVersion": "7"]) == "1.2.0+7")
        #expect(appVersionString(["CFBundleShortVersionString": "1.2.0", "CFBundleVersion": ""]) == "1.2.0")
    }
}

/// `BrokeragesController` and the Link / Edit sheet.
@MainActor
@Suite struct ChatBrokerageTests {
    private func makeForm(edit: Brokerage? = nil) -> (LinkBrokerageFormModel, DataStub, ChatStubRoutes) {
        let stub = DataStub()
        let routes = ChatStubRoutes()
        routes.stub = stub
        stub.handler = { routes.answer($0) }
        let client = stub.client
        return (LinkBrokerageFormModel(editAccount: edit, repository: { BrokerageRepository(client: client) }), stub, routes)
    }

    private func body(_ request: URLRequest?) throws -> JSON {
        try JSON(data: request?.httpBody ?? Data())
    }

    private static let alpacaEdit = Brokerage(json: [
        "id": "b1", "brokerage_type": "alpaca", "account_name": "Paper", "paper": false, "alpaca_data_feed": "sip",
    ])

    @Test func listRefreshAndRemove() async throws {
        let stub = DataStub()
        let routes = ChatStubRoutes()
        routes.set("GET /brokerages", #"{"accounts":[{"id":"b1","brokerage_type":"alpaca","account_name":"A"}]}"#)
        routes.set("DELETE /brokerages/b1", "{}")
        stub.handler = { routes.answer($0) }
        let client = stub.client
        let model = BrokeragesModel(repository: { BrokerageRepository(client: client) })
        await model.load()
        #expect(model.accounts.value?.map(\.id) == ["b1"])

        routes.set("GET /brokerages", #"{"accounts":[]}"#)
        try await model.remove("b1")
        #expect(stub.requests.contains { $0.method == "DELETE" && $0.path == "/brokerages/b1" })
        #expect(model.accounts.value?.isEmpty == true)
    }

    @Test func cardHelpers() {
        #expect(BrokeragesModel.badgeLabel(Brokerage(json: ["brokerage_type": "alpaca", "paper": true])) == "ALPACA · Paper")
        #expect(BrokeragesModel.badgeLabel(Brokerage(json: ["brokerage_type": "alpaca", "paper": false])) == "ALPACA · Live")
        #expect(BrokeragesModel.badgeLabel(Brokerage(json: ["brokerage_type": "binanceus"])) == "BINANCEUS")
        #expect(BrokeragesModel.statusTone("active") == .active)
        #expect(BrokeragesModel.statusTone("expired") == .expired)
        #expect(BrokeragesModel.statusTone(nil) == .other)
        #expect(BrokeragesModel.refreshedLabel("not a date") == "not a date")
    }

    @Test func editPrefillsTheAccountsForm() {
        let (form, _, _) = makeForm(edit: Self.alpacaEdit)
        #expect(form.isEditing && form.editForm == .alpaca)
        #expect(form.alpacaName == "Paper")
        #expect(!form.alpacaPaper)
        #expect(form.alpacaFeed == "sip")

        let (bus, _, _) = makeForm(edit: Brokerage(json: ["id": "b2", "brokerage_type": "binanceus", "account_name": "B", "paper": true]))
        #expect(bus.editForm == .binanceus && bus.tab == .binanceus)
        #expect(bus.binanceName == "B")
    }

    @Test func testWithoutCredentialsAsksForThem() async {
        let (form, stub, _) = makeForm()
        await form.runAlpacaTest()
        #expect(stub.requests.isEmpty)
        #expect(form.showTestPanel)
        let summary = LinkBrokerageFormModel.TestSummary(form.testResult ?? [:])
        #expect(summary.headline == "Probe could not run — see hints below.")
        #expect(summary.hints == ["Fill in both API Key ID and Secret Key first."])
        #expect(form.showSaveAnyway)
    }

    @Test func testBodiesForFormAndStoredCredentials() async throws {
        let (form, stub, routes) = makeForm()
        routes.set("POST /brokerages/test-alpaca", #"{"ok":true,"summary":{"passed":5,"failed":0,"total":5},"tests":[{"name":"account","ok":true,"status":200}]}"#)
        form.alpacaKey = " PK1 "
        form.alpacaSecret = "S1"
        await form.runAlpacaTest()
        let path = try #require(stub.last?.path)
        #expect(stub.last?.method == "POST")
        #expect(try body(stub.last) == ["key": "PK1", "secret": "S1", "paper": true, "alpaca_data_feed": "iex"])
        let summary = LinkBrokerageFormModel.TestSummary(form.testResult ?? [:])
        if path == "/brokerages/test-alpaca" {
            #expect(summary.headline == "All 5 tests passed")
        }

        let (edit, editStub, _) = makeForm(edit: Self.alpacaEdit)
        await edit.runAlpacaTest()
        #expect(try body(editStub.last) == ["brokerage_id": "b1", "paper": false, "alpaca_data_feed": "sip"])
    }

    @Test func testFailureBecomesAHint() async {
        let (form, _, _) = makeForm()   // every route 404s
        form.alpacaKey = "k"
        form.alpacaSecret = "s"
        await form.runAlpacaTest()
        let summary = LinkBrokerageFormModel.TestSummary(form.testResult ?? [:])
        #expect(summary.hints.count == 1)
        #expect(summary.hints[0].hasPrefix("Network error: "))
        #expect(!form.testRunning)
    }

    @Test func alpacaValidation() async {
        let (form, stub, _) = makeForm()
        #expect(await form.submitAlpaca() == false)
        #expect(form.submitMsg == "Account name is required")
        form.alpacaName = "A"
        #expect(await form.submitAlpaca() == false)
        #expect(form.submitMsg == "API Key ID is required")
        form.alpacaKey = "k"
        #expect(await form.submitAlpaca() == false)
        #expect(form.submitMsg == "Secret Key is required")
        #expect(stub.requests.isEmpty)
    }

    @Test func aFailedPreSaveTestStopsTheSaveUntilBypassed() async throws {
        let (form, stub, routes) = makeForm()
        let testPath = "POST /brokerages/test-alpaca"
        routes.set(testPath, #"{"ok":false,"summary":{"passed":3,"failed":2,"total":5},"tests":[]}"#)
        routes.set("POST /brokerages", #"{"id":"b9"}"#)
        form.alpacaName = " My Paper "
        form.alpacaKey = "k"
        form.alpacaSecret = "s"

        #expect(await form.submitAlpaca() == false)
        #expect(form.submitMsg == "Credential test failed — review below. Tap \"Save Anyway\" to bypass.")
        #expect(form.showSaveAnyway)
        #expect(!stub.requests.contains { $0.method == "POST" && $0.path == "/brokerages" })

        #expect(await form.submitAlpaca(bypassTest: true))
        let link = stub.requests.last { $0.method == "POST" && $0.path == "/brokerages" }
        #expect(try body(link) == [
            "brokerage_type": "alpaca", "account_name": "My Paper", "key": "k", "secret": "s",
            "paper": true, "alpaca_data_feed": "iex",
        ])
        #expect(form.submitMsg == "Account linked!")
        #expect(form.submitOk)
    }

    @Test func aPassingPreSaveTestSavesStraightAway() async {
        let (form, stub, routes) = makeForm()
        routes.set("POST /brokerages/test-alpaca", #"{"ok":true,"summary":{"total":5,"failed":0}}"#)
        routes.set("POST /brokerages", #"{"id":"b9"}"#)
        form.alpacaName = "A"
        form.alpacaKey = "k"
        form.alpacaSecret = "s"
        #expect(await form.submitAlpaca())
        #expect(stub.requests.filter { $0.method == "POST" }.map(\.path).last == "/brokerages")
    }

    @Test func alpacaEditSendsOnlyTheFilledFields() async throws {
        let (form, stub, routes) = makeForm(edit: Self.alpacaEdit)
        routes.set("PUT /brokerages/b1", #"{"id":"b1"}"#)
        #expect(await form.submitAlpaca())   // no creds → no pre-save test
        #expect(stub.last?.method == "PUT")
        #expect(try body(stub.last) == ["account_name": "Paper", "paper": false, "alpaca_data_feed": "sip"])
        #expect(form.submitMsg == "Account updated!")
    }

    @Test func binanceBodiesAndValidation() async throws {
        let (form, stub, routes) = makeForm()
        form.tab = .binanceus
        #expect(await form.submitBinanceus() == false)
        #expect(form.submitMsg == "Account name is required")
        form.binanceName = "Bus"
        #expect(await form.submitBinanceus() == false)
        #expect(form.submitMsg == "API Key is required")

        form.tab = .alpaca
        #expect(form.submitMsg == nil)   // switching tabs clears the message
        form.tab = .binanceus

        routes.set("POST /brokerages", #"{"id":"b5"}"#)
        form.binanceKey = "k"
        form.binanceSecret = "s"
        form.binancePaper = false
        #expect(await form.submitBinanceus())
        #expect(try body(stub.last) == [
            "brokerage_type": "binanceus", "account_name": "Bus", "key": "k", "secret": "s", "paper": false,
        ])
    }

    @Test func saveFailureShowsTheError() async {
        let (form, _, routes) = makeForm()
        routes.set("POST /brokerages", #"{"detail":"duplicate account"}"#, status: 409)
        form.tab = .binanceus
        form.binanceName = "a"
        form.binanceKey = "k"
        form.binanceSecret = "s"
        #expect(await form.submitBinanceus() == false)
        #expect(form.submitMsg == "duplicate account")
        #expect(!form.submitOk)
        #expect(!form.submitting)
    }
}

import Foundation
import Observation

/// The linked brokerage accounts — `BrokeragesController` (auto-disposed, so
/// the screen owns it).
@Observable
final class BrokeragesModel {
    private(set) var accounts: Loadable<[Brokerage]> = .loading

    @ObservationIgnored private let repository: () -> BrokerageRepository

    init(repository: @escaping () -> BrokerageRepository) {
        self.repository = repository
    }

    /// The first load (Dart's `build`).
    func load() async {
        let result = await Loadable.capture { try await self.repository().list() }
        if case .failed(let error) = result, error is CancellationError { return }
        accounts = result
    }

    /// Force a network reload: loading, then the result.
    func refresh() async {
        accounts = .loading
        await load()
    }

    /// `DELETE /brokerages/:id`, then refresh. Throws so the confirmation can
    /// report it.
    func remove(_ id: String) async throws {
        try await repository().remove(id)
        await refresh()
    }

    /// `_statusColor`'s key: active, expired, anything else.
    nonisolated enum StatusTone: Sendable { case active, expired, other }

    nonisolated static func statusTone(_ status: String?) -> StatusTone {
        switch status {
        case "active": .active
        case "expired": .expired
        default: .other
        }
    }

    /// The card badge: `ALPACA · Paper|Live`, else the upper-cased type.
    nonisolated static func badgeLabel(_ account: Brokerage) -> String {
        account.brokerageType == "alpaca"
            ? "ALPACA · \(account.paper ? "Paper" : "Live")"
            : account.brokerageType.uppercased()
    }

    /// `_fmtDateTime`: parsed and formatted, else the raw text.
    nonisolated static func refreshedLabel(_ raw: String) -> String {
        guard let date = parseDateTime(raw) else { return raw }
        return fmtDateTime(date)
    }
}

/// The Link / Edit sheet's state — `_LinkBrokerageSheetState`.
@Observable
final class LinkBrokerageFormModel {
    nonisolated enum Tab: Int, CaseIterable, Sendable {
        case alpaca, binanceus
    }

    /// The market-data feeds, in menu order.
    static let feeds: [(value: String, label: String)] = [
        ("iex", "IEX (free — Basic Market Data)"),
        ("sip", "SIP (paid — Algo Trader Plus)"),
    ]

    let editAccount: Brokerage?
    var isEditing: Bool { editAccount != nil }

    /// Switching tabs clears the status message.
    var tab: Tab {
        didSet {
            if tab != oldValue {
                submitMsg = nil
                submitOk = false
            }
        }
    }

    // Alpaca
    var alpacaName = ""
    var alpacaKey = ""
    var alpacaSecret = ""
    var alpacaPaper = true
    var alpacaFeed = "iex"

    // Alpaca test suite
    private(set) var testRunning = false
    private(set) var testResult: JSONObject?
    var showTestPanel = false

    // Binance.US (spot 0.00% maker / 0.02% taker)
    var binanceName = ""
    var binanceKey = ""
    var binanceSecret = ""
    var binancePaper = true

    // Shared submission state
    private(set) var submitting = false
    private(set) var submitMsg: String?
    private(set) var submitOk = false
    /// A save succeeded: the sheet holds the success line for 1.2 s, then
    /// closes. Nothing may submit again in that window.
    private(set) var finished = false

    /// The submit buttons are disabled: a save in flight, or done.
    var locked: Bool { submitting || finished }

    @ObservationIgnored private let repository: () -> BrokerageRepository

    init(editAccount: Brokerage?, repository: @escaping () -> BrokerageRepository) {
        self.editAccount = editAccount
        self.repository = repository
        self.tab = editAccount.map { $0.brokerageType == "binanceus" ? .binanceus : .alpaca } ?? .alpaca
        if let a = editAccount {
            if a.brokerageType == "alpaca" {
                alpacaName = a.accountName
                alpacaPaper = a.paper
                alpacaFeed = a.alpacaDataFeed ?? "iex"
            } else if a.brokerageType == "binanceus" {
                binanceName = a.accountName
                binancePaper = a.paper
            }
        }
    }

    /// In edit mode the account's own form shows, with no tab bar
    /// (`_buildEditBody`: unknown types show the Alpaca form).
    var editForm: Tab {
        editAccount?.brokerageType == "binanceus" ? .binanceus : .alpaca
    }

    private func setMsg(_ msg: String?, ok: Bool = false) {
        submitMsg = msg
        submitOk = ok
    }

    private static func failedResult(hint: String) -> JSONObject {
        [
            "ok": false,
            "summary": ["passed": 0, "failed": 0, "total": 0],
            "tests": [],
            "hints": .array([.string(hint)]),
        ]
    }

    // MARK: Alpaca test suite

    /// `_runAlpacaTest`: form credentials, else the stored account's.
    func runAlpacaTest() async {
        let key = alpacaKey.trimmed
        let secret = alpacaSecret.trimmed
        let hasFormCreds = !key.isEmpty && !secret.isEmpty
        let canTestStored = isEditing && editAccount?.id.isEmpty == false

        if !hasFormCreds && !canTestStored {
            testResult = Self.failedResult(hint: "Fill in both API Key ID and Secret Key first.")
            showTestPanel = true
            return
        }

        testRunning = true
        testResult = nil
        showTestPanel = true

        let body: JSONObject = hasFormCreds
            ? ["key": .string(key), "secret": .string(secret), "paper": .bool(alpacaPaper), "alpaca_data_feed": .string(alpacaFeed)]
            : ["brokerage_id": .string(editAccount?.id ?? ""), "paper": .bool(alpacaPaper), "alpaca_data_feed": .string(alpacaFeed)]
        do {
            testResult = try await repository().testAlpaca(body)
            testRunning = false
        } catch {
            testResult = Self.failedResult(hint: "Network error: \(brokerageErrorText(error))")
            testRunning = false
        }
    }

    /// The summary the test panel shows.
    nonisolated struct TestSummary: Equatable, Sendable {
        let ok: Bool
        let total: Int
        let failed: Int
        let tests: [JSON]
        let hints: [String]

        init(_ result: JSONObject) {
            let json = JSON.object(result)
            ok = json["ok"].bool
            total = json["summary"]["total"].int ?? 0
            failed = json["summary"]["failed"].int ?? 0
            tests = json["tests"].objectElements
            hints = json["hints"].stringElements
        }

        var headline: String {
            if total == 0 { return "Probe could not run — see hints below." }
            return ok ? "All \(total) tests passed" : "\(failed) of \(total) tests failed"
        }
    }

    /// `Save Anyway` shows when the panel holds a failed result and no test runs.
    var showSaveAnyway: Bool {
        showTestPanel && testResult != nil && JSON.object(testResult ?? [:])["ok"].bool == false && !testRunning
    }

    // MARK: Save

    /// `_submitAlpaca`. Returns true after a successful save (the sheet then
    /// refreshes the list and closes after 1.2 s).
    func submitAlpaca(bypassTest: Bool = false) async -> Bool {
        guard !locked else { return false }
        let name = alpacaName.trimmed
        let key = alpacaKey.trimmed
        let secret = alpacaSecret.trimmed

        if name.isEmpty {
            setMsg("Account name is required")
            return false
        }
        if !isEditing && key.isEmpty {
            setMsg("API Key ID is required")
            return false
        }
        if !isEditing && secret.isEmpty {
            setMsg("Secret Key is required")
            return false
        }

        // Pre-save test when there are form credentials and no bypass.
        if !bypassTest && !key.isEmpty && !secret.isEmpty {
            submitting = true
            setMsg("Validating credentials…")
            do {
                let result = try await repository().testAlpaca([
                    "key": .string(key), "secret": .string(secret),
                    "paper": .bool(alpacaPaper), "alpaca_data_feed": .string(alpacaFeed),
                ])
                testResult = result
                if JSON.object(result)["ok"].bool != true {
                    showTestPanel = true
                    setMsg("Credential test failed — review below. Tap \"Save Anyway\" to bypass.")
                    submitting = false
                    return false
                }
            } catch {
                setMsg("Pre-save test errored (\(brokerageErrorText(error))); tap Save again to bypass.")
                submitting = false
                return false
            }
        }

        submitting = true
        setMsg("")
        var body = JSONObject()
        if isEditing {
            if !name.isEmpty { body["account_name"] = .string(name) }
            if !key.isEmpty { body["key"] = .string(key) }
            if !secret.isEmpty { body["secret"] = .string(secret) }
            body["paper"] = .bool(alpacaPaper)
            body["alpaca_data_feed"] = .string(alpacaFeed)
        } else {
            body = [
                "brokerage_type": "alpaca",
                "account_name": .string(name),
                "key": .string(key),
                "secret": .string(secret),
                "paper": .bool(alpacaPaper),
                "alpaca_data_feed": .string(alpacaFeed),
            ]
        }
        return await save(body)
    }

    /// `_submitBinanceus`.
    func submitBinanceus() async -> Bool {
        guard !locked else { return false }
        let name = binanceName.trimmed
        let key = binanceKey.trimmed
        let secret = binanceSecret.trimmed

        if name.isEmpty {
            setMsg("Account name is required")
            return false
        }
        if !isEditing && key.isEmpty {
            setMsg("API Key is required")
            return false
        }
        if !isEditing && secret.isEmpty {
            setMsg("Secret Key is required")
            return false
        }

        submitting = true
        setMsg("")
        var body = JSONObject()
        if isEditing {
            if !name.isEmpty { body["account_name"] = .string(name) }
            if !key.isEmpty { body["key"] = .string(key) }
            if !secret.isEmpty { body["secret"] = .string(secret) }
            body["paper"] = .bool(binancePaper)
        } else {
            body = [
                "brokerage_type": "binanceus",
                "account_name": .string(name),
                "key": .string(key),
                "secret": .string(secret),
                "paper": .bool(binancePaper),
            ]
        }
        return await save(body)
    }

    private func save(_ body: JSONObject) async -> Bool {
        do {
            if let editAccount {
                _ = try await repository().edit(editAccount.id, body)
            } else {
                _ = try await repository().link(body)
            }
            setMsg(isEditing ? "Account updated!" : "Account linked!", ok: true)
            finished = true
            submitting = false
            return true
        } catch {
            setMsg(brokerageErrorText(error))
            submitting = false
            return false
        }
    }
}

/// Dart's `e.toString()` for a caught error (`ApiError.toString()` is its message).
nonisolated func brokerageErrorText(_ error: any Error) -> String {
    (error as? ApiError)?.message ?? error.localizedDescription
}

private extension String {
    nonisolated var trimmed: String { trimmingCharacters(in: .whitespacesAndNewlines) }
}

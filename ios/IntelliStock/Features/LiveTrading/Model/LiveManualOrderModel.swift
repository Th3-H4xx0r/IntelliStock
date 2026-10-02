import Foundation
import Observation

/// The order a person is asked to confirm: the exact `submit_order` payload
/// that Submit will send, and the one-line summary the alert shows for it.
nonisolated struct LiveOrderConfirmation: Hashable, Sendable {
    let payload: JSONObject
    let summary: String

    /// Built from a validated form, through `buildOrderPayload`, so what the
    /// alert describes is what goes over the wire.
    init(form: OrderForm) {
        let payload = buildOrderPayload(form)
        self.payload = payload
        self.summary = liveOrderSummary(payload)
    }
}

/// The confirmation's message, read off the payload that will be sent:
/// side, quantity (or dollar amount) and symbol, order type with its limit
/// price, then time in force — "Buy 10 AAPL · Limit $185.00 · Day". An
/// extended-hours order says so at the end.
nonisolated func liveOrderSummary(_ payload: JSONObject) -> String {
    let side = (payload["side"] ?? .null).stringOr("")
    let symbol = (payload["symbol"] ?? .null).stringOr("")
    let sideWord = side == "sell" ? "Sell" : (side == "buy" ? "Buy" : side.dsSentenceCased)

    var parts: [String] = []
    if let qty = payload["qty"]?.double {
        parts.append("\(sideWord) \(liveOrderQuantity(qty)) \(symbol)")
    } else if let notional = payload["notional"]?.double {
        parts.append("\(sideWord) \(fmtMoney(notional)) of \(symbol)")
    } else {
        parts.append("\(sideWord) \(symbol)")
    }

    let type = (payload["order_type"] ?? .null).stringOr("")
    if type == "limit" {
        parts.append("Limit \(fmtMoney(payload["limit_price"]?.double))")
    } else {
        parts.append(type == "market" ? "Market" : type.dsSentenceCased)
    }

    let tif = (payload["tif"] ?? .null).stringOr("")
    parts.append(liveTifOptions.first { $0.value == tif }?.label ?? tif.uppercased())

    if payload["extended_hours"]?.bool == true { parts.append("Extended hours") }
    return parts.joined(separator: " · ")
}

/// A share count as typed: whole numbers without decimals, fractions as
/// entered (`10`, `0.5`, `1,250`).
nonisolated func liveOrderQuantity(_ qty: Double) -> String {
    qty.formatted(.number.precision(.fractionLength(0...8)).locale(Locale(identifier: "en_US")))
}

/// The manual order sheet's state — the form, its validation error, the
/// pending confirmation and the in-flight guard. Real money on alpaca-main,
/// so nothing is sent until the person confirms:
///
/// 1. `requestSubmit()` validates (the guard, unchanged). An invalid form
///    shows its message and never reaches the confirmation.
/// 2. A valid form becomes a `LiveOrderConfirmation`: the alert.
/// 3. Only `confirmSubmit()` sends, once, through the same `submit_order`
///    command path the sheet always used.
@Observable
final class LiveManualOrderModel {
    var form = OrderForm()
    private(set) var error: String?
    /// Non-nil while the "Submit order?" alert is up.
    private(set) var confirmation: LiveOrderConfirmation?
    /// True from the confirm tap until the command returns.
    private(set) var submitting = false

    @ObservationIgnored private let send: (JSONObject) async -> Void

    /// `send` runs the `submit_order` command (`LiveTradingModel.runCommand`,
    /// which reports its own failures on the command toast).
    init(send: @escaping (JSONObject) async -> Void) {
        self.send = send
    }

    /// Submit Order: validate first; a valid form asks for confirmation.
    func requestSubmit() {
        guard !submitting else { return }
        if let message = validateOrderForm(form) {
            error = message
            confirmation = nil
            return
        }
        error = nil
        confirmation = LiveOrderConfirmation(form: form)
    }

    /// Cancel on the alert: nothing is sent.
    func cancelConfirmation() {
        confirmation = nil
    }

    /// Submit on the alert: sends the confirmed payload once. Returns true
    /// when this call sent it (the sheet then closes); a second tap while
    /// the first is in flight, or a call with nothing confirmed, sends
    /// nothing and returns false.
    @discardableResult
    func confirmSubmit() async -> Bool {
        guard !submitting, let pending = confirmation else { return false }
        submitting = true
        confirmation = nil
        await send(pending.payload)
        return true
    }
}

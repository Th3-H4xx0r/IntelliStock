import Foundation
import Observation

/// Configure and launch a backtest of a crypto instance's allocation —
/// `_CryptoBacktestSheetState` in crypto_backtest_sheet.dart.
@Observable
final class CryptoBacktestFormModel {
    /// `_kBandCadence`.
    static let bandCadence: [String: String] = [
        "high": "High · ~5-minute bars",
        "medium": "Medium · ~15-minute bars",
        "low": "Low · ~60-minute bars",
    ]

    /// `_kFeeVenues`: 'default' uses the instance's brokerage.
    static let feeVenues: [(key: String, label: String)] = [
        ("default", "Default — instance's brokerage"),
        ("binanceus", "Binance.US — 0.02% taker"),
        ("alpaca", "Alpaca — 0.25% taker"),
        ("kraken", "Kraken — 0.26% taker"),
        ("coinbase", "Coinbase Advanced — 0.60% taker"),
    ]

    let inst: Instance
    var start: Date
    var end: Date
    let gran: String
    var feeVenue = "default"
    var cash = "10000"
    private(set) var busy = false
    private(set) var err: String?

    @ObservationIgnored private let repository: () -> CryptoRepository

    init(inst: Instance, now: Date = Date(), repository: @escaping () -> CryptoRepository) {
        self.inst = inst
        self.repository = repository
        end = now
        start = now.addingTimeInterval(-90 * 86400)
        let band = KalshiPregame.str(inst.cryptoConfig?["band"]).lowercased()
        gran = CryptoCatalog.bandGranularity[band] ?? "900"
    }

    var cadenceLabel: String {
        let raw = inst.cryptoConfig?["band"].flatMap { $0.isNull ? nil : $0.dartDescription } ?? "medium"
        return Self.bandCadence[raw.lowercased()] ?? Self.bandCadence["medium"]!
    }

    var tickers: [String] { inst.stocks.map { String($0.split(separator: "/", omittingEmptySubsequences: false).first ?? "") } }

    var feeCaption: String {
        feeVenue == "default"
            ? "Uses this instance's own brokerage fee."
            : "Emulated — fills charged at the selected venue's fee. Compare a strategy across venues (fees make or break high-frequency crypto)."
    }

    /// `_submit`. On success returns the created row's id (`id` or
    /// `backtest_id`), "" when the response had none; nil on failure.
    func submit() async -> String? {
        // One tap, one request (busy stays set after success: the sheet closes).
        guard !busy else { return nil }
        if !(start < end) {
            err = "End date must be after start date"
            return nil
        }
        busy = true
        err = nil
        do {
            let res = try await repository().createBacktest(
                instanceId: inst.id,
                stocks: inst.stocks,
                startDate: KalshiFormat.ymd(start),
                endDate: KalshiFormat.ymd(end),
                granularity: gran,
                initialCash: JSON.parseDouble(cash) ?? 10000,
                emulateFeeVenue: feeVenue
            )
            let id = res["id"].flatMap { $0.isNull ? nil : $0 } ?? res["backtest_id"]
            return id.flatMap { $0.isNull ? nil : $0.dartDescription } ?? ""
        } catch {
            if !marketsIsCancellation(error) { err = KalshiFormat.errorText(error) }
            busy = false
            return nil
        }
    }
}

import SwiftUI

/// A Kalshi backtest's results — `KalshiBacktestResultScreen`: summary,
/// equity curve (scrub to pick a day), and Trades / Decision log / Logs. An
/// inset-grouped list under the inline "Backtest <id>" title; the run status
/// is the summary's first row.
struct KalshiBacktestResultView: View {
    let backtestId: String

    @Environment(AppServices.self) private var services
    @State private var model: KalshiBacktestResultModel?

    var body: some View {
        Group {
            if let model {
                content(model)
            } else {
                Color.clear
            }
        }
        .background(DS.Surface.canvas)
        .navigationTitle("Backtest \(backtestId.prefix(8))")
        .navigationBarTitleDisplayMode(.inline)
        .task(id: backtestId) {
            // Reused on reappear: the tab and selected day survive.
            if model?.backtestId != backtestId {
                model = KalshiBacktestResultModel(backtestId: backtestId, repository: { [services] in services.kalshiRepository })
            }
            await model?.poll(lifecycle: services.lifecycle)
        }
    }

    private func statusColor(_ st: String?) -> Color {
        switch st {
        case "finished": DS.Palette.success
        case "error": DS.Palette.danger
        case "stopped": .secondary
        default: DS.Palette.warning
        }
    }

    private func content(_ model: KalshiBacktestResultModel) -> some View {
        let eq = model.equity
        return List {
            summary(model, model.summary)
            if eq.values.count > 1 {
                equitySection(model, timestamps: eq.timestamps, values: eq.values)
            }
            tabs(model)
        }
        .listStyle(.insetGrouped)
    }

    // MARK: Summary

    private func summary(_ model: KalshiBacktestResultModel, _ s: JSONObject) -> some View {
        let pnl = s["pnl_cents"]?.double
        let confidence = s["profit_confidence"].flatMap { $0.isNull ? nil : $0.double }
        let ciLow = s["pnl_ci_low_cents"]?.double
        func v(_ k: String, _ fallback: String) -> String {
            guard let x = s[k], !x.isNull else { return fallback }
            return x.dartDescription
        }
        return Section("Summary") {
            if let st = model.statusText {
                LabeledContent("Status") {
                    StatusDot(st, color: statusColor(st), pulsing: st == "running" || st == "pending")
                }
            }
            StatGrid(columns: 3) {
                StatCell(label: "Total P&L", value: KalshiFormat.money(cents: pnl), valueColor: (pnl ?? 0) >= 0 ? DS.Palette.success : DS.Palette.danger)
                StatCell(label: "ROI", value: KalshiFormat.pct(s["roi"]?.double))
                StatCell(label: "Bets", value: v("n_bets", "—"))
                StatCell(label: "Win rate", value: KalshiFormat.pct(s["win_rate"]?.double))
                StatCell(label: "Avg CLV", value: KalshiFormat.pct(s["clv_avg"]?.double))
                StatCell(label: "API/cache", value: "\(v("api_calls", "0"))/\(v("cache_hits", "0"))", valueColor: .secondary)
            }
            .padding(.vertical, 4)
            Text("Fixtures \(v("n_fixtures", "0")) · bet \(v("bet", "0")) · no-edge \(v("no_bet", "0")) · unsettled \(v("unsettled", "0")) · unmatched \(v("unmatched", "0")) · no-price \(v("no_candle_data", "0"))")
                .font(.footnote)
                .foregroundStyle(.secondary)
            if let confidence {
                Text("Trust: profit confidence \(dartToStringAsFixed(confidence * 100, 0))% · 90% range \(KalshiFormat.money(cents: ciLow)) → \(KalshiFormat.money(cents: s["pnl_ci_high_cents"]?.double))\((ciLow ?? 0) < 0 ? " (spans a loss — not proven)" : "")")
                    .font(.footnote)
                    .foregroundStyle(confidence >= 0.9 ? DS.Palette.success : (confidence >= 0.75 ? DS.Palette.warning : DS.Palette.danger))
            }
        }
    }

    // MARK: Equity

    /// The equity curve, with the day a scrub picks (or the day menu sets)
    /// filtering the Trades tab. The Dart day chips are a `Picker` menu row.
    private func equitySection(_ model: KalshiBacktestResultModel, timestamps: [Date], values: [Double]) -> some View {
        let byDay = model.byDay
        return Section("Equity — scrub to see that day's trades") {
            VStack(alignment: .leading, spacing: 8) {
                if let day = model.selectedDay {
                    Text("\(day) · \((byDay[day] ?? []).count) trade(s)")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
                ScrubbableAreaChart(
                    timestamps: timestamps,
                    values: values,
                    lineColor: DS.Palette.accent,
                    height: 200,
                    baseline: 0,
                    onScrub: { model.scrubbed($0) },
                    indexed: true
                )
            }
            .padding(.vertical, 6)
            Picker("Day", selection: Binding(get: { model.selectedDay }, set: { model.selectedDay = $0 })) {
                if model.selectedDay == nil {
                    Text("—").tag(String?.none)
                }
                Text("All · \(model.trades.count)").tag(Optional("all"))
                ForEach(model.daysList, id: \.self) { d in
                    Text("\(d) · \(byDay[d]?.count ?? 0)").tag(Optional(d))
                }
            }
            .pickerStyle(.menu)
        }
    }

    // MARK: Tabs

    private func tabs(_ model: KalshiBacktestResultModel) -> some View {
        @Bindable var m = model
        let decisions = model.result?["decision_log"]?.objectList ?? []
        let logs = model.result?["logs"]?.arrayValue ?? []
        let dayTrades = model.dayTrades
        return Section {
            switch model.tab {
            case "trades":
                if dayTrades.isEmpty {
                    Text(model.trades.isEmpty ? "No bets were placed under these settings." : "Scrub or pick a day to see its trades.")
                        .foregroundStyle(.secondary)
                } else {
                    ForEach(Array(dayTrades.enumerated()), id: \.offset) { _, t in tradeRow(t) }
                }
            case "decisions":
                if decisions.isEmpty {
                    Text("No decision log recorded.").foregroundStyle(.secondary)
                } else {
                    ForEach(Array(decisions.enumerated()), id: \.offset) { _, d in decisionRow(d) }
                }
            default:
                if logs.isEmpty {
                    Text("No logs recorded.").foregroundStyle(.secondary)
                } else {
                    VStack(alignment: .leading, spacing: 2) {
                        ForEach(Array(logs.enumerated()), id: \.offset) { _, l in
                            Text(l.dartDescription)
                                .font(.system(.caption, design: .monospaced))
                                .textSelection(.enabled)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.vertical, 4)
                }
            }
        } header: {
            Picker("View", selection: $m.tab) {
                Text("Trades").tag("trades")
                Text("Decision log").tag("decisions")
                Text("Logs").tag("logs")
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .textCase(nil)
            .padding(.bottom, 6)
        }
    }

    private func tradeRow(_ t: JSONObject) -> some View {
        let pnl = t["realized_pnl_cents"]?.double
        let hasSharp = !(t["sharp_prob"]?.isNull ?? true)
        let home = KalshiPregame.str(t["home"])
        let outcome = (t["outcome"] ?? .null).dartDescription
        func s(_ k: String) -> String { (t[k] ?? .null).dartDescription }
        return VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 6) {
                flag(t["home_flag"])
                Text(!home.isEmpty ? "\(s("home")) v \(s("away"))" : s("side"))
                    .font(.headline)
                    .lineLimit(1)
                flag(t["away_flag"])
                Spacer(minLength: 4)
                Text(KalshiFormat.money(cents: pnl))
                    .monospacedDigit()
                    .foregroundStyle((pnl ?? 0) >= 0 ? DS.Palette.success : DS.Palette.danger)
            }
            Text("\(KalshiPregame.str(t["league"])) · \(KalshiBacktestResultModel.pickLabel(t)) · entry \(s("entry_cents"))¢ × \(s("size"))")
                .font(.subheadline)
                .foregroundStyle(.secondary)
            HStack(spacing: 8) {
                Text("edge \(dartToStringAsFixed((t["edge"]?.double ?? 0) * 100, 1))% · \(hasSharp ? "sharp" : "model-only")")
                    .font(.footnote.monospacedDigit())
                    .foregroundStyle(.secondary)
                Spacer(minLength: 4)
                StatusBadge(label: outcome.dsSentenceCased, color: t["outcome"] == .string("win") ? DS.Palette.success : DS.Palette.danger)
            }
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .combine)
    }

    private func decisionRow(_ d: JSONObject) -> some View {
        let dec = KalshiPregame.str(d["decision"])
        let col: Color = dec == "placed" ? DS.Palette.success : (dec == "no_bet" ? .secondary : DS.Palette.warning)
        return HStack(alignment: .firstTextBaseline, spacing: 8) {
            VStack(alignment: .leading, spacing: 2) {
                Text(KalshiPregame.str(d["label"]))
                Text(KalshiPregame.str(d["reason"]))
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            StatusDot(dec, color: col, font: .footnote)
        }
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder
    private func flag(_ url: JSON?) -> some View {
        let u = KalshiPregame.str(url)
        if !u.isEmpty, let link = URL(string: u) {
            AsyncImage(url: link) { phase in
                if let image = phase.image { image.resizable().scaledToFill() } else { Color.clear }
            }
            .frame(width: 18, height: 12)
            .clipped()
            .accessibilityHidden(true)
        }
    }
}

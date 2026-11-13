import SwiftUI

/// A Kalshi backtest's results — `KalshiBacktestResultScreen`, as the web's
/// result page: the run status under the title, the summary card, the
/// equity curve (scrub or pick a day), and Trades / Decision log / Logs.
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
        .background(DS.Surface.page)
        .navigationTitle("Backtest \(backtestId.prefix(8))")
        .navigationBarTitleDisplayMode(.large)
        .task(id: backtestId) {
            // Reused on reappear: the tab and selected day survive.
            if model?.backtestId != backtestId {
                model = KalshiBacktestResultModel(backtestId: backtestId, repository: { [services] in services.kalshiRepository })
            }
            await model?.poll(lifecycle: services.lifecycle)
        }
    }

    private func content(_ model: KalshiBacktestResultModel) -> some View {
        let eq = model.equity
        return List {
            if let st = model.statusText {
                Section {
                    StatusDot(st.dsSentenceCased, status: st, pulsing: st == "running" || st == "pending", font: DS.Font.pageSub)
                        .dsClearRow(top: 0)
                }
            }
            summary(model, model.summary)
            if eq.values.count > 1 {
                equitySection(model, timestamps: eq.timestamps, values: eq.values)
            }
            tabs(model)
        }
        .dsList()
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
        return DSSection("Summary") {
            StatGrid(columns: 3, horizontalSpacing: 16) {
                StatCell(label: "Total P&L", value: KalshiFormat.money(cents: pnl), valueColor: pnl == nil ? nil : ((pnl ?? 0) >= 0 ? DS.Palette.gain : DS.Palette.loss))
                StatCell(label: "ROI", value: KalshiFormat.pct(s["roi"]?.double))
                StatCell(label: "Bets", value: v("n_bets", "—"))
                StatCell(label: "Win rate", value: KalshiFormat.pct(s["win_rate"]?.double))
                StatCell(label: "Avg CLV", value: KalshiFormat.pct(s["clv_avg"]?.double))
                StatCell(label: "API / cache", value: "\(v("api_calls", "0"))/\(v("cache_hits", "0"))", valueColor: DS.Text.secondary)
            }
            .statValueStyle(.medium)
            .padding(.vertical, 12)
            Text("Fixtures \(v("n_fixtures", "0")) · bet \(v("bet", "0")) · no edge \(v("no_bet", "0")) · unsettled \(v("unsettled", "0")) · unmatched \(v("unmatched", "0")) · no price \(v("no_candle_data", "0"))")
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
                .monospacedDigit()
                .fixedSize(horizontal: false, vertical: true)
                .padding(.vertical, 6)
            if let confidence {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Trust: profit confidence \(dartToStringAsFixed(confidence * 100, 0))% · 90% P&L range \(KalshiFormat.money(cents: ciLow)) → \(KalshiFormat.money(cents: s["pnl_ci_high_cents"]?.double))")
                        .font(DS.Font.caption)
                        .foregroundStyle(confidence >= 0.9 ? DS.Palette.gain : (confidence >= 0.75 ? DS.Text.label : DS.Palette.loss))
                        .monospacedDigit()
                        .fixedSize(horizontal: false, vertical: true)
                    if (ciLow ?? 0) < 0 {
                        FormNote("The range spans a loss, so this edge is not statistically proven")
                    }
                }
                .padding(.vertical, 6)
            }
        }
    }

    // MARK: Equity

    /// The equity curve, with the day a scrub picks (or the day menu sets)
    /// filtering the Trades tab. The Dart day chips are a `Picker` menu row.
    private func equitySection(_ model: KalshiBacktestResultModel, timestamps: [Date], values: [Double]) -> some View {
        let byDay = model.byDay
        let last = values.last ?? 0
        return DSSection("Equity curve", subtitle: "Cumulative P&L by kickoff. Scrub or pick a day to see its trades") {
            VStack(alignment: .leading, spacing: 12) {
                if let day = model.selectedDay {
                    Text("\(day == "all" ? "All" : day) · \(day == "all" ? model.trades.count : (byDay[day] ?? []).count) trade(s)")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                        .monospacedDigit()
                }
                ScrubbableAreaChart(
                    timestamps: timestamps,
                    values: values,
                    lineColor: last >= 0 ? DS.Palette.gain : DS.Palette.loss,
                    height: 200,
                    baseline: 0,
                    onScrub: { model.scrubbed($0) },
                    indexed: true
                )
                DSField("Day") {
                    FormSelect(
                        "Day",
                        display: model.selectedDay.map { $0 == "all" ? "All · \(model.trades.count)" : "\($0) · \(byDay[$0]?.count ?? 0)" } ?? "—",
                        selection: Binding(get: { model.selectedDay }, set: { model.selectedDay = $0 })
                    ) {
                        if model.selectedDay == nil {
                            Text("—").tag(String?.none)
                        }
                        Text("All · \(model.trades.count)").tag(Optional("all"))
                        ForEach(model.daysList, id: \.self) { d in
                            Text("\(d) · \(byDay[d]?.count ?? 0)").tag(Optional(d))
                        }
                    }
                }
            }
            .padding(.vertical, 12)
        }
    }

    // MARK: Tabs

    private func tabs(_ model: KalshiBacktestResultModel) -> some View {
        @Bindable var m = model
        let decisions = model.result?["decision_log"]?.objectList ?? []
        let logs = model.result?["logs"]?.arrayValue ?? []
        let dayTrades = model.dayTrades
        return Group {
            // The view switch sits right above its card (the web's segmented
            // control over the tab's card).
            Section {
                DSSegmentedControl("View", selection: $m.tab, options: ["trades", "decisions", "logs"], fullWidth: true) { t in
                    t == "decisions" ? "Decision log" : (t == "trades" ? "Trades" : "Logs")
                }
                .dsClearRow()
            }
            .listSectionSpacing(DS.sectionHeaderGap)
            DSSection {
                switch model.tab {
                case "trades":
                    if dayTrades.isEmpty {
                        Text(model.trades.isEmpty ? "No bets were placed under these settings over this range" : "Scrub or pick a day above to see its trades")
                            .font(DS.Font.body)
                            .foregroundStyle(DS.Text.muted)
                    } else {
                        ForEach(Array(dayTrades.enumerated()), id: \.offset) { _, t in tradeRow(t) }
                    }
                case "decisions":
                    if decisions.isEmpty {
                        Text("No decision log recorded")
                            .font(DS.Font.body)
                            .foregroundStyle(DS.Text.muted)
                    } else {
                        ForEach(Array(decisions.enumerated()), id: \.offset) { _, d in decisionRow(d) }
                    }
                default:
                    if logs.isEmpty {
                        Text("No logs recorded")
                            .font(DS.Font.body)
                            .foregroundStyle(DS.Text.muted)
                    } else {
                        VStack(alignment: .leading, spacing: 2) {
                            ForEach(Array(logs.enumerated()), id: \.offset) { _, l in
                                Text(l.dartDescription)
                                    .font(DS.Font.mono)
                                    .foregroundStyle(DS.Text.label)
                                    .textSelection(.enabled)
                            }
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.vertical, 8)
                    }
                }
            }
        }
    }

    private func tradeRow(_ t: JSONObject) -> some View {
        let pnl = t["realized_pnl_cents"]?.double
        let hasSharp = !(t["sharp_prob"]?.isNull ?? true)
        let home = KalshiPregame.str(t["home"])
        let outcome = (t["outcome"] ?? .null).dartDescription
        func s(_ k: String) -> String { (t[k] ?? .null).dartDescription }
        return VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 6) {
                flag(t["home_flag"])
                Text(!home.isEmpty ? "\(s("home")) v \(s("away"))" : s("side"))
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.primary)
                    .lineLimit(1)
                flag(t["away_flag"])
                Spacer(minLength: 4)
                Text(KalshiFormat.money(cents: pnl))
                    .font(DS.Font.body)
                    .monospacedDigit()
                    .foregroundStyle((pnl ?? 0) >= 0 ? DS.Palette.gain : DS.Palette.loss)
            }
            Text("\(KalshiPregame.str(t["league"])) · \(KalshiBacktestResultModel.pickLabel(t)) · entry \(s("entry_cents"))¢ × \(s("size"))")
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
                .monospacedDigit()
            HStack(spacing: 8) {
                Text("Edge \(dartToStringAsFixed((t["edge"]?.double ?? 0) * 100, 1))% · \(hasSharp ? "Sharp" : "Model only")")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .monospacedDigit()
                Spacer(minLength: 4)
                Text(outcome.dsSentenceCased)
                    .font(DS.Font.caption)
                    .foregroundStyle(t["outcome"] == .string("win") ? DS.Palette.gain : DS.Palette.loss)
            }
        }
        .padding(.vertical, 6)
        .accessibilityElement(children: .combine)
    }

    private func decisionRow(_ d: JSONObject) -> some View {
        let dec = KalshiPregame.str(d["decision"])
        let col: Color = dec == "placed" ? DS.Text.soft : (dec == "no_bet" ? DS.Text.muted : DS.Text.label)
        return HStack(alignment: .firstTextBaseline, spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(KalshiPregame.str(d["label"]))
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.primary)
                Text(KalshiPregame.str(d["reason"]))
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Text(dec.replacingOccurrences(of: "_", with: " ").dsSentenceCased)
                .font(DS.Font.caption)
                .foregroundStyle(col)
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

import SwiftUI

/// A Kalshi backtest's results — `KalshiBacktestResultScreen`: summary,
/// equity curve (scrub to pick a day), and Trades / Decision log / Logs.
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
        .toolbar {
            ToolbarItem(placement: .principal) {
                VStack(spacing: 0) {
                    HStack(spacing: 0) {
                        Text("Backtest ").font(.headline)
                        Text(backtestId.prefix(8)).font(.subheadline.monospaced()).foregroundStyle(.secondary)
                    }
                    if let st = model?.statusText {
                        Text(st)
                            .font(.caption)
                            .foregroundStyle(statusColor(st))
                    }
                }
                .accessibilityElement(children: .combine)
            }
        }
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
        return ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                summary(model.summary)
                if eq.values.count > 1 {
                    equityCard(model, timestamps: eq.timestamps, values: eq.values)
                }
                tabs(model)
            }
            .padding(16)
        }
    }

    // MARK: Summary

    private func summary(_ s: JSONObject) -> some View {
        let pnl = s["pnl_cents"]?.double
        let confidence = s["profit_confidence"].flatMap { $0.isNull ? nil : $0.double }
        let ciLow = s["pnl_ci_low_cents"]?.double
        func v(_ k: String, _ fallback: String) -> String {
            guard let x = s[k], !x.isNull else { return fallback }
            return x.dartDescription
        }
        return Card(padding: 14) {
            VStack(alignment: .leading, spacing: 10) {
                Text("Summary").font(.subheadline.weight(.semibold))
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 100), spacing: 10)], spacing: 10) {
                    StatTile(label: "Total P&L", value: KalshiFormat.money(cents: pnl), valueColor: (pnl ?? 0) >= 0 ? DS.Palette.success : DS.Palette.danger)
                    StatTile(label: "ROI", value: KalshiFormat.pct(s["roi"]?.double))
                    StatTile(label: "Bets", value: v("n_bets", "—"))
                    StatTile(label: "Win rate", value: KalshiFormat.pct(s["win_rate"]?.double))
                    StatTile(label: "Avg CLV", value: KalshiFormat.pct(s["clv_avg"]?.double))
                    StatTile(label: "API/cache", value: "\(v("api_calls", "0"))/\(v("cache_hits", "0"))", valueColor: .secondary)
                }
                Text("Fixtures \(v("n_fixtures", "0")) · bet \(v("bet", "0")) · no-edge \(v("no_bet", "0")) · unsettled \(v("unsettled", "0")) · unmatched \(v("unmatched", "0")) · no-price \(v("no_candle_data", "0"))")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if let confidence {
                    Text("Trust: profit confidence \(dartToStringAsFixed(confidence * 100, 0))% · 90% range \(KalshiFormat.money(cents: ciLow)) → \(KalshiFormat.money(cents: s["pnl_ci_high_cents"]?.double))\((ciLow ?? 0) < 0 ? " (spans a loss — not proven)" : "")")
                        .font(.caption)
                        .foregroundStyle(confidence >= 0.9 ? DS.Palette.success : (confidence >= 0.75 ? DS.Palette.warning : DS.Palette.danger))
                }
            }
        }
    }

    // MARK: Equity

    private func equityCard(_ model: KalshiBacktestResultModel, timestamps: [Date], values: [Double]) -> some View {
        let byDay = model.byDay
        return Card(padding: 14) {
            VStack(alignment: .leading, spacing: 8) {
                Text("Equity — scrub to see that day's trades").font(.subheadline.weight(.semibold))
                if let day = model.selectedDay {
                    Text("\(day) · \((byDay[day] ?? []).count) trade(s)")
                        .font(.footnote)
                        .foregroundStyle(.tint)
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
                MarketsFlowLayout {
                    dayChip(model, key: "all", label: "All · \(model.trades.count)")
                    ForEach(model.daysList, id: \.self) { d in
                        dayChip(model, key: d, label: "\(d) · \(byDay[d]?.count ?? 0)")
                    }
                }
            }
        }
    }

    private func dayChip(_ model: KalshiBacktestResultModel, key: String, label: String) -> some View {
        let on = model.selectedDay == key
        return Button {
            model.selectedDay = key
        } label: {
            Text(label)
                .font(.caption)
                .padding(.horizontal, 10)
                .padding(.vertical, 5)
                .foregroundStyle(on ? AnyShapeStyle(.tint) : AnyShapeStyle(.secondary))
                .background(on ? DS.Palette.accent.opacity(0.2) : DS.Surface.inset, in: Capsule())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(on ? .isSelected : [])
    }

    // MARK: Tabs

    private func tabs(_ model: KalshiBacktestResultModel) -> some View {
        @Bindable var m = model
        let decisions = model.result?["decision_log"]?.objectList ?? []
        let logs = model.result?["logs"]?.arrayValue ?? []
        let dayTrades = model.dayTrades
        return Card(padding: 14) {
            VStack(alignment: .leading, spacing: 10) {
                Picker("View", selection: $m.tab) {
                    Text("Trades").tag("trades")
                    Text("Decision log").tag("decisions")
                    Text("Logs").tag("logs")
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                switch model.tab {
                case "trades":
                    if dayTrades.isEmpty {
                        Text(model.trades.isEmpty ? "No bets were placed under these settings." : "Scrub or pick a day to see its trades.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    } else {
                        ForEach(Array(dayTrades.enumerated()), id: \.offset) { _, t in tradeCard(t) }
                    }
                case "decisions":
                    if decisions.isEmpty {
                        Text("No decision log recorded.").font(.footnote).foregroundStyle(.secondary)
                    } else {
                        ForEach(Array(decisions.enumerated()), id: \.offset) { _, d in decisionRow(d) }
                    }
                default:
                    VStack(alignment: .leading, spacing: 2) {
                        if logs.isEmpty {
                            Text("No logs recorded.").font(.caption).foregroundStyle(.secondary)
                        } else {
                            ForEach(Array(logs.enumerated()), id: \.offset) { _, l in
                                Text(l.dartDescription)
                                    .font(.system(.caption, design: .monospaced))
                                    .textSelection(.enabled)
                            }
                        }
                    }
                    .padding(10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(DS.Surface.inset, in: .rect(cornerRadius: DS.Radius.small, style: .continuous))
                }
            }
        }
    }

    private func tradeCard(_ t: JSONObject) -> some View {
        let pnl = t["realized_pnl_cents"]?.double
        let hasSharp = !(t["sharp_prob"]?.isNull ?? true)
        let home = KalshiPregame.str(t["home"])
        let outcome = (t["outcome"] ?? .null).dartDescription
        func s(_ k: String) -> String { (t[k] ?? .null).dartDescription }
        return VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 4) {
                flag(t["home_flag"])
                Text(!home.isEmpty ? "\(s("home")) v \(s("away"))" : s("side"))
                    .font(.subheadline.weight(.medium))
                    .lineLimit(1)
                Spacer(minLength: 4)
                flag(t["away_flag"])
                Text(KalshiFormat.money(cents: pnl))
                    .font(.subheadline.weight(.semibold).monospacedDigit())
                    .foregroundStyle((pnl ?? 0) >= 0 ? DS.Palette.success : DS.Palette.danger)
            }
            Text("\(KalshiPregame.str(t["league"])) · \(KalshiBacktestResultModel.pickLabel(t)) · entry \(s("entry_cents"))¢ × \(s("size"))")
                .font(.caption)
                .foregroundStyle(.secondary)
            MarketsFlowLayout(spacing: 6, runSpacing: 4) {
                MarketsTag(text: "edge \(dartToStringAsFixed((t["edge"]?.double ?? 0) * 100, 1))%", color: .secondary)
                MarketsTag(text: hasSharp ? "sharp" : "model-only", color: hasSharp ? DS.Palette.info : DS.Palette.warning)
                MarketsTag(text: outcome, color: t["outcome"] == .string("win") ? DS.Palette.success : DS.Palette.danger)
            }
        }
        .padding(10)
        .background(DS.Surface.inset, in: .rect(cornerRadius: 10, style: .continuous))
    }

    private func decisionRow(_ d: JSONObject) -> some View {
        let dec = KalshiPregame.str(d["decision"])
        let col: Color = dec == "placed" ? DS.Palette.success : (dec == "no_bet" ? .primary : DS.Palette.warning)
        return HStack(alignment: .firstTextBaseline) {
            Text(KalshiPregame.str(d["label"])).font(.footnote).frame(maxWidth: .infinity, alignment: .leading)
            Text(dec).font(.footnote).foregroundStyle(col)
            Text(KalshiPregame.str(d["reason"]))
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .frame(maxWidth: .infinity, alignment: .trailing)
        }
        .padding(.vertical, 5)
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

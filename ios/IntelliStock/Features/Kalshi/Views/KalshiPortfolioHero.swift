import SwiftUI

/// The portfolio hero card — `KalshiPortfolioHero` in
/// kalshi_portfolio_hero.dart: value (or paper P&L) with a rolling number,
/// the day change, and a scrubbable equity chart. Shared by the Kalshi tab
/// and the instance detail.
struct KalshiPortfolioHero: View {
    let title: String
    let state: Loadable<KalshiPortfolio>?
    let onRetry: () -> Void

    @State private var scrubIdx: Int?

    var body: some View {
        Card(padding: 16) {
            VStack(alignment: .leading, spacing: 10) {
                header
                content
            }
        }
    }

    private var header: some View {
        let paper = state?.value?.isPaper ?? false
        return HStack(spacing: 8) {
            Text((paper ? "Paper P&L · progress" : title).uppercased())
                .font(.footnote.weight(.semibold))
                .foregroundStyle(.secondary)
            if paper {
                MarketsTag(text: "MOCK", color: DS.Palette.warning)
            }
        }
    }

    @ViewBuilder
    private var content: some View {
        switch state {
        case .failed(let e):
            ErrorRow(message: KalshiFormat.errorText(e), onRetry: onRetry)
        case .loaded(let p):
            loaded(p)
        case .loading, .none:
            LoadingState().frame(height: 110)
        }
    }

    private func loaded(_ p: KalshiPortfolio) -> some View {
        // Paper: the paper P&L progress curve (the broker value is a static
        // demo balance). Real: the portfolio value curve.
        let vals = p.isPaper ? p.paperSeries : p.series
        let tss = p.isPaper ? p.paperSeriesTs : p.seriesTs
        let headline = p.isPaper ? (p.paperPnl ?? 0) : p.value
        let dayChg = p.isPaper ? (vals.count >= 2 ? vals.last! - vals.first! : 0) : p.dayChange
        let hasSeries = vals.count > 1
        let baseline = vals.first ?? 0
        let scrubbing = scrubIdx.map { $0 >= 0 && $0 < vals.count } ?? false
        let v = scrubbing ? vals[scrubIdx!] : headline
        let change = scrubbing ? v - baseline : dayChg
        let positive = change >= 0
        let color = positive ? DS.Palette.success : DS.Palette.danger

        return VStack(alignment: .leading, spacing: 0) {
            // Paper P&L can be negative: the sign goes before the $.
            Text("\(v < 0 ? "-" : "")$\(dartToStringAsFixed(abs(v), 2))")
                .font(.largeTitle.weight(.heavy))
                .monospacedDigit()
                .contentTransition(.numericText(value: v))
                .animation(.easeOut(duration: 0.5), value: v)
            HStack(spacing: 4) {
                Image(systemName: Symbol.named(positive ? "trending_up" : "trending_down"))
                Text("\(positive ? "+" : "-")$\(dartToStringAsFixed(abs(change), 2))")
                    .fontWeight(.bold)
                    .monospacedDigit()
            }
            .font(.subheadline)
            .foregroundStyle(color)
            .padding(.top, 5)

            if hasSeries {
                ScrubbableAreaChart(
                    timestamps: tss,
                    values: vals,
                    lineColor: dayChg >= 0 ? DS.Palette.success : DS.Palette.danger,
                    height: 168,
                    baseline: baseline,
                    onScrub: { scrubIdx = $0 },
                    indexed: true,
                    pulsingEndDot: true
                )
                .padding(.top, 18)
            } else {
                Text("Equity curve appears once the engine records snapshots.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .padding(.top, 10)
            }
        }
    }
}

/// A Kalshi open-position tile — `kalshiPositionTile`: crest + match,
/// "Yes · pick", current value with unrealized P&L, contracts / odds chips
/// and the max payout.
struct KalshiPositionTile: View {
    let position: KalshiPosition

    var body: some View {
        let p = position
        let title = p.match.isEmpty ? p.marketTicker : p.match
        let pick = (p.pickLabel.isEmpty ? p.side : p.pickLabel).replacingOccurrences(of: " to win", with: "")
        // unrealized_cents arrives in cents → dollars.
        let u = p.unrealizedCents.map { $0 / 100 }
        let positive = (u ?? 0) >= 0
        let pnlColor: Color = u == nil ? .secondary : (positive ? DS.Palette.success : DS.Palette.danger)

        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                MarketsCrest(url: p.pickLogo, initials: KalshiFormat.initials(pick))
                VStack(alignment: .leading, spacing: 2) {
                    Text(title)
                        .font(.subheadline.weight(.semibold))
                        .lineLimit(1)
                    Text("Yes · \(pick)")
                        .font(.caption)
                        .foregroundStyle(.tint)
                        .lineLimit(1)
                }
                Spacer(minLength: 8)
                VStack(alignment: .trailing, spacing: 2) {
                    Text(p.currentValue.map { "$\(dartToStringAsFixed($0, 2))" } ?? "—")
                        .font(.headline.monospacedDigit())
                    Text(u.map { "\(positive ? "+" : "")$\(dartToStringAsFixed($0, 2))" } ?? "")
                        .font(.caption.weight(.semibold).monospacedDigit())
                        .foregroundStyle(pnlColor)
                }
            }
            HStack(spacing: 6) {
                MarketsTag(text: "\(p.contracts)×")
                MarketsTag(text: "Buy \(p.oddsPct.map { "\(dartToStringAsFixed($0, 0))%" } ?? "—")")
                Spacer()
                Text("$\(dartToStringAsFixed(p.maxPayout, 0)) max payout")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
        .padding(12)
        .background(DS.Surface.inset, in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
        .accessibilityElement(children: .combine)
    }
}

/// The leagues multi-select: a row showing the selection that pushes a
/// checkmark list (the Dart chip `Wrap`). Order follows the taps, as the
/// Dart set / list did.
struct KalshiLeaguePicker: View {
    @Binding var selected: [String]

    var body: some View {
        NavigationLink {
            KalshiLeagueList(selected: $selected)
        } label: {
            LabeledContent("Leagues") {
                Text(selected.isEmpty ? "None" : selected.joined(separator: ", "))
                    .lineLimit(2)
                    .multilineTextAlignment(.trailing)
            }
        }
    }
}

private struct KalshiLeagueList: View {
    @Binding var selected: [String]

    var body: some View {
        List {
            ForEach(kalshiLeagues, id: \.self) { league in
                let on = selected.contains(league)
                Button {
                    if let i = selected.firstIndex(of: league) {
                        selected.remove(at: i)
                    } else {
                        selected.append(league)
                    }
                } label: {
                    HStack {
                        Text(league).foregroundStyle(.primary)
                        Spacer()
                        if on {
                            Image(systemName: Symbol.named("check"))
                                .foregroundStyle(.tint)
                                .fontWeight(.semibold)
                        }
                    }
                }
                .accessibilityAddTraits(on ? .isSelected : [])
            }
        }
        .navigationTitle("Leagues")
        .navigationBarTitleDisplayMode(.inline)
    }
}

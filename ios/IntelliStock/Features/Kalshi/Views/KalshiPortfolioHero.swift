import SwiftUI

/// The portfolio hero — `KalshiPortfolioHero` in kalshi_portfolio_hero.dart:
/// value (or paper P&L) with a rolling number, the day change, and a
/// scrubbable equity chart. A list `Section` (Stocks style: the hero, then the
/// chart, no card) shared by the Kalshi tab and the instance detail. The
/// section header names it ("Portfolio value", or "Paper P&L · progress"
/// with a Mock badge).
struct KalshiPortfolioHero: View {
    let title: String
    let state: Loadable<KalshiPortfolio>?
    let onRetry: () -> Void

    @State private var scrubIdx: Int?

    var body: some View {
        Section {
            content
        } header: {
            header
        }
    }

    private var header: some View {
        let paper = state?.value?.isPaper ?? false
        return HStack(spacing: 8) {
            Text(paper ? "Paper P&L · progress" : title)
            if paper {
                MarketsTag(text: "Mock", color: DS.Palette.warning)
            }
        }
    }

    @ViewBuilder
    private var content: some View {
        switch state {
        case .failed(let e):
            ErrorRow(message: KalshiFormat.errorText(e), onRetry: onRetry)
                .listRowInsets(EdgeInsets())
                .listRowBackground(Color.clear)
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

        return VStack(alignment: .leading, spacing: DS.cardGroupSpacing) {
            // Paper P&L can be negative: the sign goes before the $.
            HeroValueHeader(
                "\(v < 0 ? "-" : "")$\(dartToStringAsFixed(abs(v), 2))",
                numericValue: v,
                valueAnimation: scrubbing ? nil : .easeOut(duration: 0.5),
                change: "\(positive ? "+" : "-")$\(dartToStringAsFixed(abs(change), 2))",
                direction: positive ? .up : .down,
                status: hasSeries ? nil : "Equity curve appears once the engine records snapshots."
            )
            if hasSeries {
                ScrubbableAreaChart(
                    timestamps: tss,
                    values: vals,
                    lineColor: dayChg >= 0 ? DS.Palette.success : DS.Palette.danger,
                    height: 180,
                    baseline: baseline,
                    onScrub: { scrubIdx = $0 },
                    // Paper and real are different curves: flipping draws in again.
                    drawInKey: AnyHashable(p.isPaper),
                    indexed: true,
                    pulsingEndDot: true
                )
            }
        }
        .padding(.vertical, 6)
    }
}

/// A Kalshi open position as a list row — `kalshiPositionTile`: the crest and
/// match, "Yes · pick", the current value with unrealized P&L, then the
/// contracts, buy odds and max payout as a footnote.
struct KalshiPositionRow: View {
    let position: KalshiPosition

    var body: some View {
        let p = position
        let title = p.match.isEmpty ? p.marketTicker : p.match
        let pick = (p.pickLabel.isEmpty ? p.side : p.pickLabel).replacingOccurrences(of: " to win", with: "")
        // unrealized_cents arrives in cents → dollars.
        let u = p.unrealizedCents.map { $0 / 100 }
        let positive = (u ?? 0) >= 0
        let pnlColor: Color = u == nil ? .secondary : (positive ? DS.Palette.success : DS.Palette.danger)

        HStack(alignment: .center, spacing: 12) {
            MarketsCrest(url: p.pickLogo, initials: KalshiFormat.initials(pick))
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.headline)
                    .lineLimit(1)
                Text("Yes · \(pick)")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                Text("\(p.contracts)× · Buy \(p.oddsPct.map { "\(dartToStringAsFixed($0, 0))%" } ?? "—") · $\(dartToStringAsFixed(p.maxPayout, 0)) max payout")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            EntityRowValue(
                p.currentValue.map { "$\(dartToStringAsFixed($0, 2))" } ?? "—",
                detail: u.map { "\(positive ? "+" : "")$\(dartToStringAsFixed($0, 2))" } ?? "",
                detailColor: pnlColor
            )
        }
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
                                .foregroundStyle(DS.Palette.accent)
                                .fontWeight(.semibold)
                        }
                    }
                }
                .tint(.primary)
                .accessibilityAddTraits(on ? .isSelected : [])
            }
        }
        .navigationTitle("Leagues")
        .navigationBarTitleDisplayMode(.inline)
    }
}

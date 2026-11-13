import SwiftUI

/// The portfolio hero — `KalshiPortfolioHero` in kalshi_portfolio_hero.dart,
/// drawn as the web's portfolio card (`KalshiPortfolioChart.vue`): the
/// headline ("Portfolio value", or "Paper P&L · progress" with Mock), the
/// value with a rolling number, the change and its caption, then the
/// scrubbable equity chart. A one-row card section shared by the Kalshi tab
/// and the instance detail.
struct KalshiPortfolioHero: View {
    let title: String
    let state: Loadable<KalshiPortfolio>?
    let onRetry: () -> Void

    @State private var scrubIdx: Int?
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        switch state {
        case .failed(let e):
            Section {
                ErrorRow(message: KalshiFormat.errorText(e), onRetry: onRetry)
                    .dsClearRow()
            }
        case .loaded(let p):
            DSSection {
                loaded(p)
            }
        case .loading, .none:
            DSSection {
                VStack(alignment: .leading, spacing: 12) {
                    Skeleton(width: 110, height: 14)
                    Skeleton(width: 150, height: 24)
                    Skeleton(width: 90, height: 14)
                    Skeleton(height: 160)
                        .padding(.top, 8)
                }
                .padding(.vertical, 12)
                .accessibilityLabel("Loading portfolio")
            }
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
        // Paper P&L can be negative: the sign goes before the $.
        let valueText = "\(v < 0 ? "-" : "")$\(dartToStringAsFixed(abs(v), 2))"
        let changeText = "\(positive ? "+" : "-")$\(dartToStringAsFixed(abs(change), 2))"

        return VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 12) {
                Text(p.isPaper ? "Paper P&L · progress" : title)
                    .font(DS.Font.bodyMedium)
                    .foregroundStyle(DS.Text.soft)
                    .lineLimit(1)
                Spacer(minLength: 8)
                if p.isPaper {
                    Text("Mock")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                }
            }
            Text(valueText)
                .font(DS.Font.figureLarge)
                .foregroundStyle(p.isPaper ? (v >= 0 ? DS.Palette.gain : DS.Palette.loss) : DS.Text.primary)
                .monospacedDigit()
                .lineLimit(1)
                .minimumScaleFactor(0.6)
                .contentTransition(.numericText(value: v))
                .animation(reduceMotion || scrubbing ? nil : DSMotion.ease(0.5), value: v)
                .padding(.top, 12)
            HStack(spacing: 6) {
                Text(changeText)
                    .foregroundStyle(positive ? DS.Palette.gain : DS.Palette.loss)
                Text(scrubbing || p.isPaper ? "Since start" : "Today")
                    .foregroundStyle(DS.Text.muted)
            }
            .font(DS.Font.body)
            .monospacedDigit()
            .padding(.top, 8)
            .accessibilityElement(children: .combine)
            if hasSeries {
                ScrubbableAreaChart(
                    timestamps: tss,
                    values: vals,
                    lineColor: dayChg >= 0 ? DS.Palette.gain : DS.Palette.loss,
                    height: 180,
                    baseline: baseline,
                    onScrub: { scrubIdx = $0 },
                    // Paper and real are different curves: flipping draws in again.
                    drawInKey: AnyHashable(p.isPaper),
                    indexed: true,
                    pulsingEndDot: true
                )
                .padding(.top, 16)
            } else {
                Text("No snapshots yet. The equity curve appears once the engine records them.")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .padding(.top, 12)
            }
        }
        .padding(.vertical, 12)
    }
}

/// A Kalshi open position as a row — `kalshiPositionTile`, as the web's
/// position row: the crest, the match over "Yes · pick" and the contracts,
/// buy odds and max payout, with the current value and unrealized P&L
/// trailing.
struct KalshiPositionRow: View {
    let position: KalshiPosition

    var body: some View {
        let p = position
        let title = p.match.isEmpty ? p.marketTicker : p.match
        let pick = (p.pickLabel.isEmpty ? p.side : p.pickLabel).replacingOccurrences(of: " to win", with: "")
        // unrealized_cents arrives in cents → dollars.
        let u = p.unrealizedCents.map { $0 / 100 }
        let positive = (u ?? 0) >= 0

        HStack(alignment: .center, spacing: 12) {
            MarketsCrest(url: p.pickLogo, initials: KalshiFormat.initials(pick))
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.primary)
                    .lineLimit(1)
                Text("Yes · \(pick)")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .lineLimit(1)
                Text("\(p.contracts)× · bought at \(p.oddsPct.map { "\(dartToStringAsFixed($0, 0))%" } ?? "—") · $\(dartToStringAsFixed(p.maxPayout, 0)) max payout")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .monospacedDigit()
                    .lineLimit(1)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            EntityRowValue(
                p.currentValue.map { "$\(dartToStringAsFixed($0, 2))" } ?? "—",
                detail: u.map { "\(positive ? "+" : "-")$\(dartToStringAsFixed(abs($0), 2))" } ?? "",
                detailColor: u == nil ? DS.Text.muted : (positive ? DS.Palette.gain : DS.Palette.loss)
            )
        }
        .accessibilityElement(children: .combine)
    }
}

/// The leagues multi-select: an input showing the selection that pushes a
/// checkmark list (the web's league dropdown). Order follows the taps, as
/// the Dart set / list did. `asRow` draws it as a list row instead ("Leagues"
/// and the selection, the list's own chevron), for a `List`, where a link
/// must be the whole row.
struct KalshiLeaguePicker: View {
    @Binding var selected: [String]
    var asRow = false

    var body: some View {
        if asRow {
            NavigationLink {
                KalshiLeagueList(selected: $selected)
            } label: {
                HStack(spacing: 16) {
                    Text("Leagues")
                        .foregroundStyle(DS.Text.label)
                    Spacer(minLength: 8)
                    Text(selected.isEmpty ? "None" : selected.joined(separator: ", "))
                        .foregroundStyle(DS.Text.secondary)
                        .lineLimit(1)
                }
                .font(DS.Font.body)
            }
            .accessibilityValue(selected.isEmpty ? "None" : selected.joined(separator: ", "))
        } else {
            field
        }
    }

    private var field: some View {
        NavigationLink {
            KalshiLeagueList(selected: $selected)
        } label: {
            HStack(spacing: 8) {
                Text(selected.isEmpty ? "Select leagues" : selected.joined(separator: ", "))
                    .foregroundStyle(selected.isEmpty ? DS.Text.faint : DS.Text.soft)
                    .lineLimit(1)
                    .frame(maxWidth: .infinity, alignment: .leading)
                Image(systemName: "chevron.forward")
                    .font(.system(size: 12))
                    .foregroundStyle(DS.Text.muted)
                    .accessibilityHidden(true)
            }
            .contentShape(Rectangle())
            .dsInput()
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Leagues")
        .accessibilityValue(selected.isEmpty ? "None" : selected.joined(separator: ", "))
    }
}

private struct KalshiLeagueList: View {
    @Binding var selected: [String]

    var body: some View {
        List {
            DSSection("", subtitle: "\(selected.count) selected", rowMinHeight: 48) {
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
                            Text(league)
                                .font(DS.Font.body)
                                .foregroundStyle(DS.Text.primary)
                            Spacer()
                            if on {
                                Image(systemName: "checkmark")
                                    .font(.system(size: 14))
                                    .foregroundStyle(DS.Text.primary)
                            }
                        }
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityAddTraits(on ? .isSelected : [])
                }
            }
        }
        .dsList()
        .navigationTitle("Leagues")
        .navigationBarTitleDisplayMode(.inline)
    }
}

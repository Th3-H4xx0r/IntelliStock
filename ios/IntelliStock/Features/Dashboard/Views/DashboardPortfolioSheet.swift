import SwiftUI

/// The account switcher — the "Portfolios" bottom sheet the dashboard's
/// account label opens (redesign spec 2026-10-02, Dashboard). One row per
/// brokerage account: its logo, its name over "Live" or "Paper", its equity
/// with today's change, and a checkmark on the account the dashboard shows.
/// A tap selects that account (`SelectedAccountModel.select`, as the old
/// menu did) and closes the sheet.
///
/// The figures come from `DashboardPortfoliosModel` (read-only
/// `GET /widget/accounts`). Until the first fetch settles each value is
/// redacted; an account without data reads "—".
struct DashboardPortfolioSheet: View {
    let accounts: [BrokerageAccount]
    let selectedId: String
    let portfolios: DashboardPortfoliosModel
    let onSelect: (String) -> Void

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Section {
                    ForEach(accounts) { account in
                        Button {
                            onSelect(account.id)
                            dismiss()
                        } label: {
                            DashboardPortfolioRow(
                                account: account,
                                summary: portfolios.summary(account.id),
                                loading: !portfolios.hasLoaded,
                                selected: account.id == selectedId
                            )
                        }
                        .accessibilityAddTraits(account.id == selectedId ? .isSelected : [])
                    }
                }
            }
            .listStyle(.insetGrouped)
            .navigationTitle("Portfolios")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button(role: .close) { dismiss() }
                }
            }
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
        .onAppear { portfolios.refreshDetached() }
    }
}

/// One account in the sheet.
private struct DashboardPortfolioRow: View {
    let account: BrokerageAccount
    let summary: DashboardAccountSummary?
    let loading: Bool
    let selected: Bool

    var body: some View {
        HStack(spacing: 12) {
            IconTile(size: EntityRowMetrics.iconSize) {
                BrokerageLogo(brokerageType: account.brokerageType, size: EntityRowMetrics.iconSize * 0.5)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(DashboardFormat.accountName(account))
                    .font(.headline)
                    .foregroundStyle(Color.primary)
                    .lineLimit(1)
                Text(account.isPaper ? "Paper" : "Live")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            figures
            Image(systemName: "checkmark")
                .font(.body.weight(.semibold))
                .foregroundStyle(.tint)
                .opacity(selected ? 1 : 0)
                .accessibilityHidden(true)
        }
        .padding(.vertical, 2)
        // A list button tints its label; the row reads as text.
        .foregroundStyle(Color.primary)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder
    private var figures: some View {
        if let summary {
            let direction = ChangeDirection(summary.dayChange)
            VStack(alignment: .trailing, spacing: 2) {
                Text(fmtMoney(summary.equity))
                    .font(.headline.monospacedDigit())
                    .foregroundStyle(Color.primary)
                Text(summary.changeText)
                    .font(.footnote.monospacedDigit())
                    .foregroundStyle(direction.color)
            }
            .lineLimit(1)
        } else if loading {
            VStack(alignment: .trailing, spacing: 2) {
                Text("$00,000.00").font(.headline.monospacedDigit())
                Text("+$00.00 (+0.00%)").font(.footnote.monospacedDigit())
            }
            .redacted(reason: .placeholder)
            .accessibilityLabel("Loading")
        } else {
            Text("—")
                .font(.headline)
                .foregroundStyle(.secondary)
        }
    }
}

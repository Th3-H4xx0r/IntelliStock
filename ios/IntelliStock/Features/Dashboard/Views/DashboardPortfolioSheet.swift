import SwiftUI

/// The account switcher — the "Portfolios" bottom sheet the dashboard's
/// account label opens (redesign spec 2026-10-02, Dashboard). One row per
/// brokerage account: its logo, its name over "Live" or "Paper", its equity
/// with today's change, and a checkmark on the account the dashboard shows.
/// A tap selects that account (`SelectedAccountModel.select`, as the old
/// menu did) and closes the sheet.
///
/// The figures come from `DashboardPortfoliosModel`: each account's own
/// read-only source, fetched in parallel and cached for the session. Each
/// row stays redacted until its account answers; an account with no data
/// reads "—". The selected account's row shows the hero's live 1D figures
/// (`heroSummary`) when it has them, so the two always agree.
struct DashboardPortfolioSheet: View {
    let accounts: [BrokerageAccount]
    let selectedId: String
    let portfolios: DashboardPortfoliosModel
    /// The hero's figures for the selected account while it shows 1D.
    var heroSummary: DashboardAccountSummary?
    let onSelect: (String) -> Void

    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Section {
                    ForEach(accounts) { account in
                        let hero = account.id == selectedId ? heroSummary : nil
                        Button {
                            onSelect(account.id)
                            dismiss()
                        } label: {
                            DashboardPortfolioRow(
                                account: account,
                                summary: hero ?? portfolios.summary(account.id),
                                loading: hero == nil && portfolios.isPending(account.id),
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
        // Cached figures show at once; fresh ones replace them as they land.
        .onAppear { portfolios.refreshDetached(accounts) }
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
            let direction = summary.direction
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
                .accessibilityLabel("No data")
        }
    }
}

import SwiftUI

/// The account switcher — the sheet the dashboard's switcher opens, the
/// web's "Accounts" panel: one row per account, its name over its kind
/// ("Alpaca · Paper"), its value and today's change, and a check on the
/// account the dashboard shows.
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
                DSSection {
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
            .dsList()
            .background(DS.Surface.card)
            .navigationTitle("Accounts")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button(role: .close) { dismiss() }
                }
            }
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
        .presentationBackground(DS.Surface.page)
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
            VStack(alignment: .leading, spacing: 2) {
                Text(DashboardFormat.accountName(account))
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.primary)
                    .lineLimit(1)
                Text(dashboardAccountKind(account))
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            figures
            Image(systemName: "checkmark")
                .font(.system(size: 14))
                .foregroundStyle(DS.Text.secondary)
                .opacity(selected ? 1 : 0)
                .accessibilityHidden(true)
        }
        // A list button tints its label; the row reads as text.
        .foregroundStyle(DS.Text.primary)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder
    private var figures: some View {
        if let summary {
            let direction = summary.direction
            VStack(alignment: .trailing, spacing: 2) {
                Text(fmtMoney(summary.equity))
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.primary)
                Text(summary.dayChangePct.map { fmtPct($0) } ?? summary.changeText)
                    .font(DS.Font.caption)
                    .foregroundStyle(summary.dayChangePct == nil ? direction.color : dashboardChangeColor(summary.dayChangePct))
            }
            .monospacedDigit()
            .lineLimit(1)
        } else if loading {
            VStack(alignment: .trailing, spacing: 4) {
                Skeleton(width: 72, height: 14)
                Skeleton(width: 44, height: 12)
            }
            .accessibilityLabel("Loading")
        } else {
            Text("—")
                .font(DS.Font.body)
                .foregroundStyle(DS.Text.faint)
                .accessibilityLabel("No data")
        }
    }
}

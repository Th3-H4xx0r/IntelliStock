import SwiftUI

/// The Strategies tab — the web's `StrategiesView.vue`: "Strategies" with
/// "All trading strategies with their best AI backtest results" under it,
/// the agent's top 5 ranked by P&L %, then every strategy with its best
/// backtest, sorted and paged. Sorting and page size are toolbar menus,
/// Create strategy is the `+`, and each row's backtest is a swipe action and
/// a context-menu item.
struct StrategiesView: View {
    @Environment(AppServices.self) private var services
    @State private var model: StrategiesModel?

    var body: some View {
        Group {
            if let model {
                content(model)
            } else {
                Color.clear
            }
        }
        .background(DS.Surface.page)
        .navigationTitle("Strategies")
        .navigationBarTitleDisplayMode(.large)
        .toolbar {
            if let model {
                ToolbarItem(placement: .topBarTrailing) { sortMenu(model) }
                ToolbarItem(placement: .topBarTrailing) { pageMenu(model) }
            }
            ToolbarItem(placement: .topBarTrailing) {
                // The web pushes /instances?createStrategy=1: the Instances tab.
                ToolbarAddButton("Create strategy") { services.router.go("/instances") }
            }
        }
        .task {
            if model == nil {
                model = StrategiesModel(repository: { [services] in services.strategyRepository })
            }
            if let model, model.needsLoad { await model.fetchAll() }
        }
    }

    // MARK: Toolbar

    private static let sortFields: [(StrategySortField, String)] = [
        (.name, "Name"), (.bestPnl, "Best P&L"), (.bestPct, "Best P&L %"), (.backtests, "Backtests"),
    ]

    /// The web's sortable columns as a menu: choosing the active field again
    /// flips its direction, shown by the arrow beside it (Files does the same).
    private func sortMenu(_ model: StrategiesModel) -> some View {
        Menu {
            ForEach(Self.sortFields, id: \.0) { field, label in
                Button {
                    model.setSort(field)
                } label: {
                    if model.sortField == field {
                        Label(label, systemImage: Symbol.named(model.sortAsc ? "arrow_upward" : "arrow_downward"))
                    } else {
                        Text(label)
                    }
                }
                .accessibilityAddTraits(model.sortField == field ? .isSelected : [])
            }
        } label: {
            Label("Sort", systemImage: "arrow.up.arrow.down")
        }
    }

    /// Per page and the page jump.
    private func pageMenu(_ model: StrategiesModel) -> some View {
        ToolbarMenu("Page options") {
            Picker("Per page", selection: Binding(get: { model.perPage }, set: { model.setPerPage($0) })) {
                ForEach(StrategiesModel.perPageOptions, id: \.self) { Text("\($0) per page").tag($0) }
            }
            .pickerStyle(.menu)
            if model.totalPages > 1 {
                // The five-page window around the current page.
                Picker("Go to page", selection: Binding(get: { model.page }, set: { model.setPage($0) })) {
                    ForEach(StrategiesModel.pageWindow(page: model.page, totalPages: model.totalPages), id: \.self) { Text("Page \($0)").tag($0) }
                }
                .pickerStyle(.menu)
            }
        }
    }

    // MARK: Content

    private func content(_ model: StrategiesModel) -> some View {
        List {
            Section {
                Text("All trading strategies with their best AI backtest results")
                    .font(DS.Font.pageSub)
                    .foregroundStyle(DS.Text.muted)
                    .dsClearRow(top: 0, bottom: 4)
            }
            if model.loading {
                DSSection {
                    ForEach(0..<5, id: \.self) { _ in skeletonRow }
                } accessory: {
                    Skeleton(width: 128, height: 16)
                }
            } else {
                let top5 = model.top5Enriched
                if !top5.isEmpty {
                    DSSection("Top \(top5.count) strategies", subtitle: "Ranked by P&L %") {
                        ForEach(top5) { e in top5Row(e) }
                    }
                }
                let paged = model.pagedRows
                DSSection("All strategies", subtitle: "\(model.rows.count) strategies") {
                    if paged.isEmpty {
                        EmptyState(title: "No strategies found", subtitle: "Create your first strategy to get started")
                    } else {
                        ForEach(paged) { row in strategyRow(row) }
                    }
                }
                pagination(model)
            }
        }
        .dsList()
        .refreshable { await model.fetchAll() }
    }

    // MARK: Rows

    /// The rank: the number, quiet (the web's `#` column).
    private func rankMark(_ rank: Int) -> some View {
        Text("\(rank)")
            .font(DS.Font.body)
            .foregroundStyle(DS.Text.muted)
            .monospacedDigit()
            .frame(width: 16, alignment: .leading)
            .accessibilityLabel("Rank \(rank)")
    }

    private func pnlValue(_ pnl: Double?, _ pct: Double?) -> some View {
        EntityRowValue(
            fmtPnl(pnl),
            color: backtestPnlColor(pnl),
            detail: fmtPct(pct),
            detailColor: backtestPnlColor(pct)
        )
    }

    private func top5Row(_ e: StrategiesModel.Top5Entry) -> some View {
        let subs = e.subs.prefix(5).joined(separator: ", ") + (e.subs.count > 5 ? " +\(e.subs.count - 5) more" : "")
        let row = StrategyRowLabel(
            title: e.name,
            subtitle: e.subs.isEmpty ? "\(e.subs.count) sub-strategies" : "\(e.subs.count) sub-strategies · \(subs)"
        ) {
            rankMark(e.rank)
        } trailing: {
            pnlValue(e.pnl, e.pct)
        }
        let backtest: (() -> Void)? = e.backtestId.map { bid in { services.router.push(.backtest(bid.dartDescription)) } }
        return Group {
            if let sid = e.strategyId {
                NavigationLink(value: Route.strategy(sid.dartDescription)) { row }
            } else {
                row
            }
        }
        .swipeActions(edge: .trailing) {
            if let backtest {
                Button(action: backtest) {
                    Label("Backtest", systemImage: Symbol.named("analytics"))
                }
                .tint(DS.Swipe.neutral)
            }
        }
        .contextMenu {
            if let sid = e.strategyId {
                Button {
                    services.router.push(.strategy(sid.dartDescription))
                } label: {
                    Label("View strategy", systemImage: Symbol.named("open_in_new"))
                }
            }
            if let backtest {
                Button(action: backtest) {
                    Label("Backtest", systemImage: Symbol.named("analytics"))
                }
            }
        }
    }

    /// A strategy (the web's table row, folded for a phone): the name over
    /// "ID 3 · Rank 1 · 2 subs · 1 instance · 4 backtests", the best P&L
    /// and its % on the right.
    private func strategyRow(_ row: StrategyListRow) -> some View {
        var parts = ["ID \(row.id)"]
        if let rank = row.rank { parts.append("Rank \(rank)") }
        parts.append("\(row.subCount) \(row.subCount == 1 ? "sub" : "subs")")
        if !row.instancesUsing.isEmpty {
            parts.append("\(row.instancesUsing.count) \(row.instancesUsing.count == 1 ? "instance" : "instances")")
        }
        if row.runCount > 0 { parts.append("\(row.runCount) \(row.runCount == 1 ? "backtest" : "backtests")") }
        let backtest: (() -> Void)? = row.bestPnlBid.map { bid in { services.router.push(.backtest(bid)) } }
        return NavigationLink(value: Route.strategy(String(row.id))) {
            StrategyRowLabel(title: row.name, subtitle: parts.joined(separator: " · ")) {
                EmptyView()
            } trailing: {
                pnlValue(row.bestPnl, row.bestPct)
            }
        }
        .swipeActions(edge: .trailing) {
            if let backtest {
                Button(action: backtest) {
                    Label("Best backtest", systemImage: Symbol.named("analytics"))
                }
                .tint(DS.Swipe.neutral)
            }
        }
        .contextMenu {
            if let backtest {
                Button(action: backtest) {
                    Label("Best backtest", systemImage: Symbol.named("analytics"))
                }
            }
        }
    }

    // MARK: Pagination

    /// The web's card footer: "20 strategies · page 1 of 3" with Previous /
    /// Next (shown only when there is more than one page).
    @ViewBuilder
    private func pagination(_ model: StrategiesModel) -> some View {
        let total = model.rows.count
        let pages = model.totalPages
        if pages > 1 {
            Section {
                HStack(spacing: 4) {
                    Text("\(total) strategies · page \(model.page) of \(pages)")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                        .monospacedDigit()
                    Spacer(minLength: 8)
                    pageButton("chevron_left", "Previous page", enabled: model.page > 1) { model.setPage(model.page - 1) }
                    pageButton("chevron_right", "Next page", enabled: model.page < pages) { model.setPage(model.page + 1) }
                }
                .frame(minHeight: 44)
                .dsClearRow(top: 4, bottom: 16, horizontal: DS.pageGutter + 4)
            }
        }
    }

    private func pageButton(_ icon: String, _ label: String, enabled: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: Symbol.named(icon))
        }
        .buttonStyle(.dsIcon(side: 44))
        .disabled(!enabled)
        .accessibilityLabel(label)
    }

    // MARK: Skeleton

    /// The loading shape (`ui-skel`): a name and a figure per row.
    private var skeletonRow: some View {
        HStack(spacing: 24) {
            Skeleton(width: 176, height: 16)
            Spacer(minLength: 8)
            Skeleton(width: 80, height: 16)
        }
        .accessibilityHidden(true)
    }
}

/// A strategy row: the `EntityRow` layout with room for long names — the
/// title takes two lines before it truncates, since strategy names run long
/// beside a two-line P&L.
private struct StrategyRowLabel<Leading: View, Trailing: View>: View {
    let title: String
    let subtitle: String
    @ViewBuilder let leading: () -> Leading
    @ViewBuilder let trailing: () -> Trailing

    @Environment(\.dynamicTypeSize) private var dynamicTypeSize

    var body: some View {
        let stacked = dynamicTypeSize.isAccessibilitySize
        let layout = stacked
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 6))
            : AnyLayout(HStackLayout(alignment: .center, spacing: 12))
        layout {
            HStack(spacing: 12) {
                leading()
                VStack(alignment: .leading, spacing: 2) {
                    Text(title)
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.primary)
                        .lineLimit(stacked ? nil : 2)
                    Text(subtitle)
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                        .monospacedDigit()
                        .lineLimit(stacked ? nil : 2)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            trailing()
                .layoutPriority(1)
        }
        .accessibilityElement(children: .combine)
    }
}

import SwiftUI
import UIKit

/// A crypto instance — `CryptoInstanceDetailScreen`, in the web page's order:
/// the status and strategy under the instance name, the account value, the
/// fixed + dynamic allocation (the drill-in `Sector3DChart`), its backtests
/// (refreshed every 4 s while one is running), then the instance and
/// brokerage details last. Start / Stop and Edit sit in the toolbar.
struct CryptoInstanceDetailView: View {
    let instanceId: String

    @Environment(AppServices.self) private var services
    @State private var model: CryptoInstanceDetailModel?
    @State private var editRequest: CryptoInstanceSheetRequest?
    @State private var backtesting = false

    var body: some View {
        Group {
            if let model {
                if model.loading {
                    CryptoDetailSkeleton()
                } else if let error = model.error {
                    ErrorRow(message: error, onRetry: { Task { await model.retry() } })
                        .padding(.horizontal, DS.pageGutter)
                        .padding(.top, 16)
                        .frame(maxHeight: .infinity, alignment: .top)
                } else if let inst = model.inst {
                    content(model, inst)
                }
            } else {
                Color.clear
            }
        }
        .background(DS.Surface.page)
        .navigationTitle(model?.inst.map(CryptoModel.displayName) ?? "")
        .navigationBarTitleDisplayMode(.large)
        .toolbar { toolbar }
        .task(id: instanceId) {
            // Reused on reappear: the data stays on screen while it refreshes.
            if model?.instanceId != instanceId {
                model = CryptoInstanceDetailModel(instanceId: instanceId, repository: { [services] in services.cryptoRepository })
            }
            await model?.poll(lifecycle: services.lifecycle)
        }
        .sheet(item: $editRequest) { req in
            CryptoInstanceSheet(request: req, repository: { [services] in services.cryptoRepository }) {
                Task { await model?.load() }
            }
        }
        .sheet(isPresented: $backtesting) {
            if let inst = model?.inst {
                CryptoBacktestSheet(inst: inst, repository: { [services] in services.cryptoRepository }) {
                    Task { await model?.refreshBacktests() }
                }
            }
        }
    }

    // MARK: Toolbar

    /// Edit, then Start / Stop (the web header's buttons), as plain toolbar
    /// items on the system glass.
    @ToolbarContentBuilder
    private var toolbar: some ToolbarContent {
        if let model, let inst = model.inst, !model.loading, model.error == nil {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    editRequest = CryptoInstanceSheetRequest(edit: inst)
                } label: {
                    Label("Edit", systemImage: Symbol.named("edit"))
                }
            }
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    Task { await model.toggleRun() }
                } label: {
                    Text(inst.runCommand ? "Stop" : "Start")
                }
                .disabled(model.busy)
            }
        }
    }

    // MARK: Content

    private func content(_ model: CryptoInstanceDetailModel, _ inst: Instance) -> some View {
        let band = model.band
        let strat = model.strategy
        let brokerage = model.brokerage
        let crashed = inst.crashed
        let running = inst.runCommand
        return List {
            // The web header's subtitle, then the account value.
            Section {
                HStack(spacing: 8) {
                    StatusDot(
                        CryptoModel.statusLabel(inst),
                        tone: crashed ? .error : (running ? .live : .off),
                        pulsing: running && !crashed,
                        font: DS.Font.pageSub
                    )
                    Text("·").foregroundStyle(DS.Text.faint)
                    Text("24/7 crypto · \(strat.isEmpty ? "—" : CryptoCatalog.capitalized(strat)) strategy")
                        .lineLimit(1)
                }
                .font(DS.Font.pageSub)
                .foregroundStyle(DS.Text.muted)
                .dsClearRow(top: 0)
                VStack(alignment: .leading, spacing: 12) {
                    Text("Account value")
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.secondary)
                    Text(model.value != nil ? CryptoInstanceDetailModel.fmtUsd(model.value) : "—")
                        .dsValueHero()
                        .foregroundStyle(DS.Text.primary)
                        .lineLimit(1)
                        .minimumScaleFactor(0.6)
                }
                .accessibilityElement(children: .combine)
                .dsClearRow(top: 24)
            }

            DSSection("Allocation", subtitle: "Fixed weights, the rest dynamic", rowMinHeight: 44) {
                // The drilled ring hangs up to 20 pt past the frame, so it
                // keeps clear space around it.
                Sector3DChart(slices: model.slices)
                    .frame(width: 220)
                    .frame(maxWidth: .infinity)
                    .padding(.top, 4)
                    .padding(.bottom, 8)
                if !model.allocChips.isEmpty {
                    MarketsFlowLayout(spacing: 6) {
                        ForEach(Array(model.allocChips.enumerated()), id: \.offset) { _, chip in
                            MarketsChip(text: chip.text)
                        }
                    }
                    .padding(.vertical, 4)
                }
                DSKeyValueRow("Dynamic strategy", strat.isEmpty ? "—" : CryptoCatalog.capitalized(strat))
            }

            backtests(model)

            DSSection("Details", subtitle: "Instance and brokerage", rowMinHeight: 44) {
                HStack(alignment: .firstTextBaseline, spacing: 16) {
                    Text("ID")
                        .foregroundStyle(DS.Text.secondary)
                    Spacer(minLength: 8)
                    Text(verbatim: inst.id)
                        .font(DS.Font.mono)
                        .foregroundStyle(DS.Text.label)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                .font(DS.Font.body)
                .accessibilityElement(children: .combine)
                .contextMenu {
                    Button {
                        UIPasteboard.general.string = inst.id
                    } label: {
                        Label("Copy ID", systemImage: "doc.on.doc")
                    }
                }
                DSKeyValueRow("Band", band.isEmpty ? "—" : CryptoCatalog.capitalized(band))
                DSKeyValueRow("Cadence", CryptoInstanceDetailModel.cadence[band] ?? "~15 min")
                DSKeyValueRow("Uptime", running ? CryptoInstanceDetailModel.fmtDuration(inst.uptimeSeconds) : "—")
                DSKeyValueRow("Created by", inst.createdBy.dsSentenceCased)
                DSKeyValueRow("Account", brokerage?["account_name"].flatMap { $0.isNull ? nil : $0.dartDescription } ?? inst.brokerageId ?? "—")
                DSKeyValueRow("Mode", model.isPaper ? "Paper" : "Live")
                DSKeyValueRow("Account value", model.value != nil ? CryptoInstanceDetailModel.fmtUsd(model.value) : "—")
            }
        }
        .dsList()
        .refreshable { await model.load() }
    }

    private func backtests(_ model: CryptoInstanceDetailModel) -> some View {
        DSSection(
            "Backtests",
            subtitle: "\(model.backtests.count) for this instance",
            action: DSSectionAction("New backtest", systemImage: Symbol.named("add")) { backtesting = true },
            rowMinHeight: 64
        ) {
            if model.backtests.isEmpty {
                Text("No backtests yet for this instance")
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.muted)
            } else {
                ForEach(model.backtests) { b in backtestRow(b) }
            }
        }
    }

    /// One backtest: its number, the coins and dates, the P&L trailing, and
    /// a status dot while it is not finished.
    private func backtestRow(_ b: InstanceBacktestRow) -> some View {
        let coins = b.stocks.map { String($0.split(separator: "/", omittingEmptySubsequences: false).first ?? "") }
        let finished = ["finished", "completed", "done"].contains(b.status.lowercased())
        let coinText = coins.isEmpty ? "Dynamic" : coins.prefix(5).joined(separator: " ")
        return NavigationLink(value: Route.backtest(b.id)) {
            EntityRow(b.id, subtitle: "\(coinText) · \(b.startDate ?? "?") → \(b.endDate ?? "?")", subtitleLineLimit: 2) {
                HStack(spacing: 12) {
                    if !finished {
                        StatusDot(b.status.dsSentenceCased, status: b.status, font: DS.Font.caption)
                    }
                    // A run still going has no P&L yet: the status says enough.
                    if finished || b.pnl != nil {
                        EntityRowValue(
                            CryptoInstanceDetailModel.fmtPnl(b.pnl),
                            color: CryptoInstanceDetailModel.pnlColor(b.pnl),
                            detail: CryptoInstanceDetailModel.fmtPct(b.pnlPercent),
                            detailColor: CryptoInstanceDetailModel.pnlColor(b.pnlPercent)
                        )
                    }
                }
            }
        }
    }
}

/// The first load: the header, the value and a card, as static blocks.
private struct CryptoDetailSkeleton: View {
    var body: some View {
        List {
            Section {
                VStack(alignment: .leading, spacing: 24) {
                    Skeleton(width: 200, height: 14)
                    VStack(alignment: .leading, spacing: 12) {
                        Skeleton(width: 100, height: 14)
                        Skeleton(width: 180, height: 40)
                    }
                }
                .dsClearRow(top: 0)
            }
            DSSection {
                Skeleton(height: 220)
                    .padding(.vertical, 12)
            }
        }
        .dsList()
        .allowsHitTesting(false)
        .accessibilityLabel("Loading instance")
    }
}

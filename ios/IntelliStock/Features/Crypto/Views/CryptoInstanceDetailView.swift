import SwiftUI
import UIKit

/// A crypto instance — `CryptoInstanceDetailScreen`: instance info, the
/// brokerage, the fixed + dynamic allocation as the drill-in `Sector3DChart`
/// (as in Flutter), and its backtests (refreshed every 4 s while one is
/// running). An inset-grouped list under the inline instance name; Start /
/// Stop and Edit sit in the toolbar.
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
                    ProgressView()
                        .frame(maxWidth: .infinity, maxHeight: .infinity)
                } else if let error = model.error {
                    ErrorRow(message: error, onRetry: { Task { await model.retry() } })
                        .padding(20)
                        .frame(maxHeight: .infinity, alignment: .top)
                } else if let inst = model.inst {
                    content(model, inst)
                }
            } else {
                Color.clear
            }
        }
        .background(DS.Surface.canvas)
        .navigationTitle(model?.inst.map(CryptoModel.displayName) ?? "")
        .navigationBarTitleDisplayMode(.inline)
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

    /// Edit, then Start / Stop as the primary action (the Dart buttons under
    /// the header).
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
                .dsProminentButton()
                .disabled(model.busy)
            }
        }
    }

    // MARK: Content

    private func content(_ model: CryptoInstanceDetailModel, _ inst: Instance) -> some View {
        let band = model.band
        let strat = model.strategy
        let brokerage = model.brokerage
        return List {
            Section("Instance info") {
                LabeledContent("Status") {
                    StatusDot(
                        CryptoModel.statusLabel(inst),
                        color: inst.crashed ? DS.Palette.danger : (inst.runCommand ? DS.Palette.success : .secondary),
                        pulsing: inst.runCommand && !inst.crashed
                    )
                }
                LabeledContent("Band", value: band.isEmpty ? "—" : CryptoCatalog.capitalized(band))
                LabeledContent("Cadence", value: CryptoInstanceDetailModel.cadence[band] ?? "~15 min")
                LabeledContent("Uptime", value: inst.runCommand ? CryptoInstanceDetailModel.fmtDuration(inst.uptimeSeconds) : "—")
                LabeledContent("Created by", value: inst.createdBy)
                LabeledContent("ID") {
                    Text(inst.id)
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                .contextMenu {
                    Button {
                        UIPasteboard.general.string = inst.id
                    } label: {
                        Label("Copy ID", systemImage: "doc.on.doc")
                    }
                }
            }
            Section("Brokerage") {
                LabeledContent("Account", value: brokerage?["account_name"].flatMap { $0.isNull ? nil : $0.dartDescription } ?? inst.brokerageId ?? "—")
                LabeledContent("Mode", value: model.isPaper ? "Paper" : "Live")
                LabeledContent("Account value", value: model.value != nil ? CryptoInstanceDetailModel.fmtUsd(model.value) : "—")
                    .monospacedDigit()
            }
            Section("Allocation") {
                // The Dart `Center(SizedBox(width: 220))`; the drilled ring
                // hangs up to 20 pt past the frame, so it keeps clear rows.
                Sector3DChart(slices: model.slices)
                    .frame(width: 220)
                    .frame(maxWidth: .infinity)
                    .padding(.vertical, 20)
                LabeledContent("Dynamic strategy", value: strat.isEmpty ? "—" : CryptoCatalog.capitalized(strat))
                if !model.allocChips.isEmpty {
                    MarketsFlowLayout {
                        ForEach(Array(model.allocChips.enumerated()), id: \.offset) { _, chip in
                            MarketsChip(text: chip.text, color: chip.dynamic ? DS.Palette.accent : .secondary, tint: chip.dynamic ? DS.Palette.accent : nil)
                        }
                    }
                    .padding(.vertical, 4)
                }
            }
            backtests(model)
        }
        .listStyle(.insetGrouped)
        .refreshable { await model.load() }
    }

    private func backtests(_ model: CryptoInstanceDetailModel) -> some View {
        DSSection(
            "Backtests (\(model.backtests.count))",
            action: DSSectionAction("New Backtest", systemImage: Symbol.named("add")) { backtesting = true }
        ) {
            if model.backtests.isEmpty {
                Text("No backtests yet for this instance.")
                    .foregroundStyle(.secondary)
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
            EntityRow("#\(b.id)", subtitle: "\(coinText) · \(b.startDate ?? "?") → \(b.endDate ?? "?")", subtitleLineLimit: 2) {
                HStack(spacing: 8) {
                    if !finished {
                        StatusDot(b.status.dsSentenceCased, color: CryptoInstanceDetailModel.btStatusColor(b.status), font: .footnote)
                    }
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

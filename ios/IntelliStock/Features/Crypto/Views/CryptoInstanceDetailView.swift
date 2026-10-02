import SwiftUI

/// A crypto instance — `CryptoInstanceDetailScreen`: info cards, the fixed +
/// dynamic allocation ring, and its backtests (refreshed every 4 s while one
/// is running).
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
                    LoadingState().padding(.top, 60).frame(maxHeight: .infinity, alignment: .top)
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
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button {
                    Task { await model?.load() }
                } label: {
                    Label("Refresh", systemImage: Symbol.named("refresh"))
                }
            }
        }
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

    private func content(_ model: CryptoInstanceDetailModel, _ inst: Instance) -> some View {
        let band = model.band
        let strat = model.strategy
        let brokerage = model.brokerage
        return ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                header(inst)
                HStack(spacing: 10) {
                    Button {
                        Task { await model.toggleRun() }
                    } label: {
                        Label(inst.runCommand ? "Stop" : "Start", systemImage: Symbol.named(inst.runCommand ? "stop" : "play_arrow"))
                            .fontWeight(.semibold)
                            .frame(maxWidth: .infinity)
                    }
                    .tint(inst.runCommand ? DS.Palette.warning : DS.Palette.success)
                    .disabled(model.busy)
                    Button {
                        editRequest = CryptoInstanceSheetRequest(edit: inst)
                    } label: {
                        Label("Edit", systemImage: Symbol.named("edit"))
                            .fontWeight(.semibold)
                            .frame(maxWidth: .infinity)
                    }
                    .tint(DS.Palette.accent)
                }
                .buttonStyle(.bordered)
                .controlSize(.large)
                .padding(.bottom, 8)

                infoCard("Instance info") {
                    kv("Band", band.isEmpty ? "—" : CryptoCatalog.capitalized(band))
                    kv("Cadence", CryptoInstanceDetailModel.cadence[band] ?? "~15 min")
                    kv("Uptime", inst.runCommand ? CryptoInstanceDetailModel.fmtDuration(inst.uptimeSeconds) : "—",
                       color: inst.runCommand ? DS.Palette.success : nil)
                    kv("Created by", inst.createdBy)
                }
                infoCard("Brokerage") {
                    kv("Account", brokerage?["account_name"].flatMap { $0.isNull ? nil : $0.dartDescription } ?? inst.brokerageId ?? "—")
                    kv("Mode", model.isPaper ? "Paper" : "Live", color: model.isPaper ? DS.Palette.info : DS.Palette.success)
                    kv("Account value", model.value != nil ? CryptoInstanceDetailModel.fmtUsd(model.value) : "—")
                }
                Card {
                    VStack(alignment: .leading, spacing: 12) {
                        eyebrow("Allocation")
                        CryptoAllocationChart(slices: model.slices)
                            .frame(maxWidth: 220)
                            .frame(maxWidth: .infinity)
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Dynamic strategy").font(.caption).foregroundStyle(.secondary)
                            Text(strat.isEmpty ? "—" : CryptoCatalog.capitalized(strat))
                                .font(.body.weight(.semibold))
                                .foregroundStyle(.tint)
                        }
                        MarketsFlowLayout {
                            ForEach(Array(model.allocChips.enumerated()), id: \.offset) { _, chip in
                                MarketsChip(text: chip.text, color: chip.dynamic ? DS.Palette.accent : .primary, tint: chip.dynamic ? DS.Palette.accent : nil)
                            }
                        }
                    }
                }
                backtests(model)
            }
            .padding(.horizontal, 20)
            .padding(.top, 4)
            .padding(.bottom, 40)
        }
        .refreshable { await model.load() }
    }

    private func header(_ inst: Instance) -> some View {
        HStack(alignment: .top, spacing: 12) {
            IconTile(systemImage: Symbol.named("currency_bitcoin"))
            VStack(alignment: .leading, spacing: 2) {
                Text(CryptoModel.displayName(inst))
                    .font(.title3.bold())
                    .lineLimit(1)
                Text(inst.id)
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Spacer(minLength: 8)
            VStack(alignment: .trailing, spacing: 6) {
                AppBadge(label: "24/7", color: DS.Palette.info)
                StatusBadge(
                    label: CryptoModel.statusLabel(inst),
                    color: inst.crashed ? DS.Palette.danger : (inst.runCommand ? DS.Palette.success : .secondary),
                    pulsing: inst.runCommand && !inst.crashed
                )
            }
        }
    }

    private func eyebrow(_ title: String) -> some View {
        Text(title.uppercased())
            .font(.footnote.weight(.semibold))
            .foregroundStyle(.secondary)
            .accessibilityAddTraits(.isHeader)
    }

    private func infoCard<Rows: View>(_ title: String, @ViewBuilder rows: () -> Rows) -> some View {
        Card {
            VStack(alignment: .leading, spacing: 10) {
                eyebrow(title)
                rows()
            }
        }
    }

    private func kv(_ label: String, _ value: String, color: Color? = nil) -> some View {
        HStack(alignment: .firstTextBaseline) {
            Text(label).foregroundStyle(.secondary)
            Spacer()
            Text(value)
                .fontWeight(.semibold)
                .foregroundStyle(color ?? .primary)
                .multilineTextAlignment(.trailing)
        }
        .font(.footnote)
        .accessibilityElement(children: .combine)
    }

    private func backtests(_ model: CryptoInstanceDetailModel) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                eyebrow("Backtests")
                Text("(\(model.backtests.count))")
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(.tertiary)
                Spacer()
                Button {
                    backtesting = true
                } label: {
                    Label("New Backtest", systemImage: Symbol.named("add"))
                        .font(.footnote.weight(.semibold))
                }
                .buttonStyle(.bordered)
                .controlSize(.small)
            }
            .padding(.top, 8)
            if model.backtests.isEmpty {
                Card {
                    VStack(spacing: 8) {
                        Image(systemName: Symbol.named("analytics"))
                            .font(.title)
                            .foregroundStyle(.tertiary)
                        Text("No backtests yet for this instance.")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity)
                }
            } else {
                ForEach(model.backtests) { b in backtestRow(b) }
            }
        }
    }

    private func backtestRow(_ b: InstanceBacktestRow) -> some View {
        let coins = b.stocks.map { String($0.split(separator: "/", omittingEmptySubsequences: false).first ?? "") }
        let color = CryptoInstanceDetailModel.btStatusColor(b.status)
        return Button {
            services.router.push(.backtest(b.id))
        } label: {
            Card(padding: EdgeInsets(top: 12, leading: 14, bottom: 12, trailing: 14)) {
                VStack(alignment: .leading, spacing: 8) {
                    HStack(spacing: 8) {
                        Text("#\(b.id)").font(.caption.monospaced()).foregroundStyle(.secondary)
                        MarketsTag(text: b.status.uppercased(), color: color)
                        Spacer()
                        Text(CryptoInstanceDetailModel.fmtPnl(b.pnl))
                            .font(.subheadline.weight(.bold).monospacedDigit())
                            .foregroundStyle(CryptoInstanceDetailModel.pnlColor(b.pnl))
                        Text(CryptoInstanceDetailModel.fmtPct(b.pnlPercent))
                            .font(.footnote.monospacedDigit())
                            .foregroundStyle(CryptoInstanceDetailModel.pnlColor(b.pnlPercent))
                    }
                    HStack {
                        Text(coins.isEmpty ? "Dynamic" : coins.prefix(5).joined(separator: "  "))
                            .font(.caption.monospaced())
                            .foregroundStyle(coins.isEmpty ? AnyShapeStyle(.tint) : AnyShapeStyle(.primary))
                            .lineLimit(1)
                        Spacer()
                        Text("\(b.startDate ?? "?") → \(b.endDate ?? "?")")
                            .font(.caption2)
                            .foregroundStyle(.secondary)
                    }
                }
            }
        }
        .buttonStyle(.plain)
    }
}

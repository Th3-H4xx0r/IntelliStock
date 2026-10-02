import SwiftUI

/// The crypto instances (`/crypto`) — `CryptoScreen`: 24/7 bots with
/// start / stop / edit / backtest / delete, and a button to create one.
struct CryptoView: View {
    @Environment(AppServices.self) private var services
    @State private var model: CryptoModel?
    @State private var sheet: CryptoInstanceSheetRequest?
    @State private var backtestFor: Instance?
    @State private var confirm: ConfirmRequest?
    @State private var toast: Toast?

    var body: some View {
        Group {
            if let model {
                content(model)
            } else {
                Color.clear
            }
        }
        .background(DS.Surface.canvas)
        .navigationTitle("Crypto")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                Button {
                    Task { await model?.load() }
                } label: {
                    Label("Refresh", systemImage: Symbol.named("refresh"))
                }
            }
            ToolbarItem(placement: .primaryAction) {
                Button {
                    sheet = CryptoInstanceSheetRequest()
                } label: {
                    Label("New", systemImage: Symbol.named("add"))
                }
            }
        }
        .task {
            if model == nil {
                model = CryptoModel(repository: { [services] in services.cryptoRepository })
            }
            if let model, model.instances.needsLoad { await model.load() }
        }
        .sheet(item: $sheet) { req in
            CryptoInstanceSheet(request: req, repository: { [services] in services.cryptoRepository }) {
                Task { await model?.load() }
            }
        }
        .sheet(item: $backtestFor) { inst in
            CryptoBacktestSheet(inst: inst, repository: { [services] in services.cryptoRepository }, onCreated: nil)
        }
        .confirmAlert($confirm)
        .toast($toast)
    }

    private func content(_ model: CryptoModel) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("TRADING")
                        .font(.footnote.weight(.bold))
                        .tracking(1.2)
                        .foregroundStyle(.tint)
                    Text("24/7 bots — pin fixed coin weights, auto-discover the rest.")
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                switch model.instances {
                case .loading:
                    LoadingState().padding(.top, 40)
                case .failed(let e):
                    ErrorRow(message: KalshiFormat.errorText(e), onRetry: { Task { await model.load() } })
                case .loaded(let instances):
                    if instances.isEmpty {
                        EmptyState(
                            systemImage: Symbol.named("currency_bitcoin"),
                            title: "No crypto instances yet",
                            subtitle: "Create a 24/7 crypto bot with a fixed + dynamic coin allocation.",
                            actionLabel: "New Crypto Instance",
                            onAction: { sheet = CryptoInstanceSheetRequest() }
                        )
                    } else {
                        VStack(spacing: 12) {
                            ForEach(instances) { inst in card(model, inst) }
                        }
                    }
                }
            }
            .padding(.horizontal, 20)
            .padding(.bottom, 40)
        }
        .refreshable { await model.load() }
    }

    private func card(_ model: CryptoModel, _ inst: Instance) -> some View {
        let running = inst.runCommand
        let busy = model.isBusy(inst.id)
        return Card {
            VStack(alignment: .leading, spacing: 12) {
                HStack(alignment: .top, spacing: 12) {
                    IconTile(systemImage: Symbol.named("currency_bitcoin"))
                    Button {
                        services.router.push(.cryptoInstance(inst.id))
                    } label: {
                        VStack(alignment: .leading, spacing: 2) {
                            Text(CryptoModel.displayName(inst))
                                .font(.subheadline.weight(.semibold))
                                .foregroundStyle(.primary)
                                .lineLimit(1)
                            Text(inst.id)
                                .font(.caption.monospaced())
                                .foregroundStyle(.secondary)
                                .lineLimit(1)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .contentShape(.rect)
                    }
                    .buttonStyle(.plain)
                    VStack(alignment: .trailing, spacing: 6) {
                        AppBadge(label: "24/7", color: DS.Palette.info)
                        StatusBadge(
                            label: CryptoModel.statusLabel(inst),
                            color: inst.crashed ? DS.Palette.danger : (running ? DS.Palette.success : .secondary),
                            pulsing: running && !inst.crashed
                        )
                    }
                }
                if inst.stocks.isEmpty {
                    Text("100% dynamic — fully auto-discovered.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                } else {
                    MarketsFlowLayout {
                        ForEach(inst.stocks, id: \.self) { s in MarketsChip(text: s) }
                    }
                }
                MarketsFlowLayout {
                    action("visibility", "View", .secondary, disabled: busy) {
                        services.router.push(.cryptoInstance(inst.id))
                    }
                    action("edit", "Edit", DS.Palette.accent, disabled: busy) {
                        sheet = CryptoInstanceSheetRequest(edit: inst)
                    }
                    action("analytics", "Backtest", DS.Palette.info, disabled: busy) {
                        backtestFor = inst
                    }
                    if running {
                        action(busy ? "progress_activity" : "stop", "Stop", DS.Palette.warning, disabled: busy) {
                            Task { await model.stop(inst.id) }
                        }
                    } else {
                        action(busy ? "progress_activity" : "play_arrow", "Start", DS.Palette.success, disabled: busy) {
                            Task { await model.start(inst.id) }
                        }
                    }
                    action("delete", "Delete", DS.Palette.danger, disabled: busy) {
                        confirm = ConfirmRequest(
                            title: "Delete instance",
                            body: "Delete \"\(CryptoModel.displayName(inst))\"? This cannot be undone.",
                            confirmLabel: "Delete",
                            onConfirm: { await model.delete(inst.id) },
                            onError: { error in
                                if !marketsIsCancellation(error) { toast = Toast(KalshiFormat.errorText(error), style: .error) }
                            }
                        )
                    }
                }
            }
        }
    }

    private func action(_ icon: String, _ label: String, _ color: Color, disabled: Bool, _ run: @escaping () -> Void) -> some View {
        Button(action: run) {
            Label(label, systemImage: Symbol.named(icon))
                .font(.caption.weight(.semibold))
        }
        .buttonStyle(.bordered)
        .controlSize(.small)
        .tint(color)
        .disabled(disabled)
    }
}

/// What the create / edit sheet opens with (`showCryptoInstanceSheet`'s
/// arguments).
struct CryptoInstanceSheetRequest: Identifiable {
    let id = UUID()
    var editInstanceId: String?
    var editName: String?
    var editBrokerageId: String?
    var editConfig: JSONObject?
    var editStocks: [String]?

    init() {}

    init(edit inst: Instance) {
        editInstanceId = inst.id
        editName = inst.name
        editBrokerageId = inst.brokerageId
        editConfig = inst.cryptoConfig
        editStocks = inst.stocks
    }
}

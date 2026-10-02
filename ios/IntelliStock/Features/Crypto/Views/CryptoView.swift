import SwiftUI
import UIKit

/// The crypto instances (`/crypto`) — `CryptoScreen`: 24/7 bots with
/// start / stop / edit / backtest / delete, and a button to create one. An
/// inset-grouped list of `EntityRow`s; the card buttons moved to the row's
/// swipe actions and context menu, create to the toolbar `+`.
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
        .navigationBarTitleDisplayMode(.large)
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                ToolbarAddButton("New Crypto Instance") {
                    sheet = CryptoInstanceSheetRequest()
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

    @ViewBuilder
    private func content(_ model: CryptoModel) -> some View {
        switch model.instances {
        case .loading:
            ProgressView()
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        case .failed(let e):
            List {
                Section {
                    ErrorRow(message: KalshiFormat.errorText(e), onRetry: { Task { await model.load() } })
                        .listRowInsets(EdgeInsets())
                        .listRowBackground(Color.clear)
                }
            }
            .listStyle(.insetGrouped)
            .refreshable { await model.load() }
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
                List {
                    DSSection(footer: "24/7 bots — pin fixed coin weights, auto-discover the rest.") {
                        ForEach(instances) { inst in row(model, inst) }
                    }
                }
                .listStyle(.insetGrouped)
                .refreshable { await model.load() }
            }
        }
    }

    /// One bot: the name over its coins (or "100% dynamic"), with the run
    /// state trailing. The row opens the instance; Edit is a leading swipe,
    /// Delete a trailing one, and the context menu holds every Dart card
    /// button.
    private func row(_ model: CryptoModel, _ inst: Instance) -> some View {
        let running = inst.runCommand
        let busy = model.isBusy(inst.id)
        let coins = inst.stocks.isEmpty ? "100% dynamic — fully auto-discovered." : inst.stocks.joined(separator: " · ")
        return NavigationLink(value: Route.cryptoInstance(inst.id)) {
            EntityRow(CryptoModel.displayName(inst), subtitle: coins, systemImage: Symbol.named("currency_bitcoin")) {
                if busy {
                    ProgressView()
                } else {
                    StatusDot(
                        CryptoModel.statusLabel(inst),
                        color: inst.crashed ? DS.Palette.danger : (running ? DS.Palette.success : .secondary),
                        pulsing: running && !inst.crashed
                    )
                }
            }
        }
        .swipeActions(edge: .leading) {
            Button {
                sheet = CryptoInstanceSheetRequest(edit: inst)
            } label: {
                Label("Edit", systemImage: Symbol.named("edit"))
            }
            .tint(DS.Palette.accent)
            .disabled(busy)
        }
        // Delete opens the Dart confirmation first, so it is tinted rather
        // than a destructive role (which would animate the row away).
        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
            Button {
                askDelete(model, inst)
            } label: {
                Label("Delete", systemImage: Symbol.named("delete"))
            }
            .tint(DS.Palette.danger)
            .disabled(busy)
        }
        .contextMenu {
            Section {
                if running {
                    Button {
                        Task { showError(await model.stop(inst.id)) }
                    } label: {
                        Label("Stop", systemImage: Symbol.named("stop"))
                    }
                } else {
                    Button {
                        Task { showError(await model.start(inst.id)) }
                    } label: {
                        Label("Start", systemImage: Symbol.named("play_arrow"))
                    }
                }
                Button {
                    services.router.push(.cryptoInstance(inst.id))
                } label: {
                    Label("View", systemImage: Symbol.named("visibility"))
                }
                Button {
                    sheet = CryptoInstanceSheetRequest(edit: inst)
                } label: {
                    Label("Edit", systemImage: Symbol.named("edit"))
                }
                Button {
                    backtestFor = inst
                } label: {
                    Label("Backtest", systemImage: Symbol.named("analytics"))
                }
                Button {
                    UIPasteboard.general.string = inst.id
                } label: {
                    Label("Copy ID", systemImage: "doc.on.doc")
                }
            }
            .disabled(busy)
            Section {
                Button(role: .destructive) {
                    askDelete(model, inst)
                } label: {
                    Label("Delete", systemImage: Symbol.named("delete"))
                }
                .disabled(busy)
            }
        }
    }

    private func askDelete(_ model: CryptoModel, _ inst: Instance) {
        confirm = ConfirmRequest(
            title: "Delete instance",
            body: "Delete \"\(CryptoModel.displayName(inst))\"? This cannot be undone.",
            confirmLabel: "Delete",
            onConfirm: {
                if let message = await model.delete(inst.id) { throw ApiError(message: message) }
            },
            onError: { error in
                if !error.isCancellation { toast = Toast(KalshiFormat.errorText(error), style: .error) }
            }
        )
    }

    /// A failed row action's toast.
    private func showError(_ message: String?) {
        if let message { toast = Toast(message, style: .error) }
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

import SwiftUI
import UIKit

/// The crypto instances (`/crypto`) — `CryptoScreen`, as the web's calm
/// Crypto page: the subtitle, then one card of instance rows (name, coins,
/// status). Start / stop / edit / backtest / delete live in the row's swipe
/// actions and context menu, create in the toolbar `+`.
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
        .background(DS.Surface.page)
        .navigationTitle("Crypto")
        .navigationBarTitleDisplayMode(.large)
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                ToolbarAddButton("New crypto instance") {
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

    static let subtitle = "Autonomous 24/7 crypto trading. Pin fixed weights, leave the rest dynamic"

    private func content(_ model: CryptoModel) -> some View {
        List {
            Section {
                Text(Self.subtitle)
                    .font(DS.Font.pageSub)
                    .foregroundStyle(DS.Text.muted)
                    .dsClearRow(top: 0)
            }
            switch model.instances {
            case .loading:
                DSSection(rowMinHeight: 64) {
                    ForEach(0..<3, id: \.self) { _ in
                        HStack(spacing: 16) {
                            VStack(alignment: .leading, spacing: 8) {
                                Skeleton(width: 150, height: 14)
                                Skeleton(width: 90, height: 12)
                            }
                            Spacer()
                            Skeleton(width: 64, height: 14)
                        }
                    }
                }
                .allowsHitTesting(false)
                .accessibilityLabel("Loading instances")
            case .failed(let e):
                Section {
                    ErrorRow(message: KalshiFormat.errorText(e), onRetry: { Task { await model.load() } })
                        .dsClearRow()
                }
            case .loaded(let instances):
                if instances.isEmpty {
                    DSSection {
                        EmptyState(
                            title: "No crypto instances yet",
                            subtitle: "Create one to trade a fixed + dynamic coin allocation around the clock",
                            actionLabel: "New crypto instance",
                            onAction: { sheet = CryptoInstanceSheetRequest() }
                        )
                    }
                } else {
                    DSSection(
                        "Instances",
                        subtitle: "\(instances.count) instance\(instances.count == 1 ? "" : "s") · open one for its allocation and backtests",
                        rowMinHeight: 64
                    ) {
                        ForEach(instances) { inst in row(model, inst) }
                    }
                }
            }
        }
        .dsList()
        .refreshable { await model.load() }
    }

    /// One bot: the name over its coins (or "100% dynamic"), with the run
    /// state trailing. The row opens the instance; Edit is a leading swipe,
    /// Delete a trailing one, and the context menu holds every Dart card
    /// button.
    private func row(_ model: CryptoModel, _ inst: Instance) -> some View {
        let running = inst.runCommand
        let busy = model.isBusy(inst.id)
        let coins = inst.stocks.isEmpty ? "100% dynamic, fully auto-discovered" : inst.stocks.joined(separator: " · ")
        return NavigationLink(value: Route.cryptoInstance(inst.id)) {
            EntityRow(CryptoModel.displayName(inst), subtitle: coins) {
                if busy {
                    ProgressView()
                        .controlSize(.small)
                } else {
                    StatusDot(
                        CryptoModel.statusLabel(inst),
                        tone: inst.crashed ? .error : (running ? .live : .off),
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
            .tint(DS.Swipe.neutral)
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
            .tint(DS.Swipe.destructive)
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
                    Label("View details", systemImage: Symbol.named("visibility"))
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

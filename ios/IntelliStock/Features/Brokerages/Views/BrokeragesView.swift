import SwiftUI

/// The linked brokerage accounts — `BrokeragesScreen` in
/// `brokerages_screen.dart`, as the web's Brokerages page: the subtitle, then
/// one card of account rows. Pushed from More. Tapping a row opens its edit
/// sheet; Edit and Remove are swipe actions and context-menu items; `+`
/// links a new account.
struct BrokeragesView: View {
    @Environment(AppServices.self) private var services
    @State private var model: BrokeragesModel?
    @State private var sheet: BrokerageSheetTarget?
    @State private var removeRequest: ConfirmRequest?
    @State private var removing: String?
    @State private var toast: Toast?

    static let subtitle = "Linked accounts the instances trade through"

    var body: some View {
        List {
            Section {
                Text(Self.subtitle)
                    .font(DS.Font.pageSub)
                    .foregroundStyle(DS.Text.muted)
                    .dsClearRow(top: 0)
            }
            switch model?.accounts ?? .loading {
            case .loading:
                DSSection(rowMinHeight: 64) {
                    ForEach(0..<3, id: \.self) { _ in
                        VStack(alignment: .leading, spacing: 8) {
                            Skeleton(width: 160, height: 16)
                            Skeleton(width: 96, height: 12)
                        }
                    }
                }
                .allowsHitTesting(false)
                .accessibilityLabel("Loading accounts")
            case .failed(let error):
                Section {
                    ErrorRow(message: brokerageErrorText(error)) {
                        Task { await model?.refresh() }
                    }
                    .dsClearRow()
                }
            case .loaded(let accounts):
                if accounts.isEmpty {
                    DSSection {
                        EmptyState(
                            title: "No brokerage linked yet",
                            subtitle: "Link an Alpaca account to start stock trading",
                            actionLabel: "Link brokerage",
                            onAction: { sheet = BrokerageSheetTarget(account: nil) }
                        )
                    }
                } else {
                    DSSection(rowMinHeight: 64) {
                        ForEach(accounts) { account in
                            accountRow(account)
                        }
                    }
                }
            }
        }
        .dsList()
        .navigationTitle("Brokerages")
        .navigationBarTitleDisplayMode(.large)
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                ToolbarAddButton("Link brokerage") {
                    sheet = BrokerageSheetTarget(account: nil)
                }
            }
        }
        .refreshable { await model?.refresh() }
        .sheet(item: $sheet) { target in
            LinkBrokerageSheet(editAccount: target.account) {
                await model?.refresh()
            }
        }
        .confirmAlert($removeRequest)
        .toast($toast)
        .task {
            if model == nil {
                let services = services
                model = BrokeragesModel(repository: { services.brokerageRepository })
            }
            if let model, model.accounts.needsLoad { await model.load() }
        }
    }

    /// One account: the row opens its edit sheet; Edit and Remove are also
    /// on the swipe and the context menu. Remove keeps its confirmation.
    private func accountRow(_ account: Brokerage) -> some View {
        Button {
            sheet = BrokerageSheetTarget(account: account)
        } label: {
            BrokerageAccountRow(account: account)
        }
        .buttonStyle(.plain)
        .disabled(removing == account.id)
        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
            // Red by tint, not by role: a destructive-role swipe button
            // animates the row away before the confirmation answers.
            Button("Remove", systemImage: Symbol.named("delete_outline")) {
                confirmRemove(account)
            }
            .tint(DS.Swipe.destructive)
            Button("Edit", systemImage: Symbol.named("edit")) {
                sheet = BrokerageSheetTarget(account: account)
            }
            .tint(DS.Swipe.neutral)
        }
        .contextMenu {
            Button("Edit", systemImage: Symbol.named("edit")) {
                sheet = BrokerageSheetTarget(account: account)
            }
            Divider()
            Button("Remove", systemImage: Symbol.named("delete_outline"), role: .destructive) {
                confirmRemove(account)
            }
        }
    }

    private func confirmRemove(_ account: Brokerage) {
        removeRequest = ConfirmRequest(
            title: "Remove \"\(account.accountName)\"?",
            body: "This unlinks the brokerage account. It cannot be undone.",
            confirmLabel: "Remove",
            role: .destructive,
            onConfirm: {
                removing = account.id
                defer { removing = nil }
                try await model?.remove(account.id)
            },
            onError: { error in
                toast = Toast(brokerageErrorText(error), style: .error)
            }
        )
    }
}

/// Which account the Link / Edit sheet is for (nil = a new one).
private struct BrokerageSheetTarget: Identifiable {
    let id = UUID()
    let account: Brokerage?
}

/// One account — the web's brokerage row: the name over "Alpaca · Paper ·
/// PA3IBY5S84PG", the status as a dot and a word (active live, expired
/// failed, anything else amber), and a refresh error in 12 pt loss red
/// under it. The last-refreshed time is in the edit sheet.
private struct BrokerageAccountRow: View {
    let account: Brokerage

    var body: some View {
        let a = account
        let tone: DSStatusTone = switch BrokeragesModel.statusTone(a.status) {
        case .active: .live
        case .expired: .error
        case .other: .warn
        }
        VStack(alignment: .leading, spacing: 6) {
            EntityRow(a.accountName, subtitle: BrokeragesModel.rowSubtitle(a)) {
                StatusDot(BrokeragesModel.statusLabel(a.status), tone: tone)
            }
            if let error = a.lastError {
                Text("Error: \(error)")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Palette.loss)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .contentShape(Rectangle())
    }
}

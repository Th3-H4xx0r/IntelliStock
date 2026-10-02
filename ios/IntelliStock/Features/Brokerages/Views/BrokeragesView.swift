import SwiftUI

/// The linked brokerage accounts — `BrokeragesScreen` in
/// `brokerages_screen.dart`. Pushed from More; native form: an inset-grouped
/// list of `EntityRow`s. Tapping a row opens its edit sheet; Edit and Remove
/// are swipe actions and context-menu items; `+` links a new account.
struct BrokeragesView: View {
    @Environment(AppServices.self) private var services
    @State private var model: BrokeragesModel?
    @State private var sheet: BrokerageSheetTarget?
    @State private var removeRequest: ConfirmRequest?
    @State private var removing: String?
    @State private var toast: Toast?

    var body: some View {
        List {
            switch model?.accounts ?? .loading {
            case .loading:
                Section("Linked Accounts") {
                    ForEach(0..<3, id: \.self) { _ in
                        BrokerageAccountRow(account: Self.placeholder)
                    }
                }
                .redacted(reason: .placeholder)
                .allowsHitTesting(false)
            case .failed(let error):
                Section {
                    ErrorRow(message: brokerageErrorText(error)) {
                        Task { await model?.refresh() }
                    }
                    .listRowInsets(EdgeInsets())
                    .listRowBackground(Color.clear)
                }
            case .loaded(let accounts):
                if accounts.isEmpty {
                    Section {
                        EmptyState(
                            systemImage: Symbol.named("account_balance"),
                            title: "No brokerages linked yet",
                            subtitle: "Link an Alpaca account to start stock trading.",
                            actionLabel: "Link Your First Brokerage",
                            onAction: { sheet = BrokerageSheetTarget(account: nil) }
                        )
                        .listRowBackground(Color.clear)
                    }
                } else {
                    Section {
                        ForEach(accounts) { account in
                            accountRow(account)
                        }
                    } header: {
                        Text("Linked Accounts")
                    } footer: {
                        Text("Manage your brokerage connections.")
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Brokerages")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .primaryAction) {
                ToolbarAddButton("Link Brokerage") {
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
        .foregroundStyle(.primary)
        .disabled(removing == account.id)
        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
            // Red by tint, not by role: a destructive-role swipe button
            // animates the row away before the confirmation answers.
            Button("Remove", systemImage: Symbol.named("delete_outline")) {
                confirmRemove(account)
            }
            .tint(DS.Palette.danger)
            Button("Edit", systemImage: Symbol.named("edit")) {
                sheet = BrokerageSheetTarget(account: account)
            }
            .tint(DS.Palette.accent)
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
            body: "This will unlink the brokerage account. This cannot be undone.",
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

    private static let placeholder = Brokerage(json: [
        "id": "placeholder", "brokerage_type": "alpaca", "account_name": "Brokerage account",
        "status": "active", "paper": true, "account_number": "PA0000000000",
        "last_refresh_at": "2026-01-01T00:00:00Z",
    ])
}

/// Which account the Link / Edit sheet is for (nil = a new one).
private struct BrokerageSheetTarget: Identifiable {
    let id = UUID()
    let account: Brokerage?
}

/// One account — `_AccountCard`, as a row: the brand logo, the name, then
/// "Alpaca · Paper · PA3IBY5S84PG", and the status as a dot and a word. A
/// refresh error shows under it in red; the last-refreshed time is in the
/// edit sheet.
private struct BrokerageAccountRow: View {
    let account: Brokerage

    var body: some View {
        let a = account
        let statusColor: Color = switch BrokeragesModel.statusTone(a.status) {
        case .active: DS.Palette.success
        case .expired: DS.Palette.danger
        case .other: DS.Palette.warning
        }
        VStack(alignment: .leading, spacing: 6) {
            EntityRow(a.accountName, subtitle: BrokeragesModel.rowSubtitle(a)) {
                IconTile(color: DS.Palette.accent, size: EntityRowMetrics.iconSize) {
                    BrokerageLogo(brokerageType: a.brokerageType, size: 16)
                }
            } trailing: {
                StatusDot(BrokeragesModel.statusLabel(a.status), color: statusColor)
            }
            if let error = a.lastError {
                Text("\(Text("Error: ").fontWeight(.semibold))\(error)")
                    .font(.footnote)
                    .foregroundStyle(DS.Palette.danger)
                    .padding(.leading, EntityRowMetrics.iconSize + 12)
            }
        }
        .padding(.vertical, 2)
    }
}

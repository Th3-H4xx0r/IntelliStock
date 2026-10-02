import SwiftUI

/// The linked brokerage accounts — `BrokeragesScreen` in
/// `brokerages_screen.dart`. Pushed from More; native form: an inset-grouped
/// list with one section per account, on the plain grouped background.
struct BrokeragesView: View {
    @Environment(AppServices.self) private var services
    @State private var model: BrokeragesModel?
    @State private var sheet: BrokerageSheetTarget?
    @State private var removeRequest: ConfirmRequest?
    @State private var removing: String?
    @State private var toast: Toast?

    var body: some View {
        List {
            Section {
                SectionHeader(
                    title: "Linked Accounts",
                    eyebrow: "Brokerages",
                    subtitle: "Manage your brokerage connections."
                ) {
                    Button {
                        sheet = BrokerageSheetTarget(account: nil)
                    } label: {
                        Label("Link Brokerage", systemImage: Symbol.named("add"))
                            .font(.subheadline.weight(.semibold))
                    }
                    .dsProminentButton()
                    .buttonBorderShape(.capsule)
                }
                .listRowBackground(Color.clear)
                .listRowInsets(EdgeInsets(top: 0, leading: 4, bottom: 8, trailing: 4))
            }

            switch model?.accounts ?? .loading {
            case .loading:
                ForEach(0..<3, id: \.self) { _ in
                    BrokerageAccountSection(account: Self.placeholder, onEdit: {}, onRemove: {})
                        .redacted(reason: .placeholder)
                        .allowsHitTesting(false)
                }
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
                    ForEach(accounts) { account in
                        BrokerageAccountSection(
                            account: account,
                            onEdit: { sheet = BrokerageSheetTarget(account: account) },
                            onRemove: { confirmRemove(account) }
                        )
                        .disabled(removing == account.id)
                    }
                }
            }
        }
        .listStyle(.insetGrouped)
        .navigationTitle("Brokerages")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button {
                    Task { await model?.refresh() }
                } label: {
                    Image(systemName: Symbol.named("refresh"))
                }
                .accessibilityLabel("Refresh")
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
                let model = BrokeragesModel(repository: { services.brokerageRepository })
                self.model = model
                await model.load()
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

/// One account — `_AccountCard`, as a list section: header row, details,
/// then Edit and Remove.
private struct BrokerageAccountSection: View {
    let account: Brokerage
    let onEdit: () -> Void
    let onRemove: () -> Void

    var body: some View {
        let a = account
        let isAlpaca = a.brokerageType == "alpaca"
        let badgeColor = isAlpaca ? (a.paper ? DS.Palette.info : DS.Palette.warning) : DS.Palette.success
        let statusColor: Color = switch BrokeragesModel.statusTone(a.status) {
        case .active: DS.Palette.success
        case .expired: DS.Palette.danger
        case .other: DS.Palette.warning
        }

        Section {
            HStack(alignment: .top, spacing: 12) {
                IconTile(color: DS.Palette.accent, size: 40) {
                    BrokerageLogo(brokerageType: a.brokerageType, size: 20)
                }
                VStack(alignment: .leading, spacing: 4) {
                    Text(a.accountName)
                        .font(.headline)
                        .lineLimit(1)
                    AppBadge(label: BrokeragesModel.badgeLabel(a), color: badgeColor)
                }
                Spacer(minLength: 8)
                HStack(spacing: 6) {
                    Circle().fill(statusColor).frame(width: 8, height: 8)
                    Text(a.status ?? "unknown")
                        .font(.caption)
                        .foregroundStyle(statusColor)
                }
                .accessibilityElement(children: .combine)
            }
            .padding(.vertical, 4)

            if a.accountNumber != nil || a.lastRefreshAt != nil || a.lastError != nil {
                VStack(alignment: .leading, spacing: 4) {
                    if let number = a.accountNumber {
                        BrokerageDetailRow(label: "Account #", value: number)
                    }
                    if let refreshed = a.lastRefreshAt {
                        BrokerageDetailRow(label: "Last refreshed", value: BrokeragesModel.refreshedLabel(refreshed))
                    }
                    if let error = a.lastError {
                        Text("\(Text("Error: ").fontWeight(.semibold))\(error)")
                            .font(.footnote)
                            .foregroundStyle(DS.Palette.danger)
                    }
                }
            }

            HStack {
                Button(action: onEdit) {
                    Label("Edit", systemImage: Symbol.named("edit"))
                }
                .buttonStyle(.borderless)
                Spacer()
                Button(role: .destructive, action: onRemove) {
                    Label("Remove", systemImage: Symbol.named("delete_outline"))
                }
                .buttonStyle(.borderless)
            }
            .font(.subheadline)
        }
    }
}

private struct BrokerageDetailRow: View {
    let label: String
    let value: String

    var body: some View {
        Text("\(Text("\(label): ").foregroundStyle(.secondary))\(value)")
            .font(.footnote)
    }
}

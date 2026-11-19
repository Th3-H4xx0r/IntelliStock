import SwiftUI

/// More › Approvals: the requests Jarvis made that wait for the operator
/// (live money, or something it can't undo), soonest expiry first with a
/// live countdown, then the recent ones. Polls every 15 s; pull to refresh.
/// A request opens its detail, where it is approved or denied.
struct ApprovalsView: View {
    @Environment(AppServices.self) private var services
    @State private var model: ApprovalsListModel?
    /// This iPhone has a device record for the current server. Re-read on
    /// every appearance, so registering in Settings and coming back hides
    /// the prompt.
    @State private var registered = true

    static let subtitle = "Requests from Jarvis that wait for you"

    var body: some View {
        Group {
            if let model {
                content(model)
            } else {
                ApprovalsPlaceholder()
            }
        }
        .navigationTitle("Approvals")
        .navigationBarTitleDisplayMode(.large)
        .onAppear {
            registered = services.approvalDeviceStore.record(for: services.apiClient.baseURL) != nil
            if model == nil {
                let services = services
                model = ApprovalsListModel(source: { services.approvalsRepository })
            }
        }
        .task(id: model == nil) {
            await model?.poll(lifecycle: services.lifecycle)
        }
    }

    private func content(_ model: ApprovalsListModel) -> some View {
        List {
            Section {
                Text(Self.subtitle)
                    .font(DS.Font.pageSub)
                    .foregroundStyle(DS.Text.muted)
                    .dsClearRow(top: 8)
            }

            if !registered {
                DSSection {
                    NavigationLink(value: Route.approvalDevices) {
                        HStack(spacing: 8) {
                            DSStatusDot(.warn)
                            Text("Register this iPhone to decide requests")
                                .font(DS.Font.body)
                                .foregroundStyle(DS.Text.primary)
                        }
                    }
                }
            }

            switch model.state {
            case .loading:
                DSSection("Waiting for you") {
                    ApprovalRowSkeleton()
                }
            case .failed(let error):
                Section {
                    ErrorRow(message: approvalsErrorText(error)) { Task { await model.load() } }
                        .dsClearRow()
                }
            case .loaded(let state):
                if let refreshError = state.refreshError {
                    Section {
                        ErrorRow(message: refreshError)
                            .dsClearRow()
                    }
                }

                DSSection("Waiting for you") {
                    if state.waiting.isEmpty {
                        EmptyState(title: "Nothing waiting", subtitle: "Jarvis's live-money and can't-undo requests show up here.")
                    } else {
                        ForEach(state.waiting) { approval in
                            NavigationLink(value: Route.approval(approval.id)) {
                                TimelineView(.periodic(from: .now, by: 1)) { context in
                                    ApprovalRow(approval: approval, now: context.date)
                                }
                            }
                        }
                    }
                }

                if !state.recent.isEmpty {
                    DSSection("Recent") {
                        ForEach(state.recent) { approval in
                            NavigationLink(value: Route.approval(approval.id)) {
                                ApprovalRow(approval: approval, now: Date())
                            }
                        }
                    }
                }
            }
        }
        .dsList()
        .refreshable { await model.refresh() }
    }
}

/// The first load, before the model exists.
private struct ApprovalsPlaceholder: View {
    var body: some View {
        List {
            Section {
                Skeleton(width: 240, height: 14)
                    .dsClearRow(top: 8)
            }
            DSSection("Waiting for you") {
                ApprovalRowSkeleton()
            }
        }
        .dsList()
        .allowsHitTesting(false)
    }
}

private struct ApprovalRowSkeleton: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Skeleton(width: 200, height: 14)
            Skeleton(width: 140, height: 12)
        }
        .accessibilityElement()
        .accessibilityLabel("Loading requests")
    }
}

import SwiftUI

/// Status and controls for the background services — the web dashboard's
/// "Services" card: one row per engine with what it does, its status as a
/// dot and a word, one line of activity, and its controls as buttons. Each
/// row's context menu holds the same controls.
///
/// Each action marks its engine busy (its controls disabled, a spinner in
/// the row) and refreshes the snapshot when it succeeds; failures are
/// swallowed, as in Dart. The snapshot refreshes by pull-to-refresh, the
/// header's refresh button and the 10 s poll, run from `DashboardView`.
struct DashboardServicesSection: View {
    /// Opens the "Start AI Backtest Agent" sheet, which `DashboardView` hosts.
    let onStartAgent: () -> Void

    @Environment(AppServices.self) private var services

    private var model: DashboardModel { services.dashboard }
    private var repo: DashboardRepository { services.dashboardRepository }

    static let footer = "Status and controls for all IntelliStock background services."

    var body: some View {
        DSSection("Services", subtitle: "Background engines that feed the bots", rowMinHeight: 68) {
            switch model.services {
            case .loading:
                ForEach(0..<5, id: \.self) { _ in
                    Skeleton(width: 160, height: 16)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .accessibilityHidden(true)
                }
            case .failed:
                HStack(spacing: 16) {
                    Text(model.services.errorMessage ?? "")
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Palette.loss)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Button("Retry") { Task { await model.refreshNow() } }
                        .buttonStyle(.dsSecondary)
                }
            case .loaded(let svc):
                priceEngine(svc)
                discoverEngine(svc)
                agent(svc)
                digest(svc)
                nexus(svc)
            }
        } accessory: {
            Button {
                Task { await model.refreshNow() }
            } label: {
                Image(systemName: Symbol.named("refresh"))
            }
            .buttonStyle(.dsIcon)
            .padding(.trailing, -6)
            .accessibilityLabel("Refresh services")
        }
    }

    /// `busyNotifier.run(id, action)`.
    private func run(_ id: String, _ action: @escaping () async throws -> Void) {
        Task { await model.run(id, action) }
    }

    // MARK: Price engine

    private func priceEngine(_ svc: ServicesSnapshot) -> some View {
        let id = "price_engine"
        let engine = svc.engineById(id)
        let running = svc.isRunning(id)
        let primary: DashboardServiceAction = running
            ? DashboardServiceAction(label: "Terminate", symbol: Symbol.named("stop_circle"), destructive: true) {
                run(id) { try await repo.terminatePrice() }
            }
            : DashboardServiceAction(label: "Start broker", symbol: Symbol.named("play_circle")) {
                run(id) { try await repo.startPriceService() }
            }
        return DashboardServiceRow(
            title: "Price engine", subtitle: "Live market data",
            status: engine?.status ?? "stopped",
            activity: .text(Self.nonEmpty(engine?.details) ?? "—"),
            actions: [primary],
            busy: model.isBusy(id)
        )
    }

    // MARK: Discover engine

    private func discoverEngine(_ svc: ServicesSnapshot) -> some View {
        let id = "discover_engine"
        let engine = svc.engineById(id)
        let running = svc.isRunning(id)
        return DashboardServiceRow(
            title: "Discover engine", subtitle: "Opportunity discovery",
            status: engine?.status ?? "stopped",
            activity: .text(Self.nonEmpty(engine?.details) ?? "—"),
            actions: [
                DashboardServiceAction(
                    label: running ? "Stop" : "Start",
                    symbol: Symbol.named(running ? "stop_circle" : "play_circle"),
                    destructive: running
                ) {
                    run(id) { try await repo.controlDiscover(running: !running) }
                },
            ],
            busy: model.isBusy(id)
        )
    }

    // MARK: AI backtest agent

    private func agent(_ svc: ServicesSnapshot) -> some View {
        let id = "ai_backtest_engine"
        let engine = svc.engineById(id)
        let running = svc.isRunning(id)
        let paused = svc.isPaused(id)
        let control = svc.agentControl
        let count = control?["count_today"].flatMap { $0.isNull ? nil : $0.dartDescription } ?? "0"
        var activity = "\(count) backtests today · last run \(Self.asString(control?["last_run_date"]) ?? "—")"
        if let resumeAt = Self.asString(control?["resume_at"]) {
            activity += " · resumes \(resumeAt)"
        }
        let active = running || paused
        var actions: [DashboardServiceAction] = []
        if paused {
            actions.append(DashboardServiceAction(label: "Resume", symbol: Symbol.named("play_circle")) {
                run(id) { try await repo.controlAgent(paused: false) }
            })
        } else if running {
            actions.append(DashboardServiceAction(label: "Pause", symbol: Symbol.named("pause_circle")) {
                run(id) { try await repo.controlAgent(paused: true) }
            })
        }
        actions.append(DashboardServiceAction(
            label: active ? "Stop" : "Start",
            symbol: Symbol.named(active ? "stop_circle" : "play_circle"),
            destructive: active
        ) {
            if active {
                run(id) { try await repo.controlAgent(running: false) }
            } else {
                onStartAgent()
            }
        })
        return DashboardServiceRow(
            title: "AI backtest agent", subtitle: "Automated strategy search",
            status: engine?.status ?? "stopped",
            activity: .text(activity),
            actions: actions,
            busy: model.isBusy(id)
        )
    }

    /// Dart `x as String?`: the string, nil for null or a non-string.
    private static func asString(_ v: JSON?) -> String? {
        if case .string(let s)? = v { return s }
        return nil
    }

    private static func nonEmpty(_ s: String?) -> String? {
        guard let s, !s.trimmingCharacters(in: .whitespaces).isEmpty else { return nil }
        return s
    }

    // MARK: Daily digest

    private func digest(_ svc: ServicesSnapshot) -> some View {
        let id = "daily_digest_engine"
        let control = svc.digestControl
        let running = control?["running"] == .bool(true)
        let morning = fmtDateTime(control?["last_morning_at"])
        let evening = fmtDateTime(control?["last_evening_at"])
        return DashboardServiceRow(
            title: "Daily digest", subtitle: "Discord market summaries",
            status: running ? "running" : "stopped",
            activity: .text("Morning \(morning.isEmpty ? "—" : morning) · evening \(evening.isEmpty ? "—" : evening)"),
            actions: [
                DashboardServiceAction(label: "Send now", symbol: Symbol.named("send")) {
                    run(id) { try await repo.digestSendNow() }
                },
                DashboardServiceAction(
                    label: running ? "Stop" : "Start",
                    symbol: Symbol.named(running ? "stop_circle" : "play_circle"),
                    destructive: running
                ) {
                    run(id) { try await repo.controlDigest(running: !running) }
                },
            ],
            busy: model.isBusy(id)
        )
    }

    // MARK: Nexus graph engine

    private func nexus(_ svc: ServicesSnapshot) -> some View {
        let id = "nexus_graph_engine"
        let engine = svc.engineById(id)
        let running = svc.isRunning(id)
        let progress = Self.nexusProgress(svc.nexusStatus)
        return DashboardServiceRow(
            title: "Nexus graph engine", subtitle: "Knowledge graph builder",
            status: engine?.status ?? "stopped",
            activity: progress.map { .progress($0.pct, $0.phase) } ?? .text("No build in progress"),
            actions: [
                DashboardServiceAction(
                    label: running ? "Stop" : "Start",
                    symbol: Symbol.named(running ? "stop_circle" : "play_circle"),
                    destructive: running
                ) {
                    run(id) { try await repo.controlNexus(running: !running) }
                },
            ],
            busy: model.isBusy(id)
        )
    }

    /// `graph_build.progress_pct` rounded, plus the last stage's message.
    static func nexusProgress(_ nexus: JSONObject?) -> (pct: Int, phase: String?)? {
        guard let build = nexus?["graph_build"], build.isObject, let pct = build["progress_pct"].double else { return nil }
        let stages = build["stages"].objectElements
        var phase: String?
        if let last = stages.last, case .string(let message) = last["message"] { phase = message }
        return (Int(pct.rounded()), phase)
    }
}

/// One engine control: its label, symbol and action. A destructive one
/// (Stop, Terminate) draws its label in loss red.
struct DashboardServiceAction {
    let label: String
    let symbol: String
    var destructive = false
    let action: () -> Void
}

/// An engine's activity line: text, or the nexus build's progress.
enum DashboardServiceActivity {
    case text(String)
    case progress(Int, String?)
}

/// One engine (the web's phone row): name and purpose with the status on the
/// right, the activity line, then the controls. Its context menu repeats
/// the controls.
private struct DashboardServiceRow: View {
    let title: String
    let subtitle: String
    let status: String
    let activity: DashboardServiceActivity
    let actions: [DashboardServiceAction]
    let busy: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(title)
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.primary)
                    Text(subtitle)
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                StatusDot(
                    DashboardFormat.pillLabel(status.lowercased()),
                    status: status
                )
            }
            activityView
            HStack(spacing: 8) {
                ForEach(actions.indices, id: \.self) { i in
                    let a = actions[i]
                    Button(a.label, role: a.destructive ? .destructive : nil, action: a.action)
                        .buttonStyle(.dsSecondary)
                }
                if busy {
                    ProgressView()
                        .controlSize(.small)
                        .padding(.leading, 4)
                }
            }
            .disabled(busy)
        }
        .padding(.vertical, 4)
        .contentShape(Rectangle())
        .contextMenu {
            ForEach(actions.indices, id: \.self) { i in
                let a = actions[i]
                Button(role: a.destructive ? .destructive : nil, action: a.action) {
                    Label(a.label, systemImage: a.symbol)
                }
                .disabled(busy)
            }
        }
        .accessibilityHint("Touch and hold for the engine's controls")
    }

    @ViewBuilder
    private var activityView: some View {
        switch activity {
        case .text(let text):
            Text(text)
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
                .monospacedDigit()
                .lineLimit(2)
        case .progress(let pct, let phase):
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 12) {
                    DashboardBar(fraction: Double(min(max(pct, 0), 100)) / 100, color: DS.Text.label)
                        .frame(width: 96)
                    Text("\(pct)%")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.label)
                        .monospacedDigit()
                }
                if let phase {
                    Text(phase)
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                        .lineLimit(1)
                }
            }
            .accessibilityElement(children: .combine)
            .accessibilityLabel("Build progress \(pct)%")
        }
    }
}

/// The "Start AI backtest agent" sheet (the web's modal): an optional
/// special request with its help line, then Cancel or Start agent.
struct DashboardStartAgentSheet: View {
    let onStart: (String?) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var text = ""

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    Text("Optionally give this run a special request")
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.muted)
                    DSField("Special request (optional)", help: "Passed to the agent's strategy-generation model every cycle") {
                        DSTextField("e.g. Include Nexus graph analysis, focus on high-volatility stocks", text: $text, axis: .vertical)
                            .lineLimit(3...3)
                    }
                }
                .padding(.horizontal, 24)
                .padding(.top, 8)
            }
            .background(DS.Surface.card)
            .navigationTitle("Start AI backtest agent")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Start agent") {
                        dismiss()
                        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
                        onStart(trimmed.isEmpty ? nil : trimmed)
                    }
                }
            }
        }
    }
}

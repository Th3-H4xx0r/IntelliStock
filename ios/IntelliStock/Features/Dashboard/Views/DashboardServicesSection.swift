import SwiftUI

/// Status and controls for the background services — `_ServicesSection`
/// and its five cards in `dashboard_screen.dart` (`ServiceCard`) — as the
/// dashboard's "Services" group, one list section per engine:
///
/// - a status row: what the engine does, and a dot with its state. Its
///   context menu holds every control the Dart card had;
/// - the card's stats as `LabeledContent` rows;
/// - the card's primary action as one inline row (Start, Stop, Terminate).
///
/// Each action marks its engine busy (its controls disabled, a spinner on
/// the inline row) and refreshes the snapshot when it succeeds; failures are
/// swallowed, as in Dart. The snapshot refreshes by pull-to-refresh and the
/// 10 s poll, both run from `DashboardView`.
struct DashboardServicesSection: View {
    /// Opens the "Start AI Backtest Agent" sheet, which `DashboardView` hosts.
    let onStartAgent: () -> Void

    @Environment(AppServices.self) private var services

    private var model: DashboardModel { services.dashboard }
    private var repo: DashboardRepository { services.dashboardRepository }

    static let footer = "Status and controls for all IntelliStock background services."

    var body: some View {
        switch model.services {
        case .loading:
            Section {
                ForEach(0..<5, id: \.self) { _ in
                    EntityRow("Service name", subtitle: "Engine description") {
                        StatusDot("Stopped", color: .secondary)
                    }
                    .redacted(reason: .placeholder)
                    .accessibilityHidden(true)
                }
            } header: {
                DashboardGroupHeader(group: "Services")
            } footer: {
                Text(Self.footer)
            }
        case .failed:
            Section {
                ErrorRow(message: model.services.errorMessage ?? "") {
                    Task { await model.refreshNow() }
                }
            } header: {
                DashboardGroupHeader(group: "Services")
            }
        case .loaded(let svc):
            priceEngine(svc)
            discoverEngine(svc)
            agent(svc)
            digest(svc)
            nexus(svc)
        }
    }

    /// `busyNotifier.run(id, action)`.
    private func run(_ id: String, _ action: @escaping () async throws -> Void) {
        Task { await model.run(id, action) }
    }

    // MARK: Price Engine

    private func priceEngine(_ svc: ServicesSnapshot) -> some View {
        let id = "price_engine"
        let engine = svc.engineById(id)
        let busy = model.isBusy(id)
        let running = svc.isRunning(id)
        let primary: DashboardServiceAction = running
            ? DashboardServiceAction(label: "Terminate", symbol: Symbol.named("stop_circle"), destructive: true) {
                run(id) { try await repo.terminatePrice() }
            }
            : DashboardServiceAction(label: "Start", symbol: Symbol.named("play_circle")) {
                run(id) { try await repo.startPriceService() }
            }
        return DashboardServiceSection(
            group: "Services",
            title: "Price Engine", subtitle: "Live market data",
            status: engine?.status ?? "stopped",
            stats: [.cell("Details", engine?.details ?? "No extra details")],
            primary: primary,
            busy: busy
        )
    }

    // MARK: Discover Engine

    private func discoverEngine(_ svc: ServicesSnapshot) -> some View {
        let id = "discover_engine"
        let engine = svc.engineById(id)
        let busy = model.isBusy(id)
        let running = svc.isRunning(id)
        return DashboardServiceSection(
            title: "Discover Engine", subtitle: "Opportunity discovery",
            status: engine?.status ?? "stopped",
            stats: [.cell("Details", engine?.details ?? "No extra details")],
            primary: DashboardServiceAction(
                label: running ? "Stop" : "Start",
                symbol: Symbol.named(running ? "stop_circle" : "play_circle"),
                destructive: running
            ) {
                run(id) { try await repo.controlDiscover(running: !running) }
            },
            busy: busy
        )
    }

    // MARK: AI Backtest Agent

    private func agent(_ svc: ServicesSnapshot) -> some View {
        let id = "ai_backtest_engine"
        let engine = svc.engineById(id)
        let busy = model.isBusy(id)
        let running = svc.isRunning(id)
        let paused = svc.isPaused(id)
        let control = svc.agentControl
        var stats: [DashboardServiceStat] = [
            .cell("Backtests today", control?["count_today"].flatMap { $0.isNull ? nil : $0.dartDescription } ?? "—"),
            .cell("Last run", Self.asString(control?["last_run_date"]) ?? "—"),
        ]
        if let resumeAt = Self.asString(control?["resume_at"]) {
            stats.append(.cell("Resume at", resumeAt))
        }
        let active = running || paused
        var secondary: [DashboardServiceAction] = []
        if paused {
            secondary.append(DashboardServiceAction(label: "Resume", symbol: Symbol.named("play_circle")) {
                run(id) { try await repo.controlAgent(paused: false) }
            })
        } else if running {
            secondary.append(DashboardServiceAction(label: "Pause", symbol: Symbol.named("pause_circle")) {
                run(id) { try await repo.controlAgent(paused: true) }
            })
        }
        return DashboardServiceSection(
            title: "AI Backtest Agent", subtitle: "Automated strategy search",
            status: engine?.status ?? "stopped",
            stats: stats,
            primary: DashboardServiceAction(
                label: active ? "Stop" : "Start",
                symbol: Symbol.named(active ? "stop_circle" : "play_circle"),
                destructive: active
            ) {
                if active {
                    run(id) { try await repo.controlAgent(running: false) }
                } else {
                    onStartAgent()
                }
            },
            secondary: secondary,
            busy: busy
        )
    }

    /// Dart `x as String?`: the string, nil for null or a non-string.
    private static func asString(_ v: JSON?) -> String? {
        if case .string(let s)? = v { return s }
        return nil
    }

    // MARK: Daily Digest

    private func digest(_ svc: ServicesSnapshot) -> some View {
        let id = "daily_digest_engine"
        let control = svc.digestControl
        let running = control?["running"] == .bool(true)
        let busy = model.isBusy(id)
        return DashboardServiceSection(
            title: "Daily Digest", subtitle: "Discord market summaries",
            status: running ? "running" : "stopped",
            stats: [
                .cell("Last morning", fmtDateTime(control?["last_morning_at"])),
                .cell("Last evening", fmtDateTime(control?["last_evening_at"])),
            ],
            primary: DashboardServiceAction(
                label: running ? "Stop" : "Start",
                symbol: Symbol.named(running ? "stop_circle" : "play_circle"),
                destructive: running
            ) {
                run(id) { try await repo.controlDigest(running: !running) }
            },
            secondary: [
                DashboardServiceAction(label: "Send Now", symbol: Symbol.named("send")) {
                    run(id) { try await repo.digestSendNow() }
                },
            ],
            busy: busy
        )
    }

    // MARK: Nexus Graph Engine

    private func nexus(_ svc: ServicesSnapshot) -> some View {
        let id = "nexus_graph_engine"
        let engine = svc.engineById(id)
        let busy = model.isBusy(id)
        let running = svc.isRunning(id)
        let progress = Self.nexusProgress(svc.nexusStatus)
        return DashboardServiceSection(
            title: "Nexus Graph Engine", subtitle: "Knowledge graph builder",
            status: engine?.status ?? "stopped",
            stats: [progress.map { .progress($0.pct, $0.phase) } ?? .cell("Build", "No build in progress")],
            primary: DashboardServiceAction(
                label: running ? "Stop" : "Start",
                symbol: Symbol.named(running ? "stop_circle" : "play_circle"),
                destructive: running
            ) {
                run(id) { try await repo.controlNexus(running: !running) }
            },
            busy: busy,
            footer: Self.footer
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

/// One stat row in a service section.
enum DashboardServiceStat {
    /// `ServiceStatCell`: a label and its value.
    case cell(String, String)
    /// `NexusProgressCell`: build progress bar + last phase.
    case progress(Int, String?)
}

/// One engine control: its label, symbol and action. A destructive one
/// (Stop, Terminate) draws in red.
struct DashboardServiceAction {
    let label: String
    let symbol: String
    var destructive = false
    let action: () -> Void
}

/// One engine (`ServiceCard`): a status row carrying every control in its
/// context menu, the stat rows, and the primary action as an inline row.
private struct DashboardServiceSection: View {
    var group: String?
    let title: String
    let subtitle: String
    let status: String
    let stats: [DashboardServiceStat]
    let primary: DashboardServiceAction
    var secondary: [DashboardServiceAction] = []
    let busy: Bool
    var footer: String?

    var body: some View {
        Section {
            LabeledContent {
                StatusDot(
                    DashboardFormat.pillLabel(status),
                    status: status,
                    pulsing: status.lowercased() == "running"
                )
            } label: {
                Text(subtitle)
            }
            .contentShape(Rectangle())
            .contextMenu {
                controlButton(primary)
                ForEach(secondary.indices, id: \.self) { i in
                    controlButton(secondary[i])
                }
            }
            .accessibilityHint("Touch and hold for the engine's controls")
            ForEach(stats.indices, id: \.self) { i in
                statRow(stats[i])
            }
            // Text only, as Settings draws its action rows: a destructive
            // row's symbol would keep the accent while its text turns red.
            InlineActionRow(
                primary.label,
                role: primary.destructive ? .destructive : nil,
                isBusy: busy,
                action: primary.action
            )
        } header: {
            DashboardGroupHeader(group: group, title: title)
        } footer: {
            if let footer { Text(footer) }
        }
    }

    private func controlButton(_ c: DashboardServiceAction) -> some View {
        Button(role: c.destructive ? .destructive : nil, action: c.action) {
            Label(c.label, systemImage: c.symbol)
        }
        .disabled(busy)
    }

    @ViewBuilder
    private func statRow(_ stat: DashboardServiceStat) -> some View {
        switch stat {
        case .cell(let label, let value):
            LabeledContent(label) {
                Text(value)
                    .lineLimit(2)
                    .multilineTextAlignment(.trailing)
            }
        case .progress(let pct, let phase):
            VStack(alignment: .leading, spacing: 6) {
                LabeledContent("Build progress") {
                    Text("\(pct)%").monospacedDigit()
                }
                ProgressView(value: Double(min(max(pct, 0), 100)), total: 100)
                    .tint(DS.Palette.accent)
                if let phase {
                    Text(phase)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }
            .accessibilityElement(children: .combine)
        }
    }
}

/// The "Start AI Backtest Agent" sheet: an optional special request, then
/// Cancel or Start Agent (`_showStartAgentSheet`).
struct DashboardStartAgentSheet: View {
    let onStart: (String?) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var text = ""

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("e.g. Focus on high-volatility stocks…", text: $text, axis: .vertical)
                        .lineLimit(3...3)
                } header: {
                    Text("Special Request")
                } footer: {
                    Text("Optionally provide a special request for this run.")
                }
            }
            .navigationTitle("Start AI Backtest Agent")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Start Agent") {
                        dismiss()
                        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
                        onStart(trimmed.isEmpty ? nil : trimmed)
                    }
                }
            }
        }
    }
}

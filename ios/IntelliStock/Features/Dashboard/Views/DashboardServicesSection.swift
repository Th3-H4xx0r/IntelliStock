import SwiftUI

/// Status and controls for the background services — `_ServicesSection`
/// and its five cards in `dashboard_screen.dart`, built on `ServiceCard`
/// (`service_card.dart`). Each action marks its engine busy (buttons
/// disabled with a spinner) and refreshes the snapshot when it succeeds;
/// failures are swallowed, as in Dart.
struct DashboardServicesSection: View {
    @Environment(AppServices.self) private var services
    @State private var startAgentSheet = false

    private var model: DashboardModel { services.dashboard }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .top) {
                VStack(alignment: .leading, spacing: 2) {
                    Text("Services")
                        .font(.title3.bold())
                        .accessibilityAddTraits(.isHeader)
                    Text("Status and controls for all IntelliStock background services.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                Button {
                    Task { await model.refreshNow() }
                } label: {
                    Image(systemName: Symbol.named("refresh"))
                        .frame(width: 44, height: 44)
                }
                .foregroundStyle(.secondary)
                .accessibilityLabel("Refresh")
            }

            switch model.services {
            case .loading:
                VStack(spacing: 12) {
                    ForEach(0..<5, id: \.self) { _ in DashboardServiceCardSkeleton() }
                }
            case .failed:
                ErrorRow(message: model.services.errorMessage ?? "") {
                    Task { await model.refreshNow() }
                }
            case .loaded(let svc):
                VStack(spacing: 12) {
                    priceEngine(svc)
                    discoverEngine(svc)
                    agent(svc)
                    digest(svc)
                    nexus(svc)
                }
            }
        }
        .sheet(isPresented: $startAgentSheet) {
            DashboardStartAgentSheet { specialRequest in
                run("ai_backtest_engine") {
                    try await services.dashboardRepository.controlAgent(running: true, specialRequest: specialRequest)
                }
            }
            .presentationDetents([.medium])
            .presentationDragIndicator(.visible)
        }
    }

    /// `busyNotifier.run(id, action)`.
    private func run(_ id: String, _ action: @escaping () async throws -> Void) {
        Task { await model.run(id, action) }
    }

    private var repo: DashboardRepository { services.dashboardRepository }

    // MARK: Price Engine

    private func priceEngine(_ svc: ServicesSnapshot) -> some View {
        let id = "price_engine"
        let engine = svc.engineById(id)
        let busy = model.isBusy(id)
        let running = svc.isRunning(id)
        return DashboardServiceCard(
            symbol: Symbol.named("trending_up"), tint: DS.Palette.info,
            title: "Price Engine", subtitle: "Live market data",
            status: engine?.status ?? "stopped",
            stats: [.cell("Details", engine?.details ?? "No extra details")]
        ) {
            if running {
                DashboardServiceButton(label: "Terminate", symbol: Symbol.named("stop_circle"), tint: DS.Palette.danger, busy: busy) {
                    run(id) { try await repo.terminatePrice() }
                }
            } else {
                DashboardServiceButton(label: "Start", symbol: Symbol.named("play_circle"), tint: DS.Palette.success, busy: busy) {
                    run(id) { try await repo.startPriceService() }
                }
            }
        }
    }

    // MARK: Discover Engine

    private func discoverEngine(_ svc: ServicesSnapshot) -> some View {
        let id = "discover_engine"
        let engine = svc.engineById(id)
        let busy = model.isBusy(id)
        let running = svc.isRunning(id)
        return DashboardServiceCard(
            symbol: Symbol.named("search"), tint: DS.Palette.accent,
            title: "Discover Engine", subtitle: "Opportunity discovery",
            status: engine?.status ?? "stopped",
            stats: [.cell("Details", engine?.details ?? "No extra details")]
        ) {
            DashboardServiceButton(
                label: running ? "Stop" : "Start",
                symbol: Symbol.named(running ? "stop_circle" : "play_circle"),
                tint: running ? DS.Palette.danger : DS.Palette.success,
                busy: busy
            ) {
                run(id) { try await repo.controlDiscover(running: !running) }
            }
        }
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
        return DashboardServiceCard(
            symbol: Symbol.named("smart_toy"), tint: DS.Palette.warning,
            title: "AI Backtest Agent", subtitle: "Automated strategy search",
            status: engine?.status ?? "stopped",
            stats: stats
        ) {
            if paused {
                DashboardServiceButton(label: "Resume", symbol: Symbol.named("play_circle"), tint: DS.Palette.info, busy: busy) {
                    run(id) { try await repo.controlAgent(paused: false) }
                }
            } else if running {
                DashboardServiceButton(label: "Pause", symbol: Symbol.named("pause_circle"), tint: DS.Palette.warning, busy: busy) {
                    run(id) { try await repo.controlAgent(paused: true) }
                }
            }
            DashboardServiceButton(
                label: active ? "Stop" : "Start",
                symbol: Symbol.named(active ? "stop_circle" : "play_circle"),
                tint: active ? DS.Palette.danger : DS.Palette.success,
                busy: busy
            ) {
                if active {
                    run(id) { try await repo.controlAgent(running: false) }
                } else {
                    startAgentSheet = true
                }
            }
        }
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
        return DashboardServiceCard(
            symbol: Symbol.named("newspaper"), tint: DS.Palette.success,
            title: "Daily Digest", subtitle: "Discord market summaries",
            status: running ? "running" : "stopped",
            stats: [
                .cell("Last morning", fmtDateTime(control?["last_morning_at"])),
                .cell("Last evening", fmtDateTime(control?["last_evening_at"])),
            ]
        ) {
            DashboardServiceButton(
                label: running ? "Stop" : "Start",
                symbol: Symbol.named(running ? "stop_circle" : "play_circle"),
                tint: running ? DS.Palette.danger : DS.Palette.success,
                busy: busy
            ) {
                run(id) { try await repo.controlDigest(running: !running) }
            }
            DashboardServiceButton(label: "Send Now", symbol: Symbol.named("send"), tint: DS.Palette.accent, busy: busy) {
                run(id) { try await repo.digestSendNow() }
            }
        }
    }

    // MARK: Nexus Graph Engine

    private func nexus(_ svc: ServicesSnapshot) -> some View {
        let id = "nexus_graph_engine"
        let engine = svc.engineById(id)
        let busy = model.isBusy(id)
        let running = svc.isRunning(id)
        let progress = Self.nexusProgress(svc.nexusStatus)
        return DashboardServiceCard(
            symbol: Symbol.named("hub"), tint: DS.Palette.accent,
            title: "Nexus Graph Engine", subtitle: "Knowledge graph builder",
            status: engine?.status ?? "stopped",
            stats: [progress.map { .progress($0.pct, $0.phase) } ?? .cell("Build", "No build in progress")]
        ) {
            DashboardServiceButton(
                label: running ? "Stop" : "Start",
                symbol: Symbol.named(running ? "stop_circle" : "play_circle"),
                tint: running ? DS.Palette.danger : DS.Palette.success,
                busy: busy
            ) {
                run(id) { try await repo.controlNexus(running: !running) }
            }
        }
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

/// One cell in a service card's stats grid.
enum DashboardServiceStat {
    /// `ServiceStatCell`: label + mono value.
    case cell(String, String)
    /// `NexusProgressCell`: build progress bar + last phase.
    case progress(Int, String?)
}

/// An engine control card (`ServiceCard`): icon tile, title, subtitle and
/// status badge; a stats grid (one cell full width, else two columns);
/// action buttons in a row.
struct DashboardServiceCard<Buttons: View>: View {
    let symbol: String
    let tint: Color
    let title: String
    let subtitle: String
    let status: String
    let stats: [DashboardServiceStat]
    @ViewBuilder let buttons: () -> Buttons

    var body: some View {
        Card {
            VStack(alignment: .leading, spacing: 16) {
                HStack(alignment: .top, spacing: 12) {
                    IconTile(systemImage: symbol, color: tint)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(title)
                            .font(.subheadline.weight(.semibold))
                            .lineLimit(1)
                        Text(subtitle)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    StatusBadge(
                        label: DashboardFormat.pillLabel(status),
                        color: StatusBadge.color(forStatus: status),
                        pulsing: status.lowercased() == "running"
                    )
                }
                if !stats.isEmpty {
                    statsGrid
                }
                HStack(spacing: 8) {
                    buttons()
                }
            }
        }
    }

    @ViewBuilder
    private var statsGrid: some View {
        if stats.count == 1 {
            cell(stats[0])
        } else {
            Grid(horizontalSpacing: 8, verticalSpacing: 8) {
                ForEach(Array(stride(from: 0, to: stats.count, by: 2)), id: \.self) { i in
                    GridRow {
                        cell(stats[i])
                        if i + 1 < stats.count {
                            cell(stats[i + 1])
                        } else {
                            Color.clear.gridCellUnsizedAxes([.horizontal, .vertical])
                        }
                    }
                }
            }
        }
    }

    @ViewBuilder
    private func cell(_ stat: DashboardServiceStat) -> some View {
        switch stat {
        case .cell(let label, let value):
            VStack(alignment: .leading, spacing: 4) {
                Text(label)
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                Text(value)
                    .font(.caption.monospaced())
                    .lineLimit(1)
                    .truncationMode(.tail)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(DS.Surface.inset, in: .rect(cornerRadius: DS.Radius.small, style: .continuous))
            .accessibilityElement(children: .combine)
        case .progress(let pct, let phase):
            VStack(alignment: .leading, spacing: 6) {
                HStack {
                    Text("Build progress")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                    Spacer()
                    Text("\(pct)%")
                        .font(.caption.monospaced().weight(.semibold))
                }
                ProgressView(value: Double(min(max(pct, 0), 100)), total: 100)
                    .tint(DS.Palette.accent)
                if let phase {
                    Text(phase)
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityElement(children: .combine)
        }
    }
}

/// `AppButton.semantic(dense: true)`: a tinted bordered button that shows
/// a spinner and disables itself while its engine is busy.
struct DashboardServiceButton: View {
    let label: String
    let symbol: String
    let tint: Color
    let busy: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                if busy {
                    ProgressView().controlSize(.small)
                } else {
                    Image(systemName: symbol)
                }
                Text(label)
            }
            .font(.subheadline.weight(.semibold))
            .frame(maxWidth: .infinity)
            .frame(minHeight: 30)
        }
        .buttonStyle(.bordered)
        .tint(tint)
        .disabled(busy)
    }
}

/// The "Start AI Backtest Agent" sheet: an optional special request, then
/// Cancel or Start Agent (`_showStartAgentSheet`).
private struct DashboardStartAgentSheet: View {
    let onStart: (String?) -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var text = ""

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    HStack(spacing: 12) {
                        IconTile(systemImage: Symbol.named("smart_toy"), color: DS.Palette.warning)
                        VStack(alignment: .leading, spacing: 2) {
                            Text("Start AI Backtest Agent")
                                .font(.subheadline.weight(.semibold))
                            Text("Optionally provide a special request for this run.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                    }
                }
                Section("Special Request") {
                    TextField("e.g. Focus on high-volatility stocks…", text: $text, axis: .vertical)
                        .lineLimit(3...3)
                }
            }
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
                    .tint(DS.Palette.success)
                }
            }
        }
    }
}

/// A service card's loading shape.
private struct DashboardServiceCardSkeleton: View {
    var body: some View {
        Card {
            VStack(alignment: .leading, spacing: 16) {
                HStack(alignment: .top, spacing: 12) {
                    Skeleton(width: 40, height: 40, radius: 10)
                    VStack(alignment: .leading, spacing: 6) {
                        Skeleton(width: 140, height: 14, radius: 6)
                        Skeleton(width: 100, height: 10, radius: 5)
                    }
                    Spacer()
                    Skeleton(width: 60, height: 22, radius: 11)
                }
                Skeleton(height: 44, radius: 8)
                Skeleton(height: 36, radius: 8)
            }
        }
        .accessibilityHidden(true)
    }
}

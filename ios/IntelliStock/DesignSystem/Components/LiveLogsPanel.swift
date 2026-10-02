import SwiftUI
import UIKit

/// The terminal-style live-log panel for an instance — `LiveLogsPanel` in
/// `features/instances/presentation/live_logs_panel.dart`, shared by the
/// instance, live-trading and Kalshi screens.
///
/// Collapsed by default; "View Live Logs" starts a `LogTailer` on
/// `/instances/{id}/live-logs?since_line=n` (5 s while running, 15 s idle).
/// The log sticks to the bottom unless the person scrolls up, when "Jump to
/// Latest" appears. Tailing pauses while the app is in the background.
struct LiveLogsPanel: View {
    let instanceId: String

    @Environment(AppServices.self) private var services

    @State private var tailer: LogTailer?
    /// The instance `tailer` tails, so only a new instance rebuilds it.
    @State private var tailerInstanceId: String?
    @State private var open = false
    /// The person's Pause/Resume toggle (independent of `open`, as in Dart).
    @State private var userPaused = false
    @State private var autoScroll = true
    @State private var showJump = false
    @State private var search = ""
    @State private var toast: Toast?

    var body: some View {
        let state = tailer?.state ?? LogTailerState()
        VStack(alignment: .leading, spacing: 0) {
            header(state)
            if open {
                Divider()
                if !state.lines.isEmpty {
                    searchField
                }
                logArea(state)
            }
        }
        .background(DS.Surface.panel, in: .rect(cornerRadius: DS.Radius.card, style: .continuous))
        .toast($toast)
        .task(id: instanceId) {
            let current: LogTailer
            if let tailer, tailerInstanceId == instanceId {
                // Back on screen: same panel, same lines; an open panel
                // resumes where it left off.
                current = tailer
                current.reattach(open: open, userPaused: userPaused, foreground: services.lifecycle.isForeground)
            } else {
                // A new instance rebuilds the tailer and closes the panel.
                tailer?.dispose()
                let instanceId = instanceId
                current = LogTailer(
                    client: services.apiClient,
                    pathBuilder: { "/instances/\(instanceId)/live-logs?since_line=\($0)" }
                )
                tailer = current
                tailerInstanceId = instanceId
                open = false
                userPaused = false
                search = ""
                autoScroll = true
                showJump = false
            }
            for await foreground in services.lifecycle.changes() {
                if !foreground {
                    current.pause()
                } else if open, !userPaused {
                    current.resume()
                }
            }
            // Off screen (a tab switch or a push): stop polling, keep the lines.
            current.detach()
        }
    }

    // MARK: Header

    private func header(_ state: LogTailerState) -> some View {
        HStack(spacing: 8) {
            PulsingDot(color: statusColor(state), size: 8, pulsing: isRunning(state))

            VStack(alignment: .leading, spacing: 2) {
                Text("Live logs")
                    .font(.subheadline.weight(.semibold))
                Text(headerDetail(state))
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                if state.source == "db" {
                    Text("(last 500 — log file not available)")
                        .font(.caption2)
                        .foregroundStyle(DS.Palette.warning)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            if open, !state.lines.isEmpty {
                Button(action: { togglePause(state) }) {
                    Image(systemName: userPaused ? Symbol.named("play_arrow") : Symbol.named("pause"))
                        .foregroundStyle(userPaused ? DS.Palette.warning : Color.secondary)
                        .frame(width: 44, height: 44)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel(userPaused ? "Resume" : "Pause")

                Button(action: { copy(state) }) {
                    Image(systemName: Symbol.named("content_copy"))
                        .foregroundStyle(.secondary)
                        .frame(width: 44, height: 44)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Copy")
            }

            Button(open ? "Hide Logs" : "View Live Logs", action: toggleOpen)
                .font(.subheadline)
                .buttonStyle(.borderless)
                .frame(minHeight: 44)
        }
        .padding(.leading, 14)
        .padding(.trailing, 10)
        .padding(.vertical, 6)
    }

    /// "Running · 120 lines · instance-ab12.log" — status first, the file last.
    private func headerDetail(_ state: LogTailerState) -> String {
        var parts: [String] = []
        if let status = state.finalStatus, status != "none" { parts.append(friendlyStatus(state)) }
        if !state.lines.isEmpty { parts.append("\(state.lines.count) lines") }
        parts.append("instance-\(shortId(state)).log")
        return parts.joined(separator: " · ")
    }

    private var searchField: some View {
        HStack(spacing: 6) {
            Image(systemName: Symbol.named("search"))
                .foregroundStyle(.secondary)
                .accessibilityHidden(true)
            TextField("Search logs...", text: $search)
                .font(.caption.monospaced())
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .background(DS.Surface.inset, in: .rect(cornerRadius: DS.Radius.small, style: .continuous))
        .padding(.horizontal, 12)
        .padding(.top, 10)
        .padding(.bottom, 4)
    }

    // MARK: Log area

    @ViewBuilder
    private func logArea(_ state: LogTailerState) -> some View {
        if state.loading, state.lines.isEmpty {
            HStack(spacing: 10) {
                ProgressView()
                    .controlSize(.small)
                Text("Loading logs…")
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
            }
            .padding(20)
        } else if state.lines.isEmpty {
            Text(emptyMessage(state))
                .font(.caption.monospaced())
                .italic()
                .foregroundStyle(.secondary)
                .padding(20)
        } else {
            logList(filteredLines(state))
        }
    }

    private func logList(_ lines: [LogLine]) -> some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    ForEach(lines) { line in
                        LiveLogRow(line: line)
                            .id(line.id)
                    }
                }
                .padding(.vertical, 6)
            }
            .frame(maxHeight: 400)
            .defaultScrollAnchor(.bottom)
            .onScrollGeometryChange(for: Bool.self) { geometry in
                geometry.contentSize.height - (geometry.contentOffset.y + geometry.containerSize.height) < 48
            } action: { _, nearBottom in
                if nearBottom != autoScroll || nearBottom == showJump {
                    autoScroll = nearBottom
                    showJump = !nearBottom
                }
            }
            .onChange(of: lines.last?.id) { _, last in
                guard autoScroll, open, let last else { return }
                proxy.scrollTo(last, anchor: .bottom)
            }
            .overlay(alignment: .bottomTrailing) {
                if showJump {
                    Button {
                        autoScroll = true
                        showJump = false
                        if let last = lines.last?.id { proxy.scrollTo(last, anchor: .bottom) }
                    } label: {
                        Label("Jump to Latest", systemImage: Symbol.named("arrow_downward"))
                            .font(.caption.weight(.semibold))
                    }
                    .buttonStyle(.glass)
                    .controlSize(.small)
                    .padding(10)
                }
            }
        }
    }

    // MARK: Actions

    private func toggleOpen() {
        open.toggle()
        guard let tailer else { return }
        if open {
            tailer.resume()
            tailer.start()
        } else {
            tailer.pause()
        }
    }

    private func togglePause(_ state: LogTailerState) {
        if state.loading { return }
        guard let tailer else { return }
        if userPaused { tailer.resume() } else { tailer.pause() }
        userPaused.toggle()
    }

    private func copy(_ state: LogTailerState) {
        UIPasteboard.general.string = state.lines.map(\.raw).joined(separator: "\n")
        toast = Toast("Copied", style: .success)
    }

    // MARK: Derived state

    private func filteredLines(_ state: LogTailerState) -> [LogLine] {
        let query = search.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if query.isEmpty { return state.lines }
        return state.lines.filter { $0.raw.lowercased().contains(query) }
    }

    private func isRunning(_ state: LogTailerState) -> Bool {
        (state.finalStatus ?? "").lowercased() == "running"
    }

    private func statusColor(_ state: LogTailerState) -> Color {
        switch (state.finalStatus ?? "").lowercased() {
        case "running": DS.Palette.info
        case "failed": DS.Palette.danger
        default: Color(uiColor: .tertiaryLabel)
        }
    }

    private func friendlyStatus(_ state: LogTailerState) -> String {
        switch (state.finalStatus ?? "").lowercased() {
        case "running": "Running"
        case "halted": "Halted"
        case "completed": "Completed"
        case "failed": "Failed"
        case "none": "Not running"
        default: "Unknown"
        }
    }

    private func shortId(_ state: LogTailerState) -> String {
        let id = state.buildId ?? instanceId
        return id.count > 12 ? String(id.suffix(12)) : id
    }

    private func emptyMessage(_ state: LogTailerState) -> String {
        if (state.finalStatus ?? "") == "none" {
            return "Broker not running. Start the instance to begin live trading logs."
        }
        if state.error != nil {
            return "Can't reach logs endpoint. Retrying…"
        }
        return "Waiting for log output…"
    }
}

/// One log row: an `MM-dd, HH:mm:ss` timestamp column when the line had one,
/// then the message in its level colour.
private struct LiveLogRow: View {
    let line: LogLine

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 0) {
            if let ts = line.ts {
                Text(Self.timestamp(ts))
                    .foregroundStyle(.secondary)
                    .fixedSize()
                    .padding(.leading, 12)
            } else {
                Color.clear.frame(width: 12, height: 1)
            }
            Text(line.message)
                .foregroundStyle(line.color)
                .padding(.horizontal, 8)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .font(.caption2.monospaced())
        .padding(.vertical, 2)
    }

    static func timestamp(_ ts: Date) -> String {
        let c = DartDateTime.localCalendar.dateComponents([.month, .day, .hour, .minute, .second], from: ts)
        return String(format: "%02d-%02d, %02d:%02d:%02d", c.month ?? 0, c.day ?? 0, c.hour ?? 0, c.minute ?? 0, c.second ?? 0)
    }
}

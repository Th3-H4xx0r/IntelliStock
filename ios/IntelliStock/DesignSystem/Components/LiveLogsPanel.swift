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
                DSDivider()
                if !state.lines.isEmpty {
                    searchField
                }
                logArea(state)
            }
        }
        .dsCardSurface()
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
            DSStatusDot(statusTone(state), pinging: isRunning(state))

            VStack(alignment: .leading, spacing: 2) {
                Text("Live logs")
                    .font(DS.Font.bodyMedium)
                    .foregroundStyle(DS.Text.soft)
                Text(headerDetail(state))
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .monospacedDigit()
                    .lineLimit(1)
                    .truncationMode(.middle)
                if state.source == "db" {
                    HStack(spacing: 6) {
                        DSStatusDot(.warn)
                        Text("Last 500 lines — log file not available")
                            .font(DS.Font.caption)
                            .foregroundStyle(DS.Text.label)
                    }
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)

            if open, !state.lines.isEmpty {
                Button(action: { togglePause(state) }) {
                    Image(systemName: userPaused ? "play" : "pause")
                }
                .buttonStyle(.dsIcon)
                .accessibilityLabel(userPaused ? "Resume" : "Pause")

                Button(action: { copy(state) }) {
                    Image(systemName: Symbol.named("content_copy"))
                }
                .buttonStyle(.dsIcon)
                .accessibilityLabel("Copy")
            }

            Button(open ? "Hide logs" : "View live logs", action: toggleOpen)
                .buttonStyle(.dsSecondary)
        }
        .padding(.leading, DS.rowPadding)
        .padding(.trailing, 12)
        .padding(.vertical, 12)
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
        HStack(spacing: 8) {
            Image(systemName: Symbol.named("search"))
                .font(.system(size: 14))
                .foregroundStyle(DS.Text.muted)
                .accessibilityHidden(true)
            TextField(text: $search, prompt: Text("Search logs").foregroundStyle(DS.Text.faint)) { Text("Search logs") }
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
        }
        .dsInput()
        .padding(.horizontal, 12)
        .padding(.top, 12)
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
                    .font(DS.Font.mono)
                    .foregroundStyle(DS.Text.muted)
            }
            .padding(20)
        } else if state.lines.isEmpty {
            Text(emptyMessage(state))
                .font(DS.Font.mono)
                .foregroundStyle(DS.Text.muted)
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
                        Label("Jump to latest", systemImage: Symbol.named("arrow_downward"))
                    }
                    .buttonStyle(.dsSecondary)
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

    /// The web's log status dot: live while running, done when completed,
    /// red when failed, grey otherwise.
    private func statusTone(_ state: LogTailerState) -> DSStatusTone {
        switch (state.finalStatus ?? "").lowercased() {
        case "running": .live
        case "completed": .off
        case "failed": .error
        default: .off
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
                    .foregroundStyle(DS.Text.faint)
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
        .font(DS.Font.monoSmall)
        .padding(.vertical, 2)
    }

    static func timestamp(_ ts: Date) -> String {
        let c = DartDateTime.localCalendar.dateComponents([.month, .day, .hour, .minute, .second], from: ts)
        return String(format: "%02d-%02d, %02d:%02d:%02d", c.month ?? 0, c.day ?? 0, c.hour ?? 0, c.minute ?? 0, c.second ?? 0)
    }
}

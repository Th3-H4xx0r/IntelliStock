import SwiftUI
import UIKit

/// The terminal-style build-log panel for the Nexus graph — `NexusLogsPanel`:
/// a `LogTailer` on `/nexus-graph-builds/latest/logs?since_line=n` (2 s while
/// building, 15 s idle), started on first open, with search, pause, copy and
/// a sticky bottom.
struct NexusLogsPanel: View {
    @Environment(AppServices.self) private var services

    @State private var tailer: LogTailer?
    @State private var open = false
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
                if !state.lines.isEmpty { searchField }
                logArea(state)
            }
        }
        .background(DS.Surface.panel, in: .rect(cornerRadius: DS.Radius.card, style: .continuous))
        .toast($toast)
        .task {
            let current: LogTailer
            if let tailer {
                // Back on screen: an open panel resumes where it left off
                // (it was a dead, open-looking panel before).
                current = tailer
                current.reattach(open: open, userPaused: userPaused, foreground: services.lifecycle.isForeground)
            } else {
                current = LogTailer(
                    client: services.apiClient,
                    pathBuilder: { "/nexus-graph-builds/latest/logs?since_line=\($0)" },
                    runningInterval: .seconds(2),
                    idleInterval: .seconds(15)
                )
                tailer = current
            }
            for await foreground in services.lifecycle.changes() {
                if !foreground {
                    current.pause()
                } else if open, !userPaused {
                    current.resume()
                }
            }
            // Off screen: stop polling, keep the lines.
            current.detach()
        }
    }

    // MARK: Header

    private func statusColor(_ s: LogTailerState) -> Color {
        switch (s.finalStatus ?? "").lowercased() {
        case "running", "building": DS.Palette.info
        case "failed": DS.Palette.danger
        case "completed": DS.Palette.success
        default: Color(uiColor: .tertiaryLabel)
        }
    }

    private func header(_ state: LogTailerState) -> some View {
        let pulsing = ["running", "building"].contains((state.finalStatus ?? "").lowercased())
        return HStack(spacing: 8) {
            PulsingDot(color: statusColor(state), size: 8, pulsing: pulsing)
            VStack(alignment: .leading, spacing: 2) {
                Text(NexusFormat.logFileName(state.buildId))
                    .font(.caption.monospaced())
                    .lineLimit(1)
                if let s = state.finalStatus, s != "none" {
                    Text(NexusFormat.friendlyStatus(s) + (state.lines.isEmpty ? "" : " · \(state.lines.count) lines"))
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
                if state.source == "db" {
                    Text("(last 500 — log file not available)")
                        .font(.caption2)
                        .foregroundStyle(DS.Palette.warning)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            if open, !state.lines.isEmpty {
                Button {
                    togglePause(state)
                } label: {
                    Image(systemName: Symbol.named(userPaused ? "play_arrow" : "pause"))
                        .foregroundStyle(userPaused ? DS.Palette.warning : Color.secondary)
                        .frame(width: 44, height: 44)
                }
                .buttonStyle(.plain)
                .accessibilityLabel(userPaused ? "Resume" : "Pause")
                Button {
                    UIPasteboard.general.string = state.lines.map(\.raw).joined(separator: "\n")
                    toast = Toast("Copied", style: .success)
                } label: {
                    Image(systemName: Symbol.named("content_copy"))
                        .foregroundStyle(.secondary)
                        .frame(width: 44, height: 44)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Copy")
            }
            Button(action: toggleOpen) {
                Label(open ? "Hide Logs" : "View Build Logs", systemImage: Symbol.named(open ? "visibility_off" : "terminal"))
                    .font(.caption.weight(.semibold))
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
            .tint(open ? DS.Palette.info : .secondary)
        }
        .padding(.leading, 14)
        .padding(.trailing, 10)
        .padding(.vertical, 6)
    }

    private var searchField: some View {
        HStack(spacing: 6) {
            Image(systemName: Symbol.named("search")).foregroundStyle(.secondary).accessibilityHidden(true)
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
                ProgressView().controlSize(.small)
                Text("Loading logs…").font(.caption.monospaced()).foregroundStyle(.secondary)
            }
            .padding(20)
        } else if state.lines.isEmpty {
            Text(emptyMessage(state))
                .font(.caption.monospaced())
                .italic()
                .foregroundStyle(.secondary)
                .padding(20)
        } else {
            logList(filtered(state))
        }
    }

    private func emptyMessage(_ state: LogTailerState) -> String {
        let s = state.finalStatus ?? ""
        if state.error != nil { return "Can't reach logs endpoint. Retrying…" }
        if s == "none" || s.isEmpty { return "No active build. Trigger a Nexus build to see live logs." }
        return "Waiting for log output…"
    }

    private func filtered(_ state: LogTailerState) -> [LogLine] {
        let q = search.lowercased().trimmingCharacters(in: .whitespacesAndNewlines)
        if q.isEmpty { return state.lines }
        return state.lines.filter { $0.raw.lowercased().contains(q) }
    }

    private func logList(_ lines: [LogLine]) -> some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 0) {
                    ForEach(lines) { line in
                        HStack(alignment: .top, spacing: 0) {
                            if let ts = line.ts {
                                Text(NexusFormat.logStamp(ts))
                                    .font(.system(.caption2, design: .monospaced))
                                    .foregroundStyle(.tertiary)
                                    .frame(width: 92, alignment: .leading)
                                    .padding(.leading, 10)
                            } else {
                                Color.clear.frame(width: 10, height: 1)
                            }
                            Text(line.message)
                                .font(.system(.caption2, design: .monospaced))
                                .foregroundStyle(line.color)
                                .padding(.horizontal, 6)
                                .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .padding(.vertical, 2)
                        .id(line.id)
                    }
                }
                .padding(.vertical, 6)
                .textSelection(.enabled)
            }
            .frame(maxHeight: 400)
            .defaultScrollAnchor(.bottom)
            .onScrollGeometryChange(for: Bool.self) { g in
                g.contentSize.height - (g.contentOffset.y + g.containerSize.height) < 48
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
}

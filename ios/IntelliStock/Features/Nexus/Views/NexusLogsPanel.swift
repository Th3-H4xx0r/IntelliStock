import SwiftUI
import UIKit

/// The build-log panel for the Nexus graph — `NexusLogsPanel`, drawn as the
/// web's `NexusLiveLogs.vue` card: a `LogTailer` on
/// `/nexus-graph-builds/latest/logs?since_line=n` (2 s while building, 15 s
/// idle), started on first open, with search, pause, copy and a sticky
/// bottom. The header is always shown; the log opens beneath it on the page
/// colour.
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
                VStack(alignment: .leading, spacing: 0) {
                    if !state.lines.isEmpty { searchField }
                    logArea(state)
                }
                .background(DS.Surface.page)
                .overlay(alignment: .top) { DSDivider() }
            }
        }
        .dsCardSurface()
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

    /// The web's log dot: live while running, red when failed, off otherwise.
    private func statusTone(_ s: LogTailerState) -> DSStatusTone {
        switch (s.finalStatus ?? "").lowercased() {
        case "running", "building": .live
        case "failed": .error
        default: .off
        }
    }

    /// "Building · 120 lines": the status first, then the count.
    private func headerDetail(_ state: LogTailerState) -> String? {
        var parts: [String] = []
        if let s = state.finalStatus, s != "none" { parts.append(NexusFormat.friendlyStatus(s)) }
        if !state.lines.isEmpty { parts.append("\(state.lines.count) lines") }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    private func header(_ state: LogTailerState) -> some View {
        HStack(spacing: 8) {
            DSStatusDot(statusTone(state))
            VStack(alignment: .leading, spacing: 2) {
                Text(NexusFormat.logFileName(state.buildId))
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.primary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                if let detail = headerDetail(state) {
                    Text(detail)
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.secondary)
                        .monospacedDigit()
                        .lineLimit(1)
                }
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
                Button {
                    togglePause(state)
                } label: {
                    Image(systemName: userPaused ? "play" : "pause")
                        .foregroundStyle(userPaused ? DS.Palette.amber : DS.Text.muted)
                }
                .buttonStyle(.dsIcon)
                .accessibilityLabel(userPaused ? "Resume" : "Pause")
                Button {
                    UIPasteboard.general.string = state.lines.map(\.raw).joined(separator: "\n")
                    toast = Toast("Copied", style: .success)
                } label: {
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
        .frame(minHeight: DS.rowMinHeight)
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
                ProgressView().controlSize(.small)
                Text("Loading logs…")
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.muted)
            }
            .padding(.horizontal, DS.rowPadding)
            .padding(.vertical, 24)
        } else if state.lines.isEmpty {
            Text(emptyMessage(state))
                .font(DS.Font.body)
                .foregroundStyle(DS.Text.muted)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, DS.rowPadding)
                .padding(.vertical, 24)
        } else {
            logList(filtered(state))
        }
    }

    private func emptyMessage(_ state: LogTailerState) -> String {
        let s = state.finalStatus ?? ""
        if state.error != nil { return "Can't reach the logs endpoint. Retrying…" }
        if s == "none" || s.isEmpty { return "No builds yet. Start the Nexus engine to begin a build" }
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
                        HStack(alignment: .firstTextBaseline, spacing: 0) {
                            if let ts = line.ts {
                                Text(NexusFormat.logStamp(ts))
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
}

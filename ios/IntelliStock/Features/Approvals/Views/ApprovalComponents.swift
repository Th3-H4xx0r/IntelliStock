import SwiftUI

// Pieces shared by the approvals list and detail. Everything the agent or
// the server wrote (a summary, a path, parameters, a result) is drawn
// verbatim: never as Markdown, never as links. `EntityRow`, `StatusDot`
// and `DSKeyValueRow` take a `String`, which `Text` draws as is.

/// One request in the approvals list: the summary over "METHOD /path",
/// then a countdown while it waits for the operator, or its status.
struct ApprovalRow: View {
    let approval: McpApproval
    let now: Date

    var body: some View {
        let display = approvalDisplayStatus(approval, now: now)
        EntityRow(approval.summary, subtitle: "\(approval.method) \(approval.path)") {
            if display == "pending", let expiresAt = approval.expiresAt {
                Text(verbatim: expiryLabel(remaining: expiresAt.timeIntervalSince(now)))
                    .font(DS.Font.caption)
                    .monospacedDigit()
                    .foregroundStyle(DS.Text.label)
                    .lineLimit(1)
            } else {
                StatusDot(approvalStatusLabel(display), tone: approvalStatusTone(display))
            }
        }
    }
}

/// What a request puts at stake, as a dot and a word: live money and
/// can't-undo in loss red, an unknown or protected account in amber.
struct MoneyStateLine: View {
    let state: String

    var body: some View {
        StatusDot(moneyStateLabel(state), tone: moneyStateTone(state))
    }
}

/// Query, body or result JSON on a card: monospaced, selectable, scrolling
/// sideways. A long text (a result can be 256 KB) scrolls inside the card,
/// where only the lines on screen are laid out, so all of it is reachable
/// and the page stays smooth.
struct ApprovalJSONBlock: View {
    let text: String

    /// A line wider than this is shown as several, so no single `Text` gets
    /// too wide to draw.
    nonisolated static let maxColumns = 400
    /// Up to this many lines the card grows to fit them; past it, it is
    /// `scrollingHeight` tall and scrolls.
    nonisolated static let inlineLines = 60
    static let scrollingHeight: CGFloat = 480

    var body: some View {
        let lines = Self.lines(text)
        Group {
            if lines.count <= Self.inlineLines {
                ScrollView(.horizontal) {
                    VStack(alignment: .leading, spacing: 0) {
                        lineViews(lines)
                    }
                    .padding(16)
                }
            } else {
                ScrollView([.horizontal, .vertical]) {
                    LazyVStack(alignment: .leading, spacing: 0) {
                        lineViews(lines)
                    }
                    .padding(16)
                }
                .defaultScrollAnchor(.topLeading)
                .frame(height: Self.scrollingHeight)
            }
        }
        .scrollIndicators(.visible)
        .textSelection(.enabled)
        .frame(maxWidth: .infinity, alignment: .leading)
        .dsCardSurface()
    }

    private func lineViews(_ lines: [String]) -> some View {
        ForEach(lines.indices, id: \.self) { i in
            Text(verbatim: lines[i].isEmpty ? " " : lines[i])
                .font(DS.Font.mono)
                .foregroundStyle(DS.Text.soft)
                .fixedSize()
        }
    }

    /// `text` split at newlines (blank lines kept), each line longer than
    /// `maxColumns` cut into pieces of at most that many characters.
    nonisolated static func lines(_ text: String) -> [String] {
        var out: [String] = []
        for line in text.split(separator: "\n", omittingEmptySubsequences: false) {
            if line.count <= maxColumns {
                out.append(String(line))
                continue
            }
            var rest = line[...]
            while !rest.isEmpty {
                out.append(String(rest.prefix(maxColumns)))
                rest = rest.dropFirst(maxColumns)
            }
        }
        return out
    }
}

import Charts
import SwiftUI

// MARK: - Message bubble (`chat_message.dart`)

/// One message, as the web dock draws it: the user's on a raised bubble at
/// the trailing edge (at most 85% wide), the assistant's as plain Markdown
/// across the width with its rich blocks and tool names, a tool's result in
/// small monospace on a dark inset. Pending confirmations are not rendered
/// here — they are tool-call cards.
struct ChatMessageBubble: View {
    let message: ChatMessage

    private var isUser: Bool { message.role == "user" }
    private var isTool: Bool { message.role == "tool" }
    private var isAssistant: Bool { message.role == "assistant" }

    var body: some View {
        Group {
            if isUser {
                column.containerRelativeFrame(.horizontal, alignment: .trailing) { width, _ in
                    width * 0.85
                }
            } else {
                column
            }
        }
        .frame(maxWidth: .infinity, alignment: isUser ? .trailing : .leading)
    }

    private var column: some View {
        let visibleBlocks = ChatNavigate.visibleBlocks(message)
        let content = message.content ?? ""
        let showBubble = !content.isEmpty || (visibleBlocks.isEmpty && message.toolCalls.isEmpty)
        return VStack(alignment: isUser ? .trailing : .leading, spacing: 8) {
            if isAssistant, !message.toolCalls.isEmpty {
                ChatFlowLayout(spacing: 12, runSpacing: 4) {
                    ForEach(Array(message.toolCalls.enumerated()), id: \.offset) { _, call in
                        ChatToolChip(name: call.name)
                    }
                }
                .fixedSize(horizontal: false, vertical: true)
            }

            if showBubble {
                bubble(content)
            }

            ForEach(Array(visibleBlocks.enumerated()), id: \.offset) { _, block in
                ChatRichBlock(block: block)
            }

            if let createdAt = message.createdAt, !isTool {
                Text(createdAt, format: .dateTime.hour(.twoDigits(amPM: .abbreviated)).minute(.twoDigits))
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.faint)
                    .monospacedDigit()
                    .padding(.top, -4)
            }
        }
    }

    @ViewBuilder
    private func bubble(_ content: String) -> some View {
        if isTool {
            Text(ChatBlockFormat.toolText(name: message.name, content: content))
                .font(DS.Font.mono)
                .foregroundStyle(DS.Text.muted)
                .lineLimit(2)
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .background(DS.Surface.page.opacity(0.6), in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
                .overlay {
                    RoundedRectangle(cornerRadius: DS.Radius.control, style: .continuous)
                        .strokeBorder(DS.Line.divider, lineWidth: DS.Line.width)
                }
                .textSelection(.enabled)
        } else if isAssistant {
            MarkdownText(content)
                .font(DS.Font.body)
                .foregroundStyle(DS.Text.soft)
                .lineSpacing(3)
                .textSelection(.enabled)
        } else {
            Text(content)
                .font(DS.Font.body)
                .foregroundStyle(DS.Text.primary)
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .background(DS.Surface.raised, in: .rect(cornerRadius: DS.Radius.card, style: .continuous))
                .overlay {
                    RoundedRectangle(cornerRadius: DS.Radius.card, style: .continuous)
                        .strokeBorder(DS.Line.card, lineWidth: DS.Line.width)
                }
                .textSelection(.enabled)
        }
    }
}

/// A tool the assistant called — `_ToolChip`, as the web's: a muted wrench
/// and the name in 12 pt zinc-500, no chip behind it.
private struct ChatToolChip: View {
    let name: String

    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: Symbol.named("build"))
                .font(.system(size: 12))
                .accessibilityHidden(true)
            Text(name)
                .lineLimit(1)
        }
        .font(DS.Font.caption)
        .foregroundStyle(DS.Text.muted)
    }
}

// MARK: - Rich blocks (`chat_rich_block.dart`)

/// A rich block emitted by the assistant: markdown, table, chart, stat or
/// navigate; anything else is a quiet note.
struct ChatRichBlock: View {
    let block: JSONObject

    var body: some View {
        let type = (block["type"] ?? .null).or("").dartDescription
        switch type {
        case "markdown":
            MarkdownText((block["content"] ?? .null).or("").dartDescription)
                .font(DS.Font.body)
                .foregroundStyle(DS.Text.soft)
                .textSelection(.enabled)
        case "table":
            ChatTableBlock(block: block)
        case "chart", "portfolio", "price":
            ChatChartBlock(block: block)
        case "stat":
            ChatStatBlock(block: block)
        case "navigate":
            ChatNavigateBlock(route: (block["route"] ?? .null).or("").dartDescription)
        default:
            Text("Unsupported block: \(type)")
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
        }
    }
}

/// The shared container of table, chart and stat blocks (the web's
/// `rounded-xl border bg-background-dark/60`). A stat hugs its content.
private struct ChatBlockCard<Content: View>: View {
    var hugs = false
    @ViewBuilder let content: Content

    var body: some View {
        content
            .frame(maxWidth: hugs ? nil : .infinity, alignment: .leading)
            .background(DS.Surface.page.opacity(0.6), in: .rect(cornerRadius: DS.Radius.card, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: DS.Radius.card, style: .continuous)
                    .strokeBorder(DS.Line.card, lineWidth: DS.Line.width)
            }
    }
}

private struct ChatTableBlock: View {
    let block: JSONObject

    var body: some View {
        let title = block["title"]?.string
        let headers = (block["headers"] ?? .null).objectElements
        let rows = (block["rows"] ?? .null).objectElements
        ChatBlockCard {
            VStack(alignment: .leading, spacing: 0) {
                if let title {
                    Text(title)
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                        .padding(.horizontal, 16)
                        .padding(.top, 12)
                }
                // Across the card when it fits (the web's full-width
                // table); scrolls sideways when it does not.
                ViewThatFits(in: .horizontal) {
                    grid(headers, rows, stretch: true)
                    ScrollView(.horizontal, showsIndicators: false) {
                        grid(headers, rows, stretch: false)
                    }
                }
            }
        }
    }

    private func grid(_ headers: [JSON], _ rows: [JSON], stretch: Bool) -> some View {
        Grid(alignment: .leading, horizontalSpacing: 24, verticalSpacing: 0) {
            GridRow {
                ForEach(Array(headers.enumerated()), id: \.offset) { _, h in
                    Text(h["label"].or(h["key"]).or("").dartDescription)
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                        .frame(maxWidth: stretch ? .infinity : nil, minHeight: 36, alignment: .leading)
                }
            }
            DSDivider()
                .gridCellUnsizedAxes(.horizontal)
            ForEach(Array(rows.enumerated()), id: \.offset) { index, row in
                if index > 0 {
                    DSDivider(row: true)
                        .gridCellUnsizedAxes(.horizontal)
                }
                GridRow {
                    ForEach(Array(headers.enumerated()), id: \.offset) { _, h in
                        let key = h["key"].or(h["label"]).dartDescription
                        Text(ChatBlockFormat.cell(row[key]))
                            .font(DS.Font.body)
                            .foregroundStyle(DS.Text.soft)
                            .monospacedDigit()
                            .padding(.vertical, 10)
                            .frame(maxWidth: stretch ? .infinity : nil, alignment: .leading)
                    }
                }
            }
        }
        .padding(.horizontal, 16)
    }
}

private struct ChatChartBlock: View {
    let block: JSONObject

    /// The web's series steps: off-white, then the zinc ramp.
    private static let palette: [Color] = [DS.Palette.accent] + DS.Chart.series + [DS.Text.secondary]

    var body: some View {
        let series = ChatChartSeries.build(block)
        if series.isEmpty {
            Text("No chart data")
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
        } else {
            ChatBlockCard {
                VStack(alignment: .leading, spacing: 8) {
                    if let title = block["title"]?.string {
                        Text(title)
                            .font(DS.Font.caption)
                            .foregroundStyle(DS.Text.muted)
                            .lineLimit(1)
                    }
                    Chart {
                        ForEach(series) { s in
                            ForEach(Array(s.points.enumerated()), id: \.offset) { _, p in
                                switch s.kind {
                                case .bar:
                                    BarMark(x: .value("x", p.x), y: .value("y", p.y))
                                        .foregroundStyle(color(s))
                                        .position(by: .value("Series", s.id))
                                case .line:
                                    LineMark(x: .value("x", p.x), y: .value("y", p.y), series: .value("Series", s.id))
                                        .foregroundStyle(color(s))
                                        .lineStyle(StrokeStyle(lineWidth: 1.75))
                                }
                            }
                        }
                    }
                    .chartXAxis {
                        AxisMarks { _ in
                            AxisValueLabel()
                                .font(DS.Font.micro)
                                .foregroundStyle(DS.Text.muted)
                        }
                    }
                    .chartYAxis {
                        AxisMarks { _ in
                            AxisGridLine(stroke: StrokeStyle(lineWidth: 1))
                                .foregroundStyle(DS.Chart.grid)
                            AxisValueLabel()
                                .font(DS.Font.micro)
                                .foregroundStyle(DS.Text.muted)
                        }
                    }
                    // The plot draws in left to right as the block appears;
                    // the axes stay put.
                    .chartPlotStyle { $0.chartDrawIn() }
                    .frame(height: 200)
                }
                .padding(16)
            }
        }
    }

    private func color(_ s: ChatChartSeries) -> Color {
        Self.palette[s.id % Self.palette.count]
    }
}

private struct ChatStatBlock: View {
    let block: JSONObject

    var body: some View {
        let label = (block["label"] ?? .null).or("").dartDescription
        let value = (block["value"] ?? .null).or("").dartDescription
        let detail = block["detail"]?.string
        let trend = block["trend"]?.string
        ChatBlockCard(hugs: true) {
            VStack(alignment: .leading, spacing: 4) {
                Text(label)
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                HStack(spacing: 8) {
                    Text(value)
                        .font(DS.Font.figure)
                        .foregroundStyle(DS.Text.primary)
                        .monospacedDigit()
                    if trend == "up" {
                        Image(systemName: Symbol.named("trending_up"))
                            .font(.system(size: 16))
                            .foregroundStyle(DS.Palette.up)
                    } else if trend == "down" {
                        Image(systemName: Symbol.named("trending_down"))
                            .font(.system(size: 16))
                            .foregroundStyle(DS.Palette.down)
                    }
                }
                if let detail {
                    Text(detail)
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
        }
        .accessibilityElement(children: .combine)
    }
}

private struct ChatNavigateBlock: View {
    let route: String

    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: Symbol.named("north_east"))
                .font(.system(size: 12))
                .accessibilityHidden(true)
            Text("Navigated to")
            Text(verbatim: route)
                .font(DS.Font.mono)
                .foregroundStyle(DS.Text.label)
        }
        .font(DS.Font.caption)
        .foregroundStyle(DS.Text.secondary)
    }
}

// MARK: - Tool-call card (`chat_tool_call.dart`)

/// A tool call waiting for approval, as the web's card: the tier as a dot
/// and a word (safe grey, write amber, destructive loss), the tool and what
/// it does, its arguments, `CONFIRM` typed for a destructive one, then
/// Decline and Approve & run.
struct ChatToolCallCard: View {
    let message: ChatMessage
    let busy: Bool
    let onApprove: () -> Void
    let onDecline: () -> Void

    @State private var argsExpanded = false
    @State private var typed = ""
    @FocusState private var typedFocused: Bool

    var body: some View {
        let tool = message.pendingTool
        let safety = tool?.safety ?? "write"
        let tier = Self.tier(safety)
        let requiresTyped = safety == "destructive"
        let typedOk = !requiresTyped || typed == "CONFIRM"
        let args = tool.map { ChatBlockFormat.prettyArguments($0.arguments) } ?? ""

        VStack(alignment: .leading, spacing: 12) {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 6) {
                    DSStatusDot(tier.tone)
                    Text(tier.label)
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.secondary)
                }
                Text(tool?.name ?? "tool")
                    .font(DS.Font.bodyMedium)
                    .foregroundStyle(DS.Text.primary)
                if let description = tool?.description {
                    Text(description)
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.secondary)
                        .lineSpacing(3)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }

            if !args.isEmpty, args != "{}" {
                VStack(alignment: .leading, spacing: 6) {
                    Button {
                        withAnimation(DSMotion.standard) { argsExpanded.toggle() }
                    } label: {
                        HStack(spacing: 4) {
                            Image(systemName: "chevron.right")
                                .font(.system(size: 10, weight: .medium))
                                .rotationEffect(.degrees(argsExpanded ? 90 : 0))
                            Text("Arguments")
                        }
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .accessibilityAddTraits(argsExpanded ? .isSelected : [])
                    if argsExpanded {
                        ScrollView(.horizontal, showsIndicators: false) {
                            Text(verbatim: args)
                                .font(DS.Font.mono)
                                .foregroundStyle(DS.Text.label)
                                .padding(8)
                        }
                        .background(DS.Surface.page, in: .rect(cornerRadius: DS.Radius.small, style: .continuous))
                        .overlay {
                            RoundedRectangle(cornerRadius: DS.Radius.small, style: .continuous)
                                .strokeBorder(DS.Line.divider, lineWidth: DS.Line.width)
                        }
                    }
                }
            }

            if requiresTyped {
                VStack(alignment: .leading, spacing: 6) {
                    Text("Type \(Text("CONFIRM").foregroundStyle(DS.Text.primary)) to proceed")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.secondary)
                    TextField(text: $typed, prompt: Text("CONFIRM").foregroundStyle(DS.Text.faint)) { Text("CONFIRM") }
                        .font(DS.Font.mono)
                        .textInputAutocapitalization(.characters)
                        .autocorrectionDisabled()
                        .focused($typedFocused)
                        .dsInput(focused: typedFocused)
                }
            }

            HStack(spacing: 8) {
                Spacer()
                Button("Decline", action: onDecline)
                    .buttonStyle(.dsGhost)
                    .disabled(busy)
                if safety == "destructive" {
                    Button(role: .destructive, action: onApprove) { approveLabel }
                        .buttonStyle(.dsSecondary)
                        .disabled(busy || !typedOk)
                } else {
                    Button(action: onApprove) { approveLabel }
                        .buttonStyle(.dsPrimary)
                        .disabled(busy || !typedOk)
                }
            }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 14)
        .frame(maxWidth: 360, alignment: .leading)
        .dsCardSurface(fill: DS.Surface.raised, border: DS.Line.input)
    }

    private var approveLabel: some View {
        HStack(spacing: 6) {
            if busy {
                ProgressView()
                    .controlSize(.small)
            }
            Text("Approve & run")
        }
    }

    struct Tier {
        let tone: DSStatusTone
        let label: String
    }

    static func tier(_ safety: String) -> Tier {
        switch safety {
        case "safe":
            Tier(tone: .off, label: "Run tool")
        case "destructive":
            Tier(tone: .error, label: "Destructive — confirm carefully")
        default:
            Tier(tone: .warn, label: "This will change your workspace")
        }
    }
}

// MARK: - Composer (`chat_composer.dart`)

/// The input, as the web dock's: a field on the page colour with an 8%
/// hairline (25% while focused) growing 1–6 lines, and a 32 pt off-white
/// send button inside it (raised and dim while it cannot send), over a
/// surface bar with a hairline on top.
struct ChatComposer: View {
    let busy: Bool
    let disabled: Bool
    let placeholder: String
    let onSend: (String) -> Void

    @State private var text = ""
    @FocusState private var focused: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var hasText: Bool { !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }

    var body: some View {
        let canSend = !(busy || disabled || !hasText)
        HStack(alignment: .bottom, spacing: 8) {
            TextField(text: $text, prompt: Text(placeholder).foregroundStyle(DS.Text.faint), axis: .vertical) {
                Text(placeholder)
            }
            .font(DS.Font.body)
            .foregroundStyle(DS.Text.primary)
            .tint(DS.Palette.accent)
            .lineLimit(1...6)
            .focused($focused)
            .disabled(disabled || busy)
            .padding(.vertical, 13)
            .padding(.leading, 12)
            Button(action: send) {
                Group {
                    if busy {
                        ProgressView()
                            .controlSize(.small)
                    } else {
                        Image(systemName: "arrow.up")
                            .font(.system(size: 15, weight: .medium))
                            .foregroundStyle(canSend ? DS.Palette.onAccent : DS.Text.faint)
                    }
                }
                .frame(width: 32, height: 32)
                .background(canSend ? DS.Palette.accent : DS.Surface.raised, in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
                .frame(width: 44, height: 44)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .disabled(busy || disabled || !hasText)
            .animation(DSMotion.standard(reduceMotion: reduceMotion), value: canSend)
            .accessibilityLabel("Send")
        }
        .padding(.trailing, 2)
        .background(DS.Surface.page, in: .rect(cornerRadius: DS.Radius.card, style: .continuous))
        .overlay {
            RoundedRectangle(cornerRadius: DS.Radius.card, style: .continuous)
                .strokeBorder(focused ? DS.Line.focus : DS.Line.input, lineWidth: DS.Line.width)
        }
        .animation(DSMotion.standard(reduceMotion: reduceMotion), value: focused)
        .padding(.horizontal, 12)
        .padding(.top, 12)
        .padding(.bottom, 8)
        .background {
            DS.Surface.card
                .overlay(alignment: .top) { DSDivider() }
                .ignoresSafeArea(edges: .bottom)
        }
    }

    private func send() {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty || busy || disabled { return }
        onSend(trimmed)
        text = ""
    }
}

// MARK: - Model picker (`chat_model_picker.dart`)

/// The first-run model picker, shown while the conversation has no model,
/// in the web's words: what to pick and why, the models as selectable rows
/// on one card, then Start chatting.
struct ChatModelPicker: View {
    let model: ChatbotModel
    let onConfirm: (ChatModel) -> Void

    @State private var selectedId: String?

    var body: some View {
        let st = model.state
        let models = st.models
        ScrollView {
            VStack(spacing: 0) {
                Text("Pick a model for this conversation")
                    .font(DS.Font.sectionTitle)
                    .foregroundStyle(DS.Text.primary)
                    .multilineTextAlignment(.center)
                    .accessibilityAddTraits(.isHeader)
                Text("I'll use this model for every reply in this conversation. You can swap it later in settings.")
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.muted)
                    .lineSpacing(3)
                    .multilineTextAlignment(.center)
                    .frame(maxWidth: 320)
                    .padding(.top, 8)

                Group {
                    if !st.modelsLoaded, models.isEmpty, let error = st.error {
                        // A failed list used to spin "Loading models…" forever.
                        ErrorRow(message: error) { Task { await model.retryModels() } }
                    } else if !st.modelsLoaded, models.isEmpty {
                        LoadingState(label: "Loading models…")
                    } else if st.modelsLoaded, models.isEmpty {
                        Text("No models configured yet. Add one on the Models page.")
                            .font(DS.Font.body)
                            .foregroundStyle(DS.Text.secondary)
                            .multilineTextAlignment(.center)
                            .padding(.horizontal, 16)
                            .padding(.vertical, 12)
                            .frame(maxWidth: .infinity)
                            .background(DS.Surface.page.opacity(0.6), in: .rect(cornerRadius: DS.Radius.card, style: .continuous))
                            .overlay {
                                RoundedRectangle(cornerRadius: DS.Radius.card, style: .continuous)
                                    .strokeBorder(DS.Line.card, lineWidth: DS.Line.width)
                            }
                    } else {
                        DSField("Model") {
                            Card(padding: 0) {
                                DSRows {
                                    ForEach(models) { m in
                                        ChatModelRow(model: m, selected: selectedId == m.id) { selectedId = m.id }
                                    }
                                }
                            }
                        }
                    }
                }
                .padding(.top, 20)

                if !models.isEmpty {
                    Button {
                        let chosen = models.first { $0.id == selectedId } ?? models[0]
                        onConfirm(chosen)
                    } label: {
                        HStack(spacing: 6) {
                            if st.busy {
                                ProgressView()
                                    .controlSize(.small)
                                    .tint(DS.Palette.onAccent)
                            }
                            Text("Start chatting")
                        }
                    }
                    .buttonStyle(.dsPrimary(size: .large, fullWidth: true))
                    .disabled(selectedId == nil || st.busy)
                    .padding(.top, 20)
                }
            }
            .padding(20)
        }
        .task { await model.loadModels() }
    }
}

/// A selectable model row: the name over "provider · model", a check and
/// the raised fill when it is the one chosen.
struct ChatModelRow: View {
    let model: ChatModel
    let selected: Bool
    var disabled = false
    let onTap: () -> Void

    var body: some View {
        let sub = ChatBlockFormat.modelSubtitle(provider: model.provider, model: model.model)
        Button(action: onTap) {
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(model.name)
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.primary)
                    if !model.provider.isEmpty || !model.model.isEmpty {
                        Text(sub)
                            .font(DS.Font.caption)
                            .foregroundStyle(DS.Text.muted)
                    }
                }
                Spacer(minLength: 8)
                if selected {
                    Image(systemName: "checkmark")
                        .font(.system(size: 14, weight: .medium))
                        .foregroundStyle(DS.Text.primary)
                        .accessibilityHidden(true)
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 8)
            .frame(maxWidth: .infinity, minHeight: 56, alignment: .leading)
            .background(selected ? DS.Surface.raised : Color.clear)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(disabled)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }
}

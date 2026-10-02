import Charts
import SwiftUI

// MARK: - Message bubble (`chat_message.dart`)

/// One message: user (trailing, accent), assistant (leading, Markdown, with
/// rich blocks and tool chips) or tool (leading, monospace result).
/// Pending confirmations are not rendered here — they are tool-call cards.
struct ChatMessageBubble: View {
    let message: ChatMessage

    private var isUser: Bool { message.role == "user" }
    private var isTool: Bool { message.role == "tool" }
    private var isAssistant: Bool { message.role == "assistant" }

    var body: some View {
        let visibleBlocks = ChatNavigate.visibleBlocks(message)
        let content = message.content ?? ""
        let showBubble = !content.isEmpty || (visibleBlocks.isEmpty && message.toolCalls.isEmpty)

        VStack(alignment: isUser ? .trailing : .leading, spacing: 0) {
            if isAssistant, !message.toolCalls.isEmpty {
                ChatFlowLayout(spacing: 6) {
                    ForEach(Array(message.toolCalls.enumerated()), id: \.offset) { _, call in
                        ChatToolChip(name: call.name)
                    }
                }
                .fixedSize(horizontal: false, vertical: true)
                .padding(.bottom, 4)
            }

            if showBubble {
                bubble(content)
            }

            ForEach(Array(visibleBlocks.enumerated()), id: \.offset) { _, block in
                ChatRichBlock(block: block)
                    .padding(.top, 6)
            }

            if let createdAt = message.createdAt, !isTool {
                Text(createdAt, format: .dateTime.hour(.twoDigits(amPM: .omitted)).minute(.twoDigits))
                    .font(.caption2)
                    .foregroundStyle(.tertiary)
                    .padding(.top, 3)
                    .padding(.horizontal, 2)
            }
        }
        .containerRelativeFrame(.horizontal, alignment: isUser ? .trailing : .leading) { width, _ in
            width * 0.82
        }
        .frame(maxWidth: .infinity, alignment: isUser ? .trailing : .leading)
    }

    @ViewBuilder
    private func bubble(_ content: String) -> some View {
        let shape = UnevenRoundedRectangle(
            topLeadingRadius: isUser ? 18 : 4,
            bottomLeadingRadius: 18,
            bottomTrailingRadius: isUser ? 4 : 18,
            topTrailingRadius: 18,
            style: .continuous
        )
        Group {
            if isTool {
                Text(ChatBlockFormat.toolText(name: message.name, content: content))
                    .font(.system(.caption, design: .monospaced))
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
            } else if isAssistant {
                MarkdownText(content)
                    .font(.body)
                    .foregroundStyle(.primary)
            } else {
                Text(content)
                    .font(.body.weight(isUser ? .medium : .regular))
                    .foregroundStyle(isUser ? DS.Palette.onAccent : .primary)
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .background(isUser ? AnyShapeStyle(DS.Palette.accent) : AnyShapeStyle(isTool ? DS.Surface.inset : DS.Surface.panel), in: shape)
        .textSelection(.enabled)
    }
}

/// A tool-call chip on an assistant message — `_ToolChip`.
private struct ChatToolChip: View {
    let name: String

    var body: some View {
        Label(name.uppercased(), systemImage: Symbol.named("build"))
            .font(.caption2.weight(.medium))
            .tracking(0.6)
            .foregroundStyle(.tint)
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(DS.Palette.accent.opacity(0.1), in: .rect(cornerRadius: 6, style: .continuous))
    }
}

// MARK: - Rich blocks (`chat_rich_block.dart`)

/// A rich block emitted by the assistant: markdown, table, chart, stat or
/// navigate; anything else falls back to an italic note.
struct ChatRichBlock: View {
    let block: JSONObject

    var body: some View {
        let type = (block["type"] ?? .null).or("").dartDescription
        switch type {
        case "markdown":
            MarkdownText((block["content"] ?? .null).or("").dartDescription)
                .font(.body)
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
            Text("(unsupported block: \(type))")
                .font(.caption.italic())
                .foregroundStyle(.secondary)
        }
    }
}

/// The shared container of table, chart and stat blocks.
private struct ChatBlockCard<Content: View>: View {
    @ViewBuilder let content: Content

    var body: some View {
        content
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(DS.Surface.panel, in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
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
                    Text(title.uppercased())
                        .font(.caption.weight(.bold))
                        .tracking(1.2)
                        .foregroundStyle(.secondary)
                        .padding(.horizontal, 12)
                        .padding(.top, 10)
                        .padding(.bottom, 4)
                }
                ScrollView(.horizontal, showsIndicators: false) {
                    Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 0) {
                        GridRow {
                            ForEach(Array(headers.enumerated()), id: \.offset) { _, h in
                                Text(h["label"].or(h["key"]).or("").dartDescription)
                                    .font(.caption2.weight(.semibold))
                                    .foregroundStyle(.secondary)
                                    .padding(.vertical, 8)
                            }
                        }
                        ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                            Divider().gridCellUnsizedAxes(.horizontal)
                            GridRow {
                                ForEach(Array(headers.enumerated()), id: \.offset) { _, h in
                                    let key = h["key"].or(h["label"]).dartDescription
                                    Text(ChatBlockFormat.cell(row[key]))
                                        .font(.subheadline.monospacedDigit())
                                        .padding(.vertical, 8)
                                }
                            }
                        }
                    }
                    .padding(.horizontal, 12)
                }
            }
        }
    }
}

private struct ChatChartBlock: View {
    let block: JSONObject

    private static let palette: [Color] = [DS.Palette.accent, DS.Palette.success, DS.Palette.info, DS.Palette.warning, DS.Palette.danger]

    var body: some View {
        let series = ChatChartSeries.build(block)
        if series.isEmpty {
            Text("(no chart data)")
                .font(.caption.italic())
                .foregroundStyle(.secondary)
        } else {
            ChatBlockCard {
                VStack(alignment: .leading, spacing: 8) {
                    if let title = block["title"]?.string {
                        Text(title.uppercased())
                            .font(.caption.weight(.bold))
                            .tracking(1.2)
                            .foregroundStyle(.secondary)
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
                                        .lineStyle(StrokeStyle(lineWidth: 2))
                                }
                            }
                        }
                    }
                    .chartXAxis {
                        AxisMarks { _ in
                            AxisValueLabel().font(.caption2)
                        }
                    }
                    .chartYAxis {
                        AxisMarks { _ in
                            AxisGridLine(stroke: StrokeStyle(lineWidth: 0.5, dash: [4, 4]))
                            AxisValueLabel().font(.caption2)
                        }
                    }
                    .frame(height: 200)
                }
                .padding(12)
            }
        }
    }

    private func color(_ s: ChatChartSeries) -> Color {
        // Portfolio lines use the chart line colour (the accent).
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
        ChatBlockCard {
            VStack(alignment: .leading, spacing: 4) {
                Text(label.uppercased())
                    .font(.caption.weight(.bold))
                    .tracking(1.2)
                    .foregroundStyle(.secondary)
                HStack(spacing: 6) {
                    Text(value)
                        .font(.title2.bold().monospacedDigit())
                    if trend == "up" {
                        Image(systemName: Symbol.named("trending_up")).foregroundStyle(DS.Palette.up)
                    } else if trend == "down" {
                        Image(systemName: Symbol.named("trending_down")).foregroundStyle(DS.Palette.down)
                    }
                }
                if let detail {
                    Text(detail)
                        .font(.caption)
                        .foregroundStyle(.secondary)
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
                .font(.caption)
            Text("Navigated to ")
                .font(.caption)
            Text(verbatim: route)
                .font(.system(.caption, design: .monospaced))
        }
        .foregroundStyle(.tint)
    }
}

// MARK: - Tool-call card (`chat_tool_call.dart`)

/// A tool call waiting for approval: safe (accent), write (orange) or
/// destructive (red, typed `CONFIRM`).
struct ChatToolCallCard: View {
    let message: ChatMessage
    let busy: Bool
    let onApprove: () -> Void
    let onDecline: () -> Void

    @State private var argsExpanded = false
    @State private var typed = ""
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        let tool = message.pendingTool
        let safety = tool?.safety ?? "write"
        let tier = Self.tier(safety)
        let ink = DS.Palette.onTint(tier.color, in: colorScheme)
        let requiresTyped = safety == "destructive"
        let typedOk = !requiresTyped || typed == "CONFIRM"
        let args = tool.map { ChatBlockFormat.prettyArguments($0.arguments) } ?? ""

        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .top, spacing: 8) {
                Image(systemName: Symbol.named(tier.icon))
                    .foregroundStyle(tier.color)
                VStack(alignment: .leading, spacing: 2) {
                    Text(tier.label.uppercased())
                        .font(.caption2.weight(.bold))
                        .tracking(1.2)
                        .foregroundStyle(ink.opacity(0.8))
                    Text(tool?.name ?? "tool")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(ink)
                    if let description = tool?.description {
                        Text(description)
                            .font(.subheadline)
                            .foregroundStyle(.primary)
                            .padding(.top, 2)
                    }
                }
            }

            if !args.isEmpty, args != "{}" {
                DisclosureGroup(isExpanded: $argsExpanded) {
                    ScrollView(.horizontal, showsIndicators: false) {
                        Text(verbatim: args)
                            .font(.system(.caption2, design: .monospaced))
                            .foregroundStyle(.secondary)
                            .padding(8)
                    }
                    .background(DS.Surface.inset, in: .rect(cornerRadius: DS.Radius.small, style: .continuous))
                    .padding(.top, 6)
                } label: {
                    Text("Arguments")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .tint(.secondary)
                .padding(.top, 10)
            }

            if requiresTyped {
                Text("Type CONFIRM to proceed")
                    .font(.caption.weight(.semibold))
                    .tracking(0.5)
                    .foregroundStyle(ink)
                    .padding(.top, 12)
                TextField("CONFIRM", text: $typed)
                    .font(.system(.footnote, design: .monospaced))
                    .textInputAutocapitalization(.characters)
                    .autocorrectionDisabled()
                    .padding(10)
                    .background(DS.Surface.inset, in: .rect(cornerRadius: DS.Radius.small, style: .continuous))
                    .padding(.top, 6)
            }

            HStack(spacing: 8) {
                Spacer()
                Button("Decline", action: onDecline)
                    .buttonStyle(.bordered)
                    .tint(.secondary)
                    .disabled(busy)
                Button(action: onApprove) {
                    HStack(spacing: 6) {
                        if busy {
                            ProgressView()
                        } else {
                            Image(systemName: Symbol.named("check"))
                        }
                        Text("Approve & Run")
                    }
                }
                .buttonStyle(.bordered)
                .tint(tier.color)
                .disabled(busy || !typedOk)
            }
            .padding(.top, 12)
        }
        .padding(14)
        .frame(maxWidth: 360, alignment: .leading)
        .background(tier.color.opacity(safety == "destructive" ? 0.12 : 0.08), in: .rect(cornerRadius: 16, style: .continuous))
    }

    struct Tier {
        let color: Color
        let label: String
        let icon: String
    }

    static func tier(_ safety: String) -> Tier {
        switch safety {
        case "safe":
            Tier(color: DS.Palette.accent, label: "Run tool", icon: "play_circle_outline")
        case "destructive":
            Tier(color: DS.Palette.danger, label: "DESTRUCTIVE — confirm carefully", icon: "warning_amber_outlined")
        default:
            Tier(color: DS.Palette.warning, label: "This will change your workspace", icon: "play_circle_outline")
        }
    }
}

// MARK: - Composer (`chat_composer.dart`)

/// The input: a growing field (1–6 lines) and a send button.
struct ChatComposer: View {
    let busy: Bool
    let disabled: Bool
    let placeholder: String
    let onSend: (String) -> Void

    @State private var text = ""

    private var hasText: Bool { !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }

    var body: some View {
        HStack(alignment: .bottom, spacing: 8) {
            TextField(placeholder, text: $text, axis: .vertical)
                .lineLimit(1...6)
                .disabled(disabled || busy)
                .padding(.vertical, 10)
                .padding(.leading, 16)
            Button(action: send) {
                Group {
                    if busy {
                        ProgressView()
                    } else {
                        Image(systemName: "arrow.up.circle.fill")
                            .font(.title)
                            .symbolRenderingMode(.hierarchical)
                    }
                }
                .frame(width: 44, height: 44)
                .contentShape(Rectangle())
            }
            .disabled(busy || disabled || !hasText)
            .padding(.trailing, 2)
            .accessibilityLabel("Send")
        }
        .glassEffect(.regular, in: .rect(cornerRadius: 22, style: .continuous))
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }

    private func send() {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty || busy || disabled { return }
        onSend(trimmed)
        text = ""
    }
}

// MARK: - Model picker (`chat_model_picker.dart`)

/// The first-run model picker: shown while the conversation has no model.
struct ChatModelPicker: View {
    let model: ChatbotModel
    let onConfirm: (ChatModel) -> Void

    @State private var selectedId: String?
    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        let st = model.state
        let models = st.models
        ScrollView {
            VStack(spacing: 0) {
                IconTile(systemImage: Symbol.named("smart_toy"), size: 56)
                Text("WELCOME")
                    .font(.footnote.weight(.bold))
                    .tracking(1.2)
                    .foregroundStyle(.tint)
                    .padding(.top, 16)
                Text("Pick the model that powers me")
                    .font(.headline)
                    .multilineTextAlignment(.center)
                    .padding(.top, 4)
                Text("I'll use this model for every reply in this conversation. You can swap it later in settings.")
                    .font(.subheadline)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(.top, 8)

                Group {
                    if !st.modelsLoaded, models.isEmpty, let error = st.error {
                        // A failed list used to spin "Loading models…" forever.
                        ErrorRow(message: error) { Task { await model.retryModels() } }
                    } else if !st.modelsLoaded, models.isEmpty {
                        LoadingState(label: "Loading models…")
                    } else if st.modelsLoaded, models.isEmpty {
                        Text("No models configured yet. Add one on the Models page.")
                            .font(.subheadline)
                            .foregroundStyle(DS.Palette.onTint(DS.Palette.warning, in: colorScheme))
                            .multilineTextAlignment(.center)
                            .padding(.horizontal, 16)
                            .padding(.vertical, 12)
                            .frame(maxWidth: .infinity)
                            .background(DS.Palette.warning.opacity(0.1), in: .rect(cornerRadius: DS.Radius.control, style: .continuous))
                    } else {
                        VStack(alignment: .leading, spacing: 6) {
                            Text("MODEL")
                                .font(.caption.weight(.bold))
                                .tracking(1.2)
                                .foregroundStyle(.secondary)
                            ForEach(models) { m in
                                ChatModelRow(model: m, selected: selectedId == m.id) { selectedId = m.id }
                            }
                        }
                    }
                }
                .padding(.top, 24)

                if !models.isEmpty {
                    Button {
                        let chosen = models.first { $0.id == selectedId } ?? models[0]
                        onConfirm(chosen)
                    } label: {
                        HStack(spacing: 6) {
                            if st.busy {
                                ProgressView().tint(DS.Palette.onAccent)
                            } else {
                                Image(systemName: Symbol.named("check"))
                            }
                            Text("Start Chatting")
                        }
                        .frame(maxWidth: .infinity)
                    }
                    .dsProminentButton()
                    .controlSize(.large)
                    .disabled(selectedId == nil || st.busy)
                    .padding(.top, 20)
                }
            }
            .padding(20)
        }
        .task { await model.loadModels() }
    }
}

/// A selectable model row (picker and settings sheet).
struct ChatModelRow: View {
    let model: ChatModel
    let selected: Bool
    var disabled = false
    let onTap: () -> Void

    var body: some View {
        let sub = ChatBlockFormat.modelSubtitle(provider: model.provider, model: model.model)
        Button(action: onTap) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(model.name)
                        .font(.body.weight(.semibold))
                        .foregroundStyle(selected ? AnyShapeStyle(.tint) : AnyShapeStyle(.primary))
                    if !model.provider.isEmpty || !model.model.isEmpty {
                        Text(sub)
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                }
                Spacer()
                if selected {
                    Image(systemName: Symbol.named("check_circle"))
                        .foregroundStyle(.tint)
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
            .frame(minHeight: 44)
            .background(
                selected ? AnyShapeStyle(DS.Palette.accent.opacity(0.12)) : AnyShapeStyle(DS.Surface.panel),
                in: .rect(cornerRadius: 10, style: .continuous)
            )
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(disabled)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }
}

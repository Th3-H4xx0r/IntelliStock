import SwiftUI

// The seven onboarding steps — `steps/step_*.dart`, styled as the web's
// `components/onboarding/Step*.vue`: a step line, a 20–24/500 title and a
// zinc-400 paragraph, then the step's content. The resource steps are a
// bordered box of labelled fields (`ui-field-label`, `ui-input`) with the
// primary action at its foot, over what was saved this session.

// MARK: - Welcome

/// `StepWelcome`: the title, what the flow sets up, who is signed in, and the
/// three things it configures as rows in one bordered box.
struct OnboardingWelcomeStep: View {
    @Environment(AppServices.self) private var services

    private static let items = [
        (label: "LLM models", desc: "OpenAI · Gemini · Azure · NVIDIA · DeepSeek"),
        (label: "Stock brokerage", desc: "Alpaca"),
        (label: "Instances", desc: "Live or paper, fully autonomous"),
    ]

    var body: some View {
        OnboardingScroll {
            Text("Set up IntelliStock")
                .font(DS.Font.pageTitle)
                .foregroundStyle(DS.Text.primary)
                .accessibilityAddTraits(.isHeader)

            Text("We'll set up an LLM model, link a brokerage, and create your first autonomous trading instance. It takes about two minutes.")
                .font(DS.Font.body)
                .foregroundStyle(DS.Text.secondary)
                .lineSpacing(4)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 12)

            let username = services.session.username
            Group {
                if username.isEmpty {
                    Text("Glad you're here")
                        .foregroundStyle(DS.Text.muted)
                } else {
                    Text("Signed in as \(Text(username).foregroundStyle(DS.Text.label))")
                        .foregroundStyle(DS.Text.muted)
                }
            }
            .font(DS.Font.body)
            .padding(.top, 8)

            OnboardingBox {
                DSRows {
                    ForEach(Self.items, id: \.label) { item in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(item.label)
                                .font(DS.Font.bodyMedium)
                                .foregroundStyle(DS.Text.primary)
                            Text(item.desc)
                                .font(DS.Font.caption)
                                .foregroundStyle(DS.Text.muted)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 16)
                        .accessibilityElement(children: .combine)
                    }
                }
            }
            .padding(.top, 32)
        }
    }
}

// MARK: - About

/// `StepAbout`: what IntelliStock is, its four ideas in one bordered box,
/// and how a trade flows through the pieces.
struct OnboardingAboutStep: View {
    private static let cards = [
        (title: "LLM-driven decisions",
         desc: "Models reason over fundamentals, technicals, news, and your custom signals to produce buy/sell/hold calls, every cycle, every ticker."),
        (title: "Autonomous instances",
         desc: "Each instance runs a strategy on its own cadence, manages capital, and writes trades to your linked brokerage. Stop and resume them at will."),
        (title: "Backtest before you trade",
         desc: "Replay any strategy against years of bars. Compare runs, debug fills, and only push to live when the numbers look right."),
        (title: "Your keys, your data",
         desc: "API keys are encrypted at rest. Everything runs on your infrastructure. No third-party service ever sees your portfolio."),
    ]

    var body: some View {
        OnboardingScroll {
            OnboardingStepHeading(
                eyebrow: "What is IntelliStock?",
                title: "An LLM trading desk you own",
                text: "IntelliStock turns large language models into a fleet of autonomous trading agents. Every component lives in your stack: data, strategies, broker connections, and execution."
            )

            OnboardingBox {
                DSRows {
                    ForEach(Self.cards, id: \.title) { card in
                        VStack(alignment: .leading, spacing: 6) {
                            Text(card.title)
                                .font(DS.Font.bodyMedium)
                                .foregroundStyle(DS.Text.primary)
                            Text(card.desc)
                                .font(DS.Font.small)
                                .foregroundStyle(DS.Text.secondary)
                                .lineSpacing(3)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(20)
                        .accessibilityElement(children: .combine)
                    }
                }
            }
            .padding(.top, 28)

            OnboardingFlowRow()
                .padding(.top, 16)
        }
    }
}

private enum OnboardingFlow {
    struct Node {
        let icon: String
        let label: String
        let desc: String
    }

    static let nodes = [
        Node(icon: "memory", label: "Model", desc: "Reasons over signals"),
        Node(icon: "tune", label: "Strategy", desc: "Picks tickers + capital"),
        Node(icon: "rocket_launch", label: "Instance", desc: "Runs on a cadence"),
        Node(icon: "account_balance", label: "Brokerage", desc: "Executes orders"),
    ]
}

/// The visual flow (the web's `StepConnect` box): each piece a muted glyph,
/// its name and one line, joined by faint arrows; scrolls sideways on a
/// narrow phone.
private struct OnboardingFlowRow: View {
    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(alignment: .top, spacing: 8) {
                ForEach(Array(OnboardingFlow.nodes.enumerated()), id: \.offset) { index, node in
                    VStack(alignment: .leading, spacing: 0) {
                        Image(systemName: Symbol.named(node.icon))
                            .font(.system(size: 17))
                            .foregroundStyle(DS.Text.muted)
                            .frame(width: 20, height: 20, alignment: .leading)
                            .accessibilityHidden(true)
                        Text(node.label)
                            .font(DS.Font.bodyMedium)
                            .foregroundStyle(DS.Text.primary)
                            .padding(.top, 8)
                        Text(node.desc)
                            .font(DS.Font.caption)
                            .foregroundStyle(DS.Text.muted)
                            .padding(.top, 2)
                    }
                    .frame(width: 92, alignment: .leading)
                    .accessibilityElement(children: .combine)
                    if index < OnboardingFlow.nodes.count - 1 {
                        Image(systemName: Symbol.named("arrow_forward"))
                            .font(.system(size: 14))
                            .foregroundStyle(DS.Text.faint)
                            .frame(height: 20)
                            .accessibilityHidden(true)
                    }
                }
            }
            .padding(16)
        }
        .overlay {
            RoundedRectangle(cornerRadius: DS.Radius.card, style: .continuous)
                .strokeBorder(DS.Line.card, lineWidth: DS.Line.width)
        }
    }
}

// MARK: - Shared step pieces

/// A step's scrolling body: 20 pt in from the edges, 24 above and below.
private struct OnboardingScroll<Content: View>: View {
    @ViewBuilder let content: Content

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                content
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, DS.pageGutter + 4)
            .padding(.vertical, 24)
        }
        .scrollDismissesKeyboard(.interactively)
    }
}

/// A box with the card's hairline and radius but no fill (the web's
/// `rounded-xl border border-white/[0.07]`), clipping its rows.
private struct OnboardingBox<Content: View>: View {
    var padding: CGFloat = 0
    @ViewBuilder let content: Content

    var body: some View {
        content
            .padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .clipShape(.rect(cornerRadius: DS.Radius.card, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: DS.Radius.card, style: .continuous)
                    .strokeBorder(DS.Line.card, lineWidth: DS.Line.width)
            }
    }
}

/// The step line ("Step 1 · Add a model") in 14 pt zinc-500, the title in
/// 20/500, and the paragraph in 14 pt zinc-400.
private struct OnboardingStepHeading: View {
    let eyebrow: String
    let title: String
    let text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(eyebrow)
                .font(DS.Font.body)
                .foregroundStyle(DS.Text.muted)
            Text(title)
                .font(DS.Font.figure)
                .foregroundStyle(DS.Text.primary)
                .padding(.top, 8)
                .accessibilityAddTraits(.isHeader)
            Text(text)
                .font(DS.Font.body)
                .foregroundStyle(DS.Text.secondary)
                .lineSpacing(4)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 12)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// A labelled input — `OnboardingField` — as the web's field: the label
/// above a `ui-input`, an error in loss under it.
private struct OnboardingFieldRow: View {
    let label: String
    let hint: String
    @Binding var text: String
    var obscure = false
    var errorText: String?

    @FocusState private var focused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            DSField(label) {
                Group {
                    if obscure {
                        SecureField(text: $text, prompt: Text(hint).foregroundStyle(DS.Text.faint)) { Text(label) }
                    } else {
                        TextField(text: $text, prompt: Text(hint).foregroundStyle(DS.Text.faint)) { Text(label) }
                    }
                }
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .focused($focused)
                .dsInput(focused: focused)
            }
            if let errorText {
                Text(errorText)
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Palette.loss)
            }
        }
    }
}

/// A menu picker in an input's frame (the web's `select.ui-input`): the
/// chosen value in 14 pt zinc-200 and a muted chevron; tapping lists the
/// options with a check on the chosen one.
private struct OnboardingMenuField<Value: Hashable, Options: View>: View {
    let label: String
    @Binding var selection: Value
    /// What the field shows for the current selection.
    let valueText: String
    @ViewBuilder let options: Options

    var body: some View {
        DSField(label) {
            SelectInput(label: label, selection: $selection, valueText: valueText) { options }
        }
    }
}

/// A success / error box after a submit — `OnboardingMessageBanner`, as the
/// web's: gain or loss text on its own faint tint.
private struct OnboardingMessageRow: View {
    let message: String
    let ok: Bool

    var body: some View {
        let color = ok ? DS.Palette.gain : DS.Palette.loss
        Text(message)
            .font(DS.Font.body)
            .foregroundStyle(color)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
            .background(color.opacity(DS.tintFill), in: .rect(cornerRadius: DS.Radius.card, style: .continuous))
            .overlay {
                RoundedRectangle(cornerRadius: DS.Radius.card, style: .continuous)
                    .strokeBorder(color.opacity(DS.tintBorder), lineWidth: DS.Line.width)
            }
            .accessibilityElement(children: .combine)
    }
}

/// What this session saved, as the web's "Already configured" list: a
/// 12 pt label over rows in a bordered box (name over a mono subline).
private struct OnboardingSavedList: View {
    let rows: [(title: String, subtitle: String)]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Saved this session")
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
            OnboardingBox {
                DSRows {
                    ForEach(Array(rows.enumerated()), id: \.offset) { _, row in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(row.title)
                                .font(DS.Font.body)
                                .foregroundStyle(DS.Text.primary)
                                .lineLimit(1)
                            Text(row.subtitle)
                                .font(DS.Font.mono)
                                .foregroundStyle(DS.Text.muted)
                                .lineLimit(1)
                        }
                        .frame(maxWidth: .infinity, minHeight: 40, alignment: .leading)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 8)
                        .accessibilityElement(children: .combine)
                    }
                }
            }
        }
    }
}

/// The submit action at the foot of a step's box (the web's
/// `ui-btn-primary`, trailing).
private struct OnboardingSubmitButton: View {
    let label: String
    let busyLabel: String
    let busy: Bool
    let enabled: Bool
    let action: () -> Void

    var body: some View {
        HStack {
            Spacer()
            Button(action: action) {
                HStack(spacing: 8) {
                    if busy {
                        ProgressView()
                            .controlSize(.small)
                            .tint(DS.Palette.onAccent)
                    }
                    Text(busy ? busyLabel : label)
                }
            }
            .buttonStyle(.dsPrimary)
            .disabled(busy || !enabled)
        }
    }
}

// MARK: - Add a model

/// `StepAddModel`: the lean inline model form.
struct OnboardingAddModelStep: View {
    let model: OnboardingModel

    @Environment(AppServices.self) private var services
    @State private var form = OnboardingAddModelForm()

    var body: some View {
        @Bindable var form = form
        OnboardingScroll {
            OnboardingStepHeading(
                eyebrow: "Step 1 · Add a model",
                title: "Choose the LLM your strategies call",
                text: "Drop in an API key for any supported LLM provider. Strategies reference models by ID, so you can swap or add more later. Gemini's free tier works well as a starter."
            )

            if !form.saved.isEmpty {
                OnboardingSavedList(rows: form.saved.map { saved in
                    (saved["name"].or("").dartDescription,
                     "\(saved["provider"].dartDescription) · \(saved["model"].dartDescription)")
                })
                .padding(.top, 24)
            }

            OnboardingBox(padding: 20) {
                VStack(alignment: .leading, spacing: 20) {
                    VStack(alignment: .leading, spacing: 20) {
                        OnboardingFieldRow(label: "Model name *", hint: "e.g. gemini-flash-prod", text: $form.name)
                        OnboardingMenuField(label: "Provider", selection: $form.provider, valueText: form.provider) {
                            ForEach(OnboardingAddModelForm.providers, id: \.self) { Text($0).tag($0) }
                        }
                        OnboardingFieldRow(label: "Model ID *", hint: "e.g. gemini-2.5-flash", text: $form.model)
                        OnboardingFieldRow(label: "API key", hint: "sk-…", text: $form.apiKey, obscure: true)
                    }
                    .disabled(form.busy)
                    if let message = form.message {
                        OnboardingMessageRow(message: message, ok: form.messageOk)
                    }
                    OnboardingSubmitButton(label: "Test & save", busyLabel: "Saving…",
                                           busy: form.busy, enabled: form.canSubmit) {
                        Task {
                            await form.testAndSave(client: services.apiClient) {
                                model.updateCounts(models: model.state.modelCount + 1)
                            }
                        }
                    }
                }
            }
            .padding(.top, 24)
        }
    }
}

// MARK: - Link a brokerage

/// `StepLinkBrokerage`: the minimal inline Alpaca form.
struct OnboardingLinkBrokerageStep: View {
    let model: OnboardingModel

    @Environment(AppServices.self) private var services
    @State private var form = OnboardingLinkBrokerageForm()

    var body: some View {
        @Bindable var form = form
        OnboardingScroll {
            OnboardingStepHeading(
                eyebrow: "Step 2 · Link a brokerage",
                title: "Connect the account your orders go to",
                text: "Alpaca is the supported stock brokerage for paper and live trading. Connect it in paper mode first to test without real money."
            )

            if !form.saved.isEmpty {
                OnboardingSavedList(rows: form.saved.map { saved in
                    (saved["account_name"].or("").dartDescription, saved["brokerage_type"].or("").dartDescription)
                })
                .padding(.top, 24)
            }

            OnboardingBox(padding: 20) {
                VStack(alignment: .leading, spacing: 20) {
                    VStack(alignment: .leading, spacing: 20) {
                        OnboardingFieldRow(label: "Account name *", hint: "e.g. alpaca-paper", text: $form.accountName)
                        OnboardingFieldRow(label: "API key *", hint: "PK…", text: $form.apiKey)
                        OnboardingFieldRow(label: "API secret *", hint: "Secret…", text: $form.apiSecret, obscure: true)
                        Toggle("Paper mode", isOn: $form.paper)
                            .font(DS.Font.body)
                            .foregroundStyle(DS.Text.label)
                    }
                    .disabled(form.busy)
                    if let message = form.message {
                        OnboardingMessageRow(message: message, ok: form.messageOk)
                    }
                    OnboardingSubmitButton(label: "Link brokerage", busyLabel: "Saving…",
                                           busy: form.busy, enabled: true) {
                        Task {
                            await form.save(client: services.apiClient) {
                                model.updateCounts(brokerages: model.state.brokerageCount + 1)
                            }
                        }
                    }
                }
            }
            .padding(.top, 24)
        }
    }
}

// MARK: - Create an instance

/// `StepCreateInstance`: id (validated live), name and cadence.
struct OnboardingCreateInstanceStep: View {
    let model: OnboardingModel

    @Environment(AppServices.self) private var services
    @State private var form = OnboardingCreateInstanceForm()

    var body: some View {
        @Bindable var form = form
        OnboardingScroll {
            OnboardingStepHeading(
                eyebrow: "Step 3 · Create your first instance",
                title: "Spin up an autonomous worker",
                text: "An instance is a long-running execution context: it picks tickers, runs your strategy on a cadence, and writes orders through the linked brokerage. Every instance gets its own container and logs."
            )

            if !form.saved.isEmpty {
                OnboardingSavedList(rows: form.saved.map { saved in
                    (saved["name"].or(saved["instance_id"]).or("").dartDescription, saved["instance_id"].or("").dartDescription)
                })
                .padding(.top, 24)
            }

            OnboardingBox(padding: 20) {
                VStack(alignment: .leading, spacing: 20) {
                    VStack(alignment: .leading, spacing: 20) {
                        VStack(alignment: .leading, spacing: 8) {
                            OnboardingFieldRow(label: "Instance ID *", hint: "e.g. my-bot", text: $form.instanceId, errorText: form.idError)
                            Text("Lowercase letters, digits, hyphens and underscores only.")
                                .font(DS.Font.caption)
                                .foregroundStyle(DS.Text.muted)
                        }
                        OnboardingFieldRow(label: "Display name *", hint: "e.g. My First Bot", text: $form.name)
                        DSField("Cadence") {
                            DSSegmentedControl("Cadence", selection: $form.cadence, options: OnboardingCreateInstanceForm.cadences, fullWidth: true, segmentHeight: 32) { $0 }
                        }
                    }
                    .disabled(form.busy)
                    if let message = form.message {
                        OnboardingMessageRow(message: message, ok: form.messageOk)
                    }
                    OnboardingSubmitButton(label: "Create instance", busyLabel: "Creating…",
                                           busy: form.busy, enabled: form.canSubmit) {
                        Task {
                            await form.create(client: services.apiClient) {
                                model.updateCounts(instances: model.state.instanceCount + 1)
                            }
                        }
                    }
                }
            }
            .padding(.top, 24)
        }
    }
}

// MARK: - Connect

/// `StepConnect`: how a trade flows, then link an instance to a brokerage.
struct OnboardingConnectStep: View {
    @Environment(AppServices.self) private var services
    @State private var form = OnboardingConnectForm()

    var body: some View {
        @Bindable var form = form
        OnboardingScroll {
            OnboardingStepHeading(
                eyebrow: "Step 4 · Connect the pieces",
                title: "How a trade actually flows",
                text: "Your instance runs a strategy that asks a model. The model returns a buy/sell call. The instance sends that order through the linked brokerage. Each link is independent, so you can swap any piece without rebuilding the rest."
            )

            OnboardingFlowRow()
                .padding(.top, 28)

            SectionHeader(title: "Link an instance to a brokerage")
                .padding(.top, 32)

            OnboardingBox(padding: 20) {
                VStack(alignment: .leading, spacing: 20) {
                    if form.loading {
                        VStack(alignment: .leading, spacing: 12) {
                            Skeleton.line(width: 120, height: 14)
                            Skeleton(height: 36, radius: DS.Radius.control)
                        }
                        .accessibilityLabel("Loading resources")
                    } else if let loadError = form.loadError {
                        ErrorRow(message: "Could not load: \(loadError)") {
                            Task { await form.load(client: services.apiClient) }
                        }
                    } else if form.instances.isEmpty || form.brokerages.isEmpty {
                        Text("You'll need at least one instance and one brokerage to link them here. Skip for now and do it later from the Instances page.")
                            .font(DS.Font.body)
                            .foregroundStyle(DS.Text.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                    } else {
                        OnboardingMenuField(
                            label: "Instance",
                            selection: $form.selectedInstance,
                            valueText: form.instances.first { OnboardingConnectForm.instanceId($0) == form.selectedInstance }
                                .map(OnboardingConnectForm.instanceLabel) ?? "Pick one…"
                        ) {
                            Text("Pick one…").tag(String?.none)
                            ForEach(Array(form.instances.enumerated()), id: \.offset) { _, instance in
                                Text(OnboardingConnectForm.instanceLabel(instance))
                                    .tag(OnboardingConnectForm.instanceId(instance))
                            }
                        }
                        .disabled(form.busy)
                        OnboardingMenuField(
                            label: "Brokerage",
                            selection: $form.selectedBrokerage,
                            valueText: form.brokerages.first { $0["id"].string == form.selectedBrokerage }
                                .map(OnboardingConnectForm.brokerageLabel) ?? "Pick one…"
                        ) {
                            Text("Pick one…").tag(String?.none)
                            ForEach(Array(form.brokerages.enumerated()), id: \.offset) { _, brokerage in
                                Text(OnboardingConnectForm.brokerageLabel(brokerage))
                                    .tag(brokerage["id"].string)
                            }
                        }
                        .disabled(form.busy)
                        if let message = form.message {
                            OnboardingMessageRow(message: message, ok: form.messageOk)
                        }
                        OnboardingSubmitButton(label: "Link brokerage to instance", busyLabel: "Linking…",
                                               busy: form.busy,
                                               enabled: form.selectedInstance != nil && form.selectedBrokerage != nil) {
                            Task { await form.link(client: services.apiClient) }
                        }
                    }
                }
            }
            .padding(.top, 12)
        }
        .task { await form.load(client: services.apiClient) }
    }
}

// MARK: - Complete

/// `StepComplete`: a gain check in a raised circle, the closing copy and the
/// live counts in one card split by hairlines.
struct OnboardingCompleteStep: View {
    let model: OnboardingModel

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var shown = false

    var body: some View {
        let state = model.state
        OnboardingScroll {
            ZStack {
                Circle()
                    .fill(DS.Surface.raised)
                    .overlay(Circle().strokeBorder(DS.Line.input, lineWidth: DS.Line.width))
                if shown {
                    let mark = Image(systemName: Symbol.named("check"))
                        .font(.system(size: 18, weight: .regular))
                        .foregroundStyle(DS.Palette.gain)
                    if reduceMotion {
                        mark.transition(.opacity)
                    } else {
                        mark.transition(.symbolEffect(.drawOn.byLayer))
                    }
                }
            }
            .frame(width: 40, height: 40)
            .accessibilityHidden(true)

            Text("Setup complete")
                .font(DS.Font.pageTitle)
                .foregroundStyle(DS.Text.primary)
                .padding(.top, 20)
                .accessibilityAddTraits(.isHeader)

            Text("IntelliStock is configured and ready. Open the dashboard to monitor portfolios, queue backtests, or jump straight into your live instance.")
                .font(DS.Font.body)
                .foregroundStyle(DS.Text.secondary)
                .lineSpacing(4)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.top, 12)

            Card(padding: 0) {
                HStack(spacing: 0) {
                    OnboardingCountTile(label: "Models", value: state.modelCount)
                    DSDivider(vertical: true)
                    OnboardingCountTile(label: "Brokerages", value: state.brokerageCount)
                    DSDivider(vertical: true)
                    OnboardingCountTile(label: "Instances", value: state.instanceCount)
                }
                .fixedSize(horizontal: false, vertical: true)
            }
            .padding(.top, 32)
        }
        .onAppear {
            withAnimation(DSMotion.ease(0.4).delay(0.05)) { shown = true }
        }
        .sensoryFeedback(.success, trigger: shown)
    }
}

/// One count in the closing card: a 12 pt label over a 24/500 figure.
private struct OnboardingCountTile: View {
    let label: String
    let value: Int

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(label)
                .font(DS.Font.caption)
                .foregroundStyle(DS.Text.muted)
                .lineLimit(1)
                .dsMinimumScaleFactor(0.8, textStyle: .caption1)
            Text("\(value)")
                .font(DS.Font.figureLarge)
                .foregroundStyle(DS.Text.primary)
                .monospacedDigit()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(.horizontal, 16)
        .padding(.vertical, 12)
        .accessibilityElement(children: .combine)
    }
}

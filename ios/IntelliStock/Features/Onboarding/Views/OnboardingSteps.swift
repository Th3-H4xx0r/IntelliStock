import SwiftUI

// The seven onboarding steps — `steps/step_*.dart`. The presentational steps
// (welcome, about, complete) are scrolling cards; the resource steps are
// inset-grouped forms (`onboarding_form_widgets.dart` fields become rows).

// MARK: - Welcome

/// `StepWelcome`: the logo in a pulsing ring, a word-by-word title, the
/// greeting and three feature tiles.
struct OnboardingWelcomeStep: View {
    @Environment(AppServices.self) private var services
    @State private var pulse = false

    var body: some View {
        ScrollView {
            VStack(spacing: 0) {
                ZStack {
                    Circle()
                        .strokeBorder(DS.Palette.accent.opacity(pulse ? 0.10 : 0.20), lineWidth: 1)
                        .frame(width: pulse ? 104 : 96, height: pulse ? 104 : 96)
                    AppLogoView(size: 72)
                }
                .frame(width: 104, height: 104)
                .padding(.top, 16)
                .onAppear {
                    withAnimation(.easeInOut(duration: 2).repeatForever(autoreverses: true)) { pulse = true }
                }

                OnboardingWordsTitle(text: "Welcome to IntelliStock")
                    .padding(.top, 24)

                Text("Hey \(services.session.username) — let's get your autonomous trading workspace dialled in. We'll set up an LLM model, link a brokerage, and spin up your first instance.")
                    .font(.body)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(.top, 16)

                VStack(spacing: 8) {
                    OnboardingFeatureRow(icon: "memory", label: "LLM Models", desc: "OpenAI · Gemini · Azure · NVIDIA")
                    OnboardingFeatureRow(icon: "account_balance", label: "Brokerages", desc: "Alpaca")
                    OnboardingFeatureRow(icon: "rocket_launch", label: "Instances", desc: "Live or paper, fully autonomous")
                }
                .padding(.top, 28)
                .padding(.bottom, 16)
            }
            .padding(.horizontal, 20)
        }
    }
}

private struct OnboardingFeatureRow: View {
    let icon: String
    let label: String
    let desc: String

    var body: some View {
        Card(padding: EdgeInsets(top: 12, leading: 16, bottom: 12, trailing: 16)) {
            HStack(spacing: 12) {
                IconTile(systemImage: Symbol.named(icon), size: 40)
                VStack(alignment: .leading, spacing: 2) {
                    Text(label).font(.subheadline.weight(.semibold))
                    Text(desc).font(.caption).foregroundStyle(.secondary)
                }
            }
        }
        .accessibilityElement(children: .combine)
    }
}

/// `_AnimatedTitle`: each word fades and rises in, 60 ms apart after 80 ms.
private struct OnboardingWordsTitle: View {
    let text: String
    @State private var shown = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let words = text.split(separator: " ").map(String.init)
        HStack(spacing: 6) {
            ForEach(Array(words.enumerated()), id: \.offset) { index, word in
                Text(word)
                    .font(.title.weight(.heavy))
                    .opacity(shown ? 1 : 0)
                    .offset(y: shown || reduceMotion ? 0 : 10)
                    .animation(.easeOut(duration: 0.4).delay(0.08 + Double(index) * 0.06), value: shown)
            }
        }
        .lineLimit(1)
        .minimumScaleFactor(0.6)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(text)
        .accessibilityAddTraits(.isHeader)
        .onAppear { shown = true }
    }
}

// MARK: - About

/// `StepAbout`: what IntelliStock is, four feature cards and the flow row.
struct OnboardingAboutStep: View {
    private let columns = [GridItem(.flexible(), spacing: 10), GridItem(.flexible(), spacing: 10)]

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                OnboardingStepHeading(
                    eyebrow: "WHAT IS INTELLISTOCK",
                    title: "AI-powered autonomous trading.",
                    text: "IntelliStock runs LLM-driven strategies on a schedule, makes buy/sell decisions, and executes through your linked brokerage — all without manual intervention."
                )

                LazyVGrid(columns: columns, spacing: 10) {
                    OnboardingFeatureCard(icon: "memory", color: DS.Palette.accent, title: "LLM Models",
                                          desc: "Plug in any provider — Gemini, OpenAI, Azure, NVIDIA, Ollama, Bedrock.")
                    OnboardingFeatureCard(icon: "tune", color: DS.Palette.info, title: "Strategies",
                                          desc: "Choose from the catalog or write your own Python strategy.")
                    OnboardingFeatureCard(icon: "rocket_launch", color: DS.Palette.success, title: "Instances",
                                          desc: "Run live or paper on a cadence from 1 min to 1 hour.")
                    OnboardingFeatureCard(icon: "account_balance", color: DS.Palette.warning, title: "Brokerages",
                                          desc: "Alpaca for stock paper and live trading.")
                }
                .padding(.top, 20)

                Card(padding: 14) {
                    HStack(spacing: 4) {
                        ForEach(Array(OnboardingFlow.nodes.enumerated()), id: \.offset) { index, node in
                            VStack(spacing: 4) {
                                Image(systemName: Symbol.named(node.icon))
                                    .font(.title3)
                                    .foregroundStyle(.tint)
                                Text(node.label).font(.caption2)
                            }
                            if index < OnboardingFlow.nodes.count - 1 {
                                Image(systemName: Symbol.named("arrow_forward"))
                                    .font(.caption)
                                    .foregroundStyle(.tertiary)
                            }
                        }
                    }
                    .frame(maxWidth: .infinity)
                }
                .padding(.top, 16)
                .padding(.bottom, 16)
            }
            .padding(.horizontal, 20)
        }
    }
}

private struct OnboardingFeatureCard: View {
    let icon: String
    let color: Color
    let title: String
    let desc: String

    var body: some View {
        Card(padding: 12) {
            VStack(alignment: .leading, spacing: 0) {
                IconTile(systemImage: Symbol.named(icon), color: color, size: 32)
                Text(title)
                    .font(.subheadline.weight(.semibold))
                    .padding(.top, 8)
                Text(desc)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .lineLimit(3)
                    .padding(.top, 4)
            }
            .frame(maxWidth: .infinity, minHeight: 112, alignment: .topLeading)
        }
        .accessibilityElement(children: .combine)
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

// MARK: - Shared step pieces

/// Eyebrow, title and body at the top of a step.
private struct OnboardingStepHeading: View {
    let eyebrow: String
    let title: String
    let text: String

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text(eyebrow)
                .font(.footnote.weight(.bold))
                .tracking(1.2)
                .foregroundStyle(.tint)
            Text(title)
                .font(.title3.bold())
                .accessibilityAddTraits(.isHeader)
            Text(text)
                .font(.body)
                .foregroundStyle(.secondary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// A labelled input row — `OnboardingField`.
private struct OnboardingFieldRow: View {
    let label: String
    let hint: String
    @Binding var text: String
    var obscure = false
    var errorText: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(label)
                .font(.footnote)
                .foregroundStyle(.secondary)
            Group {
                if obscure {
                    SecureField(label, text: $text, prompt: Text(hint))
                } else {
                    TextField(label, text: $text, prompt: Text(hint))
                }
            }
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled()
            if let errorText {
                Text(errorText)
                    .font(.caption)
                    .foregroundStyle(DS.Palette.danger)
            }
        }
        .padding(.vertical, 2)
    }
}

/// A success / error line after a submit — `OnboardingMessageBanner`.
private struct OnboardingMessageRow: View {
    let message: String
    let ok: Bool

    @Environment(\.colorScheme) private var colorScheme

    var body: some View {
        let color = ok ? DS.Palette.success : DS.Palette.danger
        Text(message)
            .font(.footnote)
            .foregroundStyle(DS.Palette.onTint(color, in: colorScheme))
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
            .background(color.opacity(DS.tintFill), in: .rect(cornerRadius: DS.Radius.small, style: .continuous))
    }
}

/// A "saved this session" row (green glyph, title, subtitle).
private struct OnboardingSavedRow: View {
    let icon: String
    let title: String
    let subtitle: String

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: Symbol.named(icon))
                .foregroundStyle(DS.Palette.success)
                .frame(width: 24)
            VStack(alignment: .leading, spacing: 1) {
                Text(title).font(.subheadline.weight(.semibold))
                Text(subtitle).font(.caption).foregroundStyle(.secondary)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

/// The full-width submit action of a step form.
private struct OnboardingSubmitButton: View {
    let label: String
    let busyLabel: String
    let icon: String
    let busy: Bool
    let enabled: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 8) {
                if busy {
                    ProgressView().tint(DS.Palette.onAccent)
                } else {
                    Image(systemName: Symbol.named(icon))
                }
                Text(busy ? busyLabel : label)
            }
            .frame(maxWidth: .infinity)
        }
        .dsProminentButton()
        .controlSize(.large)
        .disabled(busy || !enabled)
        .listRowInsets(EdgeInsets())
        .listRowBackground(Color.clear)
    }
}

private extension View {
    /// A form section whose row is free-standing content on the background.
    func onboardingBareRow() -> some View {
        listRowInsets(EdgeInsets(top: 8, leading: 0, bottom: 8, trailing: 0))
            .listRowBackground(Color.clear)
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
        Form {
            Section {
                OnboardingStepHeading(
                    eyebrow: "STEP 1 · ADD A MODEL",
                    title: "Pick the brain that powers your trades.",
                    text: "Drop in an API key for any supported LLM provider. You can add more later — Gemini's free tier works great as a starter."
                )
                .onboardingBareRow()
            }

            if !form.saved.isEmpty {
                Section("SAVED THIS SESSION") {
                    ForEach(Array(form.saved.enumerated()), id: \.offset) { _, saved in
                        OnboardingSavedRow(
                            icon: "memory",
                            title: saved["name"].or("").dartDescription,
                            subtitle: "\(saved["provider"].dartDescription) · \(saved["model"].dartDescription)"
                        )
                    }
                }
            }

            Section {
                OnboardingFieldRow(label: "Model Name *", hint: "e.g. gemini-flash-prod", text: $form.name)
                Picker("Provider", selection: $form.provider) {
                    ForEach(OnboardingAddModelForm.providers, id: \.self) { Text($0).tag($0) }
                }
                .pickerStyle(.menu)
                OnboardingFieldRow(label: "Model ID *", hint: "e.g. gemini-2.5-flash", text: $form.model)
                OnboardingFieldRow(label: "API Key", hint: "sk-…", text: $form.apiKey, obscure: true)
            }
            .disabled(form.busy)

            Section {
                if let message = form.message {
                    OnboardingMessageRow(message: message, ok: form.messageOk)
                        .onboardingBareRow()
                }
                OnboardingSubmitButton(label: "Test & Save", busyLabel: "Saving…", icon: "bolt",
                                       busy: form.busy, enabled: form.canSubmit) {
                    Task {
                        await form.testAndSave(client: services.apiClient) {
                            model.updateCounts(models: model.state.modelCount + 1)
                        }
                    }
                }
            }
        }
        .scrollContentBackground(.hidden)
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
        Form {
            Section {
                OnboardingStepHeading(
                    eyebrow: "STEP 2 · LINK A BROKERAGE",
                    title: "Connect your trading account.",
                    text: "Connect Alpaca in paper mode first to test without real money."
                )
                .onboardingBareRow()
            }

            if !form.saved.isEmpty {
                Section("SAVED THIS SESSION") {
                    ForEach(Array(form.saved.enumerated()), id: \.offset) { _, saved in
                        OnboardingSavedRow(
                            icon: "account_balance",
                            title: saved["account_name"].or("").dartDescription,
                            subtitle: saved["brokerage_type"].or("").dartDescription
                        )
                    }
                }
            }

            Section {
                OnboardingFieldRow(label: "Account Name *", hint: "e.g. alpaca-paper", text: $form.accountName)
                OnboardingFieldRow(label: "API Key *", hint: "PK…", text: $form.apiKey)
                OnboardingFieldRow(label: "API Secret *", hint: "Secret…", text: $form.apiSecret, obscure: true)
                Toggle("Paper mode", isOn: $form.paper)
            }
            .disabled(form.busy)

            Section {
                if let message = form.message {
                    OnboardingMessageRow(message: message, ok: form.messageOk)
                        .onboardingBareRow()
                }
                OnboardingSubmitButton(label: "Link Brokerage", busyLabel: "Saving…", icon: "link",
                                       busy: form.busy, enabled: true) {
                    Task {
                        await form.save(client: services.apiClient) {
                            model.updateCounts(brokerages: model.state.brokerageCount + 1)
                        }
                    }
                }
            }
        }
        .scrollContentBackground(.hidden)
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
        Form {
            Section {
                OnboardingStepHeading(
                    eyebrow: "STEP 3 · CREATE AN INSTANCE",
                    title: "Spin up your first instance.",
                    text: "An instance is the runtime that runs a strategy on a cadence and places orders through your linked brokerage."
                )
                .onboardingBareRow()
            }

            if !form.saved.isEmpty {
                Section("SAVED THIS SESSION") {
                    ForEach(Array(form.saved.enumerated()), id: \.offset) { _, saved in
                        OnboardingSavedRow(
                            icon: "rocket_launch",
                            title: saved["name"].or(saved["instance_id"]).or("").dartDescription,
                            subtitle: saved["instance_id"].or("").dartDescription
                        )
                    }
                }
            }

            Section {
                OnboardingFieldRow(label: "Instance ID *", hint: "e.g. my-bot", text: $form.instanceId, errorText: form.idError)
                OnboardingFieldRow(label: "Display Name *", hint: "e.g. My First Bot", text: $form.name)
                VStack(alignment: .leading, spacing: 8) {
                    Text("Cadence")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                    Picker("Cadence", selection: $form.cadence) {
                        ForEach(OnboardingCreateInstanceForm.cadences, id: \.self) { Text($0).tag($0) }
                    }
                    .pickerStyle(.segmented)
                }
                .padding(.vertical, 2)
            } footer: {
                Text("Lowercase letters, digits, hyphens and underscores only.")
            }
            .disabled(form.busy)

            Section {
                if let message = form.message {
                    OnboardingMessageRow(message: message, ok: form.messageOk)
                        .onboardingBareRow()
                }
                OnboardingSubmitButton(label: "Create Instance", busyLabel: "Creating…", icon: "rocket_launch",
                                       busy: form.busy, enabled: form.canSubmit) {
                    Task {
                        await form.create(client: services.apiClient) {
                            model.updateCounts(instances: model.state.instanceCount + 1)
                        }
                    }
                }
            }
        }
        .scrollContentBackground(.hidden)
    }
}

// MARK: - Connect

/// `StepConnect`: how a trade flows, then link an instance to a brokerage.
struct OnboardingConnectStep: View {
    @Environment(AppServices.self) private var services
    @State private var form = OnboardingConnectForm()

    var body: some View {
        @Bindable var form = form
        Form {
            Section {
                OnboardingStepHeading(
                    eyebrow: "STEP 4 · CONNECT THE PIECES",
                    title: "How a trade actually flows.",
                    text: "Your instance runs a strategy that asks a model. The model returns a buy/sell call. The instance sends that order through the linked brokerage."
                )
                .onboardingBareRow()
            }

            Section {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(alignment: .top, spacing: 4) {
                        ForEach(Array(OnboardingFlow.nodes.enumerated()), id: \.offset) { index, node in
                            VStack(spacing: 6) {
                                IconTile(systemImage: Symbol.named(node.icon), size: 40)
                                Text(node.label).font(.footnote)
                                Text(node.desc)
                                    .font(.caption2)
                                    .foregroundStyle(.secondary)
                                    .multilineTextAlignment(.center)
                            }
                            .frame(width: 76)
                            .accessibilityElement(children: .combine)
                            if index < OnboardingFlow.nodes.count - 1 {
                                Image(systemName: Symbol.named("arrow_forward"))
                                    .font(.caption)
                                    .foregroundStyle(.tertiary)
                                    .padding(.top, 13)
                            }
                        }
                    }
                    .padding(.vertical, 6)
                }
            }

            Section("LINK AN INSTANCE TO A BROKERAGE") {
                if form.loading {
                    LoadingState(label: "Loading resources…")
                } else if let loadError = form.loadError {
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Could not load: \(loadError)")
                            .font(.footnote)
                            .foregroundStyle(.secondary)
                        Button("Retry") { Task { await form.load(client: services.apiClient) } }
                            .buttonStyle(.borderless)
                    }
                } else if form.instances.isEmpty || form.brokerages.isEmpty {
                    Text("You'll need at least one instance and one brokerage to link them here. Skip for now and do it later from the Instances page.")
                        .font(.body)
                        .foregroundStyle(.secondary)
                } else {
                    Picker("Instance", selection: $form.selectedInstance) {
                        Text("Pick one…").tag(String?.none)
                        ForEach(Array(form.instances.enumerated()), id: \.offset) { _, instance in
                            Text(OnboardingConnectForm.instanceLabel(instance))
                                .tag(OnboardingConnectForm.instanceId(instance))
                        }
                    }
                    .pickerStyle(.menu)
                    .disabled(form.busy)
                    Picker("Brokerage", selection: $form.selectedBrokerage) {
                        Text("Pick one…").tag(String?.none)
                        ForEach(Array(form.brokerages.enumerated()), id: \.offset) { _, brokerage in
                            Text(OnboardingConnectForm.brokerageLabel(brokerage))
                                .tag(brokerage["id"].string)
                        }
                    }
                    .pickerStyle(.menu)
                    .disabled(form.busy)
                }
            }

            if !form.loading, form.loadError == nil, !form.instances.isEmpty, !form.brokerages.isEmpty {
                Section {
                    if let message = form.message {
                        OnboardingMessageRow(message: message, ok: form.messageOk)
                            .onboardingBareRow()
                    }
                    OnboardingSubmitButton(label: "Link Brokerage to Instance", busyLabel: "Linking…", icon: "link",
                                           busy: form.busy,
                                           enabled: form.selectedInstance != nil && form.selectedBrokerage != nil) {
                        Task { await form.link(client: services.apiClient) }
                    }
                }
            }
        }
        .scrollContentBackground(.hidden)
        .task { await form.load(client: services.apiClient) }
    }
}

// MARK: - Complete

/// `StepComplete`: a check, the closing copy and live count tiles.
struct OnboardingCompleteStep: View {
    let model: OnboardingModel

    @State private var shown = false

    var body: some View {
        let state = model.state
        ScrollView {
            VStack(spacing: 0) {
                Image(systemName: Symbol.named("check_circle"))
                    .font(.system(size: 44, weight: .semibold))
                    .foregroundStyle(.tint)
                    .symbolEffect(.bounce, value: shown)
                    .frame(width: 80, height: 80)
                    .background(DS.Palette.accent.opacity(DS.tintFill), in: Circle())
                    .overlay(Circle().strokeBorder(DS.Palette.accent.opacity(0.6), lineWidth: 2))
                    .scaleEffect(shown ? 1 : 0.6)
                    .animation(.spring(response: 0.5, dampingFraction: 0.5), value: shown)
                    .padding(.top, 48)
                    .accessibilityHidden(true)

                Text("You're all set.")
                    .font(.largeTitle.weight(.heavy))
                    .multilineTextAlignment(.center)
                    .padding(.top, 24)
                    .accessibilityAddTraits(.isHeader)

                Text("IntelliStock is configured and ready. Open the dashboard to monitor portfolios, queue backtests, or jump straight into your live instance.")
                    .font(.body)
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.center)
                    .padding(.top, 12)

                HStack(spacing: 10) {
                    OnboardingCountTile(icon: "memory", label: "Models", value: state.modelCount)
                    OnboardingCountTile(icon: "account_balance", label: "Brokerages", value: state.brokerageCount)
                    OnboardingCountTile(icon: "rocket_launch", label: "Instances", value: state.instanceCount)
                }
                .padding(.top, 28)
                .padding(.bottom, 16)
            }
            .padding(.horizontal, 20)
        }
        .onAppear { shown = true }
        .sensoryFeedback(.success, trigger: shown)
    }
}

private struct OnboardingCountTile: View {
    let icon: String
    let label: String
    let value: Int

    var body: some View {
        Card(padding: EdgeInsets(top: 14, leading: 8, bottom: 14, trailing: 8)) {
            VStack(spacing: 6) {
                Image(systemName: Symbol.named(icon))
                    .font(.title3)
                    .foregroundStyle(.tint)
                Text("\(value)")
                    .font(.title2.bold().monospacedDigit())
                Text(label.uppercased())
                    .font(.caption2)
                    .tracking(1)
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
            }
            .frame(maxWidth: .infinity)
        }
        .accessibilityElement(children: .combine)
    }
}

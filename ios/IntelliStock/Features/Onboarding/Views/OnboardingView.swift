import SwiftUI

/// The onboarding wizard — `OnboardingScreen` in `onboarding_screen.dart`:
/// a top bar with Exit, the numbered progress header, the current step (no
/// swipe; it slides in the direction of travel) and a Back / Skip / Next
/// footer.
///
/// Shown bare by the gate, and pushed as `Route.onboarding` from the
/// dashboard; pushed, it hides the navigation and tab bars so it covers the
/// shell as the Dart route did. Native form: the plain grouped background.
struct OnboardingView: View {
    @Environment(AppServices.self) private var services
    @State private var model: OnboardingModel?

    var body: some View {
        Group {
            if let model {
                OnboardingContent(model: model)
            } else {
                DS.Surface.canvas.ignoresSafeArea()
            }
        }
        .toolbar(.hidden, for: .navigationBar)
        .toolbar(.hidden, for: .tabBar)
        .onAppear {
            if model == nil {
                let services = services
                model = OnboardingModel(repository: { services.onboardingRepository }, session: services.session)
            }
        }
    }
}

private struct OnboardingContent: View {
    let model: OnboardingModel

    @Environment(AppServices.self) private var services
    @State private var exitRequest: ConfirmRequest?

    var body: some View {
        let state = model.state
        VStack(spacing: 0) {
            topBar
                .padding(.horizontal, 20)
                .padding(.vertical, 12)

            OnboardingProgressHeader(currentIndex: state.stepIndex)
                .padding(.vertical, 4)

            ZStack {
                step(state.currentStep)
                    .id(state.stepIndex)
                    .transition(slide(state.direction))
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .clipped()
            .padding(.top, 8)

            footer(state)
        }
        .background(DS.Surface.canvas.ignoresSafeArea())
        .confirmAlert($exitRequest)
        .task { await model.loadState() }
    }

    // MARK: Top bar

    private var topBar: some View {
        HStack(spacing: 10) {
            IconTile(systemImage: Symbol.named("auto_awesome"), size: 36)
            VStack(alignment: .leading, spacing: 1) {
                Text("IntelliStock")
                    .font(.subheadline.weight(.semibold))
                Text("Welcome flow")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Button {
                exitRequest = ConfirmRequest(
                    title: "Exit onboarding?",
                    body: "Your saved models, brokerages, and instances stay configured. You can re-run this flow later from the Settings screen.",
                    confirmLabel: "Exit",
                    role: nil,
                    onConfirm: { services.router.go("/dashboard") }
                )
            } label: {
                Label("Exit", systemImage: Symbol.named("close"))
                    .labelStyle(.titleAndIcon)
                    .font(.subheadline)
            }
            // A floating control over the step (there is no navigation bar
            // here), so it takes the system glass, like a bar button.
            .buttonStyle(.glass)
            .tint(.primary)
        }
    }

    // MARK: Steps

    @ViewBuilder
    private func step(_ step: OnboardingStep) -> some View {
        switch step {
        case .welcome: OnboardingWelcomeStep()
        case .about: OnboardingAboutStep()
        case .addModel: OnboardingAddModelStep(model: model)
        case .linkBrokerage: OnboardingLinkBrokerageStep(model: model)
        case .createInstance: OnboardingCreateInstanceStep(model: model)
        case .connect: OnboardingConnectStep()
        case .complete: OnboardingCompleteStep(model: model)
        }
    }

    private func slide(_ direction: OnboardingDirection) -> AnyTransition {
        let forward = direction == .forward
        return .asymmetric(
            insertion: .move(edge: forward ? .trailing : .leading),
            removal: .move(edge: forward ? .leading : .trailing)
        )
    }

    // MARK: Footer

    private func footer(_ state: OnboardingState) -> some View {
        let busy = state.busy
        let showBack = !state.isFirstStep && !state.isLastStep
        return HStack(spacing: 12) {
            if showBack {
                Button {
                    withAnimation(.easeInOut(duration: 0.35)) { model.back() }
                } label: {
                    Label("Back", systemImage: Symbol.named("arrow_back"))
                }
                .buttonStyle(.borderless)
                .disabled(busy)
            }
            Spacer()
            if OnboardingState.isSkippable(state.stepIndex) {
                Button("Skip for Now") {
                    withAnimation(.easeInOut(duration: 0.35)) { model.skip() }
                }
                .font(.footnote)
                .buttonStyle(.borderless)
                .tint(.secondary)
                .disabled(busy)
            }
            Button(action: handleNext) {
                HStack(spacing: 6) {
                    if busy {
                        ProgressView().tint(DS.Palette.onAccent)
                    }
                    Text(state.isLastStep ? "Open Dashboard" : "Next")
                    if !busy {
                        Image(systemName: Symbol.named("arrow_forward"))
                    }
                }
                .padding(.horizontal, 4)
            }
            .dsProminentButton()
            .controlSize(.large)
            .disabled(busy)
        }
        .padding(.horizontal, 20)
        .padding(.vertical, 12)
    }

    private func handleNext() {
        if model.state.isLastStep {
            Task {
                if await model.finish() {
                    services.router.go("/dashboard")
                }
            }
            return
        }
        withAnimation(.easeInOut(duration: 0.35)) { model.next() }
    }
}

/// The numbered step circles joined by connectors — `_ProgressHeader`.
private struct OnboardingProgressHeader: View {
    let currentIndex: Int

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 0) {
                ForEach(Array(OnboardingState.labels.enumerated()), id: \.offset) { index, label in
                    circle(index)
                        .accessibilityLabel("Step \(index + 1), \(label)")
                        .accessibilityAddTraits(index == currentIndex ? .isSelected : [])
                    if index < OnboardingState.labels.count - 1 {
                        Capsule()
                            .fill(index < currentIndex ? DS.Palette.accent.opacity(0.6) : Color(uiColor: .separator))
                            .frame(width: 20, height: 2)
                    }
                }
            }
            .padding(.horizontal, 20)
            .animation(.easeInOut(duration: 0.3), value: currentIndex)
        }
    }

    private func circle(_ index: Int) -> some View {
        let done = index < currentIndex
        let current = index == currentIndex
        let active = done || current
        return ZStack {
            Circle()
                .fill(active ? DS.Palette.accent.opacity(DS.tintFill) : .clear)
            Circle()
                .strokeBorder(active ? DS.Palette.accent : Color(uiColor: .tertiaryLabel), lineWidth: current ? 2 : 1)
            if done {
                Image(systemName: Symbol.named("check"))
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(.tint)
            } else {
                Text("\(index + 1)")
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(current ? AnyShapeStyle(.tint) : AnyShapeStyle(.tertiary))
            }
        }
        .frame(width: 26, height: 26)
    }
}

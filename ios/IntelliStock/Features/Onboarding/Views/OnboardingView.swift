import SwiftUI

/// The onboarding wizard — `OnboardingScreen` in `onboarding_screen.dart`:
/// a top bar with Exit, the numbered progress header, the current step (no
/// swipe; it slides in the direction of travel) and a Back / Skip / Next
/// footer.
///
/// Shown bare by the gate, and pushed as `Route.onboarding` from the
/// dashboard; pushed, it hides the navigation and tab bars so it covers the
/// shell as the Dart route did. Styled as the web's `OnboardingShell`: the
/// page colour, a top bar (the app tile, "IntelliStock", "Setup", Exit) and
/// the static stepper, each over a hairline, then the step, then the footer.
struct OnboardingView: View {
    @Environment(AppServices.self) private var services
    @State private var model: OnboardingModel?

    var body: some View {
        Group {
            if let model {
                OnboardingContent(model: model)
            } else {
                DS.Surface.page.ignoresSafeArea()
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
                .padding(.horizontal, DS.pageGutter)
                .frame(height: 56)
            DSDivider()

            OnboardingProgressHeader(currentIndex: state.stepIndex)
                .padding(.horizontal, DS.pageGutter)
                .padding(.vertical, 16)
            DSDivider()

            ZStack {
                step(state.currentStep)
                    .id(state.stepIndex)
                    .transition(slide(state.direction))
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .clipped()

            footer(state)
        }
        .background(DS.Surface.page.ignoresSafeArea())
        .confirmAlert($exitRequest)
        .task { await model.loadState() }
    }

    // MARK: Top bar

    private var topBar: some View {
        HStack(spacing: 12) {
            AppWordmark(iconSize: 28)
            Rectangle()
                .fill(Color.white.opacity(0.1))
                .frame(width: DS.Line.width, height: 16)
                .accessibilityHidden(true)
            Text("Setup")
                .font(DS.Font.body)
                .foregroundStyle(DS.Text.muted)
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
                HStack(spacing: 6) {
                    Text("Exit")
                    Image(systemName: Symbol.named("close"))
                        .font(.system(size: 14))
                }
            }
            .buttonStyle(.dsGhost)
            .padding(.trailing, -12)
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
        return HStack(spacing: 8) {
            if showBack {
                Button {
                    withAnimation(DSMotion.ease(0.3)) { model.back() }
                } label: {
                    HStack(spacing: 6) {
                        Image(systemName: Symbol.named("arrow_back"))
                            .font(.system(size: 14))
                        Text("Back")
                    }
                }
                .buttonStyle(.dsGhost(size: .medium))
                .padding(.leading, -12)
                .disabled(busy)
            }
            Spacer()
            if OnboardingState.isSkippable(state.stepIndex) {
                Button("Skip for now") {
                    withAnimation(DSMotion.ease(0.3)) { model.skip() }
                }
                .buttonStyle(.dsGhost(size: .medium))
                .disabled(busy)
            }
            Button(action: handleNext) {
                HStack(spacing: 6) {
                    if busy {
                        ProgressView()
                            .controlSize(.small)
                            .tint(DS.Palette.onAccent)
                    }
                    Text(state.isLastStep ? "Open dashboard" : (state.isFirstStep ? "Get started" : "Next"))
                    if !busy {
                        Image(systemName: Symbol.named("arrow_forward"))
                            .font(.system(size: 14))
                    }
                }
                // Back and Skip for now beside it squeezed "Next" onto two lines.
                .fixedSize(horizontal: true, vertical: false)
            }
            .buttonStyle(.dsPrimary)
            .disabled(busy)
        }
        .padding(.horizontal, DS.pageGutter + 4)
        .padding(.vertical, 14)
        .background {
            DS.Surface.page
                .overlay(alignment: .top) { DSDivider() }
                .ignoresSafeArea(edges: .bottom)
        }
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
        withAnimation(DSMotion.ease(0.3)) { model.next() }
    }
}

/// The numbered steps joined by connectors — `_ProgressHeader`, as the
/// web's static stepper: a done step is a raised circle with a check, the
/// current one off-white with a dark number, the rest a hairline ring with a
/// zinc-500 number; connectors zinc-500 behind, white 8% ahead.
private struct OnboardingProgressHeader: View {
    let currentIndex: Int

    var body: some View {
        HStack(spacing: 8) {
            ForEach(Array(OnboardingState.labels.enumerated()), id: \.offset) { index, label in
                circle(index)
                    .accessibilityLabel("Step \(index + 1), \(label)")
                    .accessibilityAddTraits(index == currentIndex ? .isSelected : [])
                if index < OnboardingState.labels.count - 1 {
                    Rectangle()
                        .fill(index < currentIndex ? DS.Text.muted : Color.white.opacity(0.08))
                        .frame(minWidth: 8, maxWidth: .infinity)
                        .frame(height: DS.Line.width)
                        .accessibilityHidden(true)
                }
            }
        }
        .animation(DSMotion.standard, value: currentIndex)
    }

    private func circle(_ index: Int) -> some View {
        let done = index < currentIndex
        let current = index == currentIndex
        return ZStack {
            if done {
                Circle().fill(DS.Surface.raised)
                Image(systemName: Symbol.named("check"))
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(DS.Text.label)
            } else if current {
                Circle().fill(DS.Palette.accent)
                Text("\(index + 1)")
                    .font(DS.Font.captionMedium)
                    .foregroundStyle(DS.Palette.onAccent)
            } else {
                Circle().strokeBorder(Color.white.opacity(0.1), lineWidth: DS.Line.width)
                Text("\(index + 1)")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
            }
        }
        .monospacedDigit()
        .frame(width: 24, height: 24)
    }
}

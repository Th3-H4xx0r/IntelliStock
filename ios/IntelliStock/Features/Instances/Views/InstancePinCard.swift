import SwiftUI

// The pinned-instance card: the web's `.pin-*` cards (the end of
// `frontend/src/views/InstancesView.vue`).
//
// Running is the one place the Instances screen moves:
// - a soft light orbits the card's border (an angular gradient rotated
//   behind a 1 pt ring) with a soft glow band following it;
// - the border and the top of the card take a faint green tint;
// - the status dot pings, the uptime ticks, and the 1D line draws in and
//   ends in a pinging "now" dot;
// - a stopped → running change flashes the whole border once; a stop fades
//   the orbit out.
// Stopped cards are still; crashed cards get a red hairline. None of the
// motion runs under Reduce Motion (the orbit becomes a still green ring).
//
// Candidates for the design system: `InstancePinDot` (the web's `pin-dot`)
// and `InstancePinSpark` (a sparkline with an area fade and a "now" dot).

/// How an instance is running (the web's `runState`).
nonisolated enum InstanceRunState: Hashable, Sendable {
    case running, stopped, crashed

    init(_ inst: Instance) {
        self = inst.crashed ? .crashed : (inst.runCommand ? .running : .stopped)
    }

    /// "Running", "Stopped", "Crashed" (the web's `pinStateWord`).
    var word: String {
        switch self {
        case .running: "Running"
        case .stopped: "Stopped"
        case .crashed: "Crashed"
        }
    }

    /// The status dot's tone in a list row.
    var tone: DSStatusTone {
        switch self {
        case .running: .live
        case .stopped: .off
        case .crashed: .error
        }
    }
}

/// A running instance's uptime to the second, as the web's `LiveUptime`
/// ticks it: "up 3d 4h 00m 08s", "up 6h 12m 08s", "up 4m 09s", "up 9s".
nonisolated func instanceUptimeTicking(_ secs: Int) -> String {
    let s = max(0, secs)
    let d = s / 86_400
    let h = (s % 86_400) / 3600
    let m = (s % 3600) / 60
    let sec = s % 60
    func pad(_ n: Int) -> String { n < 10 ? "0\(n)" : "\(n)" }
    if d > 0 { return "up \(d)d \(h)h \(pad(m))m \(pad(sec))s" }
    if h > 0 { return "up \(h)h \(pad(m))m \(pad(sec))s" }
    if m > 0 { return "up \(m)m \(pad(sec))s" }
    return "up \(sec)s"
}

/// The web's card motion curve, `cubic-bezier(0.22, 1, 0.36, 1)`.
nonisolated private let pinEase = UnitCurve.bezier(startControlPoint: UnitPoint(x: 0.22, y: 1), endControlPoint: UnitPoint(x: 0.36, y: 1))
// MARK: - Card

/// One pinned instance as a card, in the web's order: the name with its id,
/// Paper / Live and origin, and the pin, Start / Stop and More controls;
/// the run state with the ticking uptime and the kind; the linked account's
/// value today with its 1D line; then the strategy and symbols.
///
/// Tapping the card (anywhere but its controls) opens the instance.
struct InstancePinCard<MenuItems: View>: View {
    let inst: Instance
    /// The instance's detail, for the uptime it reported and when.
    let detail: InstanceRowDetail?
    /// "Paper" or "Live", when the app can tell.
    let moneyKind: String?
    /// "Equity", "Swing", "Crypto", "Kalshi"; nil until the strategy loads.
    let kind: String?
    let accountName: String?
    let summary: DashboardAccountSummary?
    let summaryPending: Bool
    let strategyName: String?
    let busy: Bool
    let onOpen: () -> Void
    let onUnpin: () -> Void
    let onStart: () -> Void
    let onStop: () -> Void
    @ViewBuilder let menuItems: () -> MenuItems

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// Bumped when the instance goes from stopped to running on screen.
    @State private var ignite = 0

    private var state: InstanceRunState { InstanceRunState(inst) }
    private var title: String { inst.name.isEmpty ? inst.id : inst.name }

    var body: some View {
        let state = state
        VStack(alignment: .leading, spacing: 0) {
            head
            statusLine
                .padding(.top, 16)
            account
                .padding(.top, 20)
            facts
                .padding(.top, 20)
        }
        .padding(DS.cardPadding)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(alignment: .top) {
            // The faint green wash at the top of a running card.
            if state == .running {
                LinearGradient(
                    colors: [DS.Palette.gain.opacity(0.055), DS.Palette.gain.opacity(0)],
                    startPoint: .top,
                    endPoint: .bottom
                )
                .frame(height: 128)
                .allowsHitTesting(false)
            }
        }
        .dsCardSurface(border: border(state))
        .overlay { InstancePinEffects(running: state == .running, ignite: ignite) }
        .animation(reduceMotion ? nil : DSMotion.ease(0.4), value: state)
        .contentShape(.rect(cornerRadius: DS.Radius.card))
        .onTapGesture(perform: onOpen)
        .onChange(of: state) { old, new in
            if old != .running, new == .running, !reduceMotion { ignite += 1 }
        }
        .accessibilityElement(children: .contain)
    }

    private func border(_ state: InstanceRunState) -> Color {
        switch state {
        case .running: DS.Palette.gain.opacity(0.18)
        case .crashed: DS.Palette.loss.opacity(0.28)
        case .stopped: DS.Line.card
        }
    }

    // MARK: Head

    private var head: some View {
        HStack(alignment: .top, spacing: 12) {
            Button(action: onOpen) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(title)
                        .font(DS.Font.baseMedium)
                        .foregroundStyle(state == .stopped ? DS.Text.label : DS.Text.primary)
                        .lineLimit(1)
                    Text(subline)
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                        .monospacedDigit()
                        .lineLimit(1)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityHint("Opens the instance")

            HStack(spacing: 4) {
                Button(action: onUnpin) {
                    Image(systemName: "pin.fill")
                }
                .buttonStyle(.dsIcon)
                .accessibilityLabel("Unpin \(title)")

                runButton
                    .padding(.leading, 4)

                Menu {
                    menuItems()
                } label: {
                    Image(systemName: "ellipsis")
                        .font(.system(size: 17))
                        .foregroundStyle(DS.Text.muted)
                        .frame(width: 32, height: 32)
                        .contentShape(Rectangle())
                }
                .menuIndicator(.hidden)
                .accessibilityLabel("More actions for \(title)")
            }
            .padding(.trailing, -4)
            .padding(.top, -6)
        }
    }

    /// "core-live · Live · AI created" (the web's head subline).
    private var subline: String {
        var parts = [inst.id]
        if let moneyKind { parts.append(moneyKind) }
        if inst.createdBy == "ai" { parts.append("AI created") }
        return parts.joined(separator: " · ")
    }

    @ViewBuilder
    private var runButton: some View {
        let running = inst.runCommand
        Button(role: running ? .destructive : nil) {
            running ? onStop() : onStart()
        } label: {
            Group {
                if busy {
                    ProgressView()
                        .controlSize(.mini)
                        .accessibilityLabel("Working")
                } else {
                    Text(running ? "Stop" : "Start")
                }
            }
            .frame(minWidth: 40)
        }
        .buttonStyle(.dsSecondary)
        .disabled(busy)
    }

    // MARK: Status

    /// "● Running · up 6h 12m 08s · Equity": the word in gain (running) or
    /// loss (crashed), the uptime ticking every second.
    private var statusLine: some View {
        HStack(spacing: 10) {
            InstancePinDot(state: state, ignite: ignite)
            if state == .running, detail?.instance.uptimeSeconds != nil {
                TimelineView(.periodic(from: .now, by: 1)) { context in
                    statusText(uptime: uptime(at: context.date))
                }
            } else {
                statusText(uptime: nil)
            }
        }
        .font(DS.Font.body)
        .accessibilityElement(children: .combine)
    }

    private func uptime(at date: Date) -> Int? {
        instanceLiveUptime(running: state == .running, detail: detail?.instance, fetchedAt: detail?.fetchedAt, now: date)
    }

    private func statusText(uptime: Int?) -> some View {
        let wordColor: Color = switch state {
        case .running: DS.Palette.gain
        case .crashed: DS.Palette.loss
        case .stopped: DS.Text.muted
        }
        let dot = Text(" · ").foregroundStyle(DS.Text.faint)
        var line = Text(state.word).foregroundStyle(wordColor)
        if let uptime {
            line = line + dot + Text(instanceUptimeTicking(uptime)).foregroundStyle(DS.Text.secondary)
        }
        if let kind {
            line = line + dot + Text(kind).foregroundStyle(state == .stopped ? DS.Text.muted : DS.Text.secondary)
        }
        return line
            .monospacedDigit()
            .lineLimit(1)
    }

    // MARK: Account

    /// The linked account's day: "Alpaca Live · today", its value, today's
    /// change, and the 1D line; or "No brokerage linked".
    @ViewBuilder
    private var account: some View {
        if inst.brokerageId?.isEmpty == false {
            HStack(alignment: .bottom, spacing: 16) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("\(accountName ?? "Account") · today")
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                        .lineLimit(1)
                    if let summary {
                        Text(fmtMoney(summary.equity))
                            .font(DS.Font.figureLarge)
                            .foregroundStyle(state == .stopped ? DS.Text.label : DS.Text.primary)
                            .monospacedDigit()
                            .lineLimit(1)
                            .minimumScaleFactor(0.7)
                        if let change = instanceRowChangeText(summary) {
                            Text(change)
                                .font(DS.Font.caption)
                                .foregroundStyle(summary.direction.color)
                                .monospacedDigit()
                                .lineLimit(1)
                        } else {
                            Text(" ").font(DS.Font.caption)
                        }
                    } else if summaryPending {
                        Skeleton(width: 140, height: 24, radius: DS.Radius.tiny)
                            .padding(.vertical, 2)
                        Skeleton.line(width: 96)
                            .padding(.top, 2)
                    } else {
                        Text("—")
                            .font(DS.Font.figureLarge)
                            .foregroundStyle(DS.Text.faint)
                        Text(" ").font(DS.Font.caption)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                if let summary, summary.spark.count > 1 {
                    // The web's `w-2/5 max-w-40` of a phone card's 330 pt.
                    InstancePinSpark(values: summary.spark, live: state == .running)
                        .frame(width: 132, height: 40)
                }
            }
            .frame(minHeight: 64, alignment: .bottom)
            .accessibilityElement(children: .combine)
        } else {
            Text("No brokerage linked")
                .font(DS.Font.body)
                .foregroundStyle(DS.Text.faint)
                .frame(maxWidth: .infinity, minHeight: 64, alignment: .leading)
        }
    }

    // MARK: Strategy and symbols

    private var facts: some View {
        Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 8) {
            GridRow {
                Text("Strategy")
                    .foregroundStyle(DS.Text.muted)
                Text(strategyName ?? "No strategy linked")
                    .foregroundStyle(strategyName == nil ? DS.Text.faint : DS.Text.label)
                    .lineLimit(1)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            GridRow {
                Text("Symbols")
                    .foregroundStyle(DS.Text.muted)
                Text(inst.stocks.isEmpty ? "None" : inst.stocks.joined(separator: ", "))
                    .foregroundStyle(inst.stocks.isEmpty ? DS.Text.faint : DS.Text.label)
                    .monospacedDigit()
                    .lineLimit(1)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .font(DS.Font.body)
        .padding(.top, 16)
        .overlay(alignment: .top) { DSDivider() }
        .accessibilityElement(children: .combine)
    }
}

// MARK: - Border effects

/// The running card's light: a comet orbiting the 1 pt border every 6 s
/// with a soft glow band following it, fading in on start and out on stop;
/// plus the one-time border flash on start. Under Reduce Motion the comet
/// is a still green ring and there is no glow or flash.
///
/// Performance: the comet is an angular gradient drawn once and *rotated*
/// by a repeating animation, under a static ring mask, so scrolling never
/// re-runs this view's body or a blur per frame (an earlier version rebuilt
/// the gradient and a 9 pt blur 60 times a second per card, which made the
/// Instances list stutter). The glow is layered soft strokes, not a blur.
private struct InstancePinEffects: View {
    let running: Bool
    let ignite: Int

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    /// The comet: a long faint tail brightening to a near-white head
    /// (the web's `pin-orbit` conic gradient).
    static let comet = Gradient(stops: [
        .init(color: DS.Palette.gain.opacity(0), location: 0),
        .init(color: DS.Palette.gain.opacity(0), location: 0.55),
        .init(color: DS.Palette.gain.opacity(0.36), location: 0.82),
        .init(color: DS.Palette.gain.opacity(0.9), location: 0.965),
        .init(color: Color(hex: 0xD6FFE9), location: 0.99),
        .init(color: DS.Palette.gain.opacity(0), location: 0.995),
        .init(color: DS.Palette.gain.opacity(0), location: 1),
    ])

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: DS.Radius.card, style: .continuous)
        ZStack {
            if reduceMotion {
                shape
                    .strokeBorder(DS.Palette.gain.opacity(0.35), lineWidth: DS.Line.width)
                    .opacity(running ? 1 : 0)
            } else {
                if running {
                    InstancePinOrbit()
                        .mask {
                            ZStack {
                                // The glow: two soft bands just outside and on
                                // the border, standing in for the web's blur.
                                shape.inset(by: -3).strokeBorder(lineWidth: 5).opacity(0.14)
                                shape.inset(by: -1).strokeBorder(lineWidth: 3).opacity(0.3)
                                // The line itself.
                                shape.strokeBorder(lineWidth: DS.Line.width)
                            }
                        }
                        .transition(.opacity)
                }
                flash(shape)
            }
        }
        .animation(reduceMotion ? nil : .timingCurve(0.22, 1, 0.36, 1, duration: 0.7), value: running)
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }

    /// Start: the whole border lights and settles over 1.5 s. Soft strokes
    /// instead of blurs; invisible (opacity 0) between flashes.
    private func flash(_ shape: RoundedRectangle) -> some View {
        ZStack {
            shape.inset(by: -4).strokeBorder(DS.Palette.gain.opacity(0.08), lineWidth: 8)
            shape.inset(by: -2).strokeBorder(DS.Palette.gain.opacity(0.16), lineWidth: 4)
            shape.strokeBorder(DS.Palette.gain.opacity(0.1), lineWidth: 12)
            shape.strokeBorder(DS.Palette.gain.opacity(0.9), lineWidth: DS.Line.width)
        }
        .keyframeAnimator(initialValue: 0.0, trigger: ignite) { view, opacity in
            view.opacity(opacity)
        } keyframes: { _ in
            LinearKeyframe(0.0, duration: 0)
            LinearKeyframe(1.0, duration: 0.15, timingCurve: pinEase)
            LinearKeyframe(0.0, duration: 1.35, timingCurve: pinEase)
        }
    }
}

/// The comet gradient, drawn once on a square that covers the card at any
/// angle, turning once every 6 s. Only its transform animates.
private struct InstancePinOrbit: View {
    @State private var turned = false

    var body: some View {
        GeometryReader { geo in
            let side = (geo.size.width * geo.size.width + geo.size.height * geo.size.height).squareRoot()
            // CSS conic gradients start at 12 o'clock; SwiftUI's at 3.
            AngularGradient(gradient: InstancePinEffects.comet, center: .center, angle: .degrees(-90))
                .frame(width: side, height: side)
                .rotationEffect(.degrees(turned ? 360 : 0))
                .animation(.linear(duration: 6).repeatForever(autoreverses: false), value: turned)
                .position(x: geo.size.width / 2, y: geo.size.height / 2)
        }
        .onAppear { turned = true }
    }
}

/// One ring of the web's `pin-ping`: grows from the dot to 3.2× and fades
/// out every 2.4 s, starting `delay` seconds in. A repeating animation of
/// scale and opacity only, so it costs nothing per frame on the main thread.
private struct InstancePinPingRing: View {
    let color: Color
    let size: CGFloat
    var delay: Double = 0

    @State private var out = false

    var body: some View {
        Circle()
            .fill(color.opacity(0.5))
            .frame(width: size, height: size)
            .scaleEffect(out ? 3.2 : 1)
            .opacity(out ? 0 : 0.6)
            .animation(.timingCurve(0, 0, 0.2, 1, duration: 2.4).repeatForever(autoreverses: false).delay(delay), value: out)
            .onAppear { out = true }
    }
}

// MARK: - Status dot

/// The pinned card's status dot (the web's `pin-dot`): 7 pt; a running one
/// glows and two staggered rings ping out of it every 2.4 s, and it pops
/// once on start. Crashed is loss red, stopped zinc-600. Still under Reduce
/// Motion.
struct InstancePinDot: View {
    let state: InstanceRunState
    var ignite = 0

    @ScaledMetric(relativeTo: .body) private var size: CGFloat = 7
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        ZStack {
            if state == .running {
                // `box-shadow: 0 0 8px` at 55%, as a radial fill (no blur).
                Circle()
                    .fill(RadialGradient(
                        colors: [DS.Palette.gain.opacity(0.55), DS.Palette.gain.opacity(0)],
                        center: .center, startRadius: 0, endRadius: (size + 8) / 2
                    ))
                    .frame(width: size + 8, height: size + 8)
                if !reduceMotion {
                    InstancePinPingRing(color: DS.Palette.gain, size: size)
                    InstancePinPingRing(color: DS.Palette.gain, size: size, delay: 1.2)
                }
            }
            Circle()
                .fill(color)
                .frame(width: size, height: size)
        }
        .frame(width: size, height: size)
        .keyframeAnimator(initialValue: 1.0, trigger: ignite) { view, scale in
            view.scaleEffect(scale)
        } keyframes: { _ in
            LinearKeyframe(1.0, duration: 0)
            LinearKeyframe(1.9, duration: 0.245, timingCurve: pinEase)
            LinearKeyframe(1.0, duration: 0.455, timingCurve: pinEase)
        }
        .accessibilityHidden(true)
    }

    private var color: Color {
        switch state {
        case .running: DS.Palette.gain
        case .crashed: DS.Palette.loss
        case .stopped: DS.Text.faint
        }
    }
}

// MARK: - 1D line

/// The account's 1D line on a pinned card (the web's `pin-spark`): gain or
/// loss by the day so far, a faint same-hue fade under it, and its last
/// point marked "now". While the instance runs (`live`) the line draws in
/// from the left and the "now" dot pings; otherwise the whole line rests at
/// 60%. Scaled like `Sparkline` (never stretching less than 0.5% of the
/// value to full height), with room on the right for the dot. Decorative.
struct InstancePinSpark: View {
    let values: [Double]
    let live: Bool

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// How much of the line shows, 0…1.
    @State private var reveal: CGFloat = 1
    @State private var nowShown = true

    /// The web's geometry: 5 pt above and below, 6 pt kept clear on the right.
    private static let padY: CGFloat = 5
    private static let padRight: CGFloat = 6

    /// The points in a `size` box (the web's `pinSpark`).
    nonisolated static func points(_ values: [Double], in size: CGSize) -> [CGPoint] {
        guard values.count > 1 else { return [] }
        let domain = Sparkline.domain(values)
        let span = domain.upperBound - domain.lowerBound
        let usableW = max(size.width - padRight, 0)
        let usableH = max(size.height - padY * 2, 0)
        let step = usableW / CGFloat(values.count - 1)
        return values.enumerated().map { i, v in
            CGPoint(
                x: CGFloat(i) * step,
                y: padY + usableH - CGFloat((v - domain.lowerBound) / span) * usableH
            )
        }
    }

    var body: some View {
        let color = Sparkline.isUp(values) ? DS.Palette.gain : DS.Palette.loss
        GeometryReader { geo in
            let pts = Self.points(values, in: geo.size)
            if let last = pts.last {
                ZStack(alignment: .topLeading) {
                    ZStack {
                        // The fade under the line (the web's 18% → 0).
                        area(pts, height: geo.size.height)
                            .fill(LinearGradient(
                                colors: [color.opacity(0.18), color.opacity(0)],
                                startPoint: .top,
                                endPoint: .bottom
                            ))
                        line(pts)
                            .stroke(color, style: StrokeStyle(lineWidth: 1.5, lineCap: .round, lineJoin: .round))
                    }
                    .mask(alignment: .topLeading) {
                        Rectangle()
                            .frame(width: (geo.size.width + 8) * reveal, height: geo.size.height + 8)
                            .offset(x: -4, y: -4)
                    }
                    InstancePinNowDot(color: color, live: live && !reduceMotion)
                        .scaleEffect(nowShown ? 1 : 0)
                        .opacity(nowShown ? 1 : 0)
                        .position(last)
                }
            }
        }
        .opacity(live ? 1 : 0.6)
        .onAppear { if live { drawIn() } }
        .onChange(of: live) { _, now in if now { drawIn() } }
        .accessibilityHidden(true)
    }

    /// The web's `pin-draw` (1.2 s) and `pin-now-in` (0.42 s after 1 s).
    private func drawIn() {
        guard !reduceMotion else { return }
        reveal = 0
        nowShown = false
        withAnimation(.timingCurve(0.22, 1, 0.36, 1, duration: 1.2)) { reveal = 1 }
        withAnimation(.timingCurve(0.22, 1, 0.36, 1, duration: 0.42).delay(1.0)) { nowShown = true }
    }

    private func line(_ pts: [CGPoint]) -> Path {
        Path { p in
            p.move(to: pts[0])
            for pt in pts.dropFirst() { p.addLine(to: pt) }
        }
    }

    private func area(_ pts: [CGPoint], height: CGFloat) -> Path {
        var p = line(pts)
        if let last = pts.last {
            p.addLine(to: CGPoint(x: last.x, y: height))
            p.addLine(to: CGPoint(x: pts[0].x, y: height))
            p.closeSubpath()
        }
        return p
    }
}

/// The line's "now" point: 6 pt in the line's colour with a 2 pt ring of
/// the card's colour; while `live`, a ring pings out of it every 2.4 s.
private struct InstancePinNowDot: View {
    let color: Color
    let live: Bool

    var body: some View {
        ZStack {
            if live {
                InstancePinPingRing(color: color, size: 6, delay: 0.4)
            }
            Circle()
                .fill(DS.Surface.card)
                .frame(width: 10, height: 10)
            Circle()
                .fill(color)
                .frame(width: 6, height: 6)
        }
        .frame(width: 10, height: 10)
    }
}

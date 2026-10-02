import SwiftUI

/// The sector-allocation ring that drills in on tap, ported from
/// `Sector3DChart` (mobile/lib/features/dashboard/presentation/
/// sector_3d_chart.dart).
///
/// Flat, it is a ring lit from above, with gaps between the sectors, a %
/// label per sector and a centre readout of the focused sector. Tap a sector
/// and, over 620 ms, the ring zooms, tilts low and extrudes into tall 3D
/// blocks with the focused sector raised at the top, under an
/// "Allocation / NAME N%" header and over a Back button. A swipe steps to
/// the next sector with a selection haptic, flat or drilled. Drilled, the
/// ring follows the finger and eases to the nearest sector on release, and
/// focus changes turn it over 380 ms.
///
/// The Dart drew brushed metal with gradients. This draws one solid colour
/// per face from a fixed light instead (no gradients, glows or coloured
/// shadows), with 1 pt edges for definition. See `Sector3DGeometry.swift`.
///
/// Drilled, the ring draws up to `Sector3DGeometry.overflowAllowance`
/// (20 pt) past the chart's frame on every side, as the Dart's did inside its
/// card. Give it a `Card`'s default padding.
struct Sector3DChart: View {
    let slices: [SectorSlice]
    /// Test-only: forces the drill value (0 flat ... 1 drilled).
    let debugDrill: Double?

    init(slices: [SectorSlice], debugDrill: Double? = nil) {
        self.slices = slices
        self.debugDrill = debugDrill
    }

    @State private var interaction = Sector3DInteraction()
    @State private var width: CGFloat = 0
    /// Whether the current drag is horizontal (decided on its first move),
    /// and its last horizontal translation.
    @State private var dragIsHorizontal: Bool?
    @State private var lastDragX: CGFloat = 0

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.colorSchemeContrast) private var contrast

    private static var now: TimeInterval { Date().timeIntervalSinceReferenceDate }

    var body: some View {
        if slices.isEmpty {
            Color.clear.frame(height: 8)
        } else {
            TimelineView(.animation(paused: !interaction.isAnimating(at: Self.now))) { timeline in
                chart(at: timeline.date.timeIntervalSinceReferenceDate)
            }
            .task(id: interaction.animationDeadline) {
                // Stop the timeline once the run ends.
                guard let deadline = interaction.animationDeadline else { return }
                let wait = deadline - Self.now
                if wait > 0 { try? await Task.sleep(for: .seconds(wait + 0.02)) }
                guard !Task.isCancelled else { return }
                interaction.settle(at: Self.now)
            }
            .sensoryFeedback(.selection, trigger: interaction.selectionTicks)
            .sensoryFeedback(.impact(weight: .medium), trigger: interaction.drillTicks)
            .sensoryFeedback(.impact(weight: .light), trigger: interaction.backTicks)
        }
    }

    // MARK: Frame

    /// One scene to draw, and its opacity (below 1 only in the Reduce Motion
    /// crossfade).
    private struct Layer {
        let scene: Sector3DScene
        let opacity: Double
    }

    private func drillValues(at now: TimeInterval) -> (raw: Double, eased: Double) {
        let raw = debugDrill ?? interaction.drill.value(at: now)
        return (raw, Sector3DCurve.easeInOutCubic.transform(raw))
    }

    /// The chart's height. With Reduce Motion it stays drilled-height for the
    /// whole crossfade rather than resizing as it goes.
    private func height(raw: Double, eased: Double) -> Double {
        if reduceMotion {
            return raw > 0 ? Sector3DGeometry.drillHeight : Sector3DGeometry.flatHeight
        }
        return Sector3DGeometry.height(drill: eased)
    }

    private func layers(at now: TimeInterval, width w: CGFloat) -> [Layer] {
        let (_, d) = drillValues(at: now)
        let sel = interaction.selected(count: slices.count)
        let rot = interaction.rotNow(at: now)
        func scene(_ drill: Double) -> Sector3DScene {
            Sector3DScene(
                slices: slices, selected: sel, drill: drill, ringRot: rot,
                size: CGSize(width: w, height: Sector3DGeometry.height(drill: drill))
            )
        }
        guard reduceMotion else { return [Layer(scene: scene(d), opacity: 1)] }
        // Reduce Motion: no zoom or tilt, a crossfade between the end states.
        var out: [Layer] = []
        if d < 1 { out.append(Layer(scene: scene(0), opacity: 1 - d)) }
        if d > 0 { out.append(Layer(scene: scene(1), opacity: d)) }
        return out
    }

    private func chart(at now: TimeInterval) -> some View {
        let (raw, d) = drillValues(at: now)
        let sel = interaction.selected(count: slices.count)
        let drilledChrome = min(max((d - 0.2) / 0.8, 0), 1)
        return Color.clear
            .frame(maxWidth: .infinity)
            .frame(height: height(raw: raw, eased: d))
            .onGeometryChange(for: CGFloat.self) { $0.size.width } action: { width = $0 }
            .overlay {
                GeometryReader { geo in
                    let layers = layers(at: now, width: geo.size.width)
                    ZStack(alignment: .topLeading) {
                        ring(layers)
                        ForEach(Array(layers.enumerated()), id: \.offset) { _, layer in
                            flatChrome(layer.scene)
                                .opacity(layer.opacity * layer.scene.flatAlpha)
                        }
                    }
                }
            }
            .overlay(alignment: .top) {
                if d > 0.05 {
                    header(slices[sel])
                        .padding(.top, 6)
                        .opacity(drilledChrome)
                }
            }
            .contentShape(Rectangle())
            .onTapGesture(coordinateSpace: .local) { tap($0) }
            // A UIKit pan that begins only for a sideways drag, so a vertical
            // swipe over the ring still scrolls the list around it. (A
            // simultaneous `DragGesture` held the list's pan in an
            // inset-grouped `List` on iOS 26.)
            .gesture(Sector3DHorizontalPan(onChanged: panChanged, onEnded: panEnded))
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(Sector3DGeometry.accessibilityLabel(slices[sel]))
            .accessibilityValue("\(sel + 1) of \(slices.count)")
            .accessibilityHint(
                interaction.isDrilledIn
                    ? "Swipe up or down to turn to another sector."
                    : "Swipe up or down to choose a sector. Double-tap to expand it."
            )
            .accessibilityAdjustableAction { direction in
                switch direction {
                case .increment: interaction.step(1, slices, at: Self.now, animated: !reduceMotion)
                case .decrement: interaction.step(-1, slices, at: Self.now, animated: !reduceMotion)
                @unknown default: break
                }
            }
            .accessibilityAction { toggleDrill() }
            .overlay(alignment: .bottom) {
                if d > 0.05 {
                    Button {
                        interaction.back(at: Self.now)
                    } label: {
                        Label("Back", systemImage: Symbol.named("keyboard_arrow_up"))
                            .font(.footnote.weight(.semibold))
                            .frame(minHeight: 30)
                    }
                    .buttonStyle(.glass)
                    .padding(.bottom, 6)
                    .opacity(drilledChrome)
                }
            }
            .dynamicTypeSize(...DynamicTypeSize.xxxLarge)
    }

    // MARK: Drawing

    private var palette: Sector3DPalette { colorScheme == .dark ? .dark : .light }

    /// The faces, drawn into a canvas that reaches past the frame so the
    /// drilled ring can overflow it.
    private func ring(_ layers: [Layer]) -> some View {
        let bleed = Sector3DGeometry.overflowAllowance
        let palette = palette
        let edgeBoost = contrast == .increased ? 2.5 : 1
        return Canvas { ctx, _ in
            ctx.translateBy(x: bleed, y: bleed)
            for layer in layers {
                if layer.opacity >= 1 {
                    Self.draw(layer.scene, in: &ctx, palette: palette, edgeBoost: edgeBoost)
                } else {
                    // Fade the layer as a whole, so its faces do not show
                    // through each other.
                    ctx.drawLayer { layerCtx in
                        layerCtx.opacity = layer.opacity
                        Self.draw(layer.scene, in: &layerCtx, palette: palette, edgeBoost: edgeBoost)
                    }
                }
            }
        }
        // A canvas draws past its own bounds, so clip it there: the bounds
        // reach `bleed` past the frame, where the Dart's card clipped the
        // ring.
        .clipped()
        .padding(-bleed)
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }

    private static func draw(
        _ scene: Sector3DScene, in ctx: inout GraphicsContext, palette: Sector3DPalette, edgeBoost: Double
    ) {
        for face in scene.faces() {
            var path = Path()
            path.addLines(face.points)
            path.closeSubpath()
            if face.kind == .floor {
                ctx.fill(path, with: .color(Color(palette.floor).opacity(min(max(scene.drill, 0), 1))))
                continue
            }
            let fill = palette.color(normal: face.normal, brightness: face.brightness)
            ctx.fill(path, with: .color(Color(fill)))
            let isTop = face.kind == .top || face.kind == .discTop
            let alpha = isTop ? (face.isSelected ? palette.edgeSelected : palette.edgeTop) : palette.edgeSide
            ctx.stroke(path, with: .color(Color(palette.edge).opacity(min(alpha * edgeBoost, 1))), lineWidth: 1)
        }
    }

    /// The flat-only chrome: a % label per sector and the centre readout.
    @ViewBuilder
    private func flatChrome(_ scene: Sector3DScene) -> some View {
        if scene.flatAlpha > 0.02, scene.total > 0 {
            let sel = scene.selected
            ForEach(slices.indices, id: \.self) { i in
                Text(verbatim: "\(Int(slices[i].pct.rounded()))%")
                    .font(.caption.weight(.bold))
                    .foregroundStyle(i == sel ? AnyShapeStyle(DS.Palette.accent) : AnyShapeStyle(.secondary))
                    .fixedSize()
                    .position(scene.labels[i])
            }
            let c = scene.centre
            Text(verbatim: "\(Int(slices[sel].pct.rounded()))%")
                .font(.title.weight(.heavy))
                .monospacedDigit()
                .foregroundStyle(.primary)
                .fixedSize()
                .position(x: c.x, y: c.y - 9)
            Text(verbatim: slices[sel].sector)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .truncationMode(.tail)
                .frame(maxWidth: scene.layout.ri * 1.7)
                .fixedSize(horizontal: false, vertical: true)
                .position(x: c.x, y: c.y + 13)
        }
    }

    /// The drilled header: "Allocation" over "NAME  N%".
    private func header(_ slice: SectorSlice) -> some View {
        VStack(spacing: 2) {
            Text("Allocation")
                .font(.caption)
                .foregroundStyle(.secondary)
            Text("\(Text(verbatim: "\(slice.sector)  ").foregroundStyle(.primary))\(Text(verbatim: "\(Int(slice.pct.rounded()))%").foregroundStyle(DS.Palette.accent))")
                .font(.headline)
                .lineLimit(1)
        }
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }

    // MARK: Gestures

    /// The scene a tap lands on: the one on screen, or the flat layer while
    /// Reduce Motion crossfades.
    private func tapScene(at now: TimeInterval) -> Sector3DScene? {
        layers(at: now, width: width).first?.scene
    }

    private func tap(_ point: CGPoint) {
        let now = Self.now
        guard let scene = tapScene(at: now) else { return }
        interaction.tap(at: point, scene: scene, slices, at: now)
    }

    private func toggleDrill() {
        let now = Self.now
        if interaction.isDrilledIn {
            interaction.back(at: now)
        } else {
            interaction.drillInto(interaction.selected(count: slices.count), slices, at: now)
        }
    }

    /// A horizontal drag, past the touch slop: it steps the highlight flat,
    /// and turns the ring drilled. `translation` is the pan's total since it
    /// began; nothing happens until it passes the touch slop, as
    /// `DragGesture(minimumDistance:)` behaved.
    private func panChanged(_ translation: CGSize) {
        let now = Self.now
        guard let horizontal = dragIsHorizontal else {
            guard hypot(translation.width, translation.height) >= Sector3DGeometry.touchSlop else { return }
            let horizontal = abs(translation.width) > abs(translation.height)
            dragIsHorizontal = horizontal
            if horizontal {
                // The slop is not part of the drag, as in Flutter.
                lastDragX = translation.width
                interaction.dragBegan(at: now, reduceMotion: reduceMotion)
            }
            return
        }
        guard horizontal else { return }
        let dx = translation.width - lastDragX
        lastDragX = translation.width
        interaction.dragMoved(by: dx, slices, at: now)
    }

    private func panEnded() {
        if dragIsHorizontal == true { interaction.dragEnded(slices, at: Self.now) }
        dragIsHorizontal = nil
    }
}

/// The chart's sideways pan. It begins only when the finger moves more
/// across than up or down; otherwise it fails at once and the enclosing
/// scroll view takes the vertical pan.
private struct Sector3DHorizontalPan: UIGestureRecognizerRepresentable {
    /// The total translation since the pan began.
    let onChanged: (CGSize) -> Void
    let onEnded: () -> Void

    func makeUIGestureRecognizer(context: Context) -> UIPanGestureRecognizer {
        let pan = UIPanGestureRecognizer()
        pan.delegate = context.coordinator
        return pan
    }

    func handleUIGestureRecognizerAction(_ recognizer: UIPanGestureRecognizer, context: Context) {
        switch recognizer.state {
        case .began, .changed:
            let t = recognizer.translation(in: recognizer.view)
            onChanged(CGSize(width: t.x, height: t.y))
        case .ended, .cancelled, .failed:
            onEnded()
        default:
            break
        }
    }

    func makeCoordinator(converter: CoordinateSpaceConverter) -> Coordinator {
        Coordinator()
    }

    final class Coordinator: NSObject, UIGestureRecognizerDelegate {
        func gestureRecognizerShouldBegin(_ recognizer: UIGestureRecognizer) -> Bool {
            guard let pan = recognizer as? UIPanGestureRecognizer else { return true }
            let v = pan.velocity(in: pan.view)
            return abs(v.x) > abs(v.y)
        }
    }
}

private extension Color {
    init(_ rgb: Sector3DRGB) {
        self.init(.sRGB, red: rgb.r, green: rgb.g, blue: rgb.b)
    }
}

// MARK: - Previews

private let sector3DPreviewSlices: [SectorSlice] = [
    SectorSlice(sector: "Technology", value: 4000, pct: 40),
    SectorSlice(sector: "Healthcare", value: 2500, pct: 25),
    SectorSlice(sector: "Financials", value: 1500, pct: 15),
    SectorSlice(sector: "Energy", value: 1200, pct: 12),
    SectorSlice(sector: "Consumer", value: 800, pct: 8),
]

private let sector3DPreviewEight: [SectorSlice] = [
    ("Technology", 28.0), ("Healthcare", 17), ("Financials", 14), ("Energy", 11),
    ("Consumer Cyclical", 10), ("Industrials", 9), ("Utilities", 6), ("Real Estate", 5),
].map { SectorSlice(sector: $0.0, value: $0.1 * 100, pct: $0.1) }

private struct Sector3DPreviewCard: View {
    let slices: [SectorSlice]
    var debugDrill: Double?

    var body: some View {
        ScrollView {
            Card {
                VStack(alignment: .leading, spacing: 8) {
                    Text("Sector allocation").font(.subheadline.weight(.semibold))
                    Sector3DChart(slices: slices, debugDrill: debugDrill)
                }
            }
            .padding()
        }
        .background(DS.Surface.canvas)
    }
}

#Preview("1 slice") {
    Sector3DPreviewCard(slices: [SectorSlice(sector: "Technology", value: 1000, pct: 100)])
}

#Preview("3 slices") {
    Sector3DPreviewCard(slices: Array(sector3DPreviewSlices.prefix(3)))
}

#Preview("5 slices") {
    Sector3DPreviewCard(slices: sector3DPreviewSlices)
}

#Preview("8 slices") {
    Sector3DPreviewCard(slices: sector3DPreviewEight)
}

#Preview("8 slices, dark") {
    Sector3DPreviewCard(slices: sector3DPreviewEight)
        .preferredColorScheme(.dark)
}

#Preview("Drilled, dark") {
    Sector3DPreviewCard(slices: sector3DPreviewSlices, debugDrill: 1)
        .preferredColorScheme(.dark)
}

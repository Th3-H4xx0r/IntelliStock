import SwiftUI

/// One flagged day, opened from the list or the chart: what moved it (the
/// headline and explanation), the move split into market, sector and
/// stock-specific parts, the model's confidence, the articles it cited (each
/// opens in the in-app browser), and Explain or Re-run. Reads the event from
/// the model by id, so a pending explanation fills in while it is open.
struct AnalysisEventSheet: View {
    let model: AnalysisModel
    let eventId: String
    /// Dismisses the sheet and opens Settings (no model chosen).
    let onOpenSettings: () -> Void

    @Environment(\.dismiss) private var dismiss
    @State private var browser: DashboardBrowserLink?

    var body: some View {
        let event = model.event(id: eventId)
        VStack(spacing: 0) {
            header(event)
            ScrollView {
                if let event {
                    content(event)
                        .padding(.horizontal, 24)
                        .padding(.bottom, 24)
                } else {
                    EmptyState(title: "This move is no longer in the range", subtitle: "Pick a longer range to see it again")
                }
            }
            .scrollBounceBehavior(.basedOnSize)
            if let event {
                footer(event)
            }
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
        .presentationBackground(DS.Surface.card)
        .sheet(item: $browser) { link in
            DashboardSafariView(url: link.url).ignoresSafeArea()
        }
    }

    // MARK: Header

    private func header(_ event: MoveEvent?) -> some View {
        HStack(alignment: .top, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                Text(event.map { "\($0.symbol) · \(stockExpiryText($0.date))" } ?? "Move")
                    .font(DS.Font.sectionTitle)
                    .foregroundStyle(DS.Text.primary)
                    .accessibilityAddTraits(.isHeader)
                if let event {
                    (Text("Close \(fmtMoney(event.close))").foregroundStyle(DS.Text.muted)
                        + Text("  \(fmtPct(event.retPct))").foregroundStyle(dashboardChangeColor(event.retPct)))
                        .font(DS.Font.body)
                        .monospacedDigit()
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            Button { dismiss() } label: {
                Image(systemName: "xmark")
            }
            .buttonStyle(.dsIcon)
            .accessibilityLabel("Close")
        }
        .padding(.horizontal, 24)
        .padding(.top, 20)
        .padding(.bottom, 16)
    }

    // MARK: Content

    private func content(_ event: MoveEvent) -> some View {
        VStack(alignment: .leading, spacing: 24) {
            cause(event)
            VStack(alignment: .leading, spacing: 12) {
                sectionTitle("The move, split")
                AnalysisSplitGrid(move: event.retPct, market: event.marketPct, sector: event.sectorPct, specific: event.specificPct)
                Text("Market is the part the S&P 500 (SPY) explains at this stock's beta; sector is what its sector ETF added beyond the market; the rest is the stock's own.")
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.muted)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if event.isExplained {
                sources(event)
            }
        }
    }

    @ViewBuilder
    private func cause(_ event: MoveEvent) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                AnalysisMarkerDot(style: AnalysisMarkerStyle(event))
                Text(statusLine(event))
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Text.secondary)
            }
            switch event.status {
            case .explained:
                if let headline = event.headline {
                    Text(headline)
                        .font(DS.Font.large)
                        .foregroundStyle(DS.Text.primary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let explanation = event.explanation {
                    Text(explanation)
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.label)
                        .lineSpacing(4)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let byline = byline(event) {
                    Text(byline)
                        .font(DS.Font.caption)
                        .foregroundStyle(DS.Text.muted)
                }
            case .pending:
                HStack(spacing: 8) {
                    ProgressView()
                        .controlSize(.small)
                        .tint(DS.Text.muted)
                    Text("Explaining. This takes a few seconds per move")
                        .font(DS.Font.body)
                        .foregroundStyle(DS.Text.secondary)
                }
            case .failed:
                Text(event.error ?? "The model could not explain this move")
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Palette.loss)
                    .fixedSize(horizontal: false, vertical: true)
            case .unexplained:
                Text("Explain asks the chosen model to name the cause from dated news it must cite. With no supporting article it says no catalyst was found.")
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// `Analysts · Medium confidence`, or the status.
    private func statusLine(_ event: MoveEvent) -> String {
        switch event.status {
        case .explained:
            return [event.categoryLabel ?? event.category, AnalysisFormat.confidenceLabel(event.confidence)]
                .compactMap { $0 }
                .joined(separator: " · ")
        case .pending: return "Explaining"
        case .failed: return "Explanation failed"
        case .unexplained: return "Not explained yet"
        }
    }

    /// `Explained by Opus · 2h ago`.
    private func byline(_ event: MoveEvent) -> String? {
        let parts = [event.modelName.map { "Explained by \($0)" }, event.explainedAt.map { fmtRelative($0) }].compactMap { $0 }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    @ViewBuilder
    private func sources(_ event: MoveEvent) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            sectionTitle("Sources")
            if event.sources.isEmpty {
                Text("No article was cited")
                    .font(DS.Font.body)
                    .foregroundStyle(DS.Text.secondary)
            } else {
                VStack(spacing: 0) {
                    ForEach(Array(event.sources.enumerated()), id: \.offset) { index, source in
                        if index > 0 { DSDivider() }
                        sourceRow(source)
                    }
                }
            }
        }
    }

    private func sourceRow(_ source: MoveSource) -> some View {
        let link = dashboardBrowserURL(source.url)
        let meta = [source.source.isEmpty ? nil : source.source, source.publishedAt.map { fmtDateTime($0) }]
            .compactMap { $0 }
            .joined(separator: " · ")
        return Button {
                if let link { browser = DashboardBrowserLink(url: link) }
            } label: {
                HStack(alignment: .center, spacing: 12) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(source.title.isEmpty ? source.url : source.title)
                            .font(DS.Font.body)
                            .foregroundStyle(DS.Text.primary)
                            .lineLimit(3)
                            .multilineTextAlignment(.leading)
                        if !meta.isEmpty {
                            Text(meta)
                                .font(DS.Font.caption)
                                .foregroundStyle(DS.Text.muted)
                                .lineLimit(1)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    if link != nil {
                        Image(systemName: "arrow.up.right")
                            .font(.system(size: 13))
                            .foregroundStyle(DS.Text.faint)
                            .accessibilityHidden(true)
                    }
                }
                .padding(.vertical, 12)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .disabled(link == nil)
            .accessibilityHint(link == nil ? "" : "Opens the article")
    }

    private func sectionTitle(_ text: String) -> some View {
        Text(text)
            .font(DS.Font.bodyMedium)
            .foregroundStyle(DS.Text.primary)
            .accessibilityAddTraits(.isHeader)
    }

    // MARK: Footer

    private func footer(_ event: MoveEvent) -> some View {
        VStack(spacing: 0) {
            DSDivider()
            Group {
                if !model.modelConfigured {
                    HStack(spacing: 12) {
                        Text("Choose a model in Settings to explain moves")
                            .font(DS.Font.caption)
                            .foregroundStyle(DS.Text.muted)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        Button("Open settings") {
                            dismiss()
                            onOpenSettings()
                        }
                        .buttonStyle(.dsSecondary)
                    }
                } else {
                    let busy = event.status == .pending || model.explainingIds.contains(event.id)
                    Button {
                        Task { await model.explain(event) }
                    } label: {
                        HStack(spacing: 8) {
                            if busy {
                                ProgressView()
                                    .controlSize(.small)
                                    .tint(DS.Palette.onAccent)
                            }
                            Text(busy ? "Explaining…" : AnalysisFormat.explainButtonTitle(event))
                        }
                    }
                    .buttonStyle(.dsPrimary(size: .large, fullWidth: true))
                    .disabled(busy || !model.canExplain(event))
                }
            }
            .padding(.horizontal, 24)
            .padding(.vertical, 12)
            if let error = model.actionError {
                Text(error)
                    .font(DS.Font.caption)
                    .foregroundStyle(DS.Palette.loss)
                    .padding(.horizontal, 24)
                    .padding(.bottom, 12)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }
}

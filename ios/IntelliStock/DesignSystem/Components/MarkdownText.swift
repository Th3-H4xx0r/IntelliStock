import SwiftUI

/// One rendered block of Markdown.
nonisolated enum MarkdownBlock: Hashable, Sendable {
    case heading(level: Int, text: AttributedString)
    case paragraph(AttributedString)
    /// A list paragraph. `marker` is "•" or "3." on an item's first
    /// paragraph and nil on its continuation paragraphs; `depth` 0 is
    /// top level.
    case listItem(marker: String?, depth: Int, text: AttributedString)
    case quote(AttributedString)
    case code(language: String?, text: String)
    case table(MarkdownTable)
    case rule
}

nonisolated struct MarkdownTable: Hashable, Sendable {
    enum Alignment: Hashable, Sendable {
        case leading, center, trailing
    }

    var alignments: [Alignment]
    var header: [AttributedString]
    var rows: [[AttributedString]]
}

/// Splits Markdown into blocks using Foundation's parser
/// (`AttributedString(markdown:options: .init(interpretedSyntax: .full))`),
/// which tags every run with its `presentationIntent` (innermost first).
nonisolated enum MarkdownBlocks {
    static func parse(_ markdown: String) -> [MarkdownBlock] {
        let options = AttributedString.MarkdownParsingOptions(
            allowsExtendedAttributes: false,
            interpretedSyntax: .full,
            failurePolicy: .returnPartiallyParsedIfPossible
        )
        guard let attributed = try? AttributedString(markdown: markdown, options: options) else {
            return markdown.isEmpty ? [] : [.paragraph(AttributedString(markdown))]
        }

        var blocks: [MarkdownBlock] = []
        var table: TableBuilder?
        var lastListItem: Int?

        func flushTable() {
            if let built = table?.build() { blocks.append(.table(built)) }
            table = nil
        }

        for (intent, range) in attributed.runs[\.presentationIntent] {
            var content = AttributedString(attributed[range])
            // Block structure lives in `MarkdownBlock`; keep only inline styling.
            content.presentationIntent = nil
            content.listItemDelimiter = nil
            let components = intent?.components ?? []

            if let tableComponent = components.first(where: { $0.kind.isTable }) {
                if table?.identity != tableComponent.identity {
                    flushTable()
                    table = TableBuilder(identity: tableComponent.identity, kind: tableComponent.kind)
                }
                table?.add(content, components: components)
                continue
            }
            flushTable()

            let kinds = components.map(\.kind)
            if let language = kinds.lazy.compactMap(\.codeLanguage).first {
                var text = String(content.characters)
                while text.hasSuffix("\n") { text.removeLast() }
                blocks.append(.code(language: language.isEmpty ? nil : language, text: text))
                lastListItem = nil
            } else if let level = kinds.lazy.compactMap(\.headerLevel).first {
                blocks.append(.heading(level: level, text: content))
                lastListItem = nil
            } else if kinds.contains(where: \.isThematicBreak) {
                blocks.append(.rule)
                lastListItem = nil
            } else if let itemIndex = components.firstIndex(where: { $0.kind.listOrdinal != nil }) {
                let item = components[itemIndex]
                let ordered = components[(itemIndex + 1)...].first(where: { $0.kind.isList })?.kind.isOrderedList ?? false
                let depth = components.filter { $0.kind.listOrdinal != nil }.count - 1
                let marker: String?
                if lastListItem == item.identity {
                    marker = nil
                } else {
                    marker = ordered ? "\(item.kind.listOrdinal ?? 1)." : "•"
                }
                lastListItem = item.identity
                blocks.append(.listItem(marker: marker, depth: depth, text: content))
            } else if kinds.contains(where: \.isBlockQuote) {
                blocks.append(.quote(content))
                lastListItem = nil
            } else {
                blocks.append(.paragraph(content))
                lastListItem = nil
            }
        }
        flushTable()
        return blocks
    }

    private struct TableBuilder {
        let identity: Int
        var alignments: [MarkdownTable.Alignment]
        var header: [Int: AttributedString] = [:]
        var rows: [Int: [Int: AttributedString]] = [:]

        init(identity: Int, kind: PresentationIntent.Kind) {
            self.identity = identity
            if case .table(let columns) = kind {
                alignments = columns.map { column in
                    switch column.alignment {
                    case .center: .center
                    case .right: .trailing
                    default: .leading
                    }
                }
            } else {
                alignments = []
            }
        }

        mutating func add(_ content: AttributedString, components: [PresentationIntent.IntentType]) {
            var column = 0
            var row: Int?
            for component in components {
                switch component.kind {
                case .tableCell(let index): column = index
                case .tableRow(let index): row = index
                default: break
                }
            }
            if let row {
                rows[row, default: [:]][column, default: AttributedString()] += content
            } else {
                header[column, default: AttributedString()] += content
            }
        }

        func build() -> MarkdownTable {
            let count = max(alignments.count, (header.keys.max() ?? -1) + 1)
            func line(_ cells: [Int: AttributedString]) -> [AttributedString] {
                (0..<count).map { cells[$0] ?? AttributedString() }
            }
            return MarkdownTable(
                alignments: (0..<count).map { $0 < alignments.count ? alignments[$0] : .leading },
                header: line(header),
                rows: rows.keys.sorted().map { line(rows[$0] ?? [:]) }
            )
        }
    }
}

nonisolated private extension PresentationIntent.Kind {
    var isTable: Bool {
        if case .table = self { return true }
        return false
    }

    var codeLanguage: String? {
        if case .codeBlock(let hint) = self { return hint ?? "" }
        return nil
    }

    var headerLevel: Int? {
        if case .header(let level) = self { return level }
        return nil
    }

    var isThematicBreak: Bool {
        if case .thematicBreak = self { return true }
        return false
    }

    var listOrdinal: Int? {
        if case .listItem(let ordinal) = self { return ordinal }
        return nil
    }

    var isList: Bool {
        switch self {
        case .orderedList, .unorderedList: true
        default: false
        }
    }

    var isOrderedList: Bool {
        if case .orderedList = self { return true }
        return false
    }

    var isBlockQuote: Bool {
        if case .blockQuote = self { return true }
        return false
    }
}

/// Renders Markdown block by block — the native form of `flutter_markdown`:
/// headings, paragraphs, bulleted and numbered lists, code blocks, quotes,
/// tables and rules, with inline bold, italic, code and links. Paragraphs
/// take the surrounding font, so callers style it like `Text`.
struct MarkdownText: View {
    let markdown: String

    init(_ markdown: String) {
        self.markdown = markdown
    }

    var body: some View {
        let blocks = MarkdownBlocks.parse(markdown)
        VStack(alignment: .leading, spacing: 10) {
            ForEach(blocks.indices, id: \.self) { index in
                MarkdownBlockView(block: blocks[index])
            }
        }
    }
}

private struct MarkdownBlockView: View {
    let block: MarkdownBlock

    var body: some View {
        switch block {
        case .heading(let level, let text):
            Text(text)
                .font(headingFont(level))
                .accessibilityAddTraits(.isHeader)
        case .paragraph(let text):
            Text(text)
        case .listItem(let marker, let depth, let text):
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(marker ?? "•")
                    .monospacedDigit()
                    .foregroundStyle(.secondary)
                    .opacity(marker == nil ? 0 : 1)
                    .accessibilityHidden(marker == nil)
                Text(text)
            }
            .padding(.leading, CGFloat(depth) * 16)
        case .quote(let text):
            HStack(spacing: 10) {
                RoundedRectangle(cornerRadius: 1.5)
                    .fill(Color(uiColor: .tertiaryLabel))
                    .frame(width: 3)
                Text(text)
                    .foregroundStyle(.secondary)
            }
            .fixedSize(horizontal: false, vertical: true)
        case .code(_, let text):
            ScrollView(.horizontal, showsIndicators: false) {
                Text(text)
                    .font(.system(.footnote, design: .monospaced))
                    .textSelection(.enabled)
                    .padding(12)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(DS.Surface.inset, in: .rect(cornerRadius: DS.Radius.small, style: .continuous))
        case .table(let table):
            ScrollView(.horizontal, showsIndicators: false) {
                Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 6) {
                    GridRow {
                        ForEach(table.header.indices, id: \.self) { column in
                            Text(table.header[column])
                                .font(.subheadline.weight(.semibold))
                                .gridColumnAlignment(alignment(table, column))
                        }
                    }
                    Divider()
                    ForEach(table.rows.indices, id: \.self) { row in
                        GridRow {
                            ForEach(table.rows[row].indices, id: \.self) { column in
                                Text(table.rows[row][column])
                                    .font(.subheadline)
                            }
                        }
                    }
                }
                .padding(12)
            }
            .background(DS.Surface.inset, in: .rect(cornerRadius: DS.Radius.small, style: .continuous))
        case .rule:
            Divider()
        }
    }

    private func headingFont(_ level: Int) -> Font {
        switch level {
        case 1: .title2.bold()
        case 2: .title3.bold()
        case 3: .headline
        default: .subheadline.weight(.semibold)
        }
    }

    private func alignment(_ table: MarkdownTable, _ column: Int) -> HorizontalAlignment {
        switch table.alignments[column] {
        case .leading: .leading
        case .center: .center
        case .trailing: .trailing
        }
    }
}

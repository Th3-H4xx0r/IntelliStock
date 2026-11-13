import Foundation
import SwiftUI
import Testing
import UIKit
@testable import IntelliStock

/// The behavioural parts of test/widgets_test.dart and
/// test/core/widgets/relative_time_text_test.dart, plus the component logic
/// the native forms carry.
struct DesignSystemTests {
    // MARK: StatusBadge

    /// The web's status map (`ui-dot-live|warn|off|error`): green only for
    /// what is running, zinc-400 for done, amber for paused, zinc-600 for
    /// stopped or queued, red for failed.
    @Test func statusColorsMapKnownStatuses() {
        #expect(StatusBadge.color(forStatus: "running") == DS.Palette.gain)
        #expect(StatusBadge.color(forStatus: "failed") == DS.Palette.loss)
        #expect(StatusBadge.color(forStatus: "paused") == DS.Palette.amber)
        #expect(StatusBadge.color(forStatus: "queued") == DS.Text.faint)
        #expect(StatusBadge.color(forStatus: "whatever") == DS.Text.faint)
    }

    @Test func statusColorsCoverEveryDartCase() {
        for s in ["running", "active", "passed", "RUNNING"] {
            #expect(StatusBadge.color(forStatus: s) == DS.Palette.gain)
        }
        for s in ["completed", "finished", "building"] {
            #expect(StatusBadge.color(forStatus: s) == DS.Text.secondary)
        }
        #expect(StatusBadge.color(forStatus: "building") == DS.Palette.info)
        for s in ["paused", "paused_llm_critical"] {
            #expect(StatusBadge.color(forStatus: s) == DS.Palette.warning)
        }
        for s in ["queued", "pending", "stopped", "cancelled"] {
            #expect(StatusBadge.color(forStatus: s) == DS.Text.faint)
        }
        for s in ["error", "failed", "aborted_llm_failure"] {
            #expect(StatusBadge.color(forStatus: s) == DS.Palette.danger)
        }
        #expect(StatusBadge.color(forStatus: nil) == DS.Text.faint)
    }

    /// A legacy colour argument picks the matching dot.
    @Test func statusToneFollowsALegacyColour() {
        #expect(DSStatusTone(color: DS.Palette.success) == .live)
        #expect(DSStatusTone(color: DS.Palette.warning) == .warn)
        #expect(DSStatusTone(color: DS.Palette.danger) == .error)
        #expect(DSStatusTone(color: DS.Palette.info) == .neutral)
        #expect(DSStatusTone(color: Color.secondary) == .off)
        #expect(DSStatusTone.live.wordColor == DS.Text.soft)
        #expect(DSStatusTone.off.wordColor == DS.Text.muted)
    }

    // MARK: TypedConfirmField

    /// widgets_test.dart: fires a match only on the exact phrase.
    @Test func typedConfirmFiresOnlyOnTheExactPhrase() {
        var matcher = TypedConfirmMatcher(phrase: "HALT")
        var matches: [Bool] = []
        func type(_ text: String) {
            if let changed = matcher.update(text) { matches.append(changed) }
        }
        type("HAL")
        #expect(matches.isEmpty) // never matched yet
        type("HALT")
        #expect(matches.last == true)
        type("HALTx")
        #expect(matches.last == false)
        type(" HALT ")
        #expect(matches == [true, false, true])
    }

    // MARK: RelativeTimeText

    @Test func relativeTimeTicksAsTheClockAdvances() {
        let ts = Date(timeIntervalSince1970: 1_750_000_000)
        #expect(RelativeTimeText.label(for: ts, now: ts + 120) == "2m ago")
        #expect(RelativeTimeText.label(for: ts, now: ts + 300) == "5m ago")
    }

    @Test func relativeTimeShowsJustNowForAFreshTimestamp() {
        let now = Date(timeIntervalSince1970: 1_750_000_000)
        #expect(RelativeTimeText.label(for: now - 5, now: now) == "Just now")
    }

    // MARK: BrokerageLogo

    @Test func brokerageFallbackSymbols() {
        #expect(BrokerageLogo.fallbackSymbol("Alpaca") == Symbol.named("show_chart"))
        #expect(BrokerageLogo.fallbackSymbol("robinhood") == Symbol.named("savings"))
        #expect(BrokerageLogo.assetTypes == ["alpaca", "kalshi", "binanceus"])
    }

    @MainActor
    @Test func brandAssetsShipInTheCatalog() {
        for type in BrokerageLogo.assetTypes {
            #expect(UIImage(named: "Brand/\(type)") != nil)
        }
        #expect(UIImage(named: "AppLogo") != nil)
    }

    // MARK: Loadable

    @Test func loadableAccessors() async {
        struct Boom: Error {}
        let loaded = await Loadable<Int>.capture { 3 }
        #expect(loaded.value == 3)
        #expect(!loaded.isLoading)
        #expect(loaded.map { $0 * 2 }.value == 6)
        let failed = await Loadable<Int>.capture { throw ApiError(message: "nope") }
        #expect(failed.value == nil)
        #expect(failed.errorMessage == "nope")
        #expect(Loadable<Int>.loading.isLoading)
    }
}

/// `MarkdownText`'s block parser.
struct MarkdownBlocksTests {
    @Test func splitsEveryBlockKind() {
        let blocks = MarkdownBlocks.parse("""
        # Title
        Some **bold** text
        next line.

        - one
        - two
          - nested

        1. first
        2. second

        > quoted

        ```python
        print("hi")
        ```

        | A | B |
        |---|--:|
        | 1 | 2 |

        ---
        End.
        """)

        guard blocks.count == 12 else {
            Issue.record("expected 12 blocks, got \(blocks.count): \(blocks)")
            return
        }
        #expect(blocks[0] == .heading(level: 1, text: "Title"))
        if case .paragraph(let text) = blocks[1] {
            #expect(String(text.characters) == "Some bold text next line.")
        } else {
            Issue.record("expected a paragraph")
        }
        #expect(blocks[2] == .listItem(marker: "•", depth: 0, text: "one"))
        #expect(blocks[3] == .listItem(marker: "•", depth: 0, text: "two"))
        #expect(blocks[4] == .listItem(marker: "•", depth: 1, text: "nested"))
        #expect(blocks[5] == .listItem(marker: "1.", depth: 0, text: "first"))
        #expect(blocks[6] == .listItem(marker: "2.", depth: 0, text: "second"))
        #expect(blocks[7] == .quote("quoted"))
        #expect(blocks[8] == .code(language: "python", text: "print(\"hi\")"))
        if case .table(let table) = blocks[9] {
            #expect(table.header.map { String($0.characters) } == ["A", "B"])
            #expect(table.rows.map { $0.map { String($0.characters) } } == [["1", "2"]])
            #expect(table.alignments == [.leading, .trailing])
        } else {
            Issue.record("expected a table")
        }
        #expect(blocks[10] == .rule)
        #expect(blocks[11] == .paragraph("End."))
    }

    @Test func boldSurvivesAsAnInlineIntent() throws {
        let blocks = MarkdownBlocks.parse("a **b** c")
        guard case .paragraph(let text) = try #require(blocks.first) else {
            Issue.record("expected a paragraph")
            return
        }
        let bold = text.runs.contains { $0.inlinePresentationIntent?.contains(.stronglyEmphasized) == true }
        #expect(bold)
    }

    @Test func plainTextIsOneParagraph() {
        #expect(MarkdownBlocks.parse("hello") == [.paragraph("hello")])
        #expect(MarkdownBlocks.parse("").isEmpty)
    }
}

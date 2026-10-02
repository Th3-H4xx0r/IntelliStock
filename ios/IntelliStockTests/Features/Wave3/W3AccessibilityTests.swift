import Foundation
import Testing
@testable import IntelliStock

// Wave 3 finding 10 and the accessibility minors.

/// Finding 10: a screen with a bottom floating layer hides the chat button
/// while it is on screen.
@MainActor
@Suite struct W3ChatDockChromeTests {
    @Test func theButtonHidesWhileAnyFloatingScreenIsOnScreen() {
        let chrome = ChatDockChrome()
        #expect(!chrome.hidesButton)
        let live = UUID()
        let other = UUID()
        chrome.hide(for: live)
        #expect(chrome.hidesButton)
        chrome.hide(for: other)
        chrome.show(for: live)
        #expect(chrome.hidesButton)
        chrome.show(for: other)
        #expect(!chrome.hidesButton)
        // A second disappear is harmless.
        chrome.show(for: other)
        #expect(!chrome.hidesButton)
    }

    @Test func servicesOwnOneChrome() {
        let services = AppServices(apiClient: DataStub().client)
        #expect(!services.chatDock.hidesButton)
    }
}

/// The 11 pt floor for `minimumScaleFactor`.
@Suite struct W3TextFloorTests {
    @Test func captionTwoAtTheDefaultSizeNeverShrinks() {
        #expect(DSTextFloor.factor(0.8, pointSize: 11) == 1)
        #expect(DSTextFloor.factor(0.7, pointSize: 11) == 1)
    }

    @Test func largerTextShrinksOnlyDownToElevenPoints() {
        let f = DSTextFloor.factor(0.5, pointSize: 22)
        #expect(f == 0.5)
        let g = DSTextFloor.factor(0.5, pointSize: 20)
        #expect(abs(g * 20 - 11) < 0.0001)
        #expect(DSTextFloor.factor(0.9, pointSize: 40) == 0.9)
    }

    @Test func aDegenerateSizeDoesNotShrink() {
        #expect(DSTextFloor.factor(0.5, pointSize: 0) == 1)
    }
}

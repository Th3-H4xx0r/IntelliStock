import Foundation
import Observation

/// The brokerage account id the dashboard hero shows, ported from
/// features/dashboard/application/selected_account_controller.dart.
///
/// Persisted on-device under `dashboard_selected_account` (the same keychain
/// item the Flutter build wrote), so the dashboard reopens on whichever
/// account was last selected. nil means "fall back to the first account".
@Observable
final class SelectedAccountModel {
    static let storageKey = "dashboard_selected_account"

    @ObservationIgnored private let store: any SecureStorage

    private(set) var selectedId: String?

    /// Dart's `build()` returned null and then hydrated from storage
    /// asynchronously; the keychain read is synchronous here, so the stored
    /// selection is in place before the first frame.
    init(store: any SecureStorage = KeychainStore()) {
        self.store = store
        reload()
    }

    /// Re-reads the stored selection — at launch, and again once the
    /// keychain becomes readable if the app launched before first unlock.
    func reload() {
        if let raw = store.read(Self.storageKey), !raw.isEmpty {
            selectedId = raw
        }
    }

    func select(_ id: String) {
        selectedId = id
        // Best-effort, as in Dart: the selection is a convenience.
        try? store.write(Self.storageKey, id)
    }
}

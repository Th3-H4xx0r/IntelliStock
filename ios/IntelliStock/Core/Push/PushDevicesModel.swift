import Foundation
import Observation

/// The current user's registered push devices — `PushDevicesController` in
/// `push_devices_controller.dart`. The notification settings screen owns one.
@Observable
final class PushDevicesModel {
    private(set) var devices: Loadable<[PushDevice]> = .loading

    @ObservationIgnored private let repository: () -> PushRepository

    init(repository: @escaping () -> PushRepository) {
        self.repository = repository
    }

    /// The first load (Dart's `build`).
    func load() async {
        devices = await Loadable.capture { try await self.repository().listDevices() }
    }

    /// Shows loading, then reloads (Dart's `refresh`).
    func refresh() async {
        devices = .loading
        await load()
    }
}

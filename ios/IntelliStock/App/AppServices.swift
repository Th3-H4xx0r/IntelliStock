import Observation

/// App-wide services, injected with `.environment(services)`. Wave 0 holds
/// only the router and the API client; Wave 1 adds the session, the server-URL
/// store, the lock, push and the repositories.
@Observable
final class AppServices {
    let router = AppRouter()
    var apiClient: ApiClient

    init(apiClient: ApiClient = ApiClient(baseURL: "", tokens: nil)) {
        self.apiClient = apiClient
    }
}

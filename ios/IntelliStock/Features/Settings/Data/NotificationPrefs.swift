import Foundation

// Per-category notification routing preferences (Discord and/or iOS push),
// ported from features/settings/data/models/notification_prefs.dart.
//
// Mirrors the backend `/notification-preferences` shape:
// `{ "categories": { "<key>": {"discord": bool, "push": bool}, ... } }`.

nonisolated struct CategoryRoute: Hashable, Sendable {
    let discord: Bool
    let push: Bool

    init(discord: Bool, push: Bool) {
        self.discord = discord
        self.push = push
    }

    init(json j: JSON) {
        self.init(discord: j["discord"].boolValue ?? true, push: j["push"].boolValue ?? false)
    }

    func toJSON() -> JSON { ["discord": .bool(discord), "push": .bool(push)] }

    func copyWith(discord: Bool? = nil, push: Bool? = nil) -> CategoryRoute {
        CategoryRoute(discord: discord ?? self.discord, push: push ?? self.push)
    }
}

/// Taxonomy metadata for one notification type (from the API `types` array).
nonisolated struct NotificationType: Hashable, Sendable {
    let key: String
    let group: String
    let label: String
    let desc: String

    init(key: String, group: String, label: String, desc: String) {
        self.key = key
        self.group = group
        self.label = label
        self.desc = desc
    }

    init(json j: JSON) {
        self.init(
            key: j["key"].stringOr(""),
            group: j["group"].stringOr("Other"),
            label: j["label"].or(j["key"]).stringOr(""),
            desc: j["desc"].stringOr("")
        )
    }
}

nonisolated struct NotificationPrefs: Hashable, Sendable {
    let categories: [String: CategoryRoute]
    /// Ordered taxonomy (key/group/label/desc) for grouped rendering.
    let types: [NotificationType]

    init(categories: [String: CategoryRoute], types: [NotificationType] = []) {
        self.categories = categories
        self.types = types
    }

    init(json j: JSON) {
        var cats: [String: CategoryRoute] = [:]
        for (k, v) in j["categories"].objectValue where v.isObject {
            cats[k] = CategoryRoute(json: v)
        }
        self.init(categories: cats, types: j["types"].objectElements.map(NotificationType.init(json:)))
    }

    func toJSON() -> JSON {
        ["categories": .object(categories.mapValues { $0.toJSON() })]
    }

    /// Group names in first-appearance (display) order.
    var groupsInOrder: [String] {
        var seen: [String] = []
        for t in types where !seen.contains(t.group) {
            seen.append(t.group)
        }
        return seen
    }

    func typesInGroup(_ group: String) -> [NotificationType] {
        types.filter { $0.group == group }
    }

    /// A copy with `category`'s route replaced (immutable update).
    func withRoute(_ category: String, _ route: CategoryRoute) -> NotificationPrefs {
        var next = categories
        next[category] = route
        return NotificationPrefs(categories: next, types: types)
    }

    /// Route for a category, defaulting to Discord-only if absent.
    func routeFor(_ category: String) -> CategoryRoute {
        categories[category] ?? CategoryRoute(discord: true, push: false)
    }
}

/// Channel selector used by toggles + the send-test buttons.
nonisolated enum NotifChannel: String, Hashable, Sendable {
    case discord, push
}

/// Fallback display metadata used when the API taxonomy is unavailable.
nonisolated struct NotificationCategoryMeta: Hashable, Sendable {
    let key: String
    let label: String
    let description: String

    init(_ key: String, _ label: String, _ description: String) {
        self.key = key
        self.label = label
        self.description = description
    }
}

nonisolated let kNotificationCategories: [NotificationCategoryMeta] = [
    NotificationCategoryMeta("order_submit", "Order submitted", "An order was sent to the broker"),
    NotificationCategoryMeta("order_fill", "Order filled", "An order was filled"),
    NotificationCategoryMeta("order_reject", "Order rejected", "The broker rejected an order"),
    NotificationCategoryMeta(
        "order_retry", "Order retried", "An order is being retried after a recoverable reject"
    ),
    NotificationCategoryMeta(
        "strategy_start", "Strategy start", "A strategy fired its first run of the session"
    ),
    NotificationCategoryMeta(
        "strategy_error", "Strategy error", "An unrecoverable strategy error occurred"
    ),
    NotificationCategoryMeta("halt", "Halt", "Live trading was halted"),
    NotificationCategoryMeta("drawdown_halt", "Drawdown halt", "A drawdown risk-off guard tripped"),
    NotificationCategoryMeta("crash_loop", "Crash loop", "The broker subprocess entered a crash loop"),
    NotificationCategoryMeta(
        "instance_crash",
        "Instance crashed",
        "An instance process died (not a Stop) and was held open for log capture"
    ),
    // Swing & Wheel (backend/notification_types.py, same order and wording).
    NotificationCategoryMeta("swing_entry", "Swing entry", "The swing lane sent a bracket buy"),
    NotificationCategoryMeta(
        "swing_pending_review",
        "Swing review needed",
        "A swing candidate scored 50-74 and waits for your approval"
    ),
    NotificationCategoryMeta(
        "swing_exit",
        "Swing exit",
        "The swing lane sold a position; or an exit was not placed or its "
            + "outcome is unknown, so the position may be unprotected"
    ),
    NotificationCategoryMeta(
        "swing_run_summary",
        "Swing & wheel run summary",
        "A swing or wheel scan finished; AI rejects and bear-mode notes"
    ),
    NotificationCategoryMeta("wheel_put_placed", "Wheel put sent", "The wheel lane sent a cash-secured put"),
    NotificationCategoryMeta(
        "wheel_pending_review",
        "Wheel review needed",
        "A wheel candidate scored 50-74 and waits for your approval"
    ),
    NotificationCategoryMeta(
        "wheel_position_alert",
        "Wheel position alert",
        "A short put is in the money, near expiry, has no price, or is being "
            + "bought back; or assigned shares are a covered-call candidate (dry run)"
    ),
    NotificationCategoryMeta(
        "wheel_assignment", "Wheel assignment", "A put was assigned and its shares are now held"
    ),
    NotificationCategoryMeta(
        "swing_approval_failed",
        "Approved order refused or unconfirmed",
        "A swing or wheel order you approved was not sent, may not have been "
            + "placed, or WAS placed though its signal reads failed"
    ),
]

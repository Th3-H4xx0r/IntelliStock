/// Per-category notification routing preferences (Discord and/or iOS push).
///
/// Mirrors the backend `/notification-preferences` shape:
/// `{ "categories": { "<key>": {"discord": bool, "push": bool}, ... } }`.
class CategoryRoute {
  const CategoryRoute({required this.discord, required this.push});

  final bool discord;
  final bool push;

  factory CategoryRoute.fromJson(Map j) => CategoryRoute(
        discord: j['discord'] as bool? ?? true,
        push: j['push'] as bool? ?? false,
      );

  Map<String, dynamic> toJson() => {'discord': discord, 'push': push};

  CategoryRoute copyWith({bool? discord, bool? push}) => CategoryRoute(
        discord: discord ?? this.discord,
        push: push ?? this.push,
      );
}

class NotificationPrefs {
  const NotificationPrefs({required this.categories});

  final Map<String, CategoryRoute> categories;

  factory NotificationPrefs.fromJson(Map j) {
    final raw = j['categories'];
    final cats = <String, CategoryRoute>{};
    if (raw is Map) {
      raw.forEach((k, v) {
        if (v is Map) cats[k.toString()] = CategoryRoute.fromJson(v);
      });
    }
    return NotificationPrefs(categories: cats);
  }

  Map<String, dynamic> toJson() => {
        'categories': {
          for (final e in categories.entries) e.key: e.value.toJson(),
        },
      };

  /// Return a copy with [category]'s route replaced (immutable update).
  NotificationPrefs withRoute(String category, CategoryRoute route) {
    final next = Map<String, CategoryRoute>.from(categories);
    next[category] = route;
    return NotificationPrefs(categories: next);
  }

  /// Route for a category, defaulting to Discord-only if absent.
  CategoryRoute routeFor(String category) =>
      categories[category] ?? const CategoryRoute(discord: true, push: false);
}

/// Channel selector used by toggles + the send-test buttons.
enum NotifChannel { discord, push }

/// Display metadata for the 9 categories (ordered for the settings list).
class NotificationCategoryMeta {
  const NotificationCategoryMeta(this.key, this.label, this.description);
  final String key;
  final String label;
  final String description;
}

const kNotificationCategories = <NotificationCategoryMeta>[
  NotificationCategoryMeta('order_submit', 'Order submitted', 'An order was sent to the broker'),
  NotificationCategoryMeta('order_fill', 'Order filled', 'An order was filled'),
  NotificationCategoryMeta('order_reject', 'Order rejected', 'The broker rejected an order'),
  NotificationCategoryMeta('order_retry', 'Order retried', 'An order is being retried after a recoverable reject'),
  NotificationCategoryMeta('strategy_start', 'Strategy start', 'A strategy fired its first run of the session'),
  NotificationCategoryMeta('strategy_error', 'Strategy error', 'An unrecoverable strategy error occurred'),
  NotificationCategoryMeta('halt', 'Halt', 'Live trading was halted'),
  NotificationCategoryMeta('drawdown_halt', 'Drawdown halt', 'A drawdown risk-off guard tripped'),
  NotificationCategoryMeta('crash_loop', 'Crash loop', 'The broker subprocess entered a crash loop'),
];

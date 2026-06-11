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

/// Taxonomy metadata for one notification type (from the API `types` array).
class NotificationType {
  const NotificationType({
    required this.key,
    required this.group,
    required this.label,
    required this.desc,
  });
  final String key;
  final String group;
  final String label;
  final String desc;

  factory NotificationType.fromJson(Map j) => NotificationType(
        key: (j['key'] ?? '').toString(),
        group: (j['group'] ?? 'Other').toString(),
        label: (j['label'] ?? j['key'] ?? '').toString(),
        desc: (j['desc'] ?? '').toString(),
      );
}

class NotificationPrefs {
  const NotificationPrefs({required this.categories, this.types = const []});

  final Map<String, CategoryRoute> categories;

  /// Ordered taxonomy (key/group/label/desc) for grouped rendering.
  final List<NotificationType> types;

  factory NotificationPrefs.fromJson(Map j) {
    final raw = j['categories'];
    final cats = <String, CategoryRoute>{};
    if (raw is Map) {
      raw.forEach((k, v) {
        if (v is Map) cats[k.toString()] = CategoryRoute.fromJson(v);
      });
    }
    final rawTypes = j['types'];
    final types = <NotificationType>[];
    if (rawTypes is List) {
      for (final t in rawTypes) {
        if (t is Map) types.add(NotificationType.fromJson(t));
      }
    }
    return NotificationPrefs(categories: cats, types: types);
  }

  Map<String, dynamic> toJson() => {
        'categories': {
          for (final e in categories.entries) e.key: e.value.toJson(),
        },
      };

  /// Group names in first-appearance (display) order.
  List<String> get groupsInOrder {
    final seen = <String>[];
    for (final t in types) {
      if (!seen.contains(t.group)) seen.add(t.group);
    }
    return seen;
  }

  List<NotificationType> typesInGroup(String group) =>
      types.where((t) => t.group == group).toList();

  /// Return a copy with [category]'s route replaced (immutable update).
  NotificationPrefs withRoute(String category, CategoryRoute route) {
    final next = Map<String, CategoryRoute>.from(categories);
    next[category] = route;
    return NotificationPrefs(categories: next, types: types);
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

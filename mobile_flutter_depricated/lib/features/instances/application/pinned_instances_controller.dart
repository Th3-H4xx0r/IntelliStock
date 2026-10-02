import 'dart:convert';
import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/network/session.dart'; // secureStorageProvider
import '../data/models/instance.dart';

const _kPinnedKey = 'pinned_instances';

/// Locally-persisted set of pinned instance IDs. Pinned instances sort to the
/// top of the list. Stored on-device (a per-device view preference).
class PinnedInstancesController extends Notifier<Set<String>> {
  @override
  Set<String> build() {
    _load(); // async hydrate; state starts empty then updates
    return <String>{};
  }

  Future<void> _load() async {
    try {
      final raw = await ref.read(secureStorageProvider).read(key: _kPinnedKey);
      if (raw != null && raw.isNotEmpty) {
        final decoded = jsonDecode(raw);
        if (decoded is List) {
          state = decoded.map((e) => e.toString()).toSet();
        }
      }
    } catch (_) {
      // ignore — pins are a best-effort convenience
    }
  }

  Future<void> toggle(String id) async {
    final next = Set<String>.from(state);
    if (next.contains(id)) {
      next.remove(id);
    } else {
      next.add(id);
    }
    state = next;
    try {
      await ref
          .read(secureStorageProvider)
          .write(key: _kPinnedKey, value: jsonEncode(next.toList()));
    } catch (_) {
      // ignore
    }
  }
}

final pinnedInstancesProvider =
    NotifierProvider<PinnedInstancesController, Set<String>>(
  PinnedInstancesController.new,
);

/// Returns [items] with pinned instances first, preserving each group's
/// original relative order (stable). Unmodified when nothing is pinned.
List<Instance> sortPinnedFirst(List<Instance> items, Set<String> pinned) {
  if (pinned.isEmpty) return items;
  final pinnedItems = <Instance>[];
  final rest = <Instance>[];
  for (final i in items) {
    if (pinned.contains(i.id)) {
      pinnedItems.add(i);
    } else {
      rest.add(i);
    }
  }
  return [...pinnedItems, ...rest];
}

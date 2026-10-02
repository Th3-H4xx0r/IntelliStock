import 'package:flutter_riverpod/flutter_riverpod.dart';
import '../../../core/network/session.dart'; // secureStorageProvider

const _kSelectedAccountKey = 'dashboard_selected_account';

/// The brokerage account id the dashboard hero shows. Persisted on-device so
/// the dashboard reopens on whichever account you last selected. Starts null
/// (the section falls back to the first account) then hydrates from storage.
class SelectedAccountController extends Notifier<String?> {
  @override
  String? build() {
    _load();
    return null;
  }

  Future<void> _load() async {
    try {
      final raw =
          await ref.read(secureStorageProvider).read(key: _kSelectedAccountKey);
      if (raw != null && raw.isNotEmpty) state = raw;
    } catch (_) {
      // best-effort — selection is a convenience
    }
  }

  Future<void> select(String id) async {
    state = id;
    try {
      await ref
          .read(secureStorageProvider)
          .write(key: _kSelectedAccountKey, value: id);
    } catch (_) {
      // ignore
    }
  }
}

final selectedAccountProvider =
    NotifierProvider<SelectedAccountController, String?>(
  SelectedAccountController.new,
);

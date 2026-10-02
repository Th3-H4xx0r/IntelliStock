import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/features/instances/application/pinned_instances_controller.dart';
import 'package:intellistock_mobile/features/instances/data/models/instance.dart';

Instance _inst(String id) => Instance(id: id, name: id, createdBy: 'user', runCommand: false);

void main() {
  group('sortPinnedFirst', () {
    test('floats pinned instances to the top, preserving each group order', () {
      final items = [_inst('a'), _inst('b'), _inst('c'), _inst('d')];
      final out = sortPinnedFirst(items, {'c', 'a'});
      // pinned (a, c — original relative order) then the rest (b, d)
      expect(out.map((i) => i.id).toList(), ['a', 'c', 'b', 'd']);
    });

    test('returns the same list (unchanged) when nothing is pinned', () {
      final items = [_inst('a'), _inst('b')];
      expect(identical(sortPinnedFirst(items, <String>{}), items), isTrue);
    });

    test('ignores pinned ids not present in the list', () {
      final out = sortPinnedFirst([_inst('a'), _inst('b')], {'zzz'});
      expect(out.map((i) => i.id).toList(), ['a', 'b']);
    });

    test('all pinned keeps original order', () {
      final out = sortPinnedFirst([_inst('a'), _inst('b')], {'a', 'b'});
      expect(out.map((i) => i.id).toList(), ['a', 'b']);
    });
  });
}

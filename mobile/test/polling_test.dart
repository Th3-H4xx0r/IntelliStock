import 'package:fake_async/fake_async.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/core/polling/poller.dart';
import 'package:intellistock_mobile/core/polling/log_tailer.dart';

void main() {
  group('IntervalPoller', () {
    test('fires on each interval', () {
      fakeAsync((async) {
        var count = 0;
        final p = IntervalPoller(
          fetch: () async => count++,
          interval: () => const Duration(seconds: 5),
        );
        p.start();
        async.elapse(const Duration(seconds: 16));
        p.dispose();
        expect(count, 3); // at 5s, 10s, 15s
      });
    });

    test('pause stops firing, resume restarts', () {
      fakeAsync((async) {
        var count = 0;
        final p = IntervalPoller(
          fetch: () async => count++,
          interval: () => const Duration(seconds: 5),
        );
        p.start();
        async.elapse(const Duration(seconds: 6)); // 1 fire
        p.pause();
        async.elapse(const Duration(seconds: 20)); // no fires
        expect(count, 1);
        p.resume();
        async.elapse(const Duration(seconds: 6)); // 1 more
        p.dispose();
        expect(count, 2);
      });
    });

    test('dispose stops firing', () {
      fakeAsync((async) {
        var count = 0;
        final p = IntervalPoller(
          fetch: () async => count++,
          interval: () => const Duration(seconds: 5),
        );
        p.start();
        p.dispose();
        async.elapse(const Duration(seconds: 30));
        expect(count, 0);
      });
    });
  });

  group('parseLogLine', () {
    test('extracts timestamp and message', () {
      final l = parseLogLine('[2026-01-01 12:00:00] hello world');
      expect(l.message, 'hello world');
    });
    test('classifies error', () {
      expect(parseLogLine('Traceback: boom').level, LogLevel.error);
    });
    test('classifies warn', () {
      expect(parseLogLine('retrying connection').level, LogLevel.warn);
    });
    test('classifies success', () {
      expect(parseLogLine('build completed').level, LogLevel.success);
    });
    test('normal otherwise', () {
      expect(parseLogLine('just a line').level, LogLevel.normal);
    });
  });
}

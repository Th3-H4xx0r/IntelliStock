import 'package:flutter_test/flutter_test.dart';
import 'package:intellistock_mobile/features/agent_runs/application/agent_runs_controller.dart';
import 'package:intellistock_mobile/features/agent_runs/data/agent_repository.dart';

// ── AgentControl model ────────────────────────────────────────────────────────

void main() {
  group('AgentControl.fromJson', () {
    test('running=true, paused=false → isRunning', () {
      final ctrl = AgentControl.fromJson({'running': true, 'paused': false});
      expect(ctrl.isRunning, isTrue);
      expect(ctrl.isPaused, isFalse);
      expect(ctrl.isStopped, isFalse);
    });

    test('running=true, paused=true → isPaused', () {
      final ctrl = AgentControl.fromJson({'running': true, 'paused': true});
      expect(ctrl.isPaused, isTrue);
      expect(ctrl.isRunning, isFalse);
    });

    test('running=false → isStopped', () {
      final ctrl = AgentControl.fromJson({'running': false, 'paused': false});
      expect(ctrl.isStopped, isTrue);
      expect(ctrl.isRunning, isFalse);
      expect(ctrl.isPaused, isFalse);
    });

    test('empty map → stopped (defaults to false/false)', () {
      final ctrl = AgentControl.fromJson({});
      expect(ctrl.isStopped, isTrue);
    });
  });

  // ── AgentRun.fromJson ──────────────────────────────────────────────────────

  group('AgentRun.fromJson', () {
    test('parses all standard fields', () {
      final run = AgentRun.fromJson({
        'id': 'run-abc',
        'status': 'completed',
        'cycle_id': 'cycle-01',
        'name': 'Morning run',
        'created_at': '2026-06-10T09:00:00Z',
        'final_result': 'COMPLETED',
        'stages': [],
      });
      expect(run.id, 'run-abc');
      expect(run.status, 'completed');
      expect(run.cycleId, 'cycle-01');
      expect(run.name, 'Morning run');
      expect(run.finalResult, 'COMPLETED');
      expect(run.stages, isEmpty);
    });

    test('tolerates missing optional fields', () {
      final run = AgentRun.fromJson({'id': 'x'});
      expect(run.id, 'x');
      expect(run.status, 'stopped');
      expect(run.cycleId, isNull);
      expect(run.stages, isEmpty);
    });

    test('parses nested stages', () {
      final run = AgentRun.fromJson({
        'id': 'r1',
        'stages': [
          {
            'label': 'Stock selection',
            'status': 'completed',
            'stocks': ['AAPL', 'TSLA'],
            'pnl': 250.0,
            'pnl_pct': 2.5,
          },
          {
            'label': 'Risk check',
            'status': 'running',
          }
        ],
      });
      expect(run.stages.length, 2);
      expect(run.stages[0].label, 'Stock selection');
      expect(run.stages[0].pnl, closeTo(250.0, 0.01));
      expect(run.stages[0].stocks, ['AAPL', 'TSLA']);
      expect(run.stages[1].pnl, isNull);
    });
  });

  // ── AgentStage.fromJson ────────────────────────────────────────────────────

  group('AgentStage.fromJson', () {
    test('parses pnl and pnlPct correctly', () {
      final stage = AgentStage.fromJson({
        'label': 'Eval',
        'status': 'completed',
        'pnl': -150.75,
        'pnl_pct': -1.5,
        'stocks': ['NVDA'],
        'details': 'All clear',
      });
      expect(stage.label, 'Eval');
      expect(stage.pnl, closeTo(-150.75, 0.01));
      expect(stage.pnlPct, closeTo(-1.5, 0.001));
      expect(stage.details, 'All clear');
    });

    test('null pnl and pnlPct tolerated', () {
      final stage = AgentStage.fromJson({'label': 'Start'});
      expect(stage.pnl, isNull);
      expect(stage.pnlPct, isNull);
      expect(stage.stocks, isEmpty);
    });
  });

  // ── AgentRunsPage.fromJson ─────────────────────────────────────────────────

  group('AgentRunsPage.fromJson', () {
    test('parses pagination metadata', () {
      final page = AgentRunsPage.fromJson({
        'runs': [],
        'total': 47,
        'total_pages': 3,
        'page': 2,
      });
      expect(page.total, 47);
      expect(page.totalPages, 3);
      expect(page.page, 2);
      expect(page.runs, isEmpty);
    });

    test('defaults: page=1, total_pages=1 when missing', () {
      final page = AgentRunsPage.fromJson({'runs': []});
      expect(page.page, 1);
      expect(page.totalPages, 1);
      expect(page.total, 0);
    });
  });

  // ── AgentRunsState countdown helpers ──────────────────────────────────────

  group('AgentRunsState countdown', () {
    test('countdownFraction = 0 when no scheduled resume', () {
      const state = AgentRunsState();
      expect(state.countdownFraction, 0.0);
    });

    test('countdownSecsRemaining = 0 when no scheduled resume', () {
      const state = AgentRunsState();
      expect(state.countdownSecsRemaining, 0);
    });

    test('countdownFraction is between 0 and 1 during active countdown', () {
      final resumeAt = DateTime.now().add(const Duration(seconds: 30));
      const totalMs = 60000; // 60s total
      final state = AgentRunsState(
        scheduledResumeAt: resumeAt,
        scheduledTotalMs: totalMs,
      );
      // 30s elapsed out of 60s → ~0.5 fraction
      expect(state.countdownFraction, greaterThanOrEqualTo(0.0));
      expect(state.countdownFraction, lessThanOrEqualTo(1.0));
    });

    test('countdownFraction approaches 1 as time elapses', () {
      // Nearly expired: resume 1ms in the future
      final resumeAt = DateTime.now().add(const Duration(milliseconds: 1));
      const totalMs = 60000;
      final state = AgentRunsState(
        scheduledResumeAt: resumeAt,
        scheduledTotalMs: totalMs,
      );
      // elapsed ≈ totalMs → fraction close to 1
      expect(state.countdownFraction, greaterThan(0.99));
    });

    test('countdownFraction = 0 when totalMs = 0', () {
      final state = AgentRunsState(
        scheduledResumeAt: DateTime.now().add(const Duration(minutes: 5)),
        scheduledTotalMs: 0,
      );
      expect(state.countdownFraction, 0.0);
    });

    test('countdownSecsRemaining is non-negative', () {
      final resumeAt = DateTime.now().add(const Duration(seconds: 15));
      final state = AgentRunsState(
        scheduledResumeAt: resumeAt,
        scheduledTotalMs: 30000,
      );
      expect(state.countdownSecsRemaining, greaterThanOrEqualTo(0));
      expect(state.countdownSecsRemaining, lessThanOrEqualTo(30));
    });
  });

  // ── AgentRunsState copyWith sentinel ─────────────────────────────────────

  group('AgentRunsState.copyWith', () {
    const base = AgentRunsState(
      total: 10,
      page: 2,
      perPage: 20,
      busy: true,
    );

    test('preserves unchanged fields', () {
      final next = base.copyWith(busy: false);
      expect(next.total, 10);
      expect(next.page, 2);
      expect(next.perPage, 20);
      expect(next.busy, isFalse);
    });

    test('can null out errorMessage via sentinel', () {
      final withErr = base.copyWith(errorMessage: 'oops');
      expect(withErr.errorMessage, 'oops');
      final cleared = withErr.copyWith(errorMessage: null);
      expect(cleared.errorMessage, isNull);
    });

    test('can null out scheduledResumeAt', () {
      final withTimer = base.copyWith(
        scheduledResumeAt: DateTime.now(),
        scheduledTotalMs: 5000,
      );
      expect(withTimer.scheduledResumeAt, isNotNull);
      final cleared = withTimer.copyWith(scheduledResumeAt: null);
      expect(cleared.scheduledResumeAt, isNull);
    });
  });

  // ── Pagination ellipsis helper (mirrors AgentRunsScreen logic) ────────────

  group('Pagination ellipsis (agent runs)', () {
    List<int?> buildPages(int current, int total) {
      if (total <= 7) {
        return List<int?>.generate(total, (i) => i + 1);
      }
      final result = <int>{};
      result.add(1);
      result.add(total);
      for (int p = current - 1; p <= current + 1; p++) {
        if (p >= 1 && p <= total) result.add(p);
      }
      final sorted = result.toList()..sort((a, b) => a - b);
      final out = <int?>[];
      for (int i = 0; i < sorted.length; i++) {
        if (i > 0 && sorted[i] - sorted[i - 1] > 1) out.add(null);
        out.add(sorted[i]);
      }
      return out;
    }

    test('5 pages → flat [1,2,3,4,5]', () {
      expect(buildPages(3, 5), [1, 2, 3, 4, 5]);
    });

    test('10 pages, current=1 → has trailing ellipsis', () {
      final pages = buildPages(1, 10);
      expect(pages.first, 1);
      expect(pages.last, 10);
      expect(pages.contains(null), isTrue);
    });

    test('10 pages, current=5 → both-side ellipsis', () {
      final pages = buildPages(5, 10);
      expect(pages.where((p) => p == null).length, 2);
      expect(pages.contains(5), isTrue);
    });

    test('10 pages, current=10 → no trailing null', () {
      final pages = buildPages(10, 10);
      expect(pages.last, 10);
      expect(pages[pages.length - 2], isNot(null));
    });
  });
}

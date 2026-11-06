"""TR server job runner (plan Task 13).

No network and no Docker: the launcher is a fake that records what it was
asked to do, and the runners are fakes or the real runners over
monkeypatched builders. Everything else reads the FakeStore.
"""
from __future__ import annotations

import os
import sys
from datetime import date, datetime, timedelta, timezone

import pytest

_backend = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _backend not in sys.path:
    sys.path.insert(0, _backend)

from db.fake import FakeStore  # noqa: E402
from trend_research import jobs  # noqa: E402

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
DATES = {"start": "2025-08-04", "end": "2025-08-10"}


class FakeLauncher:
    mode = "container"

    def __init__(self, *, fail=None):
        self.launched, self.stopped, self.alive = [], [], set()
        self.fail = fail

    def launch(self, job_id):
        if self.fail:
            raise RuntimeError(self.fail)
        self.launched.append(job_id)
        self.alive.add(job_id)
        return {"mode": self.mode, "container": jobs.container_name(job_id)}

    def is_alive(self, job_id):
        return job_id in self.alive

    def stop(self, job_id):
        self.stopped.append(job_id)
        self.alive.discard(job_id)


@pytest.fixture
def st():
    return FakeStore()


@pytest.fixture
def launcher():
    return FakeLauncher()


def _clock(start=NOW):
    t = [start]

    def now():
        t[0] += timedelta(seconds=1)
        return t[0]
    return now


_SUBMIT_CLOCK = {}


def submit(st, launcher, kind="ingest_macro", params=None, now=None):
    # one clock per store, so successive submits in a test get distinct ids
    now = now or _SUBMIT_CLOCK.setdefault(id(st), _clock())
    return jobs.submit(kind, dict(DATES) if params is None else params, store=st,
                       launcher=launcher, now=now)


# -- submit ---------------------------------------------------------------------------

def test_job_kinds_are_the_contract():
    assert jobs.JOB_KINDS == ("ingest_news", "ingest_themes", "ingest_gkg", "ingest_macro",
                              "ingest_filings", "build_ledger")


def test_submit_rejects_unknown_kind(st, launcher):
    with pytest.raises(ValueError, match="unknown job kind"):
        submit(st, launcher, kind="ingest_everything")
    assert launcher.launched == []
    assert st.run(st.between(jobs.TABLE, "", "￿")) == []


def test_submit_writes_a_queued_row_and_launches(st, launcher):
    job_id = submit(st, launcher)
    assert job_id.startswith("tr-job|2026-11-05T12:00:01") and job_id.endswith("|ingest_macro")
    assert launcher.launched == [job_id]
    s = jobs.status(job_id, store=st, launcher=launcher)
    assert s["state"] == "queued"
    assert s["kind"] == "ingest_macro" and s["params"]["start"] == "2025-08-04"
    assert s["started_at"] is None and s["finished_at"] is None and s["error"] is None
    assert set(s) >= {"id", "kind", "params", "state", "progress", "started_at",
                      "finished_at", "error"}


def test_one_tr_job_at_a_time_of_any_kind(st, launcher):
    first = submit(st, launcher)
    with pytest.raises(jobs.JobConflict, match="already"):
        submit(st, launcher)
    # another kind waits too: one TR job in total (A2), so build_ledger never
    # runs beside an ingest
    with pytest.raises(jobs.JobConflict, match="one job at a time"):
        submit(st, launcher, kind="ingest_news")
    with pytest.raises(jobs.JobConflict):
        submit(st, launcher, kind="build_ledger",
               params=dict(DATES, chain_id="honest-x", model_id="m1"))
    # once the first is finished, the lock is free again
    jobs.run(first, store=st, runners={"ingest_macro": lambda params, job: None},
             now=_clock())
    launcher.alive.discard(first)
    second = submit(st, launcher, kind="ingest_news")
    assert second != first and st.get(jobs.TABLE, jobs.LOCK_ID)["job_id"] == second


@pytest.mark.parametrize("kind,params,msg", [
    ("ingest_macro", {"start": "2025-08-04"}, "end"),
    ("ingest_macro", {"start": "2025-08-10", "end": "2025-08-04"}, "before"),
    ("ingest_macro", {"start": "08/04/2025", "end": "2025-08-10"}, "date"),
    ("ingest_macro", dict(DATES, chain="x"), "unknown param"),
    ("ingest_news", dict(DATES, instance="alpaca-main"), "paper"),
    ("ingest_filings", dict(DATES, symbols="ledger"), "chain_id"),
    ("ingest_filings", dict(DATES, symbols=[]), "symbols"),
    ("ingest_filings", dict(DATES, symbols="AAPL"), "symbols"),
    ("build_ledger", dict(DATES, chain_id="honest-x"), "model_id"),
    ("build_ledger", dict(DATES, chain_id="honest-x", model_id="m1", max_calls=0),
     "max_calls"),
    ("ingest_themes", dict(DATES, themes=["no-such-theme"]), "theme"),
])
def test_submit_validates_params(st, launcher, kind, params, msg):
    with pytest.raises(ValueError, match=msg):
        submit(st, launcher, kind=kind, params=params)
    assert launcher.launched == []


def test_build_ledger_params_get_defaults(st, launcher):
    job_id = submit(st, launcher, kind="build_ledger",
                    params=dict(DATES, chain_id="honest-x", model_id="m1"))
    p = jobs.status(job_id, store=st, launcher=launcher)["params"]
    assert p["max_calls"] == 4000 and p["chain_id"] == "honest-x"


def test_build_ledger_research_concurrency_param(st, launcher):
    from trend_research import build_ledger as bl
    job_id = submit(st, launcher, kind="build_ledger",
                    params=dict(DATES, chain_id="honest-x", model_id="m1"))
    p = jobs.status(job_id, store=st, launcher=launcher)["params"]
    assert p["research_concurrency"] == bl.RESEARCH_CONCURRENCY
    ok = jobs.validate_params("build_ledger", dict(DATES, chain_id="c", model_id="m",
                                                   research_concurrency=8))
    assert ok["research_concurrency"] == 8
    for bad in (0, 9, "8", True, 2.5):
        with pytest.raises(ValueError, match="research_concurrency"):
            jobs.validate_params("build_ledger", dict(DATES, chain_id="c", model_id="m",
                                                      research_concurrency=bad))


def test_launch_failure_marks_the_job_failed(st):
    bad = FakeLauncher(fail="docker down; password=hunter22")
    with pytest.raises(jobs.JobLaunchError) as ei:
        submit(st, bad)
    s = jobs.status(ei.value.job_id, store=st, launcher=bad)
    assert s["state"] == "failed" and "launch" in s["error"]
    assert "hunter22" not in s["error"]


# -- run --------------------------------------------------------------------------------

def test_status_transitions_done(st, launcher):
    job_id = submit(st, launcher)
    seen = {}

    def fake_runner(params, job):
        seen["running"] = jobs.status(job_id, store=st, launcher=launcher)["state"]
        for i in range(3):
            job.step(done=i + 1, total=3, current="item-%d" % i, rows_written=10 * (i + 1))

    final = jobs.run(job_id, store=st, runners={"ingest_macro": fake_runner}, now=_clock())
    assert final == "done"
    assert seen["running"] == "running"
    s = jobs.status(job_id, store=st, launcher=launcher)
    assert s["state"] == "done" and s["error"] is None
    assert s["progress"]["done"] == 3 and s["progress"]["total"] == 3
    assert s["progress"]["rows_written"] == 30 and s["progress"]["current"] == "item-2"
    assert s["started_at"] and s["finished_at"] and s["started_at"] <= s["finished_at"]
    # a finished job's gone container is not "lost"
    launcher.alive.discard(job_id)
    assert jobs.status(job_id, store=st, launcher=launcher)["state"] == "done"


def test_failed_job_records_error_without_secrets(st, launcher):
    job_id = submit(st, launcher)

    def boom(params, job):
        raise RuntimeError("upstream said no: secret=abc token: xyz789 "
                           "url=postgresql://tr:pgpass99@db/IntelliStock "
                           "Authorization: Bearer sk-ant-api03-ZZZZZZZZZZZZ")

    assert jobs.run(job_id, store=st, runners={"ingest_macro": boom}, now=_clock()) == "failed"
    s = jobs.status(job_id, store=st, launcher=launcher)
    assert s["state"] == "failed" and s["finished_at"]
    err = s["error"]
    assert "upstream said no" in err and "RuntimeError" in err
    for leaked in ("abc", "xyz789", "pgpass99", "sk-ant-api03-ZZZZZZZZZZZZ"):
        assert leaked not in err, err
    assert "secret=[redacted]" in err


def test_known_secret_values_are_redacted_everywhere(st, launcher):
    job_id = submit(st, launcher)

    def leaky(params, job):
        job.add_secrets("PKPAPERKEY123", "paper-secret-value-456")
        job.step(done=1, total=1, current="PKPAPERKEY123")
        raise RuntimeError("401 for key PKPAPERKEY123 / paper-secret-value-456")

    jobs.run(job_id, store=st, runners={"ingest_macro": leaky}, now=_clock())
    row = st.get(jobs.TABLE, job_id)
    assert "PKPAPERKEY123" not in repr(row) and "paper-secret-value-456" not in repr(row)


def test_stop_stops_the_container_and_marks_stopped(st, launcher):
    job_id = submit(st, launcher)
    s = jobs.stop(job_id, store=st, launcher=launcher, now=_clock())
    assert launcher.stopped == [job_id]
    assert s["state"] == "stopped" and s["finished_at"]
    # stopping again, or a finished job, is a no-op
    assert jobs.stop(job_id, store=st, launcher=launcher)["state"] == "stopped"
    assert launcher.stopped == [job_id]


def test_runner_exits_at_its_next_step_after_a_stop(st, launcher):
    job_id = submit(st, launcher)
    steps = []

    def runner(params, job):
        for i in range(5):
            job.check_stop()
            steps.append(i)
            if i == 1:
                jobs.stop(job_id, store=st, launcher=FakeLauncher())
            job.step(done=i + 1, total=5)

    assert jobs.run(job_id, store=st, runners={"ingest_macro": runner},
                    now=_clock()) == "stopped"
    assert steps == [0, 1]
    assert jobs.status(job_id, store=st, launcher=launcher)["state"] == "stopped"


def test_run_refuses_a_finished_job(st, launcher):
    job_id = submit(st, launcher)
    jobs.stop(job_id, store=st, launcher=launcher)
    called = []
    assert jobs.run(job_id, store=st, runners={"ingest_macro": called.append}) == "stopped"
    assert called == []


def test_running_job_whose_container_is_gone_is_lost_and_resubmittable(st, launcher):
    job_id = submit(st, launcher)
    st.update(jobs.TABLE, job_id, {"state": "running", "started_at": "2026-11-05T12:00:02Z"})
    launcher.alive.discard(job_id)            # container died (OOM, redeploy, ...)
    s = jobs.status(job_id, store=st, launcher=launcher)
    assert s["state"] == "failed" and s["error"].startswith("lost")
    # resubmission is allowed and is a new job
    again = submit(st, launcher)
    assert again != job_id and jobs.status(again, store=st, launcher=launcher)["state"] == "queued"


def test_unknown_docker_state_does_not_mark_lost(st, launcher):
    job_id = submit(st, launcher)

    class Blind(FakeLauncher):
        def is_alive(self, job_id):
            return None

    assert jobs.status(job_id, store=st, launcher=Blind())["state"] == "queued"


def test_status_of_unknown_job_raises_keyerror(st, launcher):
    with pytest.raises(KeyError):
        jobs.status("tr-job|nope|ingest_macro", store=st, launcher=launcher)


def test_cli_run_dispatches(monkeypatch):
    got = []
    monkeypatch.setattr(jobs, "run", lambda job_id, **kw: got.append(job_id) or "done")
    assert jobs.main(["run", "tr-job|x|ingest_macro"]) == 0
    assert got == ["tr-job|x|ingest_macro"]


# -- the real runners over fake builders -----------------------------------------------

def test_news_runner_backfills_the_whole_range_with_paper_credentials(st, launcher,
                                                                      monkeypatch):
    from trend_research import ingest_alpaca_news as news
    calls = []
    monkeypatch.setattr(news, "resolve_paper_credentials",
                        lambda instance, **kw: ("PKPAPER999", "papersecret999"))

    def fake_backfill(s, e, *, key, secret, on_day, **kw):
        calls.append((s, e, key, secret))
        for i, d in enumerate((s, s + timedelta(days=1), e)):
            on_day(d, 7 * (i + 1), i == 0)
        return 21
    monkeypatch.setattr(news, "backfill_news", fake_backfill)
    job_id = submit(st, launcher, kind="ingest_news",
                    params={"start": "2025-08-04", "end": "2025-08-06"})
    assert jobs.run(job_id, store=st, now=_clock()) == "done"
    # One call over the range: its per-day resume applies (I4 / A5).
    assert [(s, e) for s, e, _, _ in calls] == [(date(2025, 8, 4), date(2025, 8, 6))]
    assert all(k == "PKPAPER999" for _, _, k, _ in calls)
    s = jobs.status(job_id, store=st, launcher=launcher)
    assert s["progress"]["done"] == 3 and s["progress"]["rows_written"] == 21
    assert s["progress"]["days_skipped"] == 1
    assert "papersecret999" not in repr(st.get(jobs.TABLE, job_id))


def test_build_ledger_runner_builds_week_by_week_with_one_budget(st, launcher, monkeypatch):
    from trend_research import build_ledger as bl
    monkeypatch.setattr(bl, "resolve_model_row", lambda model_id: {
        "provider": "anthropic", "model": "claude-x", "api_key": "sk-ant-KEY-0000000",
        "provider_config": None})
    weeks, callers = [], set()

    def fake_build(chain_id, start, end, **kw):
        assert start == end
        weeks.append(start)
        callers.add(id(kw["caller"]))
        assert kw["max_calls"] == 50 and kw["api_key"] == "sk-ant-KEY-0000000"
        assert kw["research_concurrency"] == 6
        stopped = "budget" if len(weeks) == 3 else None
        return {"weeks": 1, "built_ok": 0 if stopped else 1, "built_failed": 0,
                "skipped_ok": 0, "not_yet_due": 0, "quota_waits": 0,
                "calls_used": 12, "stopped": stopped}

    monkeypatch.setattr(bl, "build", fake_build)
    job_id = submit(st, launcher, kind="build_ledger", params={
        "chain_id": "honest-x", "model_id": "m1", "max_calls": 50, "research_concurrency": 6,
        "start": "2025-08-04", "end": "2025-09-01"})
    # A stop ends the job failed with the reason, never "done" (A4).
    assert jobs.run(job_id, store=st, now=_clock()) == "failed"
    assert weeks == [date(2025, 8, 4), date(2025, 8, 11), date(2025, 8, 18)]
    assert len(callers) == 1                      # one StageCaller = one budget
    s = jobs.status(job_id, store=st, launcher=launcher)
    assert s["state"] == "failed" and "stopped (budget) at week 2025-08-18" in s["error"]
    assert "resubmit to resume" in s["error"]
    p = s["progress"]
    assert p["done"] == 2 and p["total"] == 5
    assert p["ledger"]["built_ok"] == 2 and p["ledger"]["calls_used"] == 36
    assert p["ledger"]["stopped"] == "budget"


def test_filings_ledger_symbols_come_from_the_chain_rows(st, launcher, monkeypatch):
    from trend_research import filings
    st.insert("TrendResearchLedger", [
        {"id": "honest-x|2025-08-04|m|tr-v1", "chain_id": "honest-x",
         "candidates": [{"symbol": "NVDA"}, {"symbol": "gld"}],
         "recommended": [{"symbol": "AAPL"}], "notes": [{"symbol": "NVDA"}],
         "book": {"picks": [{"symbol": "MSFT"}]}, "dropped": [{"symbol": "ZZZZ"}]},
        {"id": "honest-x|2025-08-11|m|tr-v1", "chain_id": "honest-x",
         "candidates": [{"symbol": "AMD"}], "recommended": [], "notes": [], "book": None},
        {"id": "honest-xy|2025-08-04|m|tr-v1", "chain_id": "honest-xy",
         "candidates": [{"symbol": "TSLA"}]},
    ])
    seen = []

    def fake_fetch(sym, start, end, **kw):
        assert kw["store"] is st
        seen.append(sym)
        if sym == "AMD":
            raise filings.FilingsFetchError("EDGAR 403")
        return 2

    monkeypatch.setattr(filings, "fetch_filings", fake_fetch)
    job_id = submit(st, launcher, kind="ingest_filings",
                    params=dict(DATES, symbols="ledger", chain_id="honest-x"))
    assert jobs.run(job_id, store=st, now=_clock()) == "failed"
    assert seen == ["AAPL", "AMD", "GLD", "MSFT", "NVDA"]
    s = jobs.status(job_id, store=st, launcher=launcher)
    assert s["progress"]["failed_symbols"] == ["AMD"] and s["progress"]["done"] == 5
    assert "AMD" in s["error"]


def test_themes_runner_does_timelines_then_title_windows(st, launcher, monkeypatch):
    from trend_research import ingest_gdelt as gd
    calls = []
    monkeypatch.setattr(gd, "backfill_themes", lambda s, e, **kw: calls.append(
        (s, e, tuple(kw["themes"]), kw["articles"], kw.get("articles_start"),
         kw.get("timelines", True))) or 1)
    theme = gd.THEMES[0]
    job_id = submit(st, launcher, kind="ingest_themes", params={
        "start": "2025-01-01", "end": "2025-08-10", "articles_start": "2025-05-01",
        "themes": [theme]})
    assert jobs.run(job_id, store=st, now=_clock()) == "done"
    assert calls[0] == (date(2025, 1, 1), date(2025, 8, 10), (theme,), False, None, True)
    titles = calls[1:]
    assert titles and all(c[3] and c[5] is False for c in titles)   # no timeline re-fetch
    assert titles[0][0] == date(2025, 5, 1) and titles[-1][1] == date(2025, 8, 10)
    assert all(c[0] == c[4] for c in titles)


class FakeClock:
    def __init__(self):
        self.t = 1000.0
        self.sleeps = []

    def clock(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += seconds


def test_gdelt_pacing_holds_across_chunk_calls(st, launcher):
    from trend_research import ingest_gdelt as gd

    class Resp:
        status_code, text, headers = 200, "{}", {}

    clock = FakeClock()
    starts = []

    def http_get(url, **kw):
        starts.append(clock.t)
        return Resp()

    gd_default = gd._default_http_get
    gd._default_http_get = http_get
    try:
        job_id = submit(st, launcher, kind="ingest_themes", params={
            "start": "2025-01-01", "end": "2025-06-30", "themes": [gd.THEMES[0]]})
        assert jobs.run(job_id, store=st, now=_clock(), sleep=clock.sleep,
                        clock=clock.clock) == "done"
    finally:
        gd._default_http_get = gd_default
    p = jobs.status(job_id, store=st, launcher=launcher)["progress"]
    assert p["total"] >= 3                        # timelines + at least two title windows
    assert len(starts) > 10
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert min(gaps) >= gd.MIN_REQUEST_INTERVAL, gaps


def test_quota_wait_writes_a_heartbeat(st, launcher, monkeypatch):
    from trend_research import build_ledger as bl
    monkeypatch.setattr(bl, "resolve_model_row", lambda model_id: {
        "provider": "claude-cli", "model": "claude-x", "api_key": None,
        "provider_config": None})
    clock = FakeClock()
    during = []

    def fake_sleep(seconds):
        row = st.get(jobs.TABLE, job_id)
        during.append((row["progress"]["phase"], row["progress"]["quota_wait_until"],
                       row["progress"]["updated_at"]))
        clock.sleep(seconds)

    def fake_build(chain_id, start, end, **kw):
        if start == date(2025, 8, 4):
            kw["sleep"](150)                    # the builder sleeping out a quota
        return {"built_ok": 1, "calls_used": 1, "stopped": None}

    monkeypatch.setattr(bl, "build", fake_build)
    job_id = submit(st, launcher, kind="build_ledger", params={
        "chain_id": "honest-x", "model_id": "m1", "start": "2025-08-04", "end": "2025-08-11"})
    assert jobs.run(job_id, store=st, now=_clock(), sleep=fake_sleep,
                    clock=clock.clock) == "done"
    assert clock.sleeps == [60.0, 60.0, 30.0]
    assert all(phase == "quota_wait" and until for phase, until, _ in during)
    stamps = [u for _, _, u in during]
    assert stamps == sorted(set(stamps))         # updated_at advances on every beat
    p = jobs.status(job_id, store=st, launcher=launcher)["progress"]
    assert p["phase"] == "weeks" and p["quota_wait_until"] is None and p["done"] == 2


def test_stop_after_the_job_finished_keeps_its_state(st):
    class FinishingLauncher(FakeLauncher):
        def stop(self, job_id):            # the runner finishes while we stop it
            super().stop(job_id)
            st.update(jobs.TABLE, job_id, {"state": "done", "finished_at": "2026-11-05T12:00:09Z"})

    launcher = FinishingLauncher()
    job_id = submit(st, launcher)
    s = jobs.stop(job_id, store=st, launcher=launcher)
    assert s["state"] == "done" and s["finished_at"] == "2026-11-05T12:00:09Z"


def test_failed_container_stop_keeps_the_job_running_and_locked(st, launcher):
    job_id = submit(st, launcher)
    st.update(jobs.TABLE, job_id, {"state": "running"})

    class Broken(FakeLauncher):
        def stop(self, job_id):
            raise RuntimeError("docker daemon unreachable")

    s = jobs.stop(job_id, store=st, launcher=Broken())
    assert s["state"] == "running" and "stop failed" in s["error"]
    with pytest.raises(jobs.JobConflict):
        submit(st, launcher)


# -- the docker launcher ---------------------------------------------------------------

class NotFound(Exception):
    """Stands in for docker.errors.NotFound (matched by class name)."""


class _FakeContainers:
    def __init__(self):
        self.runs = []

    def get(self, name):
        if name == "self-container":
            return _Self()
        raise NotFound("no such container")

    def run(self, image, **kw):
        self.runs.append((image, kw))


class _Self:
    attrs = {"Image": "sha256:deadbeef",
             "Mounts": [{"Destination": "/root/.claude", "Source": "/home/op/.claude"},
                        {"Destination": "/root/.claude.json",
                         "Source": "/home/op/.claude.json"}],
             "NetworkSettings": {"Networks": {"intellistockv4_default": {}}}}


class _FakeDocker:
    def __init__(self):
        self.containers = _FakeContainers()

    class networks:
        @staticmethod
        def get(name):
            return name


def test_docker_launcher_runs_a_detached_auto_removed_job_container(monkeypatch):
    for k in ("CLAUDE_HOST_HOME", "CLAUDE_HOST_CONFIG", "DOCKER_INSTANCE_IMAGE",
              "INSTANCE_DOCKER_NETWORK", "PG_DSN", "INSTANCE_PG_DSN", "INSTANCE_POSTGRES_HOST"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOSTNAME", "self-container")
    monkeypatch.setenv("POSTGRES_HOST", "postgres")
    monkeypatch.setenv("INTELLISTOCK_CRED_KEY", "fernet-key-value")
    fake = _FakeDocker()
    launcher = jobs.DockerLauncher(client_factory=lambda: fake)
    info = launcher.launch("tr-job|2026-11-05T12:00:00.000001Z|build_ledger")
    (image, kw), = fake.containers.runs
    assert image == "sha256:deadbeef"
    assert kw["command"] == ["python3", "-m", "trend_research.jobs", "run",
                             "tr-job|2026-11-05T12:00:00.000001Z|build_ledger"]
    assert kw["name"].startswith("tr-job-") and kw["name"] == info["container"]
    assert kw["detach"] is True and kw["auto_remove"] is True
    assert kw["network"] == "intellistockv4_default"
    # Bounded next to the live broker; the kernel kills it first (A2).
    assert kw["mem_limit"] == "2g" and kw["nano_cpus"] == 1_000_000_000
    assert kw["pids_limit"] == jobs.JOB_PIDS_LIMIT and kw["oom_score_adj"] == 1000
    assert kw["volumes"]["/home/op/.claude"] == {"bind": "/root/.claude", "mode": "ro"}
    assert kw["volumes"]["/home/op/.claude.json"] == {"bind": "/root/.claude.json",
                                                       "mode": "ro"}
    assert kw["environment"]["POSTGRES_HOST"] == "postgres"
    assert kw["environment"]["INTELLISTOCK_CRED_KEY"] == "fernet-key-value"
    # what the job row records carries no environment
    assert "fernet-key-value" not in repr(info)
    assert launcher.is_alive("tr-job|2026-11-05T12:00:00.000001Z|build_ledger") is False


def test_docker_launcher_uses_the_configured_network(monkeypatch):
    monkeypatch.setenv("INSTANCE_DOCKER_NETWORK", "intellistockv4_default")
    monkeypatch.setenv("HOSTNAME", "self-container")
    fake = _FakeDocker()
    jobs.DockerLauncher(client_factory=lambda: fake).launch("tr-job|n|ingest_macro")
    (_, kw), = fake.containers.runs
    assert kw["network"] == "intellistockv4_default"


class _DokployApi:
    """The API container as Dokploy runs it on server7 (2026-11-06): Traefik's
    ingress network listed first, the compose project's network -- the only
    one Postgres is on -- second."""
    def __init__(self, labels=True):
        self.attrs = {"Image": "sha256:deadbeef", "Mounts": [],
                      "Config": {"Labels": ({"com.docker.compose.project": "intellistockv4-fullstack-nnhtty"}
                                            if labels else {})},
                      "NetworkSettings": {"Networks": {
                          "dokploy-network": {}, "intellistockv4-fullstack-nnhtty_default": {}}}}


def _self_is(me):
    def get(name):
        if name == "self-container":
            return me
        raise NotFound("no such container")
    return get


@pytest.mark.parametrize("labels", [True, False])
def test_docker_launcher_joins_the_app_network_not_the_platform_ingress(monkeypatch, labels):
    # Live failure: the job joined dokploy-network, could not resolve
    # "postgres", and exited 1 after ~90 s before marking itself running.
    monkeypatch.delenv("INSTANCE_DOCKER_NETWORK", raising=False)
    monkeypatch.setenv("HOSTNAME", "self-container")
    fake = _FakeDocker()
    fake.containers.get = _self_is(_DokployApi(labels))
    jobs.DockerLauncher(client_factory=lambda: fake).launch("tr-job|n|ingest_news")
    (_, kw), = fake.containers.runs
    assert kw["network"] == "intellistockv4-fullstack-nnhtty_default"


def test_docker_launcher_skips_a_configured_network_that_does_not_exist(monkeypatch):
    # The backtest engine's INSTANCE_DOCKER_NETWORK=intellistockv4_default does
    # not exist on server7; the job must fall through to the app network.
    monkeypatch.setenv("INSTANCE_DOCKER_NETWORK", "intellistockv4_default")
    monkeypatch.setenv("HOSTNAME", "self-container")
    fake = _FakeDocker()
    fake.containers.get = _self_is(_DokployApi())

    class networks:
        @staticmethod
        def get(name):
            raise NotFound(name)

    fake.networks = networks
    jobs.DockerLauncher(client_factory=lambda: fake).launch("tr-job|n|ingest_news")
    (_, kw), = fake.containers.runs
    assert kw["network"] == "intellistockv4-fullstack-nnhtty_default"


def test_docker_launcher_refuses_without_a_network(monkeypatch, st):
    monkeypatch.delenv("INSTANCE_DOCKER_NETWORK", raising=False)
    monkeypatch.setenv("HOSTNAME", "not-a-container")     # this process is not in Docker
    fake = _FakeDocker()
    launcher = jobs.DockerLauncher(client_factory=lambda: fake)
    with pytest.raises(jobs.JobLaunchError, match="INSTANCE_DOCKER_NETWORK"):
        jobs.submit("ingest_macro", dict(DATES), store=st, launcher=launcher, now=_clock())
    assert fake.containers.runs == []


# -- routes -----------------------------------------------------------------------------

def _route(method, path):
    from api import main
    for r in main.app.routes:
        if getattr(r, "path", None) == path and method in getattr(r, "methods", ()):
            return r
    raise AssertionError("no route %s %s" % (method, path))


def test_routes_require_auth():
    import inspect

    from fastapi.testclient import TestClient

    from api import main
    for method, path in (("POST", "/trend-research/jobs"),
                         ("GET", "/trend-research/jobs/{job_id}"),
                         ("POST", "/trend-research/jobs/{job_id}/stop")):
        param = inspect.signature(_route(method, path).endpoint).parameters["current_user"]
        assert param.default.dependency is main.get_current_user
    main.app.dependency_overrides[main.conn_dependency] = lambda: None
    try:
        client = TestClient(main.app)
        bad = {"Authorization": "Bearer not-a-token"}
        jid = "tr-job%7C2026-11-05T12%3A00%3A00Z%7Cingest_macro"
        for method, url, body in (("POST", "/trend-research/jobs",
                                   {"kind": "ingest_macro", "params": DATES}),
                                  ("GET", "/trend-research/jobs/" + jid, None),
                                  ("POST", "/trend-research/jobs/%s/stop" % jid, None)):
            assert client.request(method, url, json=body, headers=bad).status_code == 401
            assert client.request(method, url, json=body).status_code in (401, 403)
    finally:
        main.app.dependency_overrides.pop(main.conn_dependency, None)


def test_routes_map_job_errors(monkeypatch):
    from fastapi.testclient import TestClient

    from api import main
    main.app.dependency_overrides[main.get_current_user] = lambda: {"id": "u", "username": "u"}
    main.app.dependency_overrides[main.conn_dependency] = lambda: None
    try:
        client = TestClient(main.app)
        monkeypatch.setattr(jobs, "submit", lambda kind, params: "tr-job|t|" + kind)
        r = client.post("/trend-research/jobs", json={"kind": "ingest_macro", "params": DATES})
        assert r.status_code == 200 and r.json() == {"id": "tr-job|t|ingest_macro"}

        def conflict(kind, params):
            raise jobs.JobConflict("a ingest_macro job is already running")
        monkeypatch.setattr(jobs, "submit", conflict)
        assert client.post("/trend-research/jobs",
                           json={"kind": "ingest_macro"}).status_code == 409

        def bad(kind, params):
            raise ValueError("unknown job kind 'x'")
        monkeypatch.setattr(jobs, "submit", bad)
        assert client.post("/trend-research/jobs", json={"kind": "x"}).status_code == 400

        def missing(job_id):
            raise KeyError(job_id)
        monkeypatch.setattr(jobs, "status", missing)
        monkeypatch.setattr(jobs, "stop", missing)
        assert client.get("/trend-research/jobs/nope").status_code == 404
        assert client.post("/trend-research/jobs/nope/stop").status_code == 404

        monkeypatch.setattr(jobs, "status", lambda job_id: {"id": job_id, "state": "done"})
        got = client.get("/trend-research/jobs/tr-job%7Cx%7Cingest_macro")
        assert got.status_code == 200 and got.json()["id"] == "tr-job|x|ingest_macro"
    finally:
        main.app.dependency_overrides.pop(main.get_current_user, None)
        main.app.dependency_overrides.pop(main.conn_dependency, None)


# -- fix wave A2/A4/A5 ----------------------------------------------------------------

def test_two_racing_submits_cannot_both_launch(st, launcher):
    """The lock is a compare-and-swap: a submit whose CAS loses raises and
    writes nothing, even when both saw the same free lock."""
    orig = st.replace_if
    raced = {"done": False}

    def racing(table, rid, *, when, doc, **kw):
        if not raced["done"]:
            raced["done"] = True              # the competitor's CAS lands first
            orig(table, rid, when=when, doc=dict(doc, job_id="tr-job|competitor"), **kw)
        return orig(table, rid, when=when, doc=doc, **kw)

    st.replace_if = racing
    with pytest.raises(jobs.JobConflict, match="same time"):
        submit(st, launcher)
    assert launcher.launched == [] and st.run(st.between(jobs.TABLE, "tr-job|", "tr-job|~")) == []
    st.replace_if = orig
    # The competitor's row never appears: its lock goes stale after the grace.
    with pytest.raises(jobs.JobConflict, match="in progress"):
        submit(st, launcher)
    late = _clock(NOW + timedelta(seconds=jobs.LAUNCH_GRACE_SEC + 5))
    assert submit(st, launcher, now=late) in launcher.launched


def test_a_lost_job_releases_the_lock(st, launcher):
    first = submit(st, launcher)
    launcher.alive.discard(first)                 # container gone
    second = submit(st, launcher, kind="ingest_news")
    assert jobs.status(first, store=st, launcher=launcher)["state"] == "failed"
    assert st.get(jobs.TABLE, jobs.LOCK_ID)["job_id"] == second


@pytest.mark.parametrize("stopped", ["quota_wait_cap", "inputs_missing", "transient",
                                     "week_failed"])
def test_build_ledger_job_ends_failed_on_any_builder_stop(st, launcher, monkeypatch, stopped):
    from trend_research import build_ledger as bl
    monkeypatch.setattr(bl, "resolve_model_row", lambda model_id: {
        "provider": "anthropic", "model": "claude-x", "api_key": "sk-ant-KEY-0000000",
        "provider_config": None})
    weeks = []

    def fake_build(chain_id, start, end, **kw):
        weeks.append(start)
        hit = len(weeks) == 2
        return {"weeks": 1, "built_ok": 0 if hit else 1, "built_failed": 0,
                "skipped_ok": 0, "not_yet_due": 0, "quota_waits": 3 if hit else 0,
                "calls_used": 5, "stopped": stopped if hit else None,
                "stop_reason": "why it stopped" if hit else None,
                "stopped_week": start.isoformat() if hit else None}

    monkeypatch.setattr(bl, "build", fake_build)
    job_id = submit(st, launcher, kind="build_ledger", params={
        "chain_id": "honest-x", "model_id": "m1", "start": "2025-08-04", "end": "2025-09-01"})
    assert jobs.run(job_id, store=st, now=_clock()) == "failed"
    assert weeks == [date(2025, 8, 4), date(2025, 8, 11)]      # never past the stop
    s = jobs.status(job_id, store=st, launcher=launcher)
    assert ("stopped (%s) at week 2025-08-11: why it stopped" % stopped) in s["error"]
    assert s["progress"]["ledger"]["stopped"] == stopped
    assert s["progress"]["ledger"]["stop_reason"] == "why it stopped"


def test_a_resubmitted_news_job_skips_completed_days(st, launcher, monkeypatch):
    from trend_research import ingest_alpaca_news as news
    monkeypatch.setattr(news, "resolve_paper_credentials",
                        lambda instance, **kw: ("PKPAPER999", "papersecret999"))
    requests = []

    class Resp:
        status_code = 200
        headers = {}

        def json(self):
            return {"news": [], "next_page_token": None}

    def http_get(url, *, params, headers, timeout):
        requests.append(params["start"][:10])
        return Resp()

    monkeypatch.setattr(news, "_default_http_get", http_get)
    params = {"start": "2025-08-04", "end": "2025-08-06"}
    first = submit(st, launcher, kind="ingest_news", params=params)
    assert jobs.run(first, store=st, now=_clock(), sleep=lambda s: None) == "done"
    assert requests == ["2025-08-04", "2025-08-05", "2025-08-06"]
    launcher.alive.discard(first)
    wider = submit(st, launcher, kind="ingest_news",
                   params={"start": "2025-08-04", "end": "2025-08-07"})
    assert jobs.run(wider, store=st, now=_clock(), sleep=lambda s: None) == "done"
    assert requests[3:] == ["2025-08-07"]                     # only the new day
    p = jobs.status(wider, store=st, launcher=launcher)["progress"]
    assert p["done"] == 4 and p["days_skipped"] == 3


def test_a_stop_between_news_days_ends_the_job_stopped(st, launcher, monkeypatch):
    from trend_research import ingest_alpaca_news as news
    monkeypatch.setattr(news, "resolve_paper_credentials", lambda instance, **kw: ("K1", "S1"))
    job_id = submit(st, launcher, kind="ingest_news", params={"start": "2025-08-04",
                                                              "end": "2025-08-09"})

    def fake_backfill(s, e, *, on_day, **kw):
        on_day(s, 0, False)
        st.update(jobs.TABLE, job_id, {"state": "stopped"})
        on_day(s + timedelta(days=1), 0, False)
        pytest.fail("the run should have stopped")
    monkeypatch.setattr(news, "backfill_news", fake_backfill)
    assert jobs.run(job_id, store=st, now=_clock()) == "stopped"


def test_the_lock_compare_and_swap_on_the_shared_store(store, launcher):
    """``store`` is real Postgres under PG_TEST_DSN (replace_if's SQL), else
    the FakeStore."""
    first = submit(store, launcher)
    with pytest.raises(jobs.JobConflict):
        submit(store, launcher, kind="ingest_news")
    store.update(jobs.TABLE, first, {"state": "done"})
    second = submit(store, launcher, kind="ingest_news")
    assert store.get(jobs.TABLE, jobs.LOCK_ID)["job_id"] == second
    # a stale holder seen by a loser: the CAS on the old holder fails
    from db.store import P
    assert store.replace_if(jobs.TABLE, jobs.LOCK_ID, when=P.field("job_id").eq(first),
                            doc={"id": jobs.LOCK_ID, "job_id": "x", "at": None}) is None

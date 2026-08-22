"""The cutover runbook is executable prose; these tests keep it executable.

A runbook that has drifted from the flags the scripts accept is worse than no
runbook: it is read at 2am, during a freeze, by someone who trusts it.
"""
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[2]
RUNBOOK = REPO / "docs/runbooks/postgres-cutover.md"

STEPS = ["## 1. Pre-flight", "## 2. Freeze", "## 3. Export/import", "## 4. Verify",
         "## 5. Flip", "## 6. Smoke", "## 7. Re-certify", "## 8. Rollback",
         "## 9. Decommission"]


def _text() -> str:
    return RUNBOOK.read_text()


def test_runbook_has_every_ordered_step():
    text = _text()
    for heading in STEPS:
        assert heading in text, heading


def test_every_step_carries_a_stop_condition():
    """"Ordered, each with a stop condition" is the whole design. A step with no
    stop condition is a step an operator walks straight past."""
    text = _text()
    for index, heading in enumerate(STEPS):
        body = text.split(heading, 1)[1]
        if index + 1 < len(STEPS):
            body = body.split(STEPS[index + 1], 1)[0]
        assert "**Stop" in body, "%s has no stop condition" % heading


def test_runbook_stops_on_any_verify_mismatch():
    assert "Stop on any mismatch" in _text()


def test_runbook_names_the_collation_gate_and_the_real_money_instance():
    text = _text()
    assert "latest_observation_date DESC, id DESC" in text
    assert 'COLLATE "C"' in text
    assert "alpaca-main" in text
    assert "restarted last" in text


def test_rollback_is_the_env_flip_and_says_what_is_lost():
    text = _text().split("## 8. Rollback", 1)[1].split("## 9.", 1)[0]
    assert "PG_DSN" in text
    assert "lost" in text


def test_paired_experiment_invocation_uses_flags_the_script_accepts():
    """The re-certification command is the one command in here that MUST run
    first try; the operator is mid-freeze when they reach it."""
    accepted = set(re.findall(r'add_argument\("(--[a-z-]+)"',
                              (REPO / "scripts/run_paired_experiment.py").read_text()))
    assert accepted, "could not read run_paired_experiment's flags"

    text = _text()
    block = text.split("run_paired_experiment.py", 1)[1].split("\n\n", 1)[0]
    used = set(re.findall(r"(--[a-z-]+)", block))
    assert used, "the runbook shows no flags for run_paired_experiment.py"
    assert used <= accepted, "runbook uses flags the script rejects: %s" % sorted(
        used - accepted)


def test_dev_pg_and_retention_helpers_are_named_by_their_real_paths():
    text = _text()
    for path in ("scripts/migrate_rethinkdb_to_postgres.py", "scripts/pg_retention.py",
                 "scripts/dev_pg.sh", "scripts/run_paired_experiment.py"):
        assert path in text, path
    for helper in ("scripts/pg_retention.py", "scripts/dev_pg.sh",
                   "scripts/run_paired_experiment.py"):
        assert (REPO / helper).exists(), "%s named by the runbook does not exist" % helper


def test_claude_md_and_readme_document_the_new_store():
    claude = (REPO / "CLAUDE.md").read_text()
    assert "## Datastore" in claude
    assert "from db import store" in claude
    assert 'COLLATE "C"' in claude
    assert "docs/runbooks/postgres-cutover.md" in claude

    readme = (REPO / "README.md").read_text()
    assert "POSTGRES_PASSWORD" in readme
    assert "rollback" in readme.lower()

"""The scripts triage is a contract, not a note.

`docs/superpowers/specs/2026-08-22-postgres-port-script-triage.md` decides which
ReQL scripts get ported to `db.store` and which are frozen for provenance. These
tests fail the build if a ReQL script is left unclassified, if a script is
claimed by two buckets at once, or if an ARCHIVED row does not actually live in
`scripts/archive_rethinkdb/`.
"""
import pathlib
import re
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
TRIAGE = REPO / "docs/superpowers/specs/2026-08-22-postgres-port-script-triage.md"
ROW = re.compile(r"^\| `((?:backend/)?scripts/[^`]+\.py)` \|")
REQL = re.compile(r"rethinkdb|r\.db\(")


def _rows(section: str) -> set:
    text = TRIAGE.read_text()
    body = text.split("## %s" % section, 1)[1].split("\n## ", 1)[0]
    out = set()
    for line in body.splitlines():
        match = ROW.match(line)
        if match:
            out.add(match.group(1))
    return out


def _tracked_scripts() -> list:
    """Every .py under scripts/ and backend/scripts/ that git actually tracks.

    Deliberately NOT a filesystem walk. `.gitignore` excludes `scripts/_*.py` --
    underscore-prefixed one-off diagnostics, by long-standing convention -- and a
    walk picks those up from whichever developer happens to have some lying
    around, then fails the build asking them to triage a scratch file that is not
    in the repository. The triage is a claim about the repository.
    """
    try:
        out = subprocess.run(
            ["git", "ls-files", "--", "scripts", "backend/scripts"],
            cwd=str(REPO), capture_output=True, text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError) as exc:
        pytest.skip("git is not usable here, so the tracked set is unknowable: %s" % exc)
    return [line for line in out.splitlines() if line.endswith(".py")]


def _reql_scripts() -> set:
    found = set()
    for rel in _tracked_scripts():
        path = REPO / rel
        if "archive_rethinkdb" in path.parts or not path.exists():
            continue
        if REQL.search(path.read_text(encoding="utf-8", errors="replace")):
            found.add(rel)
    return found


def test_every_reql_script_is_classified_exactly_once():
    """No ReQL script may be silently forgotten, and none may be claimed twice.

    ARCHIVED scripts have already moved under scripts/archive_rethinkdb/, which
    the scan skips, so the live set must be covered by PORTED plus RETAINED.
    """
    ported, archived, retained = _rows("PORTED"), _rows("ARCHIVED"), _rows("RETAINED")
    assert ported & archived == set(), "a script is in both PORTED and ARCHIVED"
    assert ported & retained == set(), "a script is in both PORTED and RETAINED"
    assert archived & retained == set(), "a script is in both ARCHIVED and RETAINED"

    live = _reql_scripts()
    unclassified = live - ported - retained
    assert unclassified == set(), "unclassified ReQL scripts: %s" % sorted(unclassified)


def test_every_archived_row_actually_moved():
    """A row in the ARCHIVED table is a claim about the filesystem."""
    for original in _rows("ARCHIVED"):
        moved = REPO / "scripts/archive_rethinkdb" / pathlib.Path(original).name
        assert moved.exists(), "%s is listed as ARCHIVED but is not at %s" % (
            original, moved.relative_to(REPO))
        assert not (REPO / original).exists(), (
            "%s is listed as ARCHIVED but still sits at its original path" % original)


def test_retained_scripts_are_still_where_the_document_says():
    """RETAINED means "left exactly where it is", so the path must resolve.

    The migration script itself is written on a sibling branch; a row for a file
    that does not exist here yet is a promise, not a lie, so it is skipped.
    """
    for path in _rows("RETAINED"):
        if path.endswith("migrate_rethinkdb_to_postgres.py"):
            continue
        assert (REPO / path).exists(), "%s is listed as RETAINED but is gone" % path


def test_the_two_index_scripts_are_deleted_not_archived():
    assert not (REPO / "scripts/create_backtest_list_indices.py").exists()
    assert not (REPO / "scripts/create_clear_state_indices.py").exists()
    assert not (REPO / "scripts/archive_rethinkdb/create_backtest_list_indices.py").exists()
    assert not (REPO / "scripts/archive_rethinkdb/create_clear_state_indices.py").exists()


def test_archive_readme_states_the_contract():
    readme = (REPO / "scripts/archive_rethinkdb/README.md").read_text()
    assert "do not run against Postgres" in readme
    assert "read it first" in readme


def test_gitignored_scratch_scripts_are_out_of_scope_by_construction():
    """`scripts/_*.py` is gitignored one-off scratch. It is not in the
    repository, nothing ports it, and nothing archives it -- an ignored file
    cannot be `git mv`'d anywhere without committing the very thing the ignore
    rule exists to keep out. The census must therefore never see one.
    """
    ignore = (REPO / ".gitignore").read_text()
    assert "scripts/_*.py" in ignore, "the scratch convention this test relies on is gone"

    scratch = REPO / "scripts/_triage_probe_scratch.py"
    scratch.write_text("from rethinkdb import RethinkDB\n")
    try:
        assert str(scratch.relative_to(REPO)) not in _reql_scripts()
        test_every_reql_script_is_classified_exactly_once()
    finally:
        scratch.unlink()

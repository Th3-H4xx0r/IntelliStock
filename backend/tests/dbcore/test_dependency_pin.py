import os
import re

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
_REQ = os.path.join(_REPO, "backend", "requirements.txt")
_DOCKERFILE = os.path.join(_REPO, "backend", "Dockerfile")


def test_requirements_pin_psycopg_with_binary_and_pool_extras():
    text = open(_REQ, encoding="utf-8").read()
    assert re.search(r"^psycopg\[binary,pool\]>=3\.2\.10,<4\s*$", text, re.M), \
        "psycopg[binary,pool]>=3.2.10,<4 must be pinned"


def test_dockerfile_installs_from_requirements_so_no_separate_pin_is_needed():
    assert "pip install --no-cache-dir -r requirements.txt" in \
        open(_DOCKERFILE, encoding="utf-8").read()


def test_dev_pg_script_is_executable():
    path = os.path.join(_REPO, "scripts", "dev_pg.sh")
    assert os.path.exists(path) and os.access(path, os.X_OK)

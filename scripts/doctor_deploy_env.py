"""Check the deploy environment against what docker-compose.yml actually requires.

Written after a `${VAR:?...}` variable missing from the environment cost hours
of misdiagnosis on 2026-08-03. `SOCKET_CONTROL_MASTER_KEY` was absent, which
makes docker compose refuse to start the `backend` service — and ONLY that
service, because it is the only one that references the variable. Everything
else came up fine, so the stack looked healthy: the API answered, backtests
ran, the frontend served.

What was actually broken was invisible from every angle:

  * `backend` runs server.py, which owns the Instances changefeed, so nothing
    spawned instance containers. Setting runCommand=True did nothing.
  * `POST /instances/{id}/start` is served by the `api` container, which has no
    Docker socket, so it wrote the DB flag and returned 200 {"started": true}
    without being able to create anything.
  * An already-running instance container kept running, unmanaged, because
    nothing was left to supervise it.

Three "start paths" all appeared to succeed and none produced a container. A
one-line env check would have said so immediately.

`:?` variables are the dangerous ones: a missing value silently removes a
single service from the stack rather than failing the deploy visibly.

Usage:
  python3 scripts/doctor_deploy_env.py            # check .env
  python3 scripts/doctor_deploy_env.py --env-file /path/to/.env
Exit code is non-zero when a REQUIRED variable is missing.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
# ${VAR:?msg} = hard requirement, ${VAR:-default} = optional, ${VAR} = plain.
_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(:\?|:-|)([^}]*)\}")
_SERVICE = re.compile(r"^  ([A-Za-z0-9_-]+):\s*$")

#: Extra validators for variables whose mere presence is not enough. The socket
#: key is the case in point: a present-but-malformed value passes any "is it
#: set" check and still returns "" from derive_socket_control_token, which
#: blocks every instance launch with no container and no logs.
_VALIDATORS = {
    "SOCKET_CONTROL_MASTER_KEY": (
        lambda v: bool(re.fullmatch(r"[0-9a-f]{64}", v)) and len(set(v)) >= 8,
        "must be exactly 64 lowercase hex chars with >=8 distinct "
        "(server.py derive_socket_control_token); generate with "
        "`openssl rand -hex 32`",
    ),
}


def _parse_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(errors="replace").splitlines():
        m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", line)
        if m and not line.lstrip().startswith("#"):
            out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


def _compose_requirements(compose: Path):
    """(required, optional) -> {var: [services]}, in file order."""
    required: dict[str, list[str]] = {}
    optional: dict[str, list[str]] = {}
    service = "<top-level>"
    for line in compose.read_text(errors="replace").splitlines():
        m = _SERVICE.match(line)
        if m:
            service = m.group(1)
        for var, kind, _default in _VAR.findall(line):
            bucket = required if kind == ":?" else optional
            bucket.setdefault(var, [])
            if service not in bucket[var]:
                bucket[var].append(service)
    return required, optional


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--env-file", default=str(_REPO / ".env"))
    ap.add_argument("--compose", default=str(_REPO / "docker-compose.yml"))
    args = ap.parse_args(argv)

    compose = Path(args.compose)
    if not compose.exists():
        print(f"no compose file at {compose}")
        return 2
    env = _parse_env(Path(args.env_file))
    required, optional = _compose_requirements(compose)

    missing, invalid = [], []
    print(f"REQUIRED (${{VAR:?}} — a missing value REMOVES the service from the stack)\n")
    for var, services in required.items():
        val = env.get(var)
        where = ", ".join(services)
        if not val:
            missing.append((var, where))
            print(f"  MISSING  {var:<34} kills: {where}")
            continue
        check = _VALIDATORS.get(var)
        if check and not check[0](val):
            invalid.append((var, where, check[1]))
            print(f"  INVALID  {var:<34} kills: {where}")
            print(f"           {check[1]}")
        else:
            print(f"  ok       {var:<34} ({where})")

    unset_optional = [v for v in optional if not env.get(v)]
    if unset_optional:
        print(f"\nOPTIONAL, unset ({len(unset_optional)}) — these fall back to defaults:")
        print("  " + ", ".join(sorted(unset_optional)[:24])
              + (" …" if len(unset_optional) > 24 else ""))

    if missing or invalid:
        print("\nFAIL — the services listed above will not start, and compose "
              "removes them QUIETLY: the rest of the stack comes up healthy, so "
              "nothing looks broken until some feature that lives in the missing "
              "service silently does nothing.")
        return 1
    print("\nOK — every hard-required compose variable is present and valid.")
    print("NOTE: this checks the file you pointed at. A hosted deploy "
          "(Dokploy/Railway) has its OWN environment — a green result here does "
          "not mean the deployed stack is green.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

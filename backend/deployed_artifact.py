"""The identity of the image an instance would be launched from.

Split out of ``server.py`` so a second process can ask "which artifact is
deployed?" without importing the supervisor. ``_preflight_instance_launch``
is the wrong way to ask: it reads the instance and brokerage rows, asserts
launch identity, and for a funded Kalshi instance calls
``assert_live_start_allowed`` -- which is circular for any caller whose whole
purpose is to *write* the readiness report that call validates.

Nothing here launches, stops, or mutates anything. It reads one image's id.
"""

from __future__ import annotations

import os
import re

from live_readiness import LiveReadinessError

DEFAULT_INSTANCE_IMAGE = "intellistock-backend"

_IMAGE_ID_RE = re.compile(r"sha256:[0-9a-f]{64}")


def instance_image_name() -> str:
    """The image tag instances are launched from, as server.py resolves it."""
    return os.environ.get("DOCKER_INSTANCE_IMAGE", DEFAULT_INSTANCE_IMAGE)


def image_identity(image_obj) -> str:
    """The bare sha256 of a Docker image, or a refusal.

    Exact: a tag ("latest") or a truncated/upper-case digest is not an
    identity, and a readiness report bound to one would bind to nothing.
    """
    value = getattr(image_obj, "id", "")
    if type(value) is not str or not _IMAGE_ID_RE.fullmatch(value):
        raise LiveReadinessError("Docker image identity is malformed")
    return value.split(":", 1)[1]


def deployed_artifact_digest(*, client=None) -> str:
    """The sha256 of the image an instance launch would run right now.

    Side-effect free: no container is created, removed, or inspected, and no
    instance row is read. ``client`` is injectable so callers that already
    hold a Docker client do not open a second one.
    """
    if client is None:
        try:
            import docker

            client = docker.from_env()
        except Exception as exc:
            raise LiveReadinessError(
                f"Docker client is unavailable: {type(exc).__name__}: {exc}"
            ) from exc
    return image_identity(client.images.get(instance_image_name()))

"""python -m mcp_server: the MCP server on uvicorn, port 8090.

Settings come from the environment. When they are missing or invalid the
container does not crash-loop: it serves build_unconfigured_app (/healthz, 503
elsewhere) and logs one line per problem.

uvicorn runs the app object directly, so there is one worker process; sign-in
relies on that (its throttle locks live in the process). The access log stays
off because query strings carry login-transaction and enrollment tokens, and
proxy headers are not trusted.
"""
import logging
import os

import uvicorn

from mcp_server.app import build_app, build_deps, build_unconfigured_app
from mcp_server.config import PORT, SettingsError, load_settings

LISTEN_HOST = "0.0.0.0"
UNCONFIGURED_LOG_LEVEL = "info"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
# uvicorn's level names (config.LOG_LEVELS) as levels for the service's own loggers.
_LEVELS = {"critical": logging.CRITICAL, "error": logging.ERROR, "warning": logging.WARNING, "info": logging.INFO,
           "debug": logging.DEBUG, "trace": logging.DEBUG}


def main() -> None:
    try:
        settings = load_settings(os.environ)
    except SettingsError as exc:
        logging.basicConfig(level=_LEVELS[UNCONFIGURED_LOG_LEVEL], format=LOG_FORMAT)
        app, log_level = build_unconfigured_app(exc.errors), UNCONFIGURED_LOG_LEVEL
    else:
        logging.basicConfig(level=_LEVELS[settings.log_level], format=LOG_FORMAT)
        app, log_level = build_app(build_deps(settings)), settings.log_level
    uvicorn.run(app, host=LISTEN_HOST, port=PORT, proxy_headers=False, server_header=False, access_log=False,
                log_level=log_level)


if __name__ == "__main__":
    main()

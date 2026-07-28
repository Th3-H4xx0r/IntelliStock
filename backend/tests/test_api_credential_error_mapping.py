"""A brokerage with unencrypted credentials must not 500 the dashboard.

`StockCredentialError` extends RuntimeError, and the API's `_run` only mapped
ValueError (400) and LookupError (404). So a brokerage row whose `alpaca_key`
is still legacy plaintext -- which strict decryption correctly refuses -- fell
through as an unhandled 500:

    RuntimeError: alpaca_key: plaintext secret is forbidden
      -> StockCredentialError: linked Alpaca brokerage credentials failed
         strict decryption
      -> GET /brokerages/{id}/portfolio-history  500

That is a misdiagnosis. Nothing is broken server-side; one stored credential
needs re-saving. The dashboard fans out over every brokerage, so a single
un-migrated row took down the whole panel with a 5xx instead of letting the
other accounts render and flagging the one that needs attention.
"""
import ast
import os
import sys
from pathlib import Path

import pytest

_backend = Path(__file__).resolve().parents[1]
if str(_backend) not in sys.path:
    sys.path.insert(0, str(_backend))

from fastapi import HTTPException  # noqa: E402

from stock_credential_boundary import StockCredentialError  # noqa: E402

# api/main.py builds a whole FastAPI app at import time; lift out just `_run`.
_SRC = (_backend / "api" / "main.py").read_text()
_TREE = ast.parse(_SRC)
_NS = {"HTTPException": HTTPException, "Any": object,
       "StockCredentialError": StockCredentialError}
for _node in _TREE.body:
    if isinstance(_node, ast.FunctionDef) and _node.name == "_run":
        exec(compile(ast.Module(body=[_node], type_ignores=[]), "main.py", "exec"), _NS)
assert "_run" in _NS, "api/main.py no longer defines _run"
_run = _NS["_run"]


def _raise(exc):
    def _f():
        raise exc
    return _f


def test_credential_error_is_a_client_error_not_a_500():
    with pytest.raises(HTTPException) as caught:
        _run(_raise(StockCredentialError(
            "linked Alpaca brokerage credentials failed strict decryption")))
    assert caught.value.status_code == 409, "must not surface as a server fault"


def test_credential_error_detail_is_actionable_and_secret_free():
    with pytest.raises(HTTPException) as caught:
        _run(_raise(StockCredentialError(
            "linked Alpaca brokerage credentials failed strict decryption")))
    detail = str(caught.value.detail)
    assert "re-save" in detail.lower() or "re-link" in detail.lower(), detail
    # The boundary's own message is safe; a raw key/secret never is.
    for leak in ("plaintext secret is forbidden", "PK", "SK", "Bearer"):
        assert leak not in detail, detail


def test_existing_mappings_are_unchanged():
    for exc, code in ((ValueError("bad input"), 400),
                      (LookupError("nothing here"), 404),
                      (HTTPException(status_code=418, detail="teapot"), 418)):
        with pytest.raises(HTTPException) as caught:
            _run(_raise(exc))
        assert caught.value.status_code == code, exc


def test_unrelated_runtime_errors_still_surface_as_failures():
    """Only the credential boundary is reclassified; a genuine RuntimeError
    must keep escaping so it is not silently downgraded to a 4xx."""
    with pytest.raises(RuntimeError):
        _run(_raise(RuntimeError("rethink is down")))


def test_success_passes_through():
    assert _run(lambda: {"ok": True}) == {"ok": True}

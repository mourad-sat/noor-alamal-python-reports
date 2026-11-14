"""Regression tests: administrative access must require an explicit admin role."""

import pytest
from fastapi import HTTPException
from starlette.requests import Request


def _request_with_session(session):
    return Request({"type": "http", "session": session})


def test_legacy_admin_guard_rejects_roleless_authenticated_session():
    from app import auth_required
    with pytest.raises(HTTPException) as error:
        auth_required(_request_with_session({"authenticated": True}))
    assert error.value.status_code == 403


def test_legacy_admin_guard_rejects_staff_session():
    from app import auth_required
    with pytest.raises(HTTPException) as error:
        auth_required(_request_with_session({"authenticated": True, "role": "coordinator"}))
    assert error.value.status_code == 403


def test_legacy_admin_guard_accepts_explicit_admin_session():
    from app import auth_required
    assert auth_required(_request_with_session({"authenticated": True, "role": "admin"})) is None


def test_legacy_admin_guard_redirects_unauthenticated_session():
    from app import auth_required
    with pytest.raises(HTTPException) as error:
        auth_required(_request_with_session({}))
    assert error.value.status_code == 303
    assert error.value.headers["Location"] == "/login"

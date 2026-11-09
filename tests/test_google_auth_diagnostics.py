"""Google authentication failures should be actionable without exposing secrets."""
from pathlib import Path
from app.services.search_console import _refresh_failure_reason


def test_requests_explicitly_installed():
    requirements = (Path(__file__).parents[1] / "requirements.txt").read_text("utf-8")
    assert any(line.startswith("requests==") for line in requirements.splitlines())


def test_jwt_signature_hint_no_key_exposure():
    error = Exception("invalid_grant: Invalid JWT Signature. private=secret_should_not_leak")
    hint = _refresh_failure_reason(error)
    assert "подпись JWT" in hint
    assert "secret_should_not_leak" not in hint


def test_clock_skew_hint():
    hint = _refresh_failure_reason(Exception("invalid_grant: Invalid JWT: timeframe iat exp"))
    assert "время сервера" in hint


def test_generic_google_error_is_sanitized():
    hint = _refresh_failure_reason(Exception("server reply contained SECRET123"))
    assert "SECRET123" not in hint and "RefreshError" in hint


def test_other_token_error_categories():
    assert "invalid_scope" in _refresh_failure_reason(Exception("invalid_scope"))
    assert "unauthorized_client" in _refresh_failure_reason(Exception("unauthorized_client"))
    assert "admin_policy_enforced" in _refresh_failure_reason(Exception("admin_policy_enforced"))

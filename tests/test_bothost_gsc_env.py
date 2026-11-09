import base64
import json
from pathlib import Path

from app.services.search_console import SearchConsoleClient


def test_diagnostics_detect_truncated_base64_without_printing_contents():
    text = "SUPERSECRET"
    client = SearchConsoleClient("https://zdrava.top/", Path("/nonexistent"), service_account_b64=text)
    result = client.credential_check()
    assert "формат Base64 НЕВЕРНЫЙ" in result
    assert str(len(text)) in result
    assert text not in result


def test_diagnostics_detect_bad_json():
    encoded = base64.b64encode(b"not json").decode()
    client = SearchConsoleClient("https://zdrava.top/", Path("/nonexistent"), service_account_b64=encoded)
    assert "JSON НЕВЕРНЫЙ" in client.credential_check()


def test_diagnostics_reject_arbitrary_json():
    encoded = base64.b64encode(json.dumps({"foo": "bar"}).encode()).decode()
    client = SearchConsoleClient("https://zdrava.top/", Path("/nonexistent"), service_account_b64=encoded)
    assert "не является ключом service_account" in client.credential_check()


def test_diagnostics_missing_key():
    client = SearchConsoleClient("https://zdrava.top/", Path("/nonexistent"))
    assert "ключ отсутствует" in client.credential_check()


def test_no_credential_contents_echoed():
    text = base64.b64encode(json.dumps({"type": "service_account", "client_email": "PRIVATE_ADDRESS"}).encode()).decode()
    client = SearchConsoleClient("https://zdrava.top/", Path("/nonexistent"), service_account_b64=text)
    message = client.credential_check()
    assert "PRIVATE_ADDRESS" not in message and text not in message
    assert "отсутствуют обязательные поля" in message

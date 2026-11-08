from pathlib import Path
from app.services.search_console import SearchConsoleClient


def test_google_enabled_with_env_variable():
    client = SearchConsoleClient('sc-domain:zdrava.top', Path('/does-not-exist.json'), service_account_b64='sample')
    assert client.connected


def test_google_disabled_without_file_or_env():
    client = SearchConsoleClient('sc-domain:zdrava.top', Path('/does-not-exist.json'))
    assert not client.connected

"""Optional Google Search Console integration using readonly service account."""
from __future__ import annotations

import asyncio
import base64
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import quote
import httpx

SCOPES = ["https://www.googleapis.com/auth/webmasters.readonly"]


class SearchConsoleError(RuntimeError):
    pass


@dataclass(frozen=True)
class SearchStats:
    clicks: int
    impressions: int
    ctr: float
    position: float


class SearchConsoleClient:
    def __init__(self, site_url: str, service_account_file: Path, timeout: float = 20, service_account_b64: str = ""):
        self.site_url = site_url
        self.credentials_path = Path(service_account_file)
        self.timeout = timeout
        self.service_account_b64 = service_account_b64

    @property
    def connected(self) -> bool:
        return bool(self.site_url and (self.service_account_b64 or self.credentials_path.is_file()))

    def _access_token(self) -> str:
        try:
            from google.oauth2 import service_account
            from google.auth.transport.requests import Request
            if self.service_account_b64:
                payload = base64.b64decode(self.service_account_b64, validate=True)
                account_info = json.loads(payload.decode("utf-8"))
                credentials = service_account.Credentials.from_service_account_info(account_info, scopes=SCOPES)
            else:
                credentials = service_account.Credentials.from_service_account_file(
                    str(self.credentials_path), scopes=SCOPES)
            credentials.refresh(Request())
            if not credentials.token:
                raise SearchConsoleError("Google не выдал токен")
            return credentials.token
        except (OSError, ValueError, KeyError, UnicodeError) as exc:
            raise SearchConsoleError("Невозможно прочитать ключ сервисного аккаунта") from exc
        except Exception as exc:
            raise SearchConsoleError("Ошибка авторизации сервисного аккаунта Google") from exc

    async def by_landing(self, begin: date, end: date, path: str) -> SearchStats:
        if not self.connected:
            raise SearchConsoleError("Не настроен Search Console или отсутствует файл ключа")
        if path not in ("/mikozacid/", "/hot-ice/"):
            raise ValueError("Unknown landing")
        token = await asyncio.to_thread(self._access_token)
        url = ("https://www.googleapis.com/webmasters/v3/sites/" +
               quote(self.site_url, safe="") + "/searchAnalytics/query")
        body = {
            "startDate": begin.isoformat(), "endDate": end.isoformat(),
            "type": "web", "dimensions": [], "rowLimit": 1,
            "dimensionFilterGroups": [{"groupType": "and", "filters": [
                {"dimension": "page", "operator": "contains", "expression": path}
            ]}],
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(url, json=body,
                    headers={"Authorization": "Bearer " + token})
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise SearchConsoleError(f"Search Console API: HTTP {exc.response.status_code} (проверьте доступ к ресурсу)") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise SearchConsoleError("Сбой связи с Search Console") from exc
        rows = payload.get("rows", [])
        if not rows:
            return SearchStats(0, 0, 0, 0)
        row = rows[0]
        return SearchStats(int(row.get("clicks", 0)), int(row.get("impressions", 0)),
                           float(row.get("ctr", 0)), float(row.get("position", 0)))

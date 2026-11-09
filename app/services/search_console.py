"""Readonly Search Console API. Empty response is unknown, never an invented zero."""
from __future__ import annotations

import asyncio
import base64
import binascii
import json
import math
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
    clicks: int | None
    impressions: int | None
    ctr: float | None
    position: float | None

    @property
    def available(self) -> bool:
        return self.clicks is not None and self.impressions is not None


def _integer(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SearchConsoleError(f"Google: отсутствует или некорректен показатель {field}")
    if value < 0 or int(value) != value:
        raise SearchConsoleError(f"Google: некорректное значение {field}")
    return int(value)


def parse_search_stats(payload: object) -> SearchStats:
    if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
        raise SearchConsoleError("Google: неполный ответ API, числа скрыты")
    rows = payload["rows"]
    if not rows:
        # Could be no traffic, not yet processed or inaccessible; API doesn't say which.
        return SearchStats(None, None, None, None)
    if len(rows) != 1 or not isinstance(rows[0], dict):
        raise SearchConsoleError("Google: неожиданный формат строк")
    row = rows[0]
    clicks = _integer(row.get("clicks"), "clicks")
    impressions = _integer(row.get("impressions"), "impressions")
    if clicks > 0 and impressions == 0:
        raise SearchConsoleError("Google: клики без показов; невозможно рассчитать CTR")
    if impressions == 0:
        return SearchStats(clicks, 0, None, None)
    raw_ctr, raw_position = row.get("ctr"), row.get("position")
    if not isinstance(raw_ctr, (int, float)) or isinstance(raw_ctr, bool) or not math.isfinite(raw_ctr):
        raise SearchConsoleError("Google: нет корректного CTR")
    if not isinstance(raw_position, (int, float)) or isinstance(raw_position, bool) or not math.isfinite(raw_position) or raw_position <= 0:
        raise SearchConsoleError("Google: нет корректной позиции")
    if raw_ctr < 0 or raw_ctr > 1 or abs((clicks / impressions) - raw_ctr) > 0.0001:
        raise SearchConsoleError("Google: CTR не согласуется с кликами и показами")
    return SearchStats(clicks, impressions, clicks / impressions, float(raw_position))



def _refresh_failure_reason(exc: BaseException) -> str:
    """Expose only a safe category, never the raw Google response or credentials."""
    details = str(exc).lower()
    if "invalid jwt signature" in details:
        return "invalid_grant: Google отклонил подпись JWT; проверьте, что ключ активен и относится к сервисному аккаунту."
    if "reasonable timeframe" in details or ("invalid_grant" in details and ("iat" in details or "exp" in details)):
        return "invalid_grant: неверное время сервера или срок действия JWT."
    if "invalid_grant" in details:
        return "invalid_grant: Google отклонил JWT; проверьте активность ключа и часы сервера."
    if "invalid_scope" in details:
        return "invalid_scope: Google отклонил запрошенный OAuth scope."
    if "unauthorized_client" in details:
        return "unauthorized_client: сервисному аккаунту запрещён выпуск токена с указанными правами."
    if "invalid_client" in details or "disabled_client" in details or "deleted_client" in details:
        return "invalid_client: проверьте, что сервисный аккаунт и ключ активны."
    if "admin_policy_enforced" in details:
        return "admin_policy_enforced: политика организации блокирует выдачу токена."
    return "Google отказал в выдаче OAuth-токена (RefreshError). Проверьте IAM и состояние ключа."


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
            from google.auth.exceptions import RefreshError, TransportError
        except ImportError as exc:
            raise SearchConsoleError(
                "Google: отсутствует зависимость google-auth/requests в окружении Bothost. Обновите requirements.txt и пересоберите приложение."
            ) from None

        try:
            if self.service_account_b64:
                payload = base64.b64decode(self.service_account_b64, validate=True)
                account_info = json.loads(payload.decode("utf-8"))
                if not isinstance(account_info, dict) or account_info.get("type") != "service_account":
                    raise ValueError("Expected a service-account JSON")
                credentials = service_account.Credentials.from_service_account_info(
                    account_info, scopes=SCOPES)
            else:
                credentials = service_account.Credentials.from_service_account_file(
                    str(self.credentials_path), scopes=SCOPES)
            credentials.refresh(Request())
            if not credentials.token:
                raise SearchConsoleError("Google не выдал OAuth-токен.")
            return credentials.token
        except SearchConsoleError:
            raise
        except (OSError, binascii.Error, ValueError, KeyError, UnicodeError, json.JSONDecodeError):
            raise SearchConsoleError(
                "Google: неверный Base64, повреждённый JSON или недоступный файл ключа."
            ) from None
        except RefreshError as exc:
            raise SearchConsoleError("Google: " + _refresh_failure_reason(exc)) from None
        except TransportError:
            raise SearchConsoleError(
                "Google: не удалось подключиться к OAuth-серверу Google из Bothost (сеть, DNS, SSL или таймаут)."
            ) from None
        except Exception as exc:
            # Only the exception class is exposed: raw Google responses may
            # contain details that should not be sent in a Telegram message.
            raise SearchConsoleError(
                f"Google: неожиданная ошибка авторизации ({type(exc).__name__})."
            ) from None

    async def by_landing(self, begin: date, end: date, path: str) -> SearchStats:
        if not self.connected:
            raise SearchConsoleError("Search Console не подключён")
        if path not in ("/mikozacid/", "/hot-ice/"):
            raise ValueError("Unknown landing")
        token = await asyncio.to_thread(self._access_token)
        url = ("https://www.googleapis.com/webmasters/v3/sites/" +
               quote(self.site_url, safe="") + "/searchAnalytics/query")
        body = {
            "startDate": begin.isoformat(), "endDate": end.isoformat(),
            "type": "web", "dataState": "final", "dimensions": [], "rowLimit": 1,
            "dimensionFilterGroups": [{"groupType": "and", "filters": [
                {"dimension": "page", "operator": "equals", "expression": "https://zdrava.top" + path}
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
        return parse_search_stats(payload)

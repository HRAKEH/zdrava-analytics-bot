"""Read-only Yandex Direct API integration. No account mutations or token echo."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from io import StringIO

import httpx

BASE = "https://api.direct.yandex.com"
MAX_CAMPAIGNS = 1000

class DirectError(RuntimeError):
    pass

@dataclass(frozen=True)
class Campaign:
    id: int
    name: str
    state: str
    currency: str

@dataclass(frozen=True)
class CampaignStat:
    id: int
    impressions: int
    clicks: int
    cost: Decimal

def parse_campaigns(payload: object) -> list[Campaign]:
    if not isinstance(payload, dict) or "error" in payload:
        raise DirectError("Яндекс Директ отказал в запросе списка кампаний.")
    result = payload.get("result")
    if not isinstance(result, dict) or not isinstance(result.get("Campaigns"), list):
        raise DirectError("Директ вернул неожиданный ответ о кампаниях.")
    if result.get("LimitedBy") is not None:
        raise DirectError("Кампаний больше 1000. Пока поддержан только первый набор; обратитесь к разработчику.")
    campaigns = []
    seen = set()
    for entry in result["Campaigns"]:
        if not isinstance(entry, dict):
            raise DirectError("Директ вернул неверную кампанию.")
        cid, name, currency, state = (entry.get(k) for k in ("Id", "Name", "Currency", "State"))
        if (type(cid) is not int or cid < 1 or cid in seen or not isinstance(name, str)
            or not isinstance(currency, str) or not isinstance(state, str)):
            raise DirectError("Неполные данные о кампаниях Яндекс Директа.")
        seen.add(cid)
        campaigns.append(Campaign(cid, name, state, currency))
    return campaigns

def _nonnegative_int(raw: str) -> int:
    if not raw.isdecimal():
        raise DirectError("Директ вернул некорректное число кликов/показов.")
    return int(raw)

def parse_report_tsv(content: str, expected: set[int]) -> dict[int, CampaignStat]:
    try:
        lines = [line for line in csv.reader(StringIO(content), delimiter="\t") if line]
    except csv.Error as exc:
        raise DirectError("Директ вернул повреждённый TSV-отчёт.") from exc
    if not lines:
        return {}
    if lines[0] != ["CampaignId", "Impressions", "Clicks", "Cost"]:
        raise DirectError("Формат колонок отчёта Директа изменился.")
    results: dict[int, CampaignStat] = {}
    for row in lines[1:]:
        if len(row) != 4 or not row[0].isdecimal():
            raise DirectError("Директ вернул неполную строку статистики.")
        cid = int(row[0])
        if cid not in expected or cid in results:
            raise DirectError("Директ вернул лишнюю или повторную кампанию.")
        impressions, clicks = _nonnegative_int(row[1]), _nonnegative_int(row[2])
        try:
            money = Decimal(row[3])
        except InvalidOperation:
            raise DirectError("Некорректная сумма расхода в Директе.") from None
        if (not money.is_finite() or money < 0 or money.as_tuple().exponent < -2):
            raise DirectError("Недопустимая точность или сумма расходов Директа.")
        results[cid] = CampaignStat(cid, impressions, clicks, money)
    return results

class DirectClient:
    """Only GET campaign data and reports (HTTP POST required by the Yandex API)."""
    def __init__(self, token: str, client_login: str = "", timeout: float = 20):
        self._token = token.strip()
        self._client_login = client_login.strip()
        self.timeout = timeout

    @property
    def connected(self) -> bool:
        return bool(self._token)

    def _headers(self) -> dict[str, str]:
        if not self.connected:
            raise DirectError("Яндекс Директ ещё не подключён. Добавьте YANDEX_DIRECT_TOKEN в Bothost.")
        headers = {"Authorization": "Bearer " + self._token, "Accept-Language": "ru",
                   "Content-Type": "application/json; charset=utf-8"}
        if self._client_login:
            headers["Client-Login"] = self._client_login
        return headers

    @staticmethod
    def _check_status(code: int) -> None:
        if code in (401, 403):
            raise DirectError("Директ: нет доступа. Проверьте OAuth-токен, одобрение API и права аккаунта.")
        if code == 429:
            raise DirectError("Директ ограничил частоту запросов. Повторите позже.")
        if code >= 400:
            raise DirectError(f"Директ ответил HTTP {code}. Проверьте параметры и доступ к API.")

    async def campaigns(self) -> list[Campaign]:
        payload = {"method": "get", "params": {
            "SelectionCriteria": {}, "FieldNames": ["Id", "Name", "State", "Currency"],
            "Page": {"Limit": MAX_CAMPAIGNS, "Offset": 0}}}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(BASE + "/json/v5/campaigns", json=payload, headers=self._headers())
            self._check_status(response.status_code)
            return parse_campaigns(response.json())
        except (httpx.RequestError, ValueError) as exc:
            raise DirectError("Не удалось получить кампании Директа: сеть или формат ответа.") from None

    async def report(self, begin: date, end: date, campaign_ids: set[int]) -> dict[int, CampaignStat]:
        if begin > end or not campaign_ids or len(campaign_ids) > 500:
            raise ValueError("Invalid Direct report period or campaign selection")
        if any(type(i) is not int or i <= 0 for i in campaign_ids):
            raise ValueError("Invalid campaign ID")
        body = {"params": {
            "SelectionCriteria": {
                "DateFrom": begin.isoformat(), "DateTo": end.isoformat(),
                "Filter": [{"Field": "CampaignId", "Operator": "IN",
                            "Values": [str(i) for i in sorted(campaign_ids)]}]},
            "FieldNames": ["CampaignId", "Impressions", "Clicks", "Cost"],
            "ReportName": f"ZDRAVA_{begin.isoformat()}_{end.isoformat()}",
            "ReportType": "CAMPAIGN_PERFORMANCE_REPORT",
            "DateRangeType": "CUSTOM_DATE", "Format": "TSV",
            "IncludeVAT": "NO", "IncludeDiscount": "NO"}}
        headers = self._headers() | {
            "processingMode": "online", "returnMoneyInMicros": "false",
            "skipReportHeader": "true", "skipReportSummary": "true"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(BASE + "/json/v501/reports", json=body, headers=headers)
            if response.status_code in (201, 202, 502):
                raise DirectError("Директ пока не сформировал отчёт онлайн. Попробуйте позднее.")
            self._check_status(response.status_code)
            return parse_report_tsv(response.text, campaign_ids)
        except httpx.RequestError:
            raise DirectError("Не удалось загрузить отчёт Директа: соединение или таймаут.") from None

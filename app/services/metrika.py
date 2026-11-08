"""Strict Yandex Metrica Reporting API adapter. Missing data is never zero."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any
import httpx

BASE_URL = "https://api-metrika.yandex.net/stat/v1/data"
LANDINGS = {
    "mikozacid": ("Микозацид", "zdrava.top/mikozacid/"),
    "hot_ice": ("Горячий лёд", "zdrava.top/hot-ice/"),
}


class MetrikaError(RuntimeError):
    pass


def _count(value: object, *, label: str) -> int:
    """API counter must be present, finite, nonnegative and integral."""
    if value is None or isinstance(value, bool):
        raise MetrikaError(f"Метрика: отсутствует показатель «{label}»")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise MetrikaError(f"Метрика: некорректный показатель «{label}»") from None
    if not number.is_finite() or number < 0 or number != number.to_integral_value():
        raise MetrikaError(f"Метрика: некорректный показатель «{label}»")
    return int(number)


def _filter_for(product: str) -> str:
    if product not in LANDINGS:
        raise ValueError("Unknown landing")
    # Sessions containing a pageview with this URL fragment; not entrance sessions.
    return "ym:pv:URL=@'" + LANDINGS[product][1] + "'"


@dataclass(frozen=True)
class LandingStats:
    product: str
    visits: int
    users: int
    ozon_goal_reaches: int | None
    data_lag_seconds: int | None = None


@dataclass(frozen=True)
class RankedResult:
    rows: list[tuple]
    total_rows: int
    truncated: bool


class MetrikaClient:
    def __init__(self, token: str, counter_id: int, *, ozon_goal_id: int | None = None,
                 timeout: float = 20, client: httpx.AsyncClient | None = None):
        self._token = token
        self._counter = counter_id
        self._goal = ozon_goal_id
        self._timeout = timeout
        self._client = client

    @property
    def connected(self) -> bool:
        return bool(self._token)

    async def _query(self, begin: date, end: date, metrics: list[str], *,
                     product: str | None = None, dimensions: list[str] | None = None,
                     limit: int = 100) -> dict[str, Any]:
        if not self._token:
            raise MetrikaError("Не задан METRIKA_TOKEN")
        params: dict[str, str | int] = {
            "ids": self._counter, "date1": begin.isoformat(), "date2": end.isoformat(),
            "metrics": ",".join(metrics), "accuracy": "full", "lang": "ru",
            "limit": limit, "attribution": "last", "include_undefined": "true",
        }
        if product is not None:
            params["filters"] = _filter_for(product)
        if dimensions:
            params["dimensions"] = ",".join(dimensions)
            params["sort"] = "-ym:s:visits"
        try:
            if self._client is not None:
                response = await self._client.get(BASE_URL, params=params,
                    headers={"Authorization": "OAuth " + self._token})
            else:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.get(BASE_URL, params=params,
                        headers={"Authorization": "OAuth " + self._token})
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (401, 403):
                raise MetrikaError(f"Нет доступа к Метрике (HTTP {status}); проверьте токен и счётчик") from exc
            raise MetrikaError(f"Ошибка API Метрики: HTTP {status}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise MetrikaError("Ошибка соединения или некорректный JSON Метрики") from exc
        if not isinstance(data, dict) or not isinstance(data.get("data"), list) or not isinstance(data.get("totals"), list):
            raise MetrikaError("Метрика: неполный формат ответа API; отчёт не сформирован")
        if data.get("contains_sensitive_data") is True:
            raise MetrikaError("Метрика: ответ может скрывать часть данных по правилам приватности; отчёт не сформирован")
        # Both sampled responses and unknown sampling status are not safe to present as exact.
        if data.get("sampled") is not False:
            raise MetrikaError("Метрика: невозможно подтвердить отсутствие семплирования")
        if "sample_share" in data:
            try:
                share = Decimal(str(data["sample_share"]))
            except (InvalidOperation, ValueError):
                raise MetrikaError("Метрика: некорректная информация о выборке") from None
            if not share.is_finite() or share != 1:
                raise MetrikaError("Метрика: отчёт по неполной выборке; числа скрыты")
        return data

    async def totals(self, begin: date, end: date, product: str) -> LandingStats:
        metrics = ["ym:s:visits", "ym:s:users"]
        if self._goal is not None:
            metrics.append(f"ym:s:goal{self._goal}reaches")
        raw = await self._query(begin, end, metrics, product=product)
        totals = raw["totals"]
        if len(totals) != len(metrics):
            raise MetrikaError("Метрика: неполные итоги, числа скрыты")
        lag = raw.get("data_lag")
        lag_value = _count(lag, label="задержка данных") if lag is not None else None
        return LandingStats(product, _count(totals[0], label="визиты"),
                            _count(totals[1], label="посетители"),
                            _count(totals[2], label="достижения цели") if self._goal is not None else None,
                            lag_value)

    @staticmethod
    def _ranked(raw: dict[str, Any], dimension_count: int) -> RankedResult:
        total = raw.get("total_rows")
        if total is None or raw.get("total_rows_rounded") is True:
            raise MetrikaError("Метрика: число строк неизвестно или округлено; список скрыт")
        total_count = _count(total, label="число строк")
        rows = raw["data"]
        if total_count < len(rows) or (total_count and not rows):
            raise MetrikaError("Метрика: список строк неполон")
        result: list[tuple] = []
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("metrics"), list) or len(row["metrics"]) != 1:
                raise MetrikaError("Метрика: в строке отсутствует показатель визитов")
            dims = row.get("dimensions")
            if not isinstance(dims, list) or len(dims) != dimension_count:
                raise MetrikaError("Метрика: в строке отсутствует источник или UTM")
            labels = []
            for dimension in dims:
                if not isinstance(dimension, dict) or "name" not in dimension:
                    raise MetrikaError("Метрика: пустое поле группировки")
                # Explicit undefined labels are allowed; missing dictionary keys aren't.
                labels.append(str(dimension["name"]) if dimension["name"] else "(не определено)")
            result.append((*labels, _count(row["metrics"][0], label="визиты в строке")))
        return RankedResult(result, total_count, total_count > len(rows))

    async def sources(self, begin: date, end: date, product: str) -> RankedResult:
        raw = await self._query(begin, end, ["ym:s:visits"], product=product,
                                dimensions=["ym:s:lastTrafficSource"], limit=100)
        return self._ranked(raw, 1)

    async def utm(self, begin: date, end: date, product: str) -> RankedResult:
        raw = await self._query(begin, end, ["ym:s:visits"], product=product,
                                dimensions=["ym:s:lastUTMSource", "ym:s:lastUTMCampaign"], limit=1000)
        return self._ranked(raw, 2)

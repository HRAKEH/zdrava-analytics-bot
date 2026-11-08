"""Yandex Metrica Reporting API; stats, sources, UTM and optional outbound goal."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any
import httpx

BASE_URL = "https://api-metrika.yandex.net/stat/v1/data"
LANDINGS = {
    "mikozacid": ("Микозацид", "zdrava.top/mikozacid/"),
    "hot_ice": ("Горячий лёд", "zdrava.top/hot-ice/"),
}


class MetrikaError(RuntimeError):
    pass


def _num(value: object) -> int:
    try:
        return int(round(float(value or 0)))
    except (ValueError, TypeError):
        return 0


def _filter_for(product: str) -> str:
    if product not in LANDINGS:
        raise ValueError("Unknown landing")
    # Fixed known URLs only. Metrica supports cross-prefix filters for page hits.
    return "ym:pv:URL=@'" + LANDINGS[product][1] + "'"


@dataclass(frozen=True)
class LandingStats:
    product: str
    visits: int
    users: int
    ozon_goal_reaches: int | None


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
            "ids": self._counter,
            "date1": begin.isoformat(),
            "date2": end.isoformat(),
            "metrics": ",".join(metrics),
            "accuracy": "full",
            "lang": "ru",
            "limit": limit,
            "attribution": "last",
        }
        if product is not None:
            params["filters"] = _filter_for(product)
        if dimensions:
            params["dimensions"] = ",".join(dimensions)
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
            if not isinstance(data, dict) or "data" not in data or "totals" not in data:
                raise MetrikaError("Метрика вернула неожиданный формат ответа")
            if data.get("sampled"):
                raise MetrikaError("Метрика вернула данные с семплированием; отчёт не показываем как точный")
            return data
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (401, 403):
                raise MetrikaError(f"Нет доступа к Метрике (HTTP {status}); проверьте OAuth-токен и счётчик") from exc
            raise MetrikaError(f"Ошибка API Метрики: HTTP {status}") from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise MetrikaError("Сбой связи с API Метрики или некорректный ответ") from exc

    async def totals(self, begin: date, end: date, product: str) -> LandingStats:
        metrics = ["ym:s:visits", "ym:s:users"]
        if self._goal is not None:
            metrics.append(f"ym:s:goal{self._goal}reaches")
        raw = await self._query(begin, end, metrics, product=product)
        totals = raw.get("totals", [])
        if len(totals) != len(metrics):
            raise MetrikaError("Метрика вернула неполные итоги")
        return LandingStats(product, _num(totals[0]), _num(totals[1]),
                            _num(totals[2]) if self._goal is not None else None)

    async def sources(self, begin: date, end: date, product: str) -> list[tuple[str, int]]:
        raw = await self._query(begin, end, ["ym:s:visits"], product=product,
                                dimensions=["ym:s:lastTrafficSource"], limit=100)
        return [((row.get("dimensions") or [{}])[0].get("name", "Не определён"),
                 _num((row.get("metrics") or [0])[0])) for row in raw.get("data", [])]

    async def utm(self, begin: date, end: date, product: str) -> list[tuple[str, str, int]]:
        raw = await self._query(begin, end, ["ym:s:visits"], product=product,
                                dimensions=["ym:s:lastUTMSource", "ym:s:lastUTMCampaign"],
                                limit=1000)
        result = []
        for row in raw.get("data", []):
            dims = row.get("dimensions") or []
            source = str(dims[0].get("name") or "(без источника)") if dims else "(без источника)"
            campaign = str(dims[1].get("name") or "(без кампании)") if len(dims) > 1 else "(без кампании)"
            result.append((source, campaign, _num((row.get("metrics") or [0])[0])))
        return result

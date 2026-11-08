from datetime import date
import json
import pytest
import httpx

from app.services.metrika import MetrikaClient, MetrikaError, _filter_for


@pytest.mark.asyncio
async def test_metrika_totals_query_sends_auth_and_filter():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "OAuth hidden"
        assert request.url.params["filters"] == "ym:pv:URL=@'zdrava.top/mikozacid/'"
        assert request.url.params["ids"] == "113941063"
        assert request.url.params["metrics"].endswith("ym:s:goal123reaches")
        return httpx.Response(200, json={"totals": [15, 10, 3], "data": []})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        m = MetrikaClient("hidden", 113941063, ozon_goal_id=123, client=client)
        r = await m.totals(date(2026, 11, 7), date(2026, 11, 8), "mikozacid")
        assert (r.visits, r.users, r.ozon_goal_reaches) == (15, 10, 3)


@pytest.mark.asyncio
async def test_metrika_error_does_not_leak_body():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "secret-token-123"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        m = MetrikaClient("hidden", 3, client=client)
        with pytest.raises(MetrikaError) as ex:
            await m.totals(date(2026, 11, 8), date(2026, 11, 8), "hot_ice")
        assert "secret-token-123" not in str(ex.value)


def test_product_cannot_be_injected():
    with pytest.raises(ValueError):
        _filter_for("' OR true")

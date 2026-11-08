from datetime import date
import pytest
import httpx

from app.services.metrika import MetrikaClient, MetrikaError, _filter_for


def fixture(totals=None, data=None, **extras):
    return {"sampled": False, "sample_share": 1, "totals": totals if totals is not None else [0, 0],
            "data": data if data is not None else [], **extras}


@pytest.mark.asyncio
async def test_metrika_valid_and_goal_semantics():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "OAuth hidden"
        assert request.url.params["filters"] == "ym:pv:URL=@'zdrava.top/mikozacid/'"
        assert request.url.params["ids"] == "113941063"
        assert request.url.params["metrics"].endswith("ym:s:goal123reaches")
        assert request.url.params["accuracy"] == "full"
        return httpx.Response(200, json=fixture(totals=[15, 10, 3], data_lag=11))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        m = MetrikaClient("hidden", 113941063, ozon_goal_id=123, client=client)
        r = await m.totals(date(2026, 11, 7), date(2026, 11, 8), "mikozacid")
        assert (r.visits, r.users, r.ozon_goal_reaches, r.data_lag_seconds) == (15, 10, 3, 11)


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, "bad", "NaN", "inf", -1, 2.5, True])
async def test_invalid_number_never_becomes_zero(value):
    async def handler(request):
        return httpx.Response(200, json=fixture(totals=[value, 1]))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        m = MetrikaClient("hidden", 113941063, client=client)
        with pytest.raises(MetrikaError):
            await m.totals(date(2026, 11, 8), date(2026, 11, 8), "mikozacid")


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    {"totals": [1, 2], "data": []},  # sampling status unknown
    fixture(totals=[1]),
    fixture(totals=[1, 2], sampled=True),
    fixture(totals=[1, 2], sample_share=0.25),
    fixture(totals=[1, 2], contains_sensitive_data=True),
    {"sampled": False, "totals": [1, 2], "data": None},
])
async def test_incomplete_api_is_rejected(payload):
    async def handler(request):
        return httpx.Response(200, json=payload)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(MetrikaError):
            await MetrikaClient("hidden", 113941063, client=client).totals(
                date(2026, 11, 8), date(2026, 11, 8), "hot_ice")


@pytest.mark.asyncio
async def test_metrika_error_does_not_leak_body():
    async def handler(request):
        return httpx.Response(403, json={"message": "secret-token-123"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        m = MetrikaClient("hidden", 3, client=client)
        with pytest.raises(MetrikaError) as ex:
            await m.totals(date(2026, 11, 8), date(2026, 11, 8), "hot_ice")
        assert "secret-token-123" not in str(ex.value)


@pytest.mark.asyncio
async def test_sources_truncation_is_explicit_and_sorted():
    async def handler(request):
        assert request.url.params["sort"] == "-ym:s:visits"
        assert request.url.params["include_undefined"] == "true"
        return httpx.Response(200, json=fixture(
            totals=[6], total_rows=3, data=[
                {"dimensions":[{"name":"Search"}],"metrics":[4]},
                {"dimensions":[{"name":"Direct"}],"metrics":[2]},
            ]))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await MetrikaClient("hidden", 3, client=client).sources(date(2026,11,7), date(2026,11,8), "hot_ice")
        assert result.truncated is True
        assert result.rows == [("Search",4),("Direct",2)]


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    fixture(totals=[1],total_rows=2,data=[{"dimensions":[{"name":"Search"}],"metrics":[None]}]),
    fixture(totals=[1],total_rows=2,data=[{"dimensions":[],"metrics":[1]}]),
    fixture(totals=[1],total_rows_rounded=True,total_rows=2,data=[]),
    fixture(totals=[1],data=[]),
])
async def test_missing_ranked_dimension_or_count_is_rejected(payload):
    async def handler(request):
        return httpx.Response(200, json=payload)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(MetrikaError):
            await MetrikaClient("hidden",3,client=client).sources(date(2026,11,7),date(2026,11,8),"mikozacid")


def test_product_cannot_be_injected():
    with pytest.raises(ValueError):
        _filter_for("' OR true")

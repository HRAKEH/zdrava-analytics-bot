from datetime import date
from decimal import Decimal
import pytest

from app.services.direct import parse_report_tsv, parse_campaigns, DirectError
from app.services.preferences import Preferences, CampaignAssignments
from app.storage import JsonStore


def test_direct_report_parses_without_fabricating_rows():
    s = "CampaignId\tImpressions\tClicks\tCost\n123\t55\t6\t42.75\n"
    result = parse_report_tsv(s, {123})
    assert result[123].cost == Decimal("42.75")
    assert result[123].clicks == 6
    assert parse_report_tsv("CampaignId\tImpressions\tClicks\tCost\n", {123}) == {}


@pytest.mark.parametrize("input", [
    "CampaignId\tImpressions\tClicks\tCost\n124\t55\t6\t42.75\n",
    "CampaignId\tImpressions\tClicks\tCost\n123\t55\t6\t42.751\n",
    "CampaignId\tImpressions\tClicks\tCost\n123\t55\t6\tNaN\n",
    "CampaignId\tImpressions\tClicks\tCost\n123\t55\tbad\t42.75\n",
    "bad\theader\n123\t55\t6\t42.75\n",
    "CampaignId\tImpressions\tClicks\tCost\n123\t55\t6\t42.75\n123\t55\t6\t42.75\n",
])
def test_direct_report_rejects_bad_data(input):
    with pytest.raises(DirectError):
        parse_report_tsv(input, {123})


def test_campaigns_reject_partial_pages():
    with pytest.raises(DirectError):
        parse_campaigns({"result":{"Campaigns":[],"LimitedBy":1000}})


def test_user_preferences_persist_with_validation(tmp_path):
    store=JsonStore(tmp_path)
    pref=Preferences(store,{"daily_enabled":True,"daily_time":"09:00", "backup_enabled":True,
        "backup_time":"03:00", "backup_send_telegram":False, "retention_days":7,"backup_max_count":10})
    assert pref.get()["daily_time"]=="09:00"
    assert pref.set("daily_time","08:30")["daily_time"]=="08:30"
    assert Preferences(store,pref.defaults).get()["daily_time"]=="08:30"
    for bad in ("25:90", "foobar"):
        with pytest.raises(ValueError): pref.set("daily_time",bad)
    with pytest.raises(ValueError): pref.set("retention_days",0)
    with pytest.raises(ValueError): pref.set("yandex_direct_token","secret")


def test_one_campaign_cannot_be_counted_for_two_products(tmp_path):
    mappings=CampaignAssignments(JsonStore(tmp_path))
    mappings.assign("mikozacid",123)
    assert mappings.get()["mikozacid"]==[123]
    mappings.assign("hot_ice",123)
    assert mappings.get()["mikozacid"]==[]
    assert mappings.get()["hot_ice"]==[123]
    mappings.remove("hot_ice",123)
    assert mappings.get()["hot_ice"]==[]


def test_user_prefs_and_direct_assignment_are_backed_up():
    from app.services.backups import FILES
    assert "user_settings.json" in FILES and "direct_campaigns.json" in FILES

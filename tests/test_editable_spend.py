from decimal import Decimal
import pytest
from app.storage import JsonStore
from app.services.preferences import Preferences

def test_remove_manual_spend_is_not_zero(tmp_path):
    store = JsonStore(tmp_path)
    store.set_spend("mikozacid", "2026-11-01", Decimal("999.00"))
    assert store.delete_spend("mikozacid", "2026-11-01")
    assert store.read("ad_spend.json", {}) == {}
    assert not store.delete_spend("mikozacid", "2026-11-01")

def test_settings_validate_max_zip_size(tmp_path):
    defaults={
        "daily_enabled":True,"daily_time":"09:00","backup_enabled":True,
        "backup_time":"03:00","backup_send_telegram":False,
        "retention_days":7,"backup_max_count":10,"backup_max_total_mb":512}
    prefs=Preferences(JsonStore(tmp_path), defaults)
    assert prefs.set("backup_max_total_mb",128)["backup_max_total_mb"]==128
    with pytest.raises(ValueError):
        prefs.set("backup_max_total_mb",5000)

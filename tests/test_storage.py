import json
from app.storage import JsonStore


def test_storage_atomic_and_spend_overwrite(tmp_path):
    store = JsonStore(tmp_path)
    store.set_spend("mikozacid", "2026-11-08", 5.25)
    store.set_spend("mikozacid", "2026-11-09", 7)
    store.set_spend("mikozacid", "2026-11-08", 11)
    assert store.spend_for("mikozacid", "2026-11-08", "2026-11-09") == 18
    assert store.spend_for("hot_ice", "2026-11-08", "2026-11-09") == 0
    assert json.loads((tmp_path / "ad_spend.json").read_text())["mikozacid"]["2026-11-08"] == 11


def test_storage_filename_safety(tmp_path):
    import pytest
    store = JsonStore(tmp_path)
    with pytest.raises(ValueError):
        store.write("../other.json", {})

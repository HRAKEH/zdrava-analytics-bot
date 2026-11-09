from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from app.services.daily_digest import compact_daily_digest


def test_short_daily_report_keeps_missing_and_partials_explicit():
    stats = [SimpleNamespace(product="mikozacid", visits=12, users=10,
                             ozon_goal_reaches=None, data_lag_seconds=None),
             SimpleNamespace(product="hot_ice", visits=8, users=7,
                             ozon_goal_reaches=0, data_lag_seconds=120)]
    spend = {"mikozacid": SimpleNamespace(complete=False, has_data=False,
                                           recorded_days=0, expected_days=1),
             "hot_ice": SimpleNamespace(complete=True, has_data=True,
                                        recorded_amount=Decimal("512.50"),
                                        recorded_days=1, expected_days=1)}
    text = compact_daily_digest(date(2026,11,8), stats, spend,
                                {"mikozacid": "Микозацид", "hot_ice": "Горячий лёд"})
    assert "Цель Ozon: 0" not in text
    assert "Ozon (цель): не настроена" in text
    assert "Ozon (цель): 0" in text
    assert "нет данных" in text and "512.50 ₽" in text
    assert "задержке" in text and len(text) < 500

from datetime import date
from app.services.reporting import display_summary
from app.services.metrika import LandingStats


def test_summary_never_calls_goals_orders():
    text = display_summary(date(2026, 11, 8), date(2026, 11, 8), "Вчера", [
        LandingStats("mikozacid", 100, 80, 4),
        LandingStats("hot_ice", 50, 45, None),
    ], {"mikozacid": 500.0})
    assert "Переход на Ozon" in text
    assert "не равен заказу" in text
    assert "500.00 ₽" in text
    assert "цель Метрики не настроена" in text

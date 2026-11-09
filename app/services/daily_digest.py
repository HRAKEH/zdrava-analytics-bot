"""Short daily messages without inventing metrics or silently rounding money."""
from __future__ import annotations

from datetime import date
from typing import Mapping, Iterable


def compact_daily_digest(day: date, stats: Iterable, spending: Mapping, labels: Mapping[str, str]) -> str:
    lines = [f"📬 ЗДРАВА · {day:%d.%m.%Y}"]
    delayed = False
    for stat in stats:
        label = labels[stat.product]
        coverage = spending[stat.product]
        if coverage.complete:
            money = f"{coverage.recorded_amount:,.2f}".replace(",", " ") + " ₽ (вручную)"
        elif coverage.has_data:
            money = f"неполно ({coverage.recorded_days}/{coverage.expected_days} дн.)"
        else:
            money = "нет данных"
        goal = str(stat.ozon_goal_reaches) if stat.ozon_goal_reaches is not None else "не настроена"
        lines.append(f"\n{label}: {stat.visits:,} виз. · {stat.users:,} пос.".replace(",", " "))
        lines.append(f"Ozon (цель): {goal} · Реклама: {money}")
        delayed |= bool(getattr(stat, "data_lag_seconds", None) and stat.data_lag_seconds > 0)
    if delayed:
        lines.append("⚠️ Метрика сообщает о задержке обновления.")
    lines.append("ℹ️ Цель Ozon ≠ заказ; расходы внесены вручную.")
    return "\n".join(lines)

"""Formatting and date windows, deliberately free of invented attribution."""
from __future__ import annotations

from datetime import date, timedelta
from zoneinfo import ZoneInfo
from datetime import datetime

from app.services.metrika import LANDINGS, LandingStats


WINDOWS = {"today": ("Сегодня", 0), "yesterday": ("Вчера", 1),
           "week": ("7 дней", 7), "month": ("30 дней", 30)}


def period(key: str, timezone: str) -> tuple[date, date, str]:
    if key not in WINDOWS:
        raise ValueError("Unknown period")
    today = datetime.now(ZoneInfo(timezone)).date()
    title, width = WINDOWS[key]
    if key == "today":
        return today, today, title
    if key == "yesterday":
        prior = today - timedelta(days=1)
        return prior, prior, title
    end = today - timedelta(days=1)  # closed days only
    return end - timedelta(days=width - 1), end, title


def display_summary(begin: date, end: date, title: str,
                    stats: list[LandingStats], spend: dict[str, float]) -> str:
    lines = [f"📊 ЗДРАВА — {title}", f"{begin:%d.%m.%Y} — {end:%d.%m.%Y}"]
    for item in stats:
        name = LANDINGS[item.product][0]
        lines.extend(["", f"📍 {name}", f"Визиты: {item.visits:,}",
                      f"Посетители: {item.users:,}"])
        if item.ozon_goal_reaches is not None:
            lines.append(f"Достижения цели «Переход на Ozon»: {item.ozon_goal_reaches:,}")
        else:
            lines.append("Переходы на Ozon: цель Метрики не настроена")
        if item.product in spend and spend[item.product] > 0:
            lines.append(f"Расход на рекламу (внесён вручную): {spend[item.product]:,.2f} ₽")
    lines += ["", "ℹ️ Показатели страниц могут пересекаться в рамках одного визита.",
              "Переход на Ozon не равен заказу. Расходы без привязки к UTM."]
    return "\n".join(lines).replace(",", " ")


def display_ranked(title: str, rows: list[tuple[str, int]], limit: int = 12) -> str:
    if not rows:
        return title + "\nДанных за период нет."
    lines = [title]
    for name, visits in rows[:limit]:
        lines.append(f"• {name[:90]}: {visits} визитов")
    if len(rows) > limit:
        lines.append(f"Ещё строк: {len(rows)-limit}")
    return "\n".join(lines)

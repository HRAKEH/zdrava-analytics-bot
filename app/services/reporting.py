"""Period, display and monetary calculations without silent fallback to zero."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.services.metrika import LANDINGS, LandingStats, RankedResult
from app.storage import SpendCoverage

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
    end = today - timedelta(days=1)
    return end - timedelta(days=width - 1), end, title


def format_money(amount: Decimal) -> str:
    return f"{amount:,.2f}".replace(",", " ") + " ₽"


def spend_text(coverage: SpendCoverage, *, inline: bool = False) -> str:
    if not coverage.has_data:
        return "нет введённых данных (это не 0 ₽)"
    value = format_money(coverage.recorded_amount)
    if coverage.complete:
        return f"{value} (внесено вручную, {coverage.expected_days}/{coverage.expected_days} дн.)"
    return (f"учтено {value} только за {coverage.recorded_days}/{coverage.expected_days} дн.; "
            "расход за весь период неизвестен")


def display_summary(begin: date, end: date, title: str,
                    stats: list[LandingStats], spend: dict[str, SpendCoverage]) -> str:
    lines = [f"📊 ЗДРАВА — {title}", f"{begin:%d.%m.%Y} — {end:%d.%m.%Y}"]
    if title == "Сегодня":
        lines.append("⚠️ Сегодняшний день не завершён — статистика предварительная.")
    for item in stats:
        name = LANDINGS[item.product][0]
        lines.extend(["", f"📍 {name}", f"Визиты с просмотром страницы: {item.visits:,}".replace(",", " "),
                      f"Посетители этих визитов: {item.users:,}".replace(",", " ")])
        if item.ozon_goal_reaches is not None:
            lines.append(f"Достижения цели Ozon в визитах с просмотром страницы: {item.ozon_goal_reaches:,}".replace(",", " "))
        else:
            lines.append("Цель перехода в Ozon: не настроена, показатель недоступен")
        lines.append("Рекламные расходы: " + spend_text(spend[item.product]))
    lines += ["", "ℹ️ Визит может включать обе страницы, а цель — происходить в другом месте визита.",
              "Достижения цели ≠ уникальные переходы ≠ заказы. Расходы вручную, без привязки к UTM."]
    return "\n".join(lines)


def display_ranked(title: str, result: RankedResult, limit: int = 12) -> str:
    if not result.rows:
        return title + "\nСтрок в отчёте нет (не подменяем это числом визитов)."
    lines = [title, f"Показаны {min(limit, len(result.rows))} наиболее посещаемых строк (по Метрике)."]
    for row in result.rows[:limit]:
        *dimensions, visits = row
        lines.append(f"• {' / '.join(str(x) for x in dimensions)[:90]}: {visits} визитов")
    if result.truncated:
        lines.append("⚠️ API вернул только часть группировок; полный список не загружен, итог не рассчитывается.")
    elif len(result.rows) > limit:
        lines.append(f"Остальные {len(result.rows)-limit} строк не показаны. Это не общий итог.")
    return "\n".join(lines)

"""Telegram menu and commands, accessible only to configured owners."""
from __future__ import annotations

import asyncio
from datetime import date, datetime

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.config import Config
from app.services.metrika import LANDINGS, MetrikaClient, MetrikaError
from app.services.reporting import display_ranked, display_summary, period
from app.services.search_console import SearchConsoleClient, SearchConsoleError
from app.services.ozon import OZON_ATTRIBUTION_STATUS
from app.storage import JsonStore


MENU = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="📊 Сегодня", callback_data="report:today"),
     InlineKeyboardButton(text="📊 Вчера", callback_data="report:yesterday")],
    [InlineKeyboardButton(text="📅 7 дней", callback_data="report:week"),
     InlineKeyboardButton(text="📅 30 дней", callback_data="report:month")],
    [InlineKeyboardButton(text="🌐 Источники", callback_data="sources:week"),
     InlineKeyboardButton(text="🏷 UTM", callback_data="utm:week")],
    [InlineKeyboardButton(text="🔎 Google", callback_data="search:week"),
     InlineKeyboardButton(text="⚙️ Статус", callback_data="status")],
    [InlineKeyboardButton(text="✅ Проверить API", callback_data="check")],
])


class AnalyticsBot:
    def __init__(self, config: Config, store: JsonStore):
        self.config = config
        self.store = store
        self.metrika = MetrikaClient(config.metrika_token, config.metrika_counter_id,
            ozon_goal_id=config.metrika_ozon_goal_id, timeout=config.http_timeout)
        self.gsc = SearchConsoleClient(config.gsc_property,
            config.gsc_service_account_file, timeout=config.http_timeout,
            service_account_b64=config.gsc_service_account_b64)
        self._write_lock = asyncio.Lock()

    def allowed(self, user_id: int | None) -> bool:
        return user_id is not None and user_id in self.config.owner_ids

    def status(self) -> str:
        return "\n".join([
            "⚙️ ЗДРАВА | Аналитика — подключения",
            "Яндекс.Метрика: " + ("✅ токен задан (доступ проверяется при запросе)" if self.metrika.connected else "❌ METRIKA_TOKEN отсутствует"),
            f"Счётчик: {self.config.metrika_counter_id}",
            "Цель перехода в Ozon: " + (str(self.config.metrika_ozon_goal_id) if self.config.metrika_ozon_goal_id else "не задана"),
            "Google Search Console: " + ("✅ ключ найден (доступ проверяется при запросе)" if self.gsc.connected else "не настроен"),
            "Ozon (заказы из внешнего трафика): " + OZON_ATTRIBUTION_STATUS,
            "Wildberries: не подключаем по решению проекта.",
            f"Хранилище: {self.config.data_dir}",
            "Ежедневный отчёт: " + (f"{self.config.daily_report_time} ({self.config.timezone})" if self.config.daily_report_enabled else "выключен"),
        ])

    async def report(self, key: str) -> str:
        begin, end, title = period(key, self.config.timezone)
        stats = []
        for product in LANDINGS:
            stats.append(await self.metrika.totals(begin, end, product))
        spend = {p: self.store.spend_for(p, begin.isoformat(), end.isoformat()) for p in LANDINGS}
        report = display_summary(begin, end, title, stats, spend)
        # Snapshot contains only aggregated data, never OAuth tokens or visitor IDs.
        self.store.write("last_report.json", {
            "period": key, "from": begin.isoformat(), "to": end.isoformat(),
            "products": {s.product: {
                "visits": s.visits, "users": s.users,
                "ozon_goal_reaches": s.ozon_goal_reaches,
                "spend_rub_manual": spend[s.product],
            } for s in stats},
        })
        return report

    async def sources(self, key: str) -> str:
        begin, end, title = period(key, self.config.timezone)
        parts = [f"🌐 Источники — {title}"]
        for product, (label, _) in LANDINGS.items():
            rows = await self.metrika.sources(begin, end, product)
            parts.append(display_ranked("\n" + label, rows))
        return "\n".join(parts)

    async def utm(self, key: str) -> str:
        begin, end, title = period(key, self.config.timezone)
        parts = [f"🏷 UTM-источник / кампания — {title}"]
        for product, (label, _) in LANDINGS.items():
            rows = await self.metrika.utm(begin, end, product)
            short = [(f"{source} / {campaign}", visits) for source, campaign, visits in rows]
            parts.append(display_ranked("\n" + label, short))
        parts.append("\nUTM-метки учитываются только если они переданы при посещении сайта.")
        return "\n".join(parts)

    async def search(self, key: str) -> str:
        begin, end, title = period(key, self.config.timezone)
        parts = [f"🔎 Google Search Console — {title}",
                 "Данные Google обычно поступают с задержкой."]
        for _, (label, page_url) in LANDINGS.items():
            path = "/" + page_url.split("/", 1)[1]
            stat = await self.gsc.by_landing(begin, end, path)
            parts += ["", label,
                      f"Клики: {stat.clicks} | Показы: {stat.impressions}",
                      f"CTR: {stat.ctr * 100:.2f}% | Средняя позиция: {stat.position:.1f}"]
        return "\n".join(parts)

    async def check(self) -> str:
        """Real API access check. Never echoes tokens or raw API error bodies."""
        begin, end, _ = period("yesterday", self.config.timezone)
        parts = ["✅ Проверка подключений"]
        try:
            result = await self.metrika.totals(begin, end, "mikozacid")
            parts.append(f"Метрика: доступ есть (Микозацид, визиты вчера: {result.visits})")
        except MetrikaError as exc:
            parts.append("Метрика: ошибка — " + str(exc))
        if self.gsc.connected:
            try:
                await self.gsc.by_landing(begin, end, "/mikozacid/")
                parts.append("Google Search Console: доступ есть")
            except SearchConsoleError as exc:
                parts.append("Google Search Console: ошибка — " + str(exc))
        else:
            parts.append("Google Search Console: не настроен (необязательно)")
        try:
            self.store.write("health.json", {"last_checked": datetime.now().isoformat()})
            parts.append("/app/data: запись JSON работает")
        except OSError:
            parts.append("/app/data: ошибка записи, проверьте права доступа")
        return "\n".join(parts)

    async def handle_action(self, name: str) -> str:
        if name == "check":
            return await self.check()
        if name == "status":
            return self.status()
        if name.startswith("report:"):
            return await self.report(name.partition(":")[2])
        if name.startswith("sources:"):
            return await self.sources(name.partition(":")[2])
        if name.startswith("utm:"):
            return await self.utm(name.partition(":")[2])
        if name.startswith("search:"):
            return await self.search(name.partition(":")[2])
        raise ValueError("Unknown command")

    async def set_spend(self, text: str) -> str:
        parts = text.strip().split()
        if len(parts) != 4:
            return "Формат: /spend mikozacid 2026-11-08 1200.50\nТовары: mikozacid, hot_ice. Команда заменяет сумму за дату."
        _, product, day, amount_raw = parts
        if product not in LANDINGS:
            return "Неизвестный товар. Допустимо: mikozacid или hot_ice."
        try:
            parsed_day = date.fromisoformat(day)
            amount = float(amount_raw.replace(",", "."))
            if not (0 <= amount <= 100_000_000) or amount != amount or parsed_day.isoformat() != day:
                raise ValueError
        except ValueError:
            return "Некорректные дата или сумма. Пример: /spend hot_ice 2026-11-08 500"
        async with self._write_lock:
            self.store.set_spend(product, day, amount)
        return f"✅ {LANDINGS[product][0]}: {day} — расход {amount:.2f} ₽ сохранён. Это ручной учёт, не данные рекламного API."


def build_dispatcher(service: AnalyticsBot) -> Dispatcher:
    router = Router()
    dp = Dispatcher()

    @router.message(Command("id"))
    async def get_id(message: Message) -> None:
        uid = message.from_user.id if message.from_user else None
        await message.answer(f"Ваш Telegram ID: {uid}\nДобавьте его в TELEGRAM_OWNER_IDS на Bothost.")

    @router.message(CommandStart())
    async def start(message: Message) -> None:
        if not service.allowed(message.from_user.id if message.from_user else None):
            await message.answer("Доступ закрыт. Узнать свой ID: /id")
            return
        await message.answer("ЗДРАВА | Аналитика\nДва лендинга, один отчёт. Выберите действие:", reply_markup=MENU)

    @router.message(Command("help"))
    async def help_cmd(message: Message) -> None:
        if not service.allowed(message.from_user.id if message.from_user else None):
            return
        await message.answer("/start — меню\n/today, /yesterday, /week, /month — Метрика\n/sources, /utm — источники и UTM за 7 дней\n/search — Google за 7 дней\n/status — настройки\n/check — реальная проверка API и записи JSON\n/spend <mikozacid|hot_ice> <YYYY-MM-DD> <сумма> — записать рекламный расход за дату\n/id — ваш Telegram ID")

    @router.message(Command("spend"))
    async def spend(message: Message) -> None:
        if not service.allowed(message.from_user.id if message.from_user else None):
            return
        await message.answer(await service.set_spend(message.text or ""))

    @router.message(Command("today", "yesterday", "week", "month", "sources", "utm", "search", "status", "check"))
    async def other(message: Message) -> None:
        if not service.allowed(message.from_user.id if message.from_user else None):
            return
        command = (message.text or "").split()[0].split("@")[0].lstrip("/")
        action = command if command in ("status", "check") else command + (":week" if command in ("sources", "utm", "search") else "")
        if command in ("today", "yesterday", "week", "month"):
            action = "report:" + command
        try:
            await message.answer(await service.handle_action(action), reply_markup=MENU)
        except (MetrikaError, SearchConsoleError, ValueError) as exc:
            await message.answer("⚠️ " + str(exc), reply_markup=MENU)
        except Exception:
            import logging
            logging.exception("Failed report")
            await message.answer("⚠️ Внутренняя ошибка отчёта. Проверьте журнал Bothost.")

    @router.callback_query(F.data.in_({
        "report:today", "report:yesterday", "report:week", "report:month",
        "sources:week", "utm:week", "search:week", "status", "check",
    }))
    async def inline(query: CallbackQuery) -> None:
        if not service.allowed(query.from_user.id if query.from_user else None):
            await query.answer("Нет доступа", show_alert=True)
            return
        await query.answer()
        try:
            response = await service.handle_action(query.data or "")
            if query.message:
                await query.message.answer(response, reply_markup=MENU)
        except (MetrikaError, SearchConsoleError, ValueError) as exc:
            if query.message:
                await query.message.answer("⚠️ " + str(exc), reply_markup=MENU)
        except Exception:
            import logging
            logging.exception("Failed callback report")
            if query.message:
                await query.message.answer("⚠️ Внутренняя ошибка отчёта. Проверьте журнал Bothost.")

    dp.include_router(router)
    return dp

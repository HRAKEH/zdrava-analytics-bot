"""Telegram menus and commands for ZDRAVA analytics, accessible only to owners."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from aiogram import Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.config import Config
from app.services.metrika import LANDINGS, MetrikaClient, MetrikaError
from app.services.reporting import display_ranked, display_summary, period, spend_text, format_money
from app.services.search_console import SearchConsoleClient, SearchConsoleError
from app.services.ozon import OZON_ATTRIBUTION_STATUS
from app.storage import JsonStore, money_amount, DataIntegrityError
from app.services.backups import BackupService, BackupOptions
from app.services.backup_handlers import register_backup_handlers
from app.services.daily_digest import compact_daily_digest


def keyboard(*rows: tuple[tuple[str, str], ...]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=title, callback_data=callback) for title, callback in row]
        for row in rows
    ])


HOME = keyboard(
    (("📊 Трафик", "nav:traffic"), ("📣 Реклама", "nav:ads")),
    (("🔎 SEO / Google", "nav:seo"), ("⚙️ Система", "nav:system")),
    (("❔ Помощь", "help"),),
)
TRAFFIC = keyboard(
    (("Сегодня", "report:today"), ("Вчера", "report:yesterday")),
    (("7 дней", "report:week"), ("30 дней", "report:month")),
    (("🌐 Источники", "sources:week"), ("🏷 UTM-метки", "utm:week")),
    (("🏠 Главное меню", "nav:home"),),
)
ADS = keyboard(
    (("💰 Добавить расход", "spend:start"),),
    (("💵 Расходы 7 дней", "expenses:week"), ("💵 30 дней", "expenses:month")),
    (("🏷 UTM-кампании", "utm:week"), ("🌐 Источники", "sources:week")),
    (("🛒 Ozon: статус", "ozon"),),
    (("🏠 Главное меню", "nav:home"),),
)
SEO = keyboard(
    (("🔎 Поиск Google · 7 дней", "search:week"),),
    (("🏠 Главное меню", "nav:home"),),
)
SYSTEM = keyboard(
    (("⚙️ Подключения", "status"), ("✅ Проверить API", "check")),
    (("🗄 Резервные копии", "nav:backups"),),
    (("🪪 Мой Telegram ID", "id"), ("❔ Помощь", "help")),
    (("🏠 Главное меню", "nav:home"),),
)
BACKUPS = keyboard(
    (("💾 Создать ZIP", "backup:manual"), ("📤 Скачать последний", "backup:latest")),
    (("📚 Список архивов", "backup:list"), ("🧹 Очистить старые", "backup:prune")),
    (("⬅️ В систему", "nav:system"),),
)
SPEND_PRODUCTS = keyboard(
    (("🧴 Микозацид", "spend:product:mikozacid"),),
    (("🔥 Горячий лёд", "spend:product:hot_ice"),),
    (("⬅️ К рекламе", "nav:ads"),),
)
SPEND_DATES = keyboard(
    (("Сегодня", "spend:date:today"), ("Вчера", "spend:date:yesterday")),
    (("📆 Другая дата", "spend:date:custom"),),
    (("⬅️ К товарам", "spend:start"), ("✖️ Отмена", "spend:cancel")),
)
SPEND_CANCEL = keyboard((("✖️ Отмена", "spend:cancel"),))

SECTIONS = {
    "home": ("ЗДРАВА | Аналитика\nВыберите раздел:", HOME),
    "traffic": ("📊 Трафик\nПосещения, периоды, источники и UTM:", TRAFFIC),
    "ads": ("📣 Реклама\nРучной учёт расходов и результаты переходов:", ADS),
    "seo": ("🔎 SEO / Google\nПоисковая статистика двух лендингов:", SEO),
    "system": ("⚙️ Система\nПодключения и диагностика:", SYSTEM),
    "backups": ("🗄 Резервные копии\nАрхивы JSON в /app/data/backups:", BACKUPS),
}

ACTIONS = frozenset({
    "report:today", "report:yesterday", "report:week", "report:month",
    "sources:week", "utm:week", "search:week", "expenses:week", "expenses:month",
    "ozon", "status", "check", "id", "help",
})


class SpendFlow(StatesGroup):
    custom_date = State()
    amount = State()


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
        self.backup_options = BackupOptions.from_env()
        self.backups = BackupService(config.data_dir,
            retention_days=self.backup_options.retention_days,
            max_count=self.backup_options.max_count,
            max_total_mb=self.backup_options.max_total_mb)

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
        spend = {p: self.store.spend_summary(p, begin.isoformat(), end.isoformat()) for p in LANDINGS}
        report = display_summary(begin, end, title, stats, spend)
        # Persist raw API counts and explicit spending coverage; do not cache missing as zero.
        self.store.write("last_report.json", {
            "period": key, "from": begin.isoformat(), "to": end.isoformat(),
            "source": "Yandex Metrica", "is_preliminary": key == "today",
            "products": {s.product: {
                "visits": s.visits, "users": s.users,
                "ozon_goal_reaches": s.ozon_goal_reaches,
                "spend_rub_manual_recorded": str(spend[s.product].recorded_amount) if spend[s.product].has_data else None,
                "spend_recorded_days": spend[s.product].recorded_days,
                "spend_expected_days": spend[s.product].expected_days,
                "spend_complete": spend[s.product].complete,
            } for s in stats},
        })
        return report

    async def daily_digest(self) -> str:
        """Short daily push; complete reports remain available via Traffic menu."""
        begin, end, _ = period("yesterday", self.config.timezone)
        stats = [await self.metrika.totals(begin, end, product) for product in LANDINGS]
        spend = {p: self.store.spend_summary(p, begin.isoformat(), end.isoformat()) for p in LANDINGS}
        return compact_daily_digest(end, stats, spend,
            {name: info[0] for name, info in LANDINGS.items()})

    async def expenses(self, key: str) -> str:
        begin, end, title = period(key, self.config.timezone)
        coverage = {p: self.store.spend_summary(p, begin.isoformat(), end.isoformat()) for p in LANDINGS}
        lines = [f"💵 Реклама — расходы за {title.lower()}",
                 f"{begin:%d.%m.%Y} — {end:%d.%m.%Y}", ""]
        for product, item in coverage.items():
            label = LANDINGS[product][0]
            if item.complete:
                lines.append(f"{label}: {item.recorded_amount:,.2f} ₽ — внесено за все {item.expected_days} дн.")
            elif item.has_data:
                lines.append(f"{label}: неполные данные ({item.recorded_days}/{item.expected_days} дн.); "
                             f"внесено {item.recorded_amount:,.2f} ₽, НЕ итог за период")
            else:
                lines.append(f"{label}: нет внесённых расходов, сумма неизвестна")
        if all(item.complete for item in coverage.values()):
            total = sum((item.recorded_amount for item in coverage.values()), Decimal("0.00"))
            lines += [f"Итого по двум товарам: {total:,.2f} ₽", ""]
        else:
            lines += ["Общий итог не рассчитывается: есть дни без введённых расходов.", ""]
        lines.append("Ручные записи без привязки к UTM, не данные рекламного API. Ноль учитывается только когда внесён явно.")
        return "\n".join(lines).replace(",", " ")

    async def sources(self, key: str) -> str:
        begin, end, title = period(key, self.config.timezone)
        parts = [f"🌐 Источники — {title}"]
        if key == "today":
            parts.append("⚠️ Сегодняшняя статистика предварительная.")
        for product, (label, _) in LANDINGS.items():
            ranked = await self.metrika.sources(begin, end, product)
            parts.append(display_ranked("\n" + label, ranked))
        return "\n".join(parts)

    async def utm(self, key: str) -> str:
        begin, end, title = period(key, self.config.timezone)
        parts = [f"🏷 UTM-источник / кампания — {title}"]
        if key == "today":
            parts.append("⚠️ Сегодняшняя статистика предварительная.")
        for product, (label, _) in LANDINGS.items():
            ranked = await self.metrika.utm(begin, end, product)
            parts.append(display_ranked("\n" + label, ranked))
        parts.append("\nUTM-метки учитываются только если они переданы при посещении сайта.")
        return "\n".join(parts)

    async def search(self, key: str) -> str:
        begin, end, title = period(key, self.config.timezone)
        parts = [f"🔎 Google Search Console — {title}",
                 "Только финализированные данные Google; полноту каждого дня диапазона API не подтверждает."]
        for _, (label, page_url) in LANDINGS.items():
            path = "/" + page_url.split("/", 1)[1]
            stat = await self.gsc.by_landing(begin, end, path)
            parts += ["", label]
            if not stat.available:
                parts.append("Нет подтверждённых данных за этот период (Google не вернул строку). Это НЕ означает 0 кликов или показов.")
            else:
                parts.append(f"Клики: {stat.clicks} | Показы: {stat.impressions}")
                parts.append(f"CTR: {stat.ctr * 100:.2f}%" if stat.ctr is not None else "CTR: не рассчитывается (0 показов)")
                parts.append(f"Средняя позиция: {stat.position:.1f}" if stat.position is not None else "Средняя позиция: нет данных")
        return "\n".join(parts)

    async def check(self) -> str:
        """Real API access check. Never echoes tokens or raw API error bodies."""
        begin, end, _ = period("yesterday", self.config.timezone)
        parts = ["🔎 Результат проверки подключений"]
        try:
            result = await self.metrika.totals(begin, end, "mikozacid")
            parts.append(f"Метрика: доступ есть (Микозацид, визиты вчера: {result.visits})")
        except MetrikaError as exc:
            parts.append("Метрика: ошибка — " + str(exc))
        if self.gsc.connected:
            try:
                gsc_result = await self.gsc.by_landing(begin, end, "/mikozacid/")
                parts.append("Google Search Console: API ответил; " +
                             ("есть данные" if gsc_result.available else "за период нет строк — 0 не предполагаем"))
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
        if name == "id":
            raise ValueError("Telegram ID handled separately")
        if name == "help":
            return HELP_TEXT
        if name == "ozon":
            return "🛒 Ozon\n" + OZON_ATTRIBUTION_STATUS + "\n\nПереходы на Ozon отражаются в отчётах Метрики только при заданной цели METRIKA_OZON_GOAL_ID. Переход не равен покупке."
        if name.startswith("report:"):
            return await self.report(name.partition(":")[2])
        if name.startswith("expenses:"):
            return await self.expenses(name.partition(":")[2])
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
            amount = money_amount(amount_raw)
            if parsed_day.isoformat() != day or parsed_day > datetime.now(ZoneInfo(self.config.timezone)).date():
                raise ValueError
        except ValueError:
            return "Некорректные дата или сумма. Пример: /spend hot_ice 2026-11-08 500"
        async with self._write_lock:
            self.store.set_spend(product, day, amount)
        return f"✅ {LANDINGS[product][0]}: {day} — ручной расход {format_money(amount)} сохранён. Это запись за один день, не полный отчёт и не данные рекламного API."


HELP_TEXT = (
    "❔ ЗДРАВА | Аналитика\n"
    "Выберите тематический раздел в /start. Все функции доступны кнопками.\n\n"
    "📊 Трафик — сегодня, вчера, 7/30 дней, источники и UTM.\n"
    "📣 Реклама — внесение расходов по товару, суммы за период, UTM и статус Ozon.\n"
    "🔎 SEO — Google Search Console.\n"
    "⚙️ Система — настройки, проверка API, архивы ZIP, ваш ID.\n\n"
    "Команды также доступны вручную: /today /yesterday /week /month "
    "/sources /utm /search /status /check /id /spend /cancel "
    "/backup /backups /backup_latest /backup_cleanup.\n"
    "Ограничение: переходы на маркетплейс не доказывают продажи."
)


def section_for_action(action: str) -> InlineKeyboardMarkup:
    if action.startswith("report:") or action.startswith("sources:"):
        return TRAFFIC
    if action.startswith("expenses:") or action in {"ozon", "utm:week"}:
        return ADS
    if action.startswith("search:"):
        return SEO
    return SYSTEM


def build_dispatcher(service: AnalyticsBot) -> Dispatcher:
    router = Router()
    dp = Dispatcher()

    def user_allowed(user) -> bool:
        return service.allowed(user.id if user else None)

    @router.message(Command("id"))
    async def get_id(message: Message) -> None:
        uid = message.from_user.id if message.from_user else None
        await message.answer(f"Ваш Telegram ID: {uid}\nДобавьте его в TELEGRAM_OWNER_IDS на Bothost.", reply_markup=SYSTEM if service.allowed(uid) else None)

    @router.message(CommandStart())
    async def start(message: Message, state: FSMContext) -> None:
        await state.clear()
        if not user_allowed(message.from_user):
            await message.answer("Доступ закрыт. Узнать свой ID: /id")
            return
        text, markup = SECTIONS["home"]
        await message.answer(text, reply_markup=markup)

    @router.message(Command("cancel"))
    async def cancel(message: Message, state: FSMContext) -> None:
        await state.clear()
        if user_allowed(message.from_user):
            await message.answer("Действие отменено.", reply_markup=HOME)

    @router.message(Command("help"))
    async def help_cmd(message: Message) -> None:
        if user_allowed(message.from_user):
            await message.answer(HELP_TEXT, reply_markup=SYSTEM)

    @router.message(Command("spend"))
    async def spend(message: Message, state: FSMContext) -> None:
        if not user_allowed(message.from_user):
            return
        if len((message.text or "").strip().split()) > 1:
            await state.clear()
            await message.answer(await service.set_spend(message.text or ""), reply_markup=ADS)
        else:
            await state.clear()
            await message.answer("💰 Выберите товар, для которого вносите расход:", reply_markup=SPEND_PRODUCTS)

    @router.message(Command("today", "yesterday", "week", "month", "sources", "utm", "search", "status", "check"))
    async def other(message: Message, state: FSMContext) -> None:
        if not user_allowed(message.from_user):
            return
        await state.clear()
        command = (message.text or "").split()[0].split("@")[0].lstrip("/")
        action = command if command in ("status", "check") else command + (":week" if command in ("sources", "utm", "search") else "")
        if command in ("today", "yesterday", "week", "month"):
            action = "report:" + command
        try:
            await message.answer(await service.handle_action(action), reply_markup=section_for_action(action))
        except (MetrikaError, SearchConsoleError, DataIntegrityError, ValueError) as exc:
            await message.answer("⚠️ " + str(exc), reply_markup=section_for_action(action))
        except Exception:
            import logging
            logging.exception("Failed report")
            await message.answer("⚠️ Внутренняя ошибка отчёта. Проверьте журнал Bothost.", reply_markup=HOME)

    @router.callback_query(F.data.startswith("nav:"))
    async def navigation(query: CallbackQuery, state: FSMContext) -> None:
        if not user_allowed(query.from_user):
            await query.answer("Нет доступа", show_alert=True)
            return
        key = (query.data or "").partition(":")[2]
        await query.answer()
        if key not in SECTIONS:
            return
        await state.clear()
        if query.message:
            title, markup = SECTIONS[key]
            await query.message.edit_text(title, reply_markup=markup)

    @router.callback_query(F.data.in_(ACTIONS))
    async def actions(query: CallbackQuery, state: FSMContext) -> None:
        if not user_allowed(query.from_user):
            await query.answer("Нет доступа", show_alert=True)
            return
        await query.answer()
        await state.clear()
        action = query.data or ""
        try:
            if action == "id":
                result = f"🪪 Ваш Telegram ID: {query.from_user.id}"
            else:
                result = await service.handle_action(action)
            if query.message:
                await query.message.answer(result, reply_markup=section_for_action(action))
        except (MetrikaError, SearchConsoleError, DataIntegrityError, ValueError) as exc:
            if query.message:
                await query.message.answer("⚠️ " + str(exc), reply_markup=section_for_action(action))
        except Exception:
            import logging
            logging.exception("Failed callback report")
            if query.message:
                await query.message.answer("⚠️ Внутренняя ошибка отчёта. Проверьте журнал Bothost.", reply_markup=HOME)

    @router.callback_query(F.data.startswith("spend:"))
    async def spend_buttons(query: CallbackQuery, state: FSMContext) -> None:
        if not user_allowed(query.from_user):
            await query.answer("Нет доступа", show_alert=True)
            return
        await query.answer()
        action = query.data or ""
        if not query.message:
            return
        if action == "spend:cancel":
            await state.clear()
            await query.message.edit_text("Ввод расходов отменён.", reply_markup=ADS)
        elif action == "spend:start":
            await state.clear()
            await query.message.edit_text("💰 Выберите товар, для которого вносите расход:", reply_markup=SPEND_PRODUCTS)
        elif action in {"spend:product:mikozacid", "spend:product:hot_ice"}:
            product = action.removeprefix("spend:product:")
            await state.clear()
            await state.update_data(product=product)
            await query.message.edit_text(f"💰 {LANDINGS[product][0]}\nВыберите дату расхода:", reply_markup=SPEND_DATES)
        elif action in {"spend:date:today", "spend:date:yesterday", "spend:date:custom"}:
            saved = await state.get_data()
            if saved.get("product") not in LANDINGS:
                await state.clear()
                await query.message.edit_text("Выберите товар сначала:", reply_markup=SPEND_PRODUCTS)
                return
            chosen = action.removeprefix("spend:date:")
            if chosen == "custom":
                await state.set_state(SpendFlow.custom_date)
                await query.message.answer("📆 Пришлите дату в формате ГГГГ-ММ-ДД (например, 2026-11-08).", reply_markup=SPEND_CANCEL)
                return
            # The report's configured timezone, not the host's timezone, defines a day.
            from zoneinfo import ZoneInfo
            today = datetime.now(ZoneInfo(service.config.timezone)).date()
            day = (today - timedelta(days=int(chosen == "yesterday"))).isoformat()
            await state.update_data(day=day)
            await state.set_state(SpendFlow.amount)
            await query.message.answer(f"💰 {LANDINGS[saved['product']][0]} · {day}\nПришлите сумму расхода в рублях (например, 500 или 500,50).", reply_markup=SPEND_CANCEL)

    @router.message(SpendFlow.custom_date)
    async def manual_date(message: Message, state: FSMContext) -> None:
        if not user_allowed(message.from_user):
            return
        raw = (message.text or "").strip()
        try:
            parsed = date.fromisoformat(raw)
            if parsed.isoformat() != raw or parsed > datetime.now(ZoneInfo(service.config.timezone)).date():
                raise ValueError
        except ValueError:
            await message.answer("⚠️ Нужна дата ГГГГ-ММ-ДД, например 2026-11-08.", reply_markup=SPEND_CANCEL)
            return
        await state.update_data(day=raw)
        await state.set_state(SpendFlow.amount)
        await message.answer("💰 Введите сумму рекламного расхода в рублях:", reply_markup=SPEND_CANCEL)

    @router.message(SpendFlow.amount)
    async def manual_amount(message: Message, state: FSMContext) -> None:
        if not user_allowed(message.from_user):
            return
        data = await state.get_data()
        if data.get("product") not in LANDINGS or not data.get("day"):
            await state.clear()
            await message.answer("Данные не найдены. Начните ввод заново.", reply_markup=ADS)
            return
        answer = await service.set_spend(f"/spend {data['product']} {data['day']} {(message.text or '').strip()}")
        if answer.startswith("✅"):
            await state.clear()
            await message.answer(answer, reply_markup=ADS)
        else:
            await message.answer("⚠️ Нужна сумма 0–100000000 с точностью до копейки, например 550,50. Значения с 3+ знаками не округляются.", reply_markup=SPEND_CANCEL)

    @router.message()
    async def fallback(message: Message) -> None:
        if user_allowed(message.from_user):
            await message.answer("Выберите нужный раздел кнопкой:", reply_markup=HOME)

    register_backup_handlers(router, service, BACKUPS)
    dp.include_router(router)
    return dp

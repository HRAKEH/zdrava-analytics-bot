"""Owner-only Telegram control panel for safe settings and Yandex Direct campaign attribution."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.services.direct import DirectError
from app.services.metrika import LANDINGS
from app.services.reporting import period, format_money
from app.storage import DataIntegrityError

LABELS = {"mikozacid": "🧴 Микозацид", "hot_ice": "🔥 Горячий лёд"}
SETTINGS_LABELS = {
    "daily_time": "Время ежедневной сводки",
    "backup_time": "Время создания архива",
    "retention_days": "Срок хранения архивов в днях",
    "backup_max_count": "Максимум архивов",
}
EDITABLE = frozenset(SETTINGS_LABELS)

class SettingsFlow(StatesGroup):
    value = State()

def kb(*rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=name, callback_data=action) for name, action in row]
        for row in rows
    ])

def settings_kb(p: dict) -> InlineKeyboardMarkup:
    toggle = lambda v: "✅" if p[v] else "❌"
    return kb(
        ((f"📬 Сводка {toggle('daily_enabled')}", "prefs:toggle:daily_enabled"),
         (f"🕘 {p['daily_time']}", "prefs:input:daily_time")),
        ((f"💾 Автобэкап {toggle('backup_enabled')}", "prefs:toggle:backup_enabled"),
         (f"🕒 {p['backup_time']}", "prefs:input:backup_time")),
        ((f"📤 ZIP в Telegram {toggle('backup_send_telegram')}", "prefs:toggle:backup_send_telegram"),),
        ((f"🗓 Хранить {p['retention_days']} дн.", "prefs:input:retention_days"),
         (f"📦 Макс. {p['backup_max_count']} архивов", "prefs:input:backup_max_count")),
        (("⬅️ Система", "nav:system"),),
    )

DIRECT_MENU = kb(
    (("📋 Все кампании", "direct:campaigns"),),
    (("🧴 Для Микозацида", "direct:product:mikozacid:0"),),
    (("🔥 Для Горячего льда", "direct:product:hot_ice:0"),),
    (("💸 Расходы · 7 дней", "direct:stat:week"),
     ("💸 30 дней", "direct:stat:month")),
    (("✅ Проверить подключение", "direct:check"),),
    (("⬅️ Реклама", "nav:ads"),),
)

def settings_text(p: dict) -> str:
    return (
        "⚙️ Настройки ЗДРАВА\n"
        f"📬 Ежедневная сводка: {'включена' if p['daily_enabled'] else 'выключена'} · {p['daily_time']}\n"
        f"💾 Автоматическая копия: {'включена' if p['backup_enabled'] else 'выключена'} · {p['backup_time']}\n"
        f"📤 Автоматически присылать ZIP: {'да' if p['backup_send_telegram'] else 'нет'}\n"
        f"🗓 Срок хранения: {p['retention_days']} дн. · максимум {p['backup_max_count']} шт.\n\n"
        "Время по Москве. Нажмите кнопку, чтобы изменить настройку.\n"
        "🔒 Токены и пароли меняются только в защищённых переменных Bothost."
    )

def _short(name: str, limit: int = 36) -> str:
    safe = name.replace("\n", " ").replace("\r", " ").strip()
    return safe[:limit] + ("…" if len(safe) > limit else "")

def register_manager_handlers(router: Router, service) -> None:
    def owner(user) -> bool:
        return service.allowed(user.id if user else None)

    async def direct_campaign_keyboard(query: CallbackQuery, product: str, page: int) -> None:
        campaigns = await service.direct.campaigns()
        mappings = service.assignments.get()
        ordered = sorted(campaigns, key=lambda c: (c.state != "ON", c.id))
        pagesize = 8
        if not ordered:
            await query.message.answer("Кампаний не найдено. Проверьте доступ к рекламному кабинету.", reply_markup=DIRECT_MENU)
            return
        page = max(0, min(page, (len(ordered) - 1)//pagesize))
        buttons = []
        for item in ordered[page*pagesize:(page+1)*pagesize]:
            assigned = next((p for p, ids in mappings.items() if item.id in ids), None)
            if assigned == product:
                caption = "✅ " + _short(item.name, 31)
                action = f"direct:remove:{product}:{item.id}"
            else:
                caption = ("🔁 " if assigned else "➕ ") + _short(item.name, 31)
                action = f"direct:assign:{product}:{item.id}"
            buttons.append([(caption, action)])
        if page:
            buttons.append([("⬅️ Назад", f"direct:product:{product}:{page-1}")])
        if (page+1)*pagesize < len(ordered):
            buttons.append([("➡️ Далее", f"direct:product:{product}:{page+1}")])
        buttons.append([("⬅️ Реклама", "direct:home")])
        await query.message.answer(
            f"{LABELS[product]} — привязка рекламных кампаний\n"
            "➕ Добавить · ✅ убрать · 🔁 перенести с другого товара\n"
            "Одна кампания относится только к одному товару. "
            "Смешанные кампании нельзя корректно разделить автоматически.",
            reply_markup=kb(*(tuple(row) for row in buttons)))

    async def direct_report(key: str) -> str:
        mapping = service.assignments.get()
        ids = set(mapping["mikozacid"]) | set(mapping["hot_ice"])
        if not ids:
            return "Сначала назначьте рекламные кампании товарам кнопками выше."
        campaigns = {c.id: c for c in await service.direct.campaigns()}
        missing = ids - set(campaigns)
        if missing:
            return "Некоторые назначенные кампании недоступны в аккаунте. Проверьте настройки назначений."
        if any(campaigns[cid].currency != "RUB" for cid in ids):
            return "У назначенных кампаний валюта не RUB. Отчёт в рублях не рассчитывается."
        begin, end, title = period(key, service.config.timezone)
        result = await service.direct.report(begin, end, ids)
        lines = [f"💸 Яндекс Директ · {title}", f"{begin:%d.%m.%Y} — {end:%d.%m.%Y}"]
        for product, selected in mapping.items():
            if not selected:
                lines.append(f"\n{LABELS[product]}: кампании не назначены")
                continue
            present = [result[cid] for cid in selected if cid in result]
            if len(present) != len(selected):
                lines.append(f"\n{LABELS[product]}: данных лишь по {len(present)}/{len(selected)} кампаниям — итог неизвестен")
                continue
            spent = sum((item.cost for item in present), Decimal("0"))
            clicks = sum(item.clicks for item in present)
            impressions = sum(item.impressions for item in present)
            lines.append(f"\n{LABELS[product]}: {format_money(spent)}")
            lines.append(f"Показы: {impressions} · Клики: {clicks}")
        lines.append("\nДанные Директа без НДС. Это расходы кампаний, не покупки. "
                     "Ручные расходы отдельно и не суммируются с API.")
        return "\n".join(lines)

    @router.message(Command("settings"))
    async def settings_cmd(message: Message, state: FSMContext):
        if not owner(message.from_user): return
        await state.clear()
        p = service.preferences.get()
        await message.answer(settings_text(p), reply_markup=settings_kb(p))

    @router.message(Command("direct"))
    async def direct_cmd(message: Message, state: FSMContext):
        if not owner(message.from_user): return
        await state.clear()
        await message.answer("📣 Яндекс Директ\nСписок кампаний и расходы по двум товарам.",
                             reply_markup=DIRECT_MENU)

    @router.callback_query(F.data.startswith("prefs:"))
    async def prefs_callback(query: CallbackQuery, state: FSMContext):
        if not owner(query.from_user):
            await query.answer("Нет доступа", show_alert=True); return
        await query.answer()
        if not isinstance(query.message, Message): return
        action = (query.data or "").split(":")
        try:
            if action[1] == "home":
                await state.clear()
                p = service.preferences.get()
                await query.message.answer(settings_text(p), reply_markup=settings_kb(p))
            elif action[1] == "toggle" and len(action) == 3:
                name = action[2]
                if name not in ("daily_enabled","backup_enabled","backup_send_telegram"):
                    return
                p = service.preferences.get()
                service.preferences.set(name, not p[name])
                p = service.preferences.get()
                await query.message.answer("✅ Настройка сохранена.\n" + settings_text(p),
                                           reply_markup=settings_kb(p))
            elif action[1] == "input" and len(action) == 3 and action[2] in EDITABLE:
                await state.set_state(SettingsFlow.value)
                await state.update_data(name=action[2])
                await query.message.answer(
                    f"✏️ {SETTINGS_LABELS[action[2]]}\n"
                    "Пришлите новое значение одним сообщением. Время: ЧЧ:ММ.\n"
                    "Для отмены нажмите кнопку.",
                    reply_markup=kb((("✖️ Отмена", "prefs:cancel"),)))
            elif action[1] == "cancel":
                await state.clear()
                p = service.preferences.get()
                await query.message.answer("Отменено.", reply_markup=settings_kb(p))
        except (ValueError, DataIntegrityError) as exc:
            await query.message.answer("⚠️ " + str(exc), reply_markup=SETTINGS_BACK)

    @router.message(SettingsFlow.value)
    async def prefs_value(message: Message, state: FSMContext):
        if not owner(message.from_user): return
        saved = await state.get_data()
        name = saved.get("name")
        if name not in EDITABLE:
            await state.clear()
            await message.answer("Настройка не выбрана. Откройте /settings."); return
        value = (message.text or "").strip()
        try:
            if name in ("retention_days", "backup_max_count"):
                if not value.isdecimal(): raise ValueError("Введите целое положительное число")
                parsed = int(value)
            else:
                parsed = value
            p = service.preferences.set(name, parsed)
        except (ValueError, DataIntegrityError) as exc:
            await message.answer("⚠️ " + str(exc),
                reply_markup=kb((("✖️ Отмена", "prefs:cancel"),)))
            return
        await state.clear()
        await message.answer("✅ Сохранено.\n" + settings_text(p), reply_markup=settings_kb(p))

    @router.callback_query(F.data.startswith("direct:"))
    async def direct_callback(query: CallbackQuery, state: FSMContext):
        if not owner(query.from_user):
            await query.answer("Нет доступа", show_alert=True); return
        await query.answer()
        if not isinstance(query.message, Message): return
        action = (query.data or "").split(":")
        try:
            if action[1] == "home":
                await query.message.answer("📣 Яндекс Директ\nВыберите действие:", reply_markup=DIRECT_MENU)
            elif action[1] == "check":
                camps = await service.direct.campaigns()
                await query.message.answer(f"✅ Директ отвечает. Доступно кампаний: {len(camps)}.",
                                           reply_markup=DIRECT_MENU)
            elif action[1] == "campaigns":
                camps = await service.direct.campaigns()
                lines = [f"📋 Кампании Директа: {len(camps)}"]
                for c in camps[:25]:
                    lines.append(f"• {_short(c.name, 45)} · {c.id} · {c.state} · {c.currency}")
                if len(camps) > 25: lines.append("Остальные кампании доступны в списке привязки.")
                await query.message.answer("\n".join(lines), reply_markup=DIRECT_MENU)
            elif action[1] == "stat" and len(action)==3 and action[2] in ("week","month"):
                await query.message.answer(await direct_report(action[2]), reply_markup=DIRECT_MENU)
            elif action[1] == "product" and len(action)==4 and action[2] in LABELS:
                await direct_campaign_keyboard(query, action[2], int(action[3]))
            elif action[1] in ("assign", "remove") and len(action)==4 and action[2] in LABELS:
                product, campaign_id = action[2], int(action[3])
                if action[1] == "assign":
                    available = {c.id for c in await service.direct.campaigns()}
                    if campaign_id not in available:
                        raise DirectError("Кампания недоступна. Попробуйте обновить список.")
                    service.assignments.assign(product, campaign_id)
                    message = "✅ Кампания назначена."
                else:
                    service.assignments.remove(product, campaign_id)
                    message = "✅ Кампания отвязана."
                await query.message.answer(message)
                await direct_campaign_keyboard(query, product, 0)
        except (DirectError, ValueError, DataIntegrityError) as exc:
            await query.message.answer("⚠️ " + str(exc), reply_markup=DIRECT_MENU)
        except Exception:
            import logging
            logging.exception("Direct / settings UI failure")
            await query.message.answer("⚠️ Ошибка запроса. Проверьте журнал Bothost.", reply_markup=DIRECT_MENU)

SETTINGS_BACK = kb((("⚙️ К настройкам", "prefs:home"),))

"""Bothost entry point: `python main.py` (Telegram long polling)."""
from __future__ import annotations

import asyncio
from datetime import datetime
import logging
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from aiogram import Bot
from aiogram.types import BotCommand

from app.bot import AnalyticsBot, build_dispatcher
from app.config import Config
from app.storage import JsonStore
from app.services.backup_scheduler import backup_loop

log = logging.getLogger("zdrava.analytics")


async def daily_loop(bot: Bot, service: AnalyticsBot) -> None:
    """Send once per owner per local day; persist delivery state in /app/data."""
    config = service.config
    while True:
        await asyncio.sleep(60)
        if not service.metrika.connected:
            continue
        prefs = service.preferences.get()
        if not prefs["daily_enabled"]:
            continue
        now = datetime.now(ZoneInfo(config.timezone))
        if now.strftime("%H:%M") < prefs["daily_time"]:
            continue
        today = now.date().isoformat()
        try:
            delivery = service.store.read("daily_delivery.json", {})
            sent = delivery.get("date") == today and set(delivery.get("sent_to", [])) or set()
            missing = set(config.owner_ids) - sent
            if not missing:
                continue
            report = await service.daily_digest()
            for owner in sorted(missing):
                try:
                    await bot.send_message(owner, report)
                    sent.add(owner)
                    service.store.write("daily_delivery.json", {"date": today, "sent_to": sorted(sent)})
                except Exception:
                    log.exception("Daily report delivery failed for owner %s", owner)
        except Exception:
            log.exception("Daily report build failed")


async def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = Config.from_env()
    config.validate_startup()
    store = JsonStore(config.data_dir)
    service = AnalyticsBot(config, store)
    bot = Bot(token=config.telegram_token)
    dp = build_dispatcher(service)
    await bot.set_my_commands([
        BotCommand(command="start", description="🏠 Главное меню"),
        BotCommand(command="today", description="📊 Трафик сегодня"),
        BotCommand(command="yesterday", description="📊 Трафик вчера"),
        BotCommand(command="week", description="📅 Трафик за 7 дней"),
        BotCommand(command="month", description="📅 Трафик за 30 дней"),
        BotCommand(command="sources", description="🌐 Источники"),
        BotCommand(command="utm", description="🏷 UTM-кампании"),
        BotCommand(command="spend", description="💰 Добавить расход"),
        BotCommand(command="direct", description="📣 Яндекс Директ"),
        BotCommand(command="settings", description="✏️ Настройки бота"),
        BotCommand(command="search", description="🔎 Google Search Console"),
        BotCommand(command="status", description="⚙️ Подключения"),
        BotCommand(command="check", description="✅ Проверить API"),
        BotCommand(command="backup", description="💾 Создать ZIP-бэкап"),
        BotCommand(command="backups", description="🗄 Список бэкапов"),
        BotCommand(command="backup_latest", description="📤 Скачать последний ZIP"),
        BotCommand(command="backup_cleanup", description="🧹 Очистить старые ZIP"),
        BotCommand(command="id", description="🪪 Мой Telegram ID"),
        BotCommand(command="help", description="❔ Помощь"),
        BotCommand(command="cancel", description="✖️ Отменить ввод"),
    ])
    daily = asyncio.create_task(daily_loop(bot, service), name="daily-report")
    backups_task = asyncio.create_task(backup_loop(bot, service), name="automatic-backups")
    log.info("ZDRAVA analytics started; authorized owners=%s; data=%s", len(config.owner_ids), config.data_dir)
    try:
        # Exactly one running deployment per Telegram token. No webhook is required.
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot, handle_signals=True)
    finally:
        daily.cancel()
        backups_task.cancel()
        for task in (daily, backups_task):
            try:
                await task
            except asyncio.CancelledError:
                pass
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

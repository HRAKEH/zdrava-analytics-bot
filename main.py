"""Bothost entry point: `python main.py` (Telegram long polling)."""
from __future__ import annotations

import asyncio
from datetime import datetime
import logging
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from aiogram import Bot

from app.bot import AnalyticsBot, build_dispatcher
from app.config import Config
from app.storage import JsonStore

log = logging.getLogger("zdrava.analytics")


async def daily_loop(bot: Bot, service: AnalyticsBot) -> None:
    """Send once per owner per local day; persist delivery state in /app/data."""
    config = service.config
    while True:
        await asyncio.sleep(60)
        if not config.daily_report_enabled or not service.metrika.connected:
            continue
        now = datetime.now(ZoneInfo(config.timezone))
        if now.strftime("%H:%M") < config.daily_report_time:
            continue
        today = now.date().isoformat()
        try:
            delivery = service.store.read("daily_delivery.json", {})
            sent = delivery.get("date") == today and set(delivery.get("sent_to", [])) or set()
            missing = set(config.owner_ids) - sent
            if not missing:
                continue
            report = await service.report("yesterday")
            for owner in sorted(missing):
                try:
                    await bot.send_message(owner, "📬 Ежедневная сводка\n" + report)
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
    daily = asyncio.create_task(daily_loop(bot, service), name="daily-report")
    log.info("ZDRAVA analytics started; authorized owners=%s; data=%s", len(config.owner_ids), config.data_dir)
    try:
        # Exactly one running deployment per Telegram token. No webhook is required.
        await bot.delete_webhook(drop_pending_updates=False)
        await dp.start_polling(bot, handle_signals=True)
    finally:
        daily.cancel()
        try:
            await daily
        except asyncio.CancelledError:
            pass
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())

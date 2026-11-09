"""Automatic ZIP snapshot and independent hourly pruning on Bothost."""
from __future__ import annotations

import asyncio
from datetime import datetime
import logging
from zoneinfo import ZoneInfo

from aiogram.types import FSInputFile

log = logging.getLogger(__name__)


async def backup_loop(bot, service) -> None:
    config = service.config
    last_prune_hour = None
    last_attempt_at = -10000000.0
    while True:
        await asyncio.sleep(60)
        try:
            now = datetime.now(ZoneInfo(config.timezone))
            options = service.preferences.get()
            service.backups.retention_days = options["retention_days"]
            service.backups.max_count = options["backup_max_count"]
            hour_key = now.strftime("%Y-%m-%d %H")
            if hour_key != last_prune_hour:
                await asyncio.to_thread(service.backups.prune)
                last_prune_hour = hour_key
            if not options["backup_enabled"] or now.strftime("%H:%M") < options["backup_time"]:
                continue
            today = now.date().isoformat()
            state = service.store.read("backup_state.json", {})
            if not isinstance(state, dict):
                raise ValueError("Некорректный backup_state.json")
            if state.get("date") == today:
                continue
            tick = asyncio.get_running_loop().time()
            if tick - last_attempt_at < 900:
                continue
            last_attempt_at = tick
            info = await asyncio.to_thread(service.backups.create, "automatic")
            service.store.write("backup_state.json", {
                "date": today, "filename": info.path.name, "sha256": info.sha256
            })
            log.info("Automatic JSON backup stored: %s", info.path.name)
            if options["backup_send_telegram"]:
                with service.backups.preserve(info.path):
                    for owner in config.owner_ids:
                        try:
                            await bot.send_document(
                                owner, FSInputFile(info.path),
                                caption=f"💾 Автоматическая ZIP-копия\n{info.path.name}")
                        except Exception:
                            log.exception("Automatic ZIP delivery failed for %s", owner)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Automatic backup/retention failed")

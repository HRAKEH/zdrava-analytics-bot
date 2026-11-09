"""Owner-only ZIP archive actions; attach to existing thematic Telegram menus."""
from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, FSInputFile, Message

from app.services.backups import BackupError

log = logging.getLogger(__name__)


def register_backup_handlers(router: Router, service, menu) -> None:
    def allowed(user) -> bool:
        return service.allowed(user.id if user else None)

    async def send_archive(message: Message, *, new: bool) -> None:
        try:
            info = await asyncio.to_thread(service.backups.create, "manual") if new else await asyncio.to_thread(service.backups.latest)
            if info is None:
                await message.answer("Архивов пока нет. Создайте ZIP-копию.", reply_markup=menu)
                return
            with service.backups.preserve(info.path):
                await message.answer_document(
                    document=FSInputFile(info.path),
                    caption=(f"💾 {info.path.name}\n"
                             f"Файлов JSON: {info.files} · {info.size/1024:.1f} КиБ\n"
                             f"SHA-256: {info.sha256}"),
                    reply_markup=menu)
        except (BackupError, OSError) as exc:
            await message.answer(f"⚠️ Не удалось подготовить архив: {exc}", reply_markup=menu)
        except Exception:
            log.exception("Backup create/send failed")
            await message.answer("⚠️ Ошибка работы с архивом. Проверьте журнал Bothost.", reply_markup=menu)

    async def listing(message: Message) -> None:
        infos = await asyncio.to_thread(service.backups.list_backups)
        total_count, size = await asyncio.to_thread(service.backups.usage)
        opts = service.backup_options
        lines = [f"🗄 Архивы: {total_count} · {size/1048576:.2f} МиБ",
                 f"Авто: {'включено' if opts.enabled else 'выключено'}, {opts.time} ({service.config.timezone})",
                 f"Хранение: {opts.retention_days} дн. · {opts.max_count} ZIP · {opts.max_total_mb} МиБ"]
        if not infos:
            lines.append("Исправных ZIP-архивов нет.")
        else:
            lines.append("Последние исправные:")
            for item in infos[:10]:
                lines.append(f"• {item.path.name} · {item.size/1024:.1f} КиБ")
            if total_count > 10:
                lines.append("Остальные файлы не показаны.")
        await message.answer("\n".join(lines), reply_markup=menu)

    async def prune(message: Message) -> None:
        try:
            removed = await asyncio.to_thread(service.backups.prune)
            count, size = await asyncio.to_thread(service.backups.usage)
            await message.answer(
                f"🧹 Удалено старых ZIP: {removed}.\nОсталось: {count} ({size/1048576:.2f} МиБ).\nПоследняя исправная копия защищена.",
                reply_markup=menu)
        except Exception:
            log.exception("Backup pruning failed")
            await message.answer("⚠️ Очистка не удалась. Проверьте журнал Bothost.", reply_markup=menu)

    @router.message(Command("backup"))
    async def cmd_create(message: Message) -> None:
        if allowed(message.from_user):
            await send_archive(message, new=True)

    @router.message(Command("backups"))
    async def cmd_list(message: Message) -> None:
        if allowed(message.from_user):
            await listing(message)

    @router.message(Command("backup_latest"))
    async def cmd_latest(message: Message) -> None:
        if allowed(message.from_user):
            await send_archive(message, new=False)

    @router.message(Command("backup_cleanup"))
    async def cmd_cleanup(message: Message) -> None:
        if allowed(message.from_user):
            await prune(message)

    @router.callback_query(F.data.startswith("backup:"))
    async def callbacks(query: CallbackQuery, state: FSMContext) -> None:
        if not allowed(query.from_user):
            await query.answer("Нет доступа", show_alert=True)
            return
        await query.answer()
        await state.clear()
        if not isinstance(query.message, Message):
            return
        if query.data == "backup:manual":
            await send_archive(query.message, new=True)
        elif query.data == "backup:latest":
            await send_archive(query.message, new=False)
        elif query.data == "backup:list":
            await listing(query.message)
        elif query.data == "backup:prune":
            await prune(query.message)

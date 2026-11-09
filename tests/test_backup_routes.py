"""Static integration checks for menus, scheduler, and daily push."""
from pathlib import Path
import ast

ROOT = Path(__file__).parents[1]


def test_backup_actions_mapped_and_authorized():
    bot = (ROOT / "app/bot.py").read_text(encoding="utf-8")
    ast.parse(bot)
    for action in ("backup:manual", "backup:latest", "backup:list", "backup:prune", "nav:backups"):
        assert action in bot
    assert "register_backup_handlers(router, service, BACKUPS)" in bot


def test_auto_backup_and_short_digest_wired():
    main = (ROOT / "main.py").read_text(encoding="utf-8")
    ast.parse(main)
    assert 'await service.daily_digest()' in main
    assert 'backup_loop(bot, service)' in main
    assert 'automatic-backups' in main

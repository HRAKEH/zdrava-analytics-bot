from pathlib import Path
import ast


def test_menu_sections_have_main_navigation():
    """Static safety check: main module exposes all four thematic menus."""
    text = (Path(__file__).parents[1] / "app" / "bot.py").read_text(encoding="utf-8")
    ast.parse(text)
    for key in ("nav:traffic", "nav:ads", "nav:seo", "nav:system"):
        assert key in text
    for action in ("report:today", "report:yesterday", "report:week", "report:month",
                   "sources:week", "utm:week", "search:week", "expenses:week",
                   "expenses:month", "spend:start", "status", "check", "id", "help"):
        assert f'"{action}"' in text
    assert '"nav:home"' in text


def test_spend_step_flow_has_cancel_and_validation():
    text = (Path(__file__).parents[1] / "app" / "bot.py").read_text(encoding="utf-8")
    for marker in ("SpendFlow.custom_date", "SpendFlow.amount", "spend:cancel",
                   "state.clear()", "service.set_spend"):
        assert marker in text

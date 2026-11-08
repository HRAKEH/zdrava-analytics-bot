from datetime import date
from decimal import Decimal
from app.services.reporting import display_summary,display_ranked,spend_text
from app.services.metrika import LandingStats, RankedResult
from app.storage import SpendCoverage


def test_summary_does_not_invent_complete_spend_or_orders():
    text = display_summary(date(2026, 11, 8), date(2026, 11, 8), "Вчера", [
        LandingStats("mikozacid", 100, 80, 4),
        LandingStats("hot_ice", 50, 45, None),
    ], {"mikozacid": SpendCoverage(Decimal('500.00'),1,1),
         "hot_ice":SpendCoverage(Decimal('0'),0,1)})
    assert 'в визитах с просмотром страницы' in text
    assert 'не настроена' in text
    assert '500.00 ₽' in text
    assert 'нет введённых данных (это не 0 ₽)' in text
    assert '≠ заказы' in text


def test_partial_spend_does_not_claim_total():
    s=spend_text(SpendCoverage(Decimal('555.60'),1,7))
    assert '1/7' in s and 'неизвестен' in s and '555.60' in s


def test_ranked_truncation_not_total():
    s=display_ranked('Источники',RankedResult([('Search',4)],total_rows=9,truncated=True))
    assert 'часть группировок' in s and 'итог не рассчитывается' in s


def test_today_marked_as_preliminary():
    s=display_summary(date(2026,11,8),date(2026,11,8),'Сегодня',[
        LandingStats('mikozacid',10,8,None)],{'mikozacid':SpendCoverage(Decimal('0'),0,1)})
    assert 'предварительная' in s

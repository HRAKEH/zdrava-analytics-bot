import json
from decimal import Decimal
import pytest
from app.storage import JsonStore, DataIntegrityError, money_amount


def test_storage_atomic_spend_overwrite_and_coverage(tmp_path):
    store = JsonStore(tmp_path)
    store.set_spend("mikozacid", "2026-11-08", money_amount("5,25"))
    store.set_spend("mikozacid", "2026-11-09", money_amount("7"))
    store.set_spend("mikozacid", "2026-11-08", money_amount("11"))
    s=store.spend_summary("mikozacid", "2026-11-08", "2026-11-09")
    assert (s.recorded_amount,s.recorded_days,s.expected_days,s.complete)==(Decimal('18.00'),2,2,True)
    missing=store.spend_summary("hot_ice", "2026-11-08", "2026-11-09")
    assert missing.recorded_days == 0 and not missing.complete and not missing.has_data
    assert json.loads((tmp_path / "ad_spend.json").read_text())["mikozacid"]["2026-11-08"] == '11.00'


def test_zeros_count_only_when_explicitly_entered(tmp_path):
    store=JsonStore(tmp_path)
    store.set_spend('hot_ice','2026-11-08',money_amount('0'))
    s=store.spend_summary('hot_ice','2026-11-08','2026-11-09')
    assert s.recorded_amount==Decimal('0') and s.recorded_days==1 and not s.complete


@pytest.mark.parametrize('raw',['-1','NaN','inf','1e3','1.234','100000000.01','', '1,200.20'])
def test_money_disallows_unsafe_or_rounded_input(raw):
    with pytest.raises(ValueError): money_amount(raw)


def test_decimal_accuracy(tmp_path):
    store=JsonStore(tmp_path)
    for day in range(1,4):store.set_spend('mikozacid',f'2026-11-0{day}',money_amount('0.10'))
    assert store.spend_summary('mikozacid','2026-11-01','2026-11-03').recorded_amount==Decimal('0.30')


def test_corrupt_store_never_uses_zero(tmp_path):
    store=JsonStore(tmp_path)
    (tmp_path/'ad_spend.json').write_text('{ broken',encoding='utf8')
    with pytest.raises(DataIntegrityError): store.spend_summary('mikozacid','2026-11-01','2026-11-07')


def test_corrupt_values_never_become_zero(tmp_path):
    store=JsonStore(tmp_path)
    (tmp_path/'ad_spend.json').write_text('{"mikozacid":{"2026-11-01":null}}',encoding='utf8')
    with pytest.raises(DataIntegrityError): store.spend_summary('mikozacid','2026-11-01','2026-11-07')


def test_storage_filename_safety(tmp_path):
    store=JsonStore(tmp_path)
    with pytest.raises(ValueError): store.write("../other.json", {})

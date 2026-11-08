import pytest
from app.services.search_console import parse_search_stats, SearchConsoleError


def test_missing_rows_not_zero():
    result=parse_search_stats({'rows':[]})
    assert not result.available and result.clicks is None and result.ctr is None


def test_gsc_ctr_calculated_from_counts():
    result=parse_search_stats({'rows':[{'clicks':30,'impressions':200,'ctr':0.15,'position':2.5}]})
    assert result.available and result.ctr==0.15 and result.position==2.5


def test_no_impressions_ctr_position_na():
    result=parse_search_stats({'rows':[{'clicks':0,'impressions':0,'ctr':0,'position':0}]})
    assert result.available and result.ctr is None and result.position is None


@pytest.mark.parametrize('payload',[
    {}, {'rows':None}, {'rows':[{}]},
    {'rows':[{'clicks':0,'impressions':100,'ctr':None,'position':1}]},
    {'rows':[{'clicks':10,'impressions':100,'ctr':0.9,'position':3}]},
    {'rows':[{'clicks':10,'impressions':0,'ctr':0,'position':0}]},
    {'rows':[{'clicks':-1,'impressions':100,'ctr':0,'position':1}]},
    {'rows':[{'clicks':1.5,'impressions':100,'ctr':0.015,'position':1}]},
])
def test_gsc_no_fabrication(payload):
    with pytest.raises(SearchConsoleError):parse_search_stats(payload)

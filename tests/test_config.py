import pytest
from app.config import owners, optional_goal


def test_owners():
    assert owners("123, 456,123") == (123, 456)
    with pytest.raises(ValueError):
        owners("123,wrong")


def test_optional_goal():
    assert optional_goal("") is None
    assert optional_goal("123") == 123
    with pytest.raises(ValueError):
        optional_goal("-1")

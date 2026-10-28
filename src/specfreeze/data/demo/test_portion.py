"""Tests for the offline portion demo. The numbers are fictional."""

import pytest

from portion import format_shares, split_cents


def test_even():
    assert split_cents(100, 4) == [25, 25, 25, 25]


def test_remainder_front():
    assert split_cents(100, 3) == [34, 33, 33]


def test_zero_total():
    assert split_cents(0, 2) == [0, 0]


def test_one():
    assert split_cents(1, 1) == [1]


def test_sum():
    shares = split_cents(5, 3)
    assert sum(shares) == 5
    assert shares == [2, 2, 1]


def test_bad_people():
    with pytest.raises(ValueError):
        split_cents(10, 0)


def test_negative_total():
    with pytest.raises(ValueError):
        split_cents(-1, 2)


def test_format():
    assert format_shares([34, 33]) == "34, 33"


def test_format_empty():
    with pytest.raises(ValueError):
        format_shares([])


def test_format_negative():
    with pytest.raises(ValueError):
        format_shares([1, -1])
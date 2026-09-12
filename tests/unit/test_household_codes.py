import pytest

from app.households.codes import normalize_household_code


@pytest.mark.parametrize(
    "submitted, expected",
    [
        ("4v8f-29hc", "4V8F-29HC"),
        ("  4v8f29hc  ", "4V8F-29HC"),
        ("4V8F-29HC", "4V8F-29HC"),
        ("4V8F-29H", None),
        ("4V8F-29H0", None),
        ("4V8F_29HC", None),
        ("", None),
    ],
)
def test_normalize_household_code(submitted, expected):
    assert normalize_household_code(submitted) == expected

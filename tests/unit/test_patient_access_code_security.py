import pytest

from app.household_access.security import (
    ACCESS_CODE_ALPHABET,
    access_code_selector,
    generate_access_code,
    normalize_access_code,
)


def test_generated_code_has_canonical_format_and_unambiguous_alphabet():
    code = generate_access_code()
    assert len(code) == 14
    assert code[4] == code[9] == "-"
    assert set(code.replace("-", "")) <= set(ACCESS_CODE_ALPHABET)
    assert set("0O1IL").isdisjoint(code)
    assert access_code_selector(code) == code[:4]


@pytest.mark.parametrize(
    "submitted, expected",
    [
        ("7k3m-9q2d-r8tx", "7K3M-9Q2D-R8TX"),
        ("  7k3m9q2dr8tx  ", "7K3M-9Q2D-R8TX"),
        ("7K3M-9Q2D-R8TX", "7K3M-9Q2D-R8TX"),
        ("7K3M-9Q2D", None),
        ("7K3M-9Q2D-R8T0", None),
        ("7K3M_9Q2D_R8TX", None),
        ("", None),
    ],
)
def test_normalization(submitted, expected):
    assert normalize_access_code(submitted) == expected

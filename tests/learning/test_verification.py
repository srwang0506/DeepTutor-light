import pytest

from deeptutor.learning.verification import verify_equality


@pytest.mark.parametrize(
    "left,right,variables,status",
    [
        ("100 cm", "1 m", None, "passed"),
        ("1 m", "1 s", None, "failed"),
        ("2*x+1", "5", {"x": "2"}, "passed"),
        ("1/3", "2/6", None, "passed"),
        ("2+2", "5", None, "failed"),
        ("1/0", "1", None, "execution_failed"),
        ("2**999999999", "1", None, "resource_limit"),
        ('__import__("os").system("echo bad")', "1", None, "unknown"),
        ("sin(x)", "1", None, "unknown"),
        ("1e20", "100000000000000000000", None, "passed"),
    ],
)
def test_bounded_checks(left, right, variables, status):
    assert verify_equality(left, right, variables)["status"] == status

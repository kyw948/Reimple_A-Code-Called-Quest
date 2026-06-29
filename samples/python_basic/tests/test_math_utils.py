from src.math_utils import add_numbers, multiply_numbers, clamp, factorial, average


def test_add_numbers():
    assert add_numbers(1, 2) == 3
    assert add_numbers(-1, 1) == 0
    assert add_numbers(0, 0) == 0


def test_multiply_numbers():
    assert multiply_numbers(3, 4) == 12
    assert multiply_numbers(0, 5) == 0
    assert multiply_numbers(-2, 3) == -6


def test_clamp():
    assert clamp(5, 0, 10) == 5
    assert clamp(-1, 0, 10) == 0
    assert clamp(15, 0, 10) == 10
    assert clamp(0, 0, 10) == 0
    assert clamp(10, 0, 10) == 10


def test_factorial():
    assert factorial(0) == 1
    assert factorial(1) == 1
    assert factorial(5) == 120
    assert factorial(10) == 3628800


def test_factorial_negative():
    try:
        factorial(-1)
        assert False, "Should have raised ValueError"
    except ValueError:
        pass


def test_average():
    assert average([1, 2, 3]) == 2.0
    assert average([10]) == 10.0
    assert average([]) == 0.0
    assert average([1, 2, 3, 4]) == 2.5

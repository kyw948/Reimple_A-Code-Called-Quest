def add_numbers(a: int, b: int) -> int:
    """두 정수를 더한 값을 반환한다."""
    result = a + b
    return result


def multiply_numbers(a: int, b: int) -> int:
    """두 정수를 곱한 값을 반환한다."""
    result = a * b
    return result


def clamp(value: int, min_val: int, max_val: int) -> int:
    """value를 min_val~max_val 범위로 제한한다."""
    if value < min_val:
        return min_val
    if value > max_val:
        return max_val
    return value


def factorial(n: int) -> int:
    """음이 아닌 정수의 팩토리얼을 반환한다."""
    if n < 0:
        raise ValueError("n must be non-negative")
    if n == 0:
        return 1
    result = 1
    for i in range(1, n + 1):
        result *= i
    return result


def average(numbers: list) -> float:
    """숫자 리스트의 평균을 반환한다. 빈 리스트이면 0.0을 반환한다."""
    if not numbers:
        return 0.0
    total = sum(numbers)
    count = len(numbers)
    return total / count

def reverse_string(s: str) -> str:
    """문자열을 뒤집어 반환한다."""
    chars = list(s)
    chars.reverse()
    return "".join(chars)


def count_vowels(s: str) -> int:
    """문자열에서 모음(a, e, i, o, u)의 개수를 반환한다. 대소문자 구분하지 않는다."""
    vowels = set("aeiouAEIOU")
    count = 0
    for char in s:
        if char in vowels:
            count += 1
    return count


def truncate(s: str, max_length: int, suffix: str = "...") -> str:
    """문자열이 max_length보다 길면 잘라내고 suffix를 붙인다."""
    if len(s) <= max_length:
        return s
    cut_length = max_length - len(suffix)
    if cut_length < 0:
        return suffix[:max_length]
    return s[:cut_length] + suffix


def is_palindrome(s: str) -> bool:
    """문자열이 팰린드롬인지 확인한다. 공백과 대소문자를 무시한다."""
    cleaned = s.replace(" ", "").lower()
    reversed_cleaned = cleaned[::-1]
    return cleaned == reversed_cleaned

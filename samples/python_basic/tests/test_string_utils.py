from src.string_utils import reverse_string, count_vowels, truncate, is_palindrome


def test_reverse_string():
    assert reverse_string("hello") == "olleh"
    assert reverse_string("") == ""
    assert reverse_string("a") == "a"
    assert reverse_string("ab") == "ba"


def test_count_vowels():
    assert count_vowels("hello") == 2
    assert count_vowels("HELLO") == 2
    assert count_vowels("xyz") == 0
    assert count_vowels("") == 0
    assert count_vowels("aeiou") == 5


def test_truncate():
    assert truncate("hello", 10) == "hello"
    assert truncate("hello world", 8) == "hello..."
    assert truncate("hello world", 5) == "he..."
    assert truncate("hi", 2) == "hi"


def test_is_palindrome():
    assert is_palindrome("racecar") is True
    assert is_palindrome("hello") is False
    assert is_palindrome("A man a plan a canal Panama") is True
    assert is_palindrome("") is True

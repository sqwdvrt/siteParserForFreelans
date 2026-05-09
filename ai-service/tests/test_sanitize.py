"""Тесты sanitize_for_classifier."""


from ai_service.util.sanitize import MAX_CLASSIFIER_INPUT, sanitize_for_classifier


def test_truncates_long_input() -> None:
    long_text = "x" * (MAX_CLASSIFIER_INPUT + 100)
    assert len(sanitize_for_classifier(long_text)) == MAX_CLASSIFIER_INPUT


def test_replaces_newlines() -> None:
    assert sanitize_for_classifier("a\nb\nc") == "a b c"
    assert sanitize_for_classifier("a\rb") == "a b"


def test_empty_input() -> None:
    assert sanitize_for_classifier("") == ""
    assert sanitize_for_classifier("   ") == ""


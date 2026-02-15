"""Тесты text_cleaner."""

import pytest

from ai_service.util.text_cleaner import MAX_LENGTH, clean_text


def test_removes_html() -> None:
    assert clean_text("<p>Hello <b>world</b></p>") == "Hello world"
    assert clean_text("<div>Foo</div><span>Bar</span>") == "Foo Bar"


def test_normalizes_whitespace() -> None:
    assert clean_text("  a   b   c  \n\t  d  ") == "a b c d"
    assert clean_text("one\ntwo\nthree") == "one two three"


def test_truncates_to_max_length() -> None:
    long_text = "x" * 3000
    result = clean_text(long_text)
    assert len(result) == MAX_LENGTH
    assert result == "x" * MAX_LENGTH


def test_empty_input() -> None:
    assert clean_text("") == ""
    assert clean_text("   ") == ""
    assert clean_text("\n\t") == ""


def test_empty_html() -> None:
    assert clean_text("<p></p>") == ""
    assert clean_text("<div></div><span></span>") == ""


def test_preserves_content() -> None:
    html = "<h1>Title</h1><p>Description with <em>emphasis</em></p>"
    assert "Title" in clean_text(html)
    assert "Description" in clean_text(html)
    assert "emphasis" in clean_text(html)

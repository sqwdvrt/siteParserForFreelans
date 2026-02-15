"""Очистка текста для embedding: HTML → plain text, нормализация, обрезка."""

import re

from bs4 import BeautifulSoup

MAX_LENGTH = 2000


def clean_text(html: str) -> str:
    """Извлечь и очистить текст из HTML.

    1. Удаление HTML-тегов (BeautifulSoup)
    2. Нормализация пробелов
    3. Обрезка до 2000 символов
    """
    if not html or not html.strip():
        return ""

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator=" ", strip=True)

    # Нормализация пробелов: множественные → один, trim
    text = re.sub(r"\s+", " ", text).strip()

    if len(text) > MAX_LENGTH:
        text = text[:MAX_LENGTH].rstrip()

    return text

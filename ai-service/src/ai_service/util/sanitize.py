"""Санитизация ввода для классификатора (защита от промпт-инъекции)."""

MAX_CLASSIFIER_INPUT = 4000


def sanitize_for_classifier(text: str) -> str:
    """Подготовка текста для LLM/классификатора.

    - Обрезка до MAX_CLASSIFIER_INPUT (DoS)
    - Замена переносов строк на пробел (промпт-инъекция)
    """
    if not text or not isinstance(text, str):
        return ""
    t = text.strip()
    if len(t) > MAX_CLASSIFIER_INPUT:
        t = t[:MAX_CLASSIFIER_INPUT].rstrip()
    return t.replace("\n", " ").replace("\r", " ")

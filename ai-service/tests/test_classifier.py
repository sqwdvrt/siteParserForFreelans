"""Тесты Classifier: RuleBasedClassifier, OllamaClassifier fallback."""

from __future__ import annotations

import json
from unittest.mock import patch

from ai_service.adapter.ollama import OllamaClassifier
from ai_service.adapter.rule_based import RuleBasedClassifier


# --- RuleBasedClassifier ---


def test_classify_typical_text() -> None:
    """Типичный текст: веб-проект, технологии, бюджет."""
    c = RuleBasedClassifier()
    text = "Нужен Python разработчик для веб-сайта на Django и PostgreSQL. Бюджет до 10000 руб."
    r = c.classify(text)
    assert isinstance(r, dict)
    assert r.get("project_type") == "web"
    assert "python" in r.get("technologies", [])
    assert "django" in r.get("technologies", [])
    assert "postgresql" in r.get("technologies", []) or "postgres" in r.get("technologies", [])
    assert r.get("budget_level") == "low"
    assert r.get("is_spam") is False


def test_classify_spam() -> None:
    """Спам: казино, крипто, заработок."""
    c = RuleBasedClassifier()
    r = c.classify("Заработок на криптовалюте! Казино и биткоин.")
    assert r.get("is_spam") is True


def test_classify_empty() -> None:
    """Пустой ввод."""
    c = RuleBasedClassifier()
    assert c.classify("") == {"is_spam": False}
    assert c.classify("   ") == {"is_spam": False}
    assert c.classify("\n\t") == {"is_spam": False}


def test_classify_seniority() -> None:
    """Уровень: junior, middle, senior."""
    c = RuleBasedClassifier()
    assert c.classify("Ищем джуниор разработчика")["seniority"] == "junior"
    assert c.classify("Нужен middle Python")["seniority"] == "middle"
    assert c.classify("Требуется senior lead")["seniority"] == "senior"


def test_classify_mobile_bot() -> None:
    """Типы: mobile, bot."""
    c = RuleBasedClassifier()
    r = c.classify("Мобильное приложение на Android")
    assert r.get("project_type") == "mobile"
    r2 = c.classify("Telegram бот для автоматизации")
    assert r2.get("project_type") == "bot"


# --- OllamaClassifier fallback ---


def test_ollama_fallback_when_unavailable() -> None:
    """При недоступности Ollama возвращается пустой dict."""
    c = OllamaClassifier(base_url="http://127.0.0.1:19999", timeout_sec=1)
    r = c.classify("test project")
    assert r == {}


def test_ollama_fallback_on_bad_json() -> None:
    """Битый JSON в response от LLM — fallback в {}."""
    c = OllamaClassifier(base_url="http://localhost:11434", timeout_sec=1)
    # Внешний JSON валиден, но response содержит невалидный JSON
    bad_inner = "not valid json {"
    with patch("ai_service.adapter.ollama.classifier._safe_open") as mock_open:
        mock_resp = mock_open.return_value.__enter__.return_value
        mock_resp.read.return_value = json.dumps({"response": bad_inner}).encode()
        r = c.classify("test")
    assert r == {}


def test_ollama_parse_valid_response() -> None:
    """Корректный JSON от LLM парсится."""
    c = OllamaClassifier(base_url="http://localhost:11434", timeout_sec=1)
    valid_json = '{"project_type":"web","seniority":"middle","technologies":["Python"],"complexity":"medium","budget_level":"unknown","is_spam":false}'

    with patch("ai_service.adapter.ollama.classifier._safe_open") as mock_open:
        mock_resp = mock_open.return_value.__enter__.return_value
        mock_resp.read.return_value = json.dumps({"response": valid_json}).encode()
        r = c.classify("test")
    assert r.get("project_type") == "web"
    assert r.get("seniority") == "middle"
    assert "Python" in r.get("technologies", [])
    assert r.get("is_spam") is False


def test_ollama_fallback_on_disallowed_host() -> None:
    """URL хоста вне allowlist не должен открываться."""
    c = OllamaClassifier(base_url="http://evil.example", timeout_sec=1)
    r = c.classify("test project")
    assert r == {}

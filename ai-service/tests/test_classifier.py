"""Тесты Classifier: RuleBasedClassifier, OllamaClassifier fallback, FallbackClassifier."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from ai_service.adapter.fallback import FallbackClassifier
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


# --- FallbackClassifier ---


def test_fallback_uses_fallback_when_primary_empty() -> None:
    """Если primary вернул пустой dict — используется fallback."""
    primary = MagicMock()
    primary.classify.return_value = {}
    fallback = RuleBasedClassifier()
    c = FallbackClassifier(primary=primary, fallback=fallback)
    r = c.classify("Нужен Python разработчик для веб-сайта")
    assert r.get("project_type") == "web"
    assert "python" in r.get("technologies", [])


def test_fallback_primary_wins_on_conflict() -> None:
    """Поля primary имеют приоритет над fallback при merge."""
    primary = MagicMock()
    primary.classify.return_value = {
        "project_type": "mobile",
        "seniority": "senior",
        "complexity": "high",
        "is_spam": False,
    }
    fallback = MagicMock()
    fallback.classify.return_value = {
        "project_type": "web",
        "seniority": "junior",
        "complexity": "low",
        "budget_level": "unknown",
        "technologies": ["python"],
        "is_spam": False,
    }
    c = FallbackClassifier(primary=primary, fallback=fallback)
    r = c.classify("any text")
    assert r["project_type"] == "mobile"
    assert r["seniority"] == "senior"
    assert r["complexity"] == "high"
    # пропущенное поле дополняется из fallback
    assert r["budget_level"] == "unknown"
    assert r["technologies"] == ["python"]


def test_fallback_merge_missing_fields_from_fallback() -> None:
    """Primary заполняет часть полей, остальные берутся из fallback."""
    primary = MagicMock()
    primary.classify.return_value = {
        "project_type": "bot",
        "complexity": "medium",
        "is_spam": False,
    }
    fallback = MagicMock()
    fallback.classify.return_value = {
        "project_type": "web",
        "seniority": "middle",
        "technologies": ["go"],
        "budget_level": "high",
        "is_spam": False,
    }
    c = FallbackClassifier(primary=primary, fallback=fallback)
    r = c.classify("any text")
    assert r["project_type"] == "bot"       # primary wins
    assert r["seniority"] == "middle"       # из fallback
    assert r["technologies"] == ["go"]      # из fallback
    assert r["budget_level"] == "high"      # из fallback
    assert r["complexity"] == "medium"      # primary


def test_fallback_uses_fallback_when_primary_raises() -> None:
    """Если primary выбросил исключение — используется fallback."""
    primary = MagicMock()
    primary.classify.side_effect = RuntimeError("connection refused")
    fallback = RuleBasedClassifier()
    c = FallbackClassifier(primary=primary, fallback=fallback)
    r = c.classify("Telegram бот для автоматизации")
    assert r.get("project_type") == "bot"
    assert r.get("is_spam") is False


def test_rule_based_complexity_configurable() -> None:
    """Пороги complexity настраиваются через конструктор."""
    # Низкий порог: даже 1 технология → high
    c = RuleBasedClassifier(complexity_high_tech_count=1)
    r = c.classify("Нужен Python разработчик")
    assert r.get("complexity") == "high"

    # Высокий порог и маленький лимит текста: без технологий и короткий текст → low
    c2 = RuleBasedClassifier(complexity_high_tech_count=10, complexity_low_text_len=10000)
    r2 = c2.classify("Небольшой простой сайт")
    assert r2.get("complexity") == "low"

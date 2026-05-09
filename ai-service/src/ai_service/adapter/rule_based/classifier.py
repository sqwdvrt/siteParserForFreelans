"""Rule-based Classifier: ключевые слова, без LLM."""

from __future__ import annotations

import re

from ai_service.port.classifier import ClassificationResult, Classifier
from ai_service.util.sanitize import sanitize_for_classifier

# Ключевые слова для извлечения
TECH_KEYWORDS = {
    "python", "javascript", "typescript", "go", "golang", "rust", "java", "php",
    "react", "vue", "angular", "node", "django", "fastapi", "flask",
    "postgresql", "postgres", "mysql", "redis", "mongodb", "sqlite",
    "docker", "kubernetes", "aws", "linux", "git",
}
PROJECT_TYPES = {
    "web": ["сайт", "веб", "web", "landing", "лендинг", "frontend", "backend"],
    "mobile": ["мобильн", "mobile", "android", "ios", "приложен"],
    "bot": ["бот", "bot", "telegram", "telegram bot"],
}
SENIORITY_KEYWORDS = {
    "junior": ["джуниор", "junior", "начинающ", "стажёр"],
    "middle": ["мидл", "middle", "опыт"],
    "senior": ["сеньор", "senior", "lead", "тимлид"],
}
BUDGET_KEYWORDS = {
    "low": ["до 5000", "до 10000", "бюджет 5", "недорого", "дешево"],
    "high": ["от 100000", "крупный бюджет", "оплата по договорённости"],
}
SPAM_KEYWORDS = ["казино", "заработок", "крипто", "crypto", "биткоин", "bitcoin"]

# Признаки высокой сложности: архитектурные решения, масштабирование, интеграции
COMPLEXITY_HIGH_KEYWORDS = [
    "архитектур",    # архитектура, архитектурный
    "интеграц",      # интеграция, интеграций
    "микросервис",
    "highload",
    "high load",
    "нагрузк",       # нагрузка, нагрузки
    "масштаб",       # масштабирование
    "enterprise",
    "платформ",      # платформа, платформенный
    "инфраструктур", # инфраструктура
    "распределённ",  # распределённая система
    "distributed",
    "big data",
    "machine learning",
    "нейронн",       # нейронная сеть
]

# Признаки низкой сложности: небольшие/простые проекты
COMPLEXITY_LOW_KEYWORDS = [
    "лендинг",
    "landing",
    "небольшой",
    "небольшую",
    "небольшое",
    "простой",
    "простую",
    "простое",
    "несложн",
    "мелкий",
    "маленьк",
]

# Дефолтные пороги сложности (переопределяются в конструкторе)
_DEFAULT_COMPLEXITY_HIGH_TECH_COUNT = 4
_DEFAULT_COMPLEXITY_LOW_TEXT_LEN = 150


def _normalize(text: str) -> str:
    """Нижний регистр для поиска."""
    return text.lower().strip()


def _infer_complexity(
    normalized: str,
    tech_count: int,
    seniority: str,
    high_tech_count: int,
    low_text_len: int,
) -> str:
    """Определить сложность проекта по эвристикам.

    Приоритет: ключевые слова → кол-во технологий/уровень → ключевые слова «просто» → умолчание.
    """
    if any(kw in normalized for kw in COMPLEXITY_HIGH_KEYWORDS):
        return "high"
    if tech_count >= high_tech_count or seniority == "senior":
        return "high"
    if any(kw in normalized for kw in COMPLEXITY_LOW_KEYWORDS):
        return "low"
    if tech_count == 0 and seniority in ("junior", "unknown") and len(normalized) < low_text_len:
        return "low"
    return "medium"


class RuleBasedClassifier(Classifier):
    """Классификатор на правилах. Без LLM, без таймаутов."""

    def __init__(
        self,
        complexity_high_tech_count: int = _DEFAULT_COMPLEXITY_HIGH_TECH_COUNT,
        complexity_low_text_len: int = _DEFAULT_COMPLEXITY_LOW_TEXT_LEN,
    ) -> None:
        self._high_tech_count = complexity_high_tech_count
        self._low_text_len = complexity_low_text_len

    def classify(self, text: str) -> ClassificationResult:
        text = sanitize_for_classifier(text or "")
        if not text:
            return {"is_spam": False}

        normalized = _normalize(text)
        result: ClassificationResult = {}

        # project_type
        for ptype, keywords in PROJECT_TYPES.items():
            if any(kw in normalized for kw in keywords):
                result["project_type"] = ptype
                break
        if "project_type" not in result:
            result["project_type"] = "other"

        # technologies (word boundary to avoid "go" in "django")
        found = [
            t for t in TECH_KEYWORDS
            if re.search(r"\b" + re.escape(t) + r"\b", normalized)
        ]
        if found:
            result["technologies"] = found[:10]

        # seniority
        for level, keywords in SENIORITY_KEYWORDS.items():
            if any(kw in normalized for kw in keywords):
                result["seniority"] = level
                break
        if "seniority" not in result:
            result["seniority"] = "unknown"

        # budget_level
        for level, keywords in BUDGET_KEYWORDS.items():
            if any(kw in normalized for kw in keywords):
                result["budget_level"] = level
                break
        if "budget_level" not in result:
            result["budget_level"] = "unknown"

        # complexity: эвристика по ключевым словам, кол-ву технологий и уровню
        result["complexity"] = _infer_complexity(
            normalized, len(found), result.get("seniority", "unknown"),
            self._high_tech_count, self._low_text_len,
        )

        # is_spam
        result["is_spam"] = any(kw in normalized for kw in SPAM_KEYWORDS)

        return result

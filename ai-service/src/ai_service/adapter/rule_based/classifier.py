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


def _normalize(text: str) -> str:
    """Нижний регистр для поиска."""
    return text.lower().strip()


class RuleBasedClassifier(Classifier):
    """Классификатор на правилах. Без LLM, без таймаутов."""

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

        # complexity
        result["complexity"] = "medium"

        # is_spam
        result["is_spam"] = any(kw in normalized for kw in SPAM_KEYWORDS)

        return result

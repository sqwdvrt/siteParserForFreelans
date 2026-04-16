package filter

import (
	"os"
	"strings"
)

// DefaultAllowedCategories — белый список категорий.
// Если площадка отдаёт категорию, проверяем по нему.
var DefaultAllowedCategories = []string{
	"Программирование",
	"Боты и скрипты",
	"Парсинг данных",
	"Мобильные приложения",
	"Веб-разработка",
	"Базы данных",
	"DevOps и администрирование",
	"API и интеграции",
	"Тестирование",
	"Информационная безопасность",
}

// DefaultBlockedKeywords — стоп-слова в заголовке.
// Применяются только если площадка не отдаёт категорию.
var DefaultBlockedKeywords = []string{
	// Дизайн
	"дизайн", "логотип", "баннер", "инфографика",
	"иллюстрац", "визитк", "фирменный стиль", "макет",
	"полиграф", "ui kit", "figma", "photoshop",
	// Тексты и переводы
	"копирайт", "рерайт", "текст для", "стать",
	"перевод", "транскрипц", "описание товар",
	"seo-текст", "продающий текст",
	// SMM и маркетинг
	"smm", "таргет", "реклама вконтакте",
	"instagram", "tiktok", "reels", "контент-план",
	"ведение соцсет",
	// Медиа
	"монтаж", "видеомонтаж", "озвучк", "подкаст",
	"фотограф", "обработка фото",
	// Маркетплейсы (не разработка)
	"карточки товар", "wildberries", "ozon",
	"инфографика wb", "wb карточк",
	// Прочее нерелевантное
	"рассылка писем", "спам-рассылк",
	"ввод данных", "заполнение таблиц",
	"поиск информации", "ресёрч",
}

// ReasonCategory и ReasonKeyword — метки причины для Prometheus.
const (
	ReasonCategory = "category"
	ReasonKeyword  = "keyword"
)

// JobFilter фильтрует заказы по категории и стоп-словам заголовка.
type JobFilter struct {
	allowedCategories []string
	blockedKeywords   []string
	enabled           bool
}

// New создаёт JobFilter с настройками из переменных окружения.
//
// CRAWLER_FILTER_ENABLED=false  — отключить фильтр полностью.
// CRAWLER_ALLOWED_CATEGORIES    — список категорий через запятую (заменяет дефолтный).
// CRAWLER_BLOCKED_KEYWORDS      — дополнительные стоп-слова через запятую (добавляются к дефолтным).
func New() *JobFilter {
	enabled := os.Getenv("CRAWLER_FILTER_ENABLED") != "false"

	categories := DefaultAllowedCategories
	if envCats := os.Getenv("CRAWLER_ALLOWED_CATEGORIES"); envCats != "" {
		categories = splitTrim(envCats, ",")
	}

	blocked := make([]string, len(DefaultBlockedKeywords))
	copy(blocked, DefaultBlockedKeywords)
	if envBlocked := os.Getenv("CRAWLER_BLOCKED_KEYWORDS"); envBlocked != "" {
		extra := splitTrim(envBlocked, ",")
		blocked = append(blocked, extra...)
	}

	return &JobFilter{
		allowedCategories: categories,
		blockedKeywords:   blocked,
		enabled:           enabled,
	}
}

// IsRelevant возвращает (true, "", "") если заказ стоит сохранять.
// При отклонении: (false, reasonTag, reasonDetail), где
// reasonTag = "category"|"keyword" — метка для Prometheus,
// reasonDetail — человекочитаемое описание для лога.
func (f *JobFilter) IsRelevant(title, category string) (relevant bool, reasonTag, reasonDetail string) {
	if !f.enabled {
		return true, "", ""
	}

	// Шаг 1 — если площадка отдаёт категорию, проверяем whitelist.
	if category != "" {
		catLower := strings.ToLower(category)
		for _, allowed := range f.allowedCategories {
			if strings.Contains(catLower, strings.ToLower(allowed)) {
				return true, "", ""
			}
		}
		return false, ReasonCategory, "category not in whitelist: " + category
	}

	// Шаг 2 — нет категории, проверяем стоп-слова в заголовке.
	titleLower := strings.ToLower(title)
	for _, kw := range f.blockedKeywords {
		if strings.Contains(titleLower, strings.ToLower(kw)) {
			return false, ReasonKeyword, "blocked keyword in title: " + kw
		}
	}

	// Шаг 3 — нет ни категории, ни стоп-слов → пропускаем.
	// Лучше взять лишнее, чем пропустить релевантное.
	return true, "", ""
}

func splitTrim(s, sep string) []string {
	parts := strings.Split(s, sep)
	result := make([]string, 0, len(parts))
	for _, p := range parts {
		if t := strings.TrimSpace(p); t != "" {
			result = append(result, t)
		}
	}
	return result
}

package filter_test

import (
	"testing"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/crawler/filter"
)

func newFilter(t *testing.T) *filter.JobFilter {
	t.Helper()
	t.Setenv("CRAWLER_FILTER_ENABLED", "true")
	t.Setenv("CRAWLER_ALLOWED_CATEGORIES", "")
	t.Setenv("CRAWLER_BLOCKED_KEYWORDS", "")
	return filter.New()
}

// --- Должны проходить фильтр ---

func TestRelevant_DevTitles(t *testing.T) {
	f := newFilter(t)
	cases := []string{
		"Разработка Telegram бота на Python",
		"FastAPI backend для маркетплейса",
		"Парсер сайтов на Go",
		"REST API на Django + PostgreSQL",
		"Деплой сервиса на VPS, Docker Compose",
	}
	for _, title := range cases {
		ok, _, detail := f.IsRelevant(title, "")
		if !ok {
			t.Errorf("IsRelevant(%q, \"\") = false, want true; detail: %s", title, detail)
		}
	}
}

func TestRelevant_AllowedCategory(t *testing.T) {
	f := newFilter(t)
	cases := []struct {
		title    string
		category string
	}{
		{"Любой заголовок", "Программирование"},
		{"Любой заголовок", "Боты и скрипты"},
		{"Что угодно", "Веб-разработка"},
		{"Что угодно", "Парсинг данных"},
		{"Что угодно", "DevOps и администрирование"},
	}
	for _, tc := range cases {
		ok, _, detail := f.IsRelevant(tc.title, tc.category)
		if !ok {
			t.Errorf("IsRelevant(%q, %q) = false, want true; detail: %s", tc.title, tc.category, detail)
		}
	}
}

func TestRelevant_EmptyTitle(t *testing.T) {
	f := newFilter(t)
	// Пустой заголовок не блокируем — нет данных для решения.
	ok, _, _ := f.IsRelevant("", "")
	if !ok {
		t.Error("IsRelevant(\"\", \"\") = false, want true")
	}
}

// --- Должны отсеиваться ---

func TestBlocked_DesignKeywords(t *testing.T) {
	f := newFilter(t)
	cases := []string{
		"Дизайн логотипа для кофейни",
		"Создание баннера для сайта",
		"Инфографика для WB карточек",
		"Нарисовать иллюстрацию для книги",
		"Разработка макета в Figma",
	}
	for _, title := range cases {
		ok, tag, _ := f.IsRelevant(title, "")
		if ok {
			t.Errorf("IsRelevant(%q, \"\") = true, want false", title)
			continue
		}
		if tag != filter.ReasonKeyword {
			t.Errorf("IsRelevant(%q): tag = %q, want %q", title, tag, filter.ReasonKeyword)
		}
	}
}

func TestBlocked_ContentKeywords(t *testing.T) {
	f := newFilter(t)
	cases := []string{
		"Написать статью про путешествия",
		"Перевод текста с английского",
		"Копирайтинг для лендинга",
		"Описание товаров для магазина",
		"SEO-текст для сайта",
	}
	for _, title := range cases {
		ok, tag, _ := f.IsRelevant(title, "")
		if ok {
			t.Errorf("IsRelevant(%q, \"\") = true, want false", title)
			continue
		}
		if tag != filter.ReasonKeyword {
			t.Errorf("IsRelevant(%q): tag = %q, want %q", title, tag, filter.ReasonKeyword)
		}
	}
}

func TestBlocked_SMMKeywords(t *testing.T) {
	f := newFilter(t)
	cases := []string{
		"Монтаж видео для Instagram",
		"SMM ведение аккаунта",
		"Контент-план для соцсетей",
		"Таргет реклама вконтакте",
		"Монтаж reels для TikTok",
	}
	for _, title := range cases {
		ok, tag, _ := f.IsRelevant(title, "")
		if ok {
			t.Errorf("IsRelevant(%q, \"\") = true, want false", title)
			continue
		}
		if tag != filter.ReasonKeyword {
			t.Errorf("IsRelevant(%q): tag = %q, want %q", title, tag, filter.ReasonKeyword)
		}
	}
}

func TestBlocked_NonAllowedCategory(t *testing.T) {
	f := newFilter(t)
	cases := []struct {
		title    string
		category string
	}{
		{"Любой заголовок", "Дизайн и графика"},
		{"Любой заголовок", "Тексты и переводы"},
		{"Любой заголовок", "SMM и маркетинг"},
		{"Любой заголовок", "Фото и видео"},
	}
	for _, tc := range cases {
		ok, tag, _ := f.IsRelevant(tc.title, tc.category)
		if ok {
			t.Errorf("IsRelevant(%q, %q) = true, want false", tc.title, tc.category)
			continue
		}
		if tag != filter.ReasonCategory {
			t.Errorf("IsRelevant(%q, %q): tag = %q, want %q", tc.title, tc.category, tag, filter.ReasonCategory)
		}
	}
}

// --- Граничные случаи ---

func TestDisabled_AllPass(t *testing.T) {
	t.Setenv("CRAWLER_FILTER_ENABLED", "false")
	f := filter.New()

	blocked := []struct{ title, category string }{
		{"Дизайн логотипа", ""},
		{"Написать статью", ""},
		{"Любой", "Дизайн и графика"},
	}
	for _, tc := range blocked {
		ok, _, _ := f.IsRelevant(tc.title, tc.category)
		if !ok {
			t.Errorf("disabled filter: IsRelevant(%q, %q) = false, want true", tc.title, tc.category)
		}
	}
}

func TestCaseInsensitive(t *testing.T) {
	f := newFilter(t)
	cases := []string{
		"ДИЗАЙН ЛОГОТИПА",
		"Написать СТАТЬЮ",
		"МОНТАЖ видео",
		"Figma макет",
		"PHOTOSHOP обработка",
	}
	for _, title := range cases {
		ok, _, _ := f.IsRelevant(title, "")
		if ok {
			t.Errorf("IsRelevant(%q, \"\") = true, want false (case-insensitive check)", title)
		}
	}
}

func TestExtraBlockedKeywordsFromEnv(t *testing.T) {
	t.Setenv("CRAWLER_FILTER_ENABLED", "true")
	t.Setenv("CRAWLER_ALLOWED_CATEGORIES", "")
	t.Setenv("CRAWLER_BLOCKED_KEYWORDS", "нейросеть,ChatGPT,промпт")
	f := filter.New()

	blocked := []string{
		"Написать промпт для нейросети",
		"ChatGPT автоматизация",
		"Работа с нейросетью Midjourney",
	}
	for _, title := range blocked {
		ok, _, _ := f.IsRelevant(title, "")
		if ok {
			t.Errorf("IsRelevant(%q, \"\") = true, want false (extra env keyword)", title)
		}
	}

	// Обычные dev-задачи из env не должны блокироваться
	ok, _, _ := f.IsRelevant("Разработка FastAPI сервиса", "")
	if !ok {
		t.Error("IsRelevant(\"Разработка FastAPI сервиса\", \"\") = false, want true")
	}
}

func TestCustomCategoriesFromEnv(t *testing.T) {
	t.Setenv("CRAWLER_FILTER_ENABLED", "true")
	t.Setenv("CRAWLER_ALLOWED_CATEGORIES", "Только эта категория")
	t.Setenv("CRAWLER_BLOCKED_KEYWORDS", "")
	f := filter.New()

	ok, tag, _ := f.IsRelevant("Что угодно", "Программирование")
	if ok {
		t.Error("custom categories: 'Программирование' should be blocked when not in custom list")
	}
	if tag != filter.ReasonCategory {
		t.Errorf("tag = %q, want %q", tag, filter.ReasonCategory)
	}

	ok, _, _ = f.IsRelevant("Что угодно", "Только эта категория")
	if !ok {
		t.Error("custom categories: 'Только эта категория' should pass")
	}
}

func TestCategoryWinsOverKeyword(t *testing.T) {
	f := newFilter(t)
	// Заголовок содержит стоп-слово, но категория разрешена → проходит.
	ok, _, _ := f.IsRelevant("Дизайн интерфейса бота (Telegram-бот)", "Программирование")
	if !ok {
		t.Error("allowed category should win over blocked keyword in title")
	}
}

func TestCategoryBlocksEvenCleanTitle(t *testing.T) {
	f := newFilter(t)
	// Заголовок чистый, но категория не в whitelist → блокируем.
	ok, tag, _ := f.IsRelevant("Интересный проект", "Тексты и переводы")
	if ok {
		t.Error("non-allowed category should block even with clean title")
	}
	if tag != filter.ReasonCategory {
		t.Errorf("tag = %q, want %q", tag, filter.ReasonCategory)
	}
}

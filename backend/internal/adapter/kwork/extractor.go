package kwork

import (
	"net/url"
	"regexp"
	"strings"
	"time"

	"github.com/PuerkitoBio/goquery"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
)

const baseURL = "https://kwork.ru"
const baseHost = "kwork.ru"

// Extractor парсит HTML страниц Kwork.
type Extractor struct{}

// NewExtractor создаёт экстрактор для Kwork.
func NewExtractor() *Extractor {
	return &Extractor{}
}

// ExtractList извлекает URL проектов из HTML списка.
// Возвращает пустой слайс при пустом или битом HTML, не паникует.
func (e *Extractor) ExtractList(html []byte) ([]string, error) {
	if len(html) == 0 {
		return nil, nil
	}
	doc, err := goquery.NewDocumentFromReader(strings.NewReader(string(html)))
	if err != nil {
		return nil, nil
	}
	var urls []string
	seen := make(map[string]bool)
	doc.Find("a[href*=\"/projects/\"]").Each(func(_ int, s *goquery.Selection) {
		href, ok := s.Attr("href")
		if !ok || href == "" {
			return
		}
		// Пропускаем страницы-списки вида /projects/list/... — это не проекты
		if !projectURLRe.MatchString(href) {
			return
		}
		abs, err := resolveURL(href)
		if err != nil {
			return
		}
		if seen[abs] {
			return
		}
		seen[abs] = true
		urls = append(urls, abs)
	})
	return urls, nil
}

// ExtractDetail извлекает Job из HTML страницы проекта.
// Возвращает nil при пустом или битом HTML, не паникует.
func (e *Extractor) ExtractDetail(html []byte, pageURL string) (*domain.Job, error) {
	if len(html) == 0 {
		return nil, nil
	}
	doc, err := goquery.NewDocumentFromReader(strings.NewReader(string(html)))
	if err != nil {
		return nil, nil
	}
	job := &domain.Job{
		Source:     "kwork",
		URL:        pageURL,
		ExternalID: extractExternalID(pageURL),
		RawHTML:    string(html),
		CreatedAt:  time.Now(),
	}

	// Заголовок: h1.wants-card__header-title → <title> без суффикса " - Kwork"
	if title := strings.TrimSpace(doc.Find("h1.wants-card__header-title").First().Text()); title != "" {
		job.Title = title
	} else if title := strings.TrimSpace(doc.Find("h1").First().Text()); title != "" {
		job.Title = title
	} else if pageTitle := strings.TrimSpace(doc.Find("title").First().Text()); pageTitle != "" {
		job.Title = strings.TrimSuffix(strings.TrimSuffix(pageTitle, " - Kwork"), " — Kwork")
	} else {
		job.Title = "(без названия)"
	}

	// Описание: .wants-card__description-text
	job.Description = strings.TrimSpace(doc.Find(".wants-card__description-text").First().Text())

	// Бюджет: .wants-card__price — содержит "Цена 500 ₽", берём полный текст и чистим
	if raw := strings.TrimSpace(doc.Find(".wants-card__price").First().Text()); raw != "" {
		// Убираем слово "Цена" в начале, нормализуем пробелы
		budget := strings.TrimSpace(strings.TrimPrefix(raw, "Цена"))
		budget = strings.Join(strings.Fields(budget), " ")
		job.Budget = budget
	}

	return job, nil
}

func resolveURL(href string) (string, error) {
	if strings.HasPrefix(href, "http://") || strings.HasPrefix(href, "https://") {
		u, err := url.Parse(href)
		if err != nil {
			return "", err
		}
		if !isAllowedHost(u.Hostname()) {
			return "", url.InvalidHostError(u.Hostname())
		}
		return u.String(), nil
	}
	base, err := url.Parse(baseURL)
	if err != nil {
		return "", err
	}
	ref, err := url.Parse(href)
	if err != nil {
		return "", err
	}
	resolved := base.ResolveReference(ref)
	if !isAllowedHost(resolved.Hostname()) {
		return "", url.InvalidHostError(resolved.Hostname())
	}
	return resolved.String(), nil
}

func isAllowedHost(host string) bool {
	host = strings.ToLower(strings.TrimSuffix(strings.TrimSpace(host), "."))
	return host == baseHost || strings.HasSuffix(host, "."+baseHost)
}

var externalIDRe = regexp.MustCompile(`/projects/(\d+)`)
var projectURLRe = regexp.MustCompile(`/projects/\d+`)

func extractExternalID(u string) string {
	m := externalIDRe.FindStringSubmatch(u)
	if len(m) > 1 {
		return m[1]
	}
	return ""
}

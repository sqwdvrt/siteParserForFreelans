package flru

import (
	"net/url"
	"regexp"
	"strings"
	"time"

	"github.com/PuerkitoBio/goquery"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
)

const baseURL = "https://www.fl.ru"
const baseHost = "fl.ru"

// Extractor парсит HTML страниц FL.ru.
type Extractor struct{}

// NewExtractor создаёт экстрактор для FL.ru.
func NewExtractor() *Extractor {
	return &Extractor{}
}

// ExtractList извлекает URL проектов из HTML страницы со списком.
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
	// FL.ru: ссылки на проекты /projects/ID/...
	doc.Find("a[href*=\"/projects/\"]").Each(func(_ int, s *goquery.Selection) {
		href, ok := s.Attr("href")
		if !ok || href == "" {
			return
		}
		// Отфильтровываем страницы списка (/projects/ без ID) и якоря
		if !projectDetailRe.MatchString(href) {
			return
		}
		abs, err := resolveURL(href)
		if err != nil {
			return
		}
		// Убираем query-параметры для дедупликации
		abs = stripQuery(abs)
		if seen[abs] {
			return
		}
		seen[abs] = true
		urls = append(urls, abs)
	})
	return urls, nil
}

// ExtractDetail извлекает Job из HTML страницы проекта FL.ru.
func (e *Extractor) ExtractDetail(html []byte, pageURL string) (*domain.Job, error) {
	if len(html) == 0 {
		return nil, nil
	}
	doc, err := goquery.NewDocumentFromReader(strings.NewReader(string(html)))
	if err != nil {
		return nil, nil
	}

	job := &domain.Job{
		Source:     "flru",
		URL:        pageURL,
		ExternalID: extractExternalID(pageURL),
		RawHTML:    string(html),
		CreatedAt:  time.Now(),
	}

	// Заголовок: несколько вариантов селекторов FL.ru
	for _, sel := range []string{
		"h1.posting-txt",
		"h1.project-name",
		"h1[itemprop='name']",
		".project-header h1",
		"h1",
	} {
		if t := strings.TrimSpace(doc.Find(sel).First().Text()); t != "" {
			job.Title = t
			break
		}
	}
	if job.Title == "" {
		job.Title = "(без названия)"
	}

	// Описание
	for _, sel := range []string{
		".b-post-body",
		".project-description",
		"[itemprop='description']",
		".posting-description",
		".task-desc",
	} {
		if d := strings.TrimSpace(doc.Find(sel).First().Text()); d != "" {
			job.Description = d
			break
		}
	}

	// Бюджет
	for _, sel := range []string{
		".price-total",
		".b-post-price",
		".project-price",
		".cost",
		"[itemprop='price']",
	} {
		if b := strings.TrimSpace(doc.Find(sel).First().Text()); b != "" {
			job.Budget = b
			break
		}
	}

	// Навыки / теги
	for _, sel := range []string{
		".tags-list a",
		".tags-list span",
		".project-tags li",
		".b-post-tags a",
		".skills li",
	} {
		doc.Find(sel).Each(func(_ int, s *goquery.Selection) {
			if t := strings.TrimSpace(s.Text()); t != "" {
				job.Skills = append(job.Skills, t)
			}
		})
		if len(job.Skills) > 0 {
			break
		}
	}

	// Дата публикации
	for _, sel := range []string{
		"[itemprop='datePosted']",
		".b-post-date",
		".project-date",
		".posted-date",
	} {
		node := doc.Find(sel).First()
		// Сначала атрибут datetime, потом текст
		if dt, exists := node.Attr("datetime"); exists && dt != "" {
			if t := parseDateTime(dt); t != nil {
				job.PostedAt = t
				break
			}
		}
		if txt := strings.TrimSpace(node.Text()); txt != "" {
			if t := parseDateTime(txt); t != nil {
				job.PostedAt = t
				break
			}
		}
	}

	return job, nil
}

// resolveURL превращает относительный href в абсолютный URL fl.ru.
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

func stripQuery(rawURL string) string {
	u, err := url.Parse(rawURL)
	if err != nil {
		return rawURL
	}
	u.RawQuery = ""
	u.Fragment = ""
	return u.String()
}

// projectDetailRe: ссылка на конкретный проект (содержит числовой ID)
var projectDetailRe = regexp.MustCompile(`/projects/\d+`)

var externalIDRe = regexp.MustCompile(`/projects/(\d+)`)

func extractExternalID(u string) string {
	m := externalIDRe.FindStringSubmatch(u)
	if len(m) > 1 {
		return m[1]
	}
	return ""
}

var dateLayouts = []string{
	time.RFC3339,
	"2006-01-02T15:04:05",
	"2006-01-02",
	"02.01.2006",
	"2.1.2006",
}

func parseDateTime(s string) *time.Time {
	s = strings.TrimSpace(s)
	for _, layout := range dateLayouts {
		t, err := time.Parse(layout, s)
		if err == nil {
			return &t
		}
	}
	return nil
}

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
	if title := strings.TrimSpace(doc.Find(".project-title").First().Text()); title != "" {
		job.Title = title
	} else {
		job.Title = "(без названия)"
	}
	job.Description = strings.TrimSpace(doc.Find(".project-description").First().Text())
	if budget := strings.TrimSpace(doc.Find(".project-budget").First().Text()); budget != "" {
		job.Budget = strings.TrimPrefix(budget, "Бюджет: ")
	}
	skillsText := strings.TrimSpace(doc.Find(".project-skills").First().Text())
	if skillsText != "" {
		for _, s := range strings.Split(skillsText, ",") {
			if t := strings.TrimSpace(s); t != "" {
				job.Skills = append(job.Skills, t)
			}
		}
	}
	if dateText := strings.TrimSpace(doc.Find(".project-date").First().Text()); dateText != "" {
		if t := parsePostedAt(dateText); t != nil {
			job.PostedAt = t
		}
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

var externalIDRe = regexp.MustCompile(`/projects/(\d+)/`)

func extractExternalID(u string) string {
	m := externalIDRe.FindStringSubmatch(u)
	if len(m) > 1 {
		return m[1]
	}
	return ""
}

var dateLayouts = []string{
	"02.01.2006",
	"2.1.2006",
}

func parsePostedAt(s string) *time.Time {
	s = strings.TrimPrefix(s, "Опубликовано:")
	s = strings.TrimSpace(s)
	for _, layout := range dateLayouts {
		t, err := time.Parse(layout, s)
		if err == nil {
			return &t
		}
	}
	return nil
}

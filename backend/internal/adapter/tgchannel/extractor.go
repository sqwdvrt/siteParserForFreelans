package tgchannel

import (
	"regexp"
	"strings"
	"time"

	"github.com/PuerkitoBio/goquery"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
)

// Extractor парсит HTML публичных Telegram-каналов через t.me/s/<channel>.
type Extractor struct {
	channelUsername string
}

// NewExtractor создаёт экстрактор для Telegram-канала (username без @).
func NewExtractor(username string) *Extractor {
	username = strings.TrimPrefix(strings.TrimSpace(username), "@")
	return &Extractor{channelUsername: username}
}

// FetchExistingDetails disables detail refetch for already known Telegram posts.
func (e *Extractor) FetchExistingDetails() bool {
	return false
}

// ExtractList парсит https://t.me/s/<channel> и возвращает embed-ссылки на посты.
// Каждая ссылка имеет вид https://t.me/<channel>/<msgID>?embed=1&mode=tme.
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
	doc.Find("a.tgme_widget_message_date").Each(func(_ int, s *goquery.Selection) {
		href, ok := s.Attr("href")
		if !ok || href == "" {
			return
		}
		if !msgURLRe.MatchString(href) {
			return
		}
		embedURL := href + "?embed=1&mode=tme"
		if seen[embedURL] {
			return
		}
		seen[embedURL] = true
		urls = append(urls, embedURL)
	})
	return urls, nil
}

// ExtractDetail парсит embed-страницу поста Telegram и возвращает Job.
// pageURL — embed-ссылка вида https://t.me/<channel>/<msgID>?embed=1&mode=tme.
func (e *Extractor) ExtractDetail(html []byte, pageURL string) (*domain.Job, error) {
	if len(html) == 0 {
		return nil, nil
	}
	doc, err := goquery.NewDocumentFromReader(strings.NewReader(string(html)))
	if err != nil {
		return nil, nil
	}

	job := &domain.Job{
		Source:     "tgchannel:" + e.channelUsername,
		URL:        pageURL,
		ExternalID: extractMsgID(pageURL),
		RawHTML:    string(html),
		CreatedAt:  time.Now(),
	}

	// Текст поста
	text := strings.TrimSpace(doc.Find(".tgme_widget_message_text").First().Text())

	// Title — первые ~100 символов до первого перевода строки
	title := text
	if idx := strings.IndexByte(title, '\n'); idx >= 0 {
		title = title[:idx]
	}
	runes := []rune(strings.TrimSpace(title))
	if len(runes) > 100 {
		title = string(runes[:100])
	} else {
		title = strings.TrimSpace(title)
	}
	if title == "" {
		title = "(без текста)"
	}
	job.Title = title
	job.Description = text

	// Дата публикации
	node := doc.Find("time[datetime]").First()
	if dt, exists := node.Attr("datetime"); exists && dt != "" {
		if t, parseErr := time.Parse(time.RFC3339, dt); parseErr == nil {
			job.PostedAt = &t
		}
	}

	return job, nil
}

// msgURLRe: канонический URL поста t.me без query-параметров.
var msgURLRe = regexp.MustCompile(`^https://t\.me/[A-Za-z0-9_]+/\d+$`)

// msgIDRe: извлекает числовой ID сообщения из URL (до ? или конца строки).
var msgIDRe = regexp.MustCompile(`/(\d+)(?:[?#]|$)`)

func extractMsgID(u string) string {
	m := msgIDRe.FindStringSubmatch(u)
	if len(m) > 1 {
		return m[1]
	}
	return ""
}

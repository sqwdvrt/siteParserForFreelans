package telegram

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

const (
	apiBase       = "https://api.telegram.org/bot"
	timeout       = 15 * time.Second
	maxDescLen    = 500
	maxRetries    = 3
	retryBaseWait = 1 * time.Second
)

// Notifier реализует port.Notifier через Telegram Bot API sendMessage.
// Важно: ошибки возвращаются без токена (только "telegram api: http N") — токен не должен попадать в логи.
type Notifier struct {
	token  string
	client *http.Client
}

// NewNotifier создаёт Notifier с заданным токеном бота.
func NewNotifier(token string) *Notifier {
	return &Notifier{
		token: token,
		client: &http.Client{
			Timeout: timeout,
		},
	}
}

// NewNotifierWithClient создаёт Notifier с кастомным HTTP-клиентом (для тестов).
func NewNotifierWithClient(token string, client *http.Client) *Notifier {
	if client == nil {
		client = &http.Client{Timeout: timeout}
	}
	return &Notifier{token: token, client: client}
}

// Send отправляет уведомление о проекте в Telegram.
func (n *Notifier) Send(ctx context.Context, telegramID int64, p port.NotifyPayload) error {
	if p.Job == nil {
		return fmt.Errorf("job is nil")
	}
	text := formatMessage(p)

	url := apiBase + n.token + "/sendMessage"
	body := map[string]interface{}{
		"chat_id":    telegramID,
		"text":       text,
		"parse_mode": "HTML",
	}
	raw, err := json.Marshal(body)
	if err != nil {
		return fmt.Errorf("marshal request: %w", err)
	}

	var lastErr error
	for attempt := 0; attempt < maxRetries; attempt++ {
		req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(raw))
		if err != nil {
			return fmt.Errorf("create request: %w", err)
		}
		req.Header.Set("Content-Type", "application/json")

		resp, err := n.client.Do(req)
		if err != nil {
			lastErr = fmt.Errorf("send request: %w", err)
			if attempt < maxRetries-1 {
				if err := waitForRetry(ctx, retryBackoff(attempt)); err != nil {
					return err
				}
			}
			continue
		}
		status := resp.StatusCode
		resp.Body.Close()

		if status == http.StatusOK {
			return nil
		}
		lastErr = fmt.Errorf("telegram api: http %d", status)
		if (status == 429 || status >= 500) && attempt < maxRetries-1 {
			if err := waitForRetry(ctx, retryBackoff(attempt)); err != nil {
				return err
			}
			continue
		}
		return lastErr
	}
	return lastErr
}

func retryBackoff(attempt int) time.Duration {
	d := retryBaseWait
	for i := 0; i < attempt; i++ {
		d *= 2
	}
	return d
}

func waitForRetry(ctx context.Context, d time.Duration) error {
	if d <= 0 {
		return nil
	}
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

// formatMessage формирует текст уведомления: заголовок, описание (500 символов), бюджет, ссылка, почему подходит.
func formatMessage(p port.NotifyPayload) string {
	job := p.Job
	var b strings.Builder

	// Заголовок
	b.WriteString("<b>")
	b.WriteString(escapeHTML(job.Title))
	b.WriteString("</b>\n\n")

	// Описание (до 500 символов)
	desc := job.Description
	if desc == "" {
		desc = stripHTML(job.RawHTML)
	}
	desc = truncateRunes(desc, maxDescLen)
	b.WriteString(escapeHTML(desc))

	// Бюджет
	if job.Budget != "" {
		b.WriteString("\n\n💰 ")
		b.WriteString(escapeHTML(job.Budget))
	}

	// Ссылка на проект (кликабельная)
	b.WriteString("\n\n")
	b.WriteString(formatProjectLink(job.URL))

	// Почему подходит (из ai_metadata)
	if p.WhyItFits != "" {
		b.WriteString("\n\n")
		b.WriteString("✨ ")
		b.WriteString(escapeHTML(p.WhyItFits))
	}

	b.WriteString(fmt.Sprintf("\n\n📊 Рекомендация: %.0f%%", p.Score*100))
	return b.String()
}

// formatProjectLink возвращает кликабельную HTML-ссылку на проект.
func formatProjectLink(url string) string {
	if !isValidJobURL(url) {
		return escapeHTML(url)
	}
	return fmt.Sprintf(`<a href="%s">Открыть проект</a>`, escapeForAttr(url))
}

// blockedURLSchemes — опасные схемы, запрещённые в ссылках (XSS, инъекция).
var blockedURLSchemes = []string{
	"javascript:", "data:", "vbscript:", "file:", "about:",
}

func isValidJobURL(raw string) bool {
	if raw == "" || len(raw) > 2000 {
		return false
	}
	parsed, err := url.Parse(raw)
	if err != nil {
		return false
	}
	scheme := strings.ToLower(parsed.Scheme)
	if scheme != "http" && scheme != "https" {
		return false
	}
	lower := strings.ToLower(raw)
	for _, s := range blockedURLSchemes {
		if strings.HasPrefix(lower, s) {
			return false
		}
	}
	return true
}

// escapeHTML экранирует HTML-спецсимволы для защиты от инъекции (parse_mode HTML).
// Обязательно: & < > — иначе возможна подмена разметки или XSS.
func escapeHTML(s string) string {
	s = strings.ReplaceAll(s, "&", "&amp;")
	s = strings.ReplaceAll(s, "<", "&lt;")
	s = strings.ReplaceAll(s, ">", "&gt;")
	return s
}

// escapeForAttr экранирует строку для использования в HTML-атрибуте (href и т.п.).
func escapeForAttr(s string) string {
	s = strings.ReplaceAll(s, "&", "&amp;")
	s = strings.ReplaceAll(s, "\"", "&quot;")
	s = strings.ReplaceAll(s, "<", "&lt;")
	s = strings.ReplaceAll(s, ">", "&gt;")
	return s
}

func stripHTML(s string) string {
	// Простое удаление тегов для raw_html
	var b strings.Builder
	inTag := false
	for _, r := range s {
		if r == '<' {
			inTag = true
			continue
		}
		if r == '>' {
			inTag = false
			continue
		}
		if !inTag && (r == ' ' || r == '\n' || r == '\t' || r > 32) {
			b.WriteRune(r)
		}
	}
	return strings.TrimSpace(b.String())
}

func truncateRunes(s string, max int) string {
	if max <= 0 {
		return ""
	}
	runes := []rune(s)
	if len(runes) <= max {
		return s
	}
	return string(runes[:max]) + "..."
}

// Проверка, что Notifier реализует port.Notifier.
var _ port.Notifier = (*Notifier)(nil)

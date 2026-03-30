package telegram

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math/rand"
	"net/http"
	"net/url"
	"sort"
	"strings"
	"sync"
	"time"
	"unicode"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

const (
	apiBase       = "https://api.telegram.org/bot"
	timeout       = 15 * time.Second
	maxDescLen    = 500
	maxRetries    = 3
	retryBaseWait = 1 * time.Second

	breakerFailureThreshold = 3
	breakerOpenInterval     = 30 * time.Second
	breakerOpenJitter       = 0.2
)

// Notifier реализует port.Notifier через Telegram Bot API sendMessage.
// Важно: ошибки возвращаются без токена (только "telegram api: http N") — токен не должен попадать в логи.
type Notifier struct {
	token                   string
	client                  *http.Client
	maxRetries              int
	retryBaseWait           time.Duration
	breakerFailureThreshold int
	breakerOpenInterval     time.Duration
	breakerOpenJitter       float64
	breaker                 *circuitBreaker
	batchSessionStore       batchSessionStore
	batchSessionPrefix      string
	batchSessionTTL         time.Duration
}

// NewNotifier создаёт Notifier с заданным токеном бота.
func NewNotifier(token string) *Notifier {
	return &Notifier{
		token: token,
		client: &http.Client{
			Timeout: timeout,
		},
		maxRetries:              maxRetries,
		retryBaseWait:           retryBaseWait,
		breakerFailureThreshold: breakerFailureThreshold,
		breakerOpenInterval:     breakerOpenInterval,
		breakerOpenJitter:       breakerOpenJitter,
		breaker:                 newCircuitBreaker(breakerFailureThreshold, breakerOpenInterval, breakerOpenJitter),
		batchSessionTTL:         defaultBatchSessionTTL,
	}
}

// NewNotifierWithClient создаёт Notifier с кастомным HTTP-клиентом (для тестов).
func NewNotifierWithClient(token string, client *http.Client) *Notifier {
	if client == nil {
		client = &http.Client{Timeout: timeout}
	}
	return &Notifier{
		token:                   token,
		client:                  client,
		maxRetries:              maxRetries,
		retryBaseWait:           retryBaseWait,
		breakerFailureThreshold: breakerFailureThreshold,
		breakerOpenInterval:     breakerOpenInterval,
		breakerOpenJitter:       breakerOpenJitter,
		breaker:                 newCircuitBreaker(breakerFailureThreshold, breakerOpenInterval, breakerOpenJitter),
		batchSessionTTL:         defaultBatchSessionTTL,
	}
}

// ConfigureBatchSessionStore configures Redis-backed batch sessions.
func (n *Notifier) ConfigureBatchSessionStore(store batchSessionStore, prefix string) {
	n.configureBatchSessionStore(store, prefix)
	if n.batchSessionTTL <= 0 {
		n.batchSessionTTL = defaultBatchSessionTTL
	}
}

// Config задаёт retry/circuit-breaker параметры Notifier.
type Config struct {
	MaxRetries              int
	RetryBaseWait           time.Duration
	BreakerFailureThreshold int
	BreakerOpenInterval     time.Duration
	BreakerOpenJitter       float64 // 0..1: доля положительного jitter к базовой задержке
}

// Configure применяет retry/circuit-breaker параметры.
func (n *Notifier) Configure(cfg Config) {
	if cfg.MaxRetries > 0 {
		n.maxRetries = cfg.MaxRetries
	}
	if cfg.RetryBaseWait > 0 {
		n.retryBaseWait = cfg.RetryBaseWait
	}
	if cfg.BreakerFailureThreshold > 0 {
		n.breakerFailureThreshold = cfg.BreakerFailureThreshold
	}
	if cfg.BreakerOpenInterval > 0 {
		n.breakerOpenInterval = cfg.BreakerOpenInterval
	}
	if cfg.BreakerOpenJitter >= 0 {
		n.breakerOpenJitter = clampJitter(cfg.BreakerOpenJitter)
	}
	n.breaker = newCircuitBreaker(n.breakerFailureThreshold, n.breakerOpenInterval, n.breakerOpenJitter)
}

func (n *Notifier) ensureConfigDefaults() {
	if n.client == nil {
		n.client = &http.Client{Timeout: timeout}
	}
	if n.maxRetries <= 0 {
		n.maxRetries = maxRetries
	}
	if n.retryBaseWait <= 0 {
		n.retryBaseWait = retryBaseWait
	}
	if n.breakerFailureThreshold <= 0 {
		n.breakerFailureThreshold = breakerFailureThreshold
	}
	if n.breakerOpenInterval <= 0 {
		n.breakerOpenInterval = breakerOpenInterval
	}
	if n.breakerOpenJitter < 0 {
		n.breakerOpenJitter = breakerOpenJitter
	}
	n.breakerOpenJitter = clampJitter(n.breakerOpenJitter)
	if n.breaker == nil {
		n.breaker = newCircuitBreaker(n.breakerFailureThreshold, n.breakerOpenInterval, n.breakerOpenJitter)
	}
	if n.batchSessionTTL <= 0 {
		n.batchSessionTTL = defaultBatchSessionTTL
	}
}

func clampJitter(v float64) float64 {
	if v < 0 {
		return 0
	}
	if v > 1 {
		return 1
	}
	return v
}

// Send отправляет уведомление о проекте в Telegram.
func (n *Notifier) Send(ctx context.Context, telegramID int64, p port.NotifyPayload) error {
	if len(p.Batch) == 0 && p.Job == nil {
		return fmt.Errorf("job is nil")
	}
	n.ensureConfigDefaults()

	text := ""
	var keyboard [][]map[string]interface{}
	if len(p.Batch) > 0 {
		items := sortedBatchItems(p.Batch)
		if len(items) == 0 {
			return fmt.Errorf("job is nil")
		}
		first := items[0]
		text = formatMessage(port.NotifyPayload{
			Job:           first.Job,
			Score:         first.FinalScore,
			FinalScore:    first.FinalScore,
			RankerVersion: first.RankerVersion,
			ReasonCodes:   first.ReasonCodes,
			WhyItFits:     first.WhyItFits,
		})

		if n.batchSessionStore != nil {
			state, err := n.storeBatchSession(ctx, telegramID, items)
			if err != nil {
				return err
			}
			keyboard = buildBatchNavigationKeyboard(state)
		} else {
			keyboard = buildFeedbackKeyboard(port.NotifyPayload{Job: first.Job})
		}
	} else {
		text = formatMessage(p)
		keyboard = buildFeedbackKeyboard(p)
	}

	url := apiBase + n.token + "/sendMessage"
	body := map[string]interface{}{
		"chat_id":                  telegramID,
		"text":                     text,
		"parse_mode":               "HTML",
		"disable_web_page_preview": true,
	}
	if len(keyboard) > 0 {
		body["reply_markup"] = map[string]interface{}{"inline_keyboard": keyboard}
	}
	raw, err := json.Marshal(body)
	if err != nil {
		return fmt.Errorf("marshal request: %w", err)
	}

	var lastErr error
	var transientFailed bool
	var transientImmediateOpen bool
	var transientDelay time.Duration
	for attempt := 0; attempt < n.maxRetries; attempt++ {
		if wait, allowed := n.breaker.beforeRequest(time.Now()); !allowed {
			return newCircuitOpenError(wait)
		}

		req, err := http.NewRequestWithContext(ctx, http.MethodPost, url, bytes.NewReader(raw))
		if err != nil {
			return fmt.Errorf("create request: %w", err)
		}
		req.Header.Set("Content-Type", "application/json")

		resp, err := n.client.Do(req)
		if err != nil {
			delay := n.retryBackoff(attempt)
			transientFailed = true
			if delay > transientDelay {
				transientDelay = delay
			}
			lastErr = newRetryableError(fmt.Sprintf("send request: %v", err), delay)
			if attempt < n.maxRetries-1 {
				if err := waitForRetry(ctx, delay); err != nil {
					return err
				}
			}
			continue
		}
		status := resp.StatusCode
		detail := parseTelegramErrorDescription(resp.Body)
		var rateLimitDelay time.Duration
		if status == http.StatusTooManyRequests {
			rateLimitDelay = parseTelegramRetryAfter(strings.NewReader(detail.rawBody))
		}
		_ = resp.Body.Close()

		if status == http.StatusOK {
			n.breaker.markSuccess()
			return nil
		}

		lastErr = formatTelegramHTTPError(status, detail.description)
		if status == http.StatusTooManyRequests {
			delay := rateLimitDelay
			if delay <= 0 {
				delay = n.retryBackoff(attempt)
			}
			transientFailed = true
			transientImmediateOpen = true
			if delay > transientDelay {
				transientDelay = delay
			}
			lastErr = newRetryableError(lastErr.Error(), delay)
			if attempt < n.maxRetries-1 {
				if err := waitForRetry(ctx, delay); err != nil {
					return err
				}
				continue
			}
			continue
		}
		if status >= http.StatusInternalServerError {
			delay := n.retryBackoff(attempt)
			transientFailed = true
			if delay > transientDelay {
				transientDelay = delay
			}
			lastErr = newRetryableError(lastErr.Error(), delay)
			if attempt < n.maxRetries-1 {
				if err := waitForRetry(ctx, delay); err != nil {
					return err
				}
				continue
			}
			continue
		}

		n.breaker.markPermanentFailure()
		return newPermanentError(lastErr.Error())
	}
	if transientFailed {
		n.breaker.markTransientFailure(time.Now(), transientDelay, transientImmediateOpen)
	}
	if lastErr == nil {
		return errors.New("telegram send failed")
	}
	return lastErr
}

func (n *Notifier) retryBackoff(attempt int) time.Duration {
	d := n.retryBaseWait
	for i := 0; i < attempt; i++ {
		d *= 2
	}
	return d
}

type retryableError struct {
	msg        string
	retryAfter time.Duration
	noAttempt  bool
}

type permanentError struct {
	msg string
}

func newRetryableError(msg string, retryAfter time.Duration) *retryableError {
	if retryAfter <= 0 {
		retryAfter = retryBaseWait
	}
	return &retryableError{msg: msg, retryAfter: retryAfter}
}

func newPermanentError(msg string) *permanentError {
	return &permanentError{msg: msg}
}

func newCircuitOpenError(retryAfter time.Duration) *retryableError {
	e := newRetryableError("telegram circuit open", retryAfter)
	e.noAttempt = true
	return e
}

func (e *retryableError) Error() string {
	return e.msg
}

func (e *retryableError) RetryAfter() time.Duration {
	return e.retryAfter
}

func (e *retryableError) NoAttempt() bool {
	return e.noAttempt
}

func (e *permanentError) Error() string {
	return e.msg
}

func (e *permanentError) Permanent() bool {
	return true
}

// RetryAfter возвращает задержку до следующей попытки, если ошибка retryable.
func RetryAfter(err error) (time.Duration, bool) {
	if err == nil {
		return 0, false
	}
	var withRetry interface{ RetryAfter() time.Duration }
	if errors.As(err, &withRetry) {
		delay := withRetry.RetryAfter()
		if delay <= 0 {
			delay = retryBaseWait
		}
		return delay, true
	}
	return 0, false
}

// ShouldRequeueWithoutRetry сообщает, что delivery надо вернуть в очередь без роста retry-счётчика.
func ShouldRequeueWithoutRetry(err error) bool {
	if err == nil {
		return false
	}
	var mark interface{ NoAttempt() bool }
	if errors.As(err, &mark) {
		return mark.NoAttempt()
	}
	return false
}

func parseTelegramRetryAfter(body io.Reader) time.Duration {
	if body == nil {
		return 0
	}
	raw, err := io.ReadAll(io.LimitReader(body, 8*1024))
	if err != nil || len(raw) == 0 {
		return 0
	}
	var payload struct {
		Parameters struct {
			RetryAfter int `json:"retry_after"`
		} `json:"parameters"`
	}
	if err := json.Unmarshal(raw, &payload); err != nil {
		return 0
	}
	if payload.Parameters.RetryAfter <= 0 {
		return 0
	}
	return time.Duration(payload.Parameters.RetryAfter) * time.Second
}

type telegramErrorDescription struct {
	description string
	rawBody     string
}

func parseTelegramErrorDescription(body io.Reader) telegramErrorDescription {
	if body == nil {
		return telegramErrorDescription{}
	}
	raw, err := io.ReadAll(io.LimitReader(body, 8*1024))
	if err != nil || len(raw) == 0 {
		return telegramErrorDescription{}
	}
	result := telegramErrorDescription{rawBody: string(raw)}
	var payload struct {
		Description string `json:"description"`
	}
	if err := json.Unmarshal(raw, &payload); err == nil {
		result.description = strings.TrimSpace(payload.Description)
	}
	return result
}

func formatTelegramHTTPError(status int, description string) error {
	if description == "" {
		return fmt.Errorf("telegram api: http %d", status)
	}
	return fmt.Errorf("telegram api: http %d: %s", status, description)
}

type breakerState uint8

const (
	breakerClosed breakerState = iota
	breakerOpen
	breakerHalfOpen
)

type circuitBreaker struct {
	mu               sync.Mutex
	state            breakerState
	failures         int
	openedUntil      time.Time
	halfOpenInFlight bool

	failureThreshold int
	openInterval     time.Duration
	openJitter       float64
	randFloat64      func() float64
}

func newCircuitBreaker(failureThreshold int, openInterval time.Duration, openJitter float64) *circuitBreaker {
	if failureThreshold <= 0 {
		failureThreshold = breakerFailureThreshold
	}
	if openInterval <= 0 {
		openInterval = breakerOpenInterval
	}
	openJitter = clampJitter(openJitter)
	return &circuitBreaker{
		state:            breakerClosed,
		failureThreshold: failureThreshold,
		openInterval:     openInterval,
		openJitter:       openJitter,
		randFloat64:      rand.Float64,
	}
}

func (b *circuitBreaker) beforeRequest(now time.Time) (time.Duration, bool) {
	b.mu.Lock()
	defer b.mu.Unlock()

	switch b.state {
	case breakerOpen:
		if now.Before(b.openedUntil) {
			return b.openedUntil.Sub(now), false
		}
		b.state = breakerHalfOpen
		b.halfOpenInFlight = false
	case breakerHalfOpen:
		if b.halfOpenInFlight {
			wait := b.openInterval
			if now.Before(b.openedUntil) {
				wait = b.openedUntil.Sub(now)
			}
			wait = withPositiveJitter(wait, b.openJitter, b.randFloat64)
			if wait <= 0 {
				wait = retryBaseWait
			}
			return wait, false
		}
	}

	if b.state == breakerHalfOpen {
		b.halfOpenInFlight = true
	}
	return 0, true
}

func (b *circuitBreaker) markSuccess() {
	b.mu.Lock()
	defer b.mu.Unlock()

	b.state = breakerClosed
	b.failures = 0
	b.halfOpenInFlight = false
	b.openedUntil = time.Time{}
}

func (b *circuitBreaker) markPermanentFailure() {
	b.mu.Lock()
	defer b.mu.Unlock()

	if b.state == breakerHalfOpen {
		b.state = breakerClosed
	}
	b.failures = 0
	b.halfOpenInFlight = false
}

func (b *circuitBreaker) markTransientFailure(now time.Time, delay time.Duration, immediateOpen bool) {
	b.mu.Lock()
	defer b.mu.Unlock()

	if b.state == breakerHalfOpen {
		b.open(now, delay, immediateOpen)
		return
	}
	b.halfOpenInFlight = false

	if immediateOpen {
		b.open(now, delay, true)
		return
	}

	b.failures++
	if b.failures >= b.failureThreshold {
		b.open(now, delay, false)
	}
}

func (b *circuitBreaker) open(now time.Time, delay time.Duration, useDelayAsIs bool) {
	openFor := b.openInterval
	if useDelayAsIs && delay > 0 {
		openFor = delay
	} else if delay > openFor {
		openFor = delay
	}
	if openFor <= 0 {
		openFor = retryBaseWait
	}
	openFor = withPositiveJitter(openFor, b.openJitter, b.randFloat64)

	b.state = breakerOpen
	b.failures = 0
	b.halfOpenInFlight = false
	b.openedUntil = now.Add(openFor)
}

func withPositiveJitter(base time.Duration, jitter float64, rnd func() float64) time.Duration {
	if base <= 0 || jitter <= 0 {
		return base
	}
	if rnd == nil {
		rnd = rand.Float64
	}
	v := rnd()
	if v < 0 {
		v = 0
	}
	if v > 1 {
		v = 1
	}
	extra := float64(base) * jitter * v
	return base + time.Duration(extra)
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

// formatJobAge возвращает строку с возрастом заказа и цветовым индикатором свежести.
// Использует PostedAt если задан, иначе CreatedAt.
func formatJobAge(job *domain.Job, now time.Time) string {
	var posted time.Time
	if job.PostedAt != nil {
		posted = *job.PostedAt
	} else {
		posted = job.CreatedAt
	}
	if posted.IsZero() {
		return ""
	}
	age := now.Sub(posted)
	if age < 0 {
		age = 0
	}
	var emoji, label string
	switch {
	case age < time.Minute:
		emoji = "🟢"
		label = "только что"
	case age < time.Hour:
		mins := int(age.Minutes())
		emoji = "🟢"
		label = fmt.Sprintf("%d мин назад", mins)
	case age < 6*time.Hour:
		hours := int(age.Hours())
		emoji = "🟡"
		label = fmt.Sprintf("%d ч назад", hours)
	case age < 24*time.Hour:
		hours := int(age.Hours())
		emoji = "🟠"
		label = fmt.Sprintf("%d ч назад", hours)
	default:
		days := int(age.Hours() / 24)
		emoji = "🔴"
		label = fmt.Sprintf("%d дн назад", days)
	}
	return emoji + " " + label
}

// formatMessage формирует текст уведомления: заголовок, описание (500 символов), бюджет, ссылка, почему подходит.
func formatMessage(p port.NotifyPayload) string {
	job := p.Job
	var b strings.Builder

	// Заголовок
	b.WriteString("<b>")
	b.WriteString(escapeHTML(job.Title))
	b.WriteString("</b>")

	// Возраст заказа
	if age := formatJobAge(job, time.Now()); age != "" {
		b.WriteString("\n")
		b.WriteString(age)
	}
	b.WriteString("\n\n")

	// Описание (до 500 символов)
	desc := job.Description
	if desc == "" {
		desc = stripHTML(job.RawHTML)
	}
	desc = normalizeTelegramText(desc)
	desc = trimRepeatedTitlePrefix(desc, job.Title)
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

	fmt.Fprintf(&b, "\n\n📊 Рекомендация: %.0f%%", p.Score*100)
	return b.String()
}

// formatBatchMessage формирует одно batch-уведомление с несколькими проектами.
func formatBatchMessage(p port.NotifyPayload) string {
	items := sortedBatchItems(p.Batch)
	var b strings.Builder

	score := p.EffectiveBatchScore()
	if score < 0 {
		score = 0
	}
	if score > 10 {
		score = 10
	}

	b.WriteString("🎯 <b>Подборка для вас</b> (оценка: ")
	fmt.Fprintf(&b, "%.1f/10", score)
	b.WriteString(")")

	links := make([]string, 0, len(items))
	for idx, item := range items {
		job := item.Job
		title := strings.TrimSpace(job.Title)
		if title == "" {
			title = "Проект"
		}
		b.WriteString("\n\n<b>")
		fmt.Fprintf(&b, "%d. ", idx+1)
		b.WriteString(escapeHTML(title))
		b.WriteString("</b>")
		if age := formatJobAge(job, time.Now()); age != "" {
			b.WriteString(" · ")
			b.WriteString(age)
		}

		why := strings.TrimSpace(item.WhyItFits)
		if why != "" {
			b.WriteString("\n💡 ")
			b.WriteString(escapeHTML(truncateRunes(why, 280)))
		}
		links = append(links, formatProjectLinkWithLabel(job.URL, fmt.Sprintf("Открыть #%d", idx+1)))
	}

	if len(links) > 0 {
		b.WriteString("\n\n")
		b.WriteString(strings.Join(links, " | "))
	}
	return b.String()
}

func buildFeedbackKeyboard(p port.NotifyPayload) [][]map[string]interface{} {
	if p.Job == nil || p.Job.ID <= 0 {
		return nil
	}
	return [][]map[string]interface{}{
		{
			{"text": "👍", "callback_data": fmt.Sprintf("fb:g:%d", p.Job.ID)},
			{"text": "👎", "callback_data": fmt.Sprintf("fb:b:%d", p.Job.ID)},
		},
	}
}

func sortedBatchItems(items []port.BatchNotifyItem) []port.BatchNotifyItem {
	out := make([]port.BatchNotifyItem, 0, len(items))
	for _, item := range items {
		if item.Job == nil {
			continue
		}
		out = append(out, item)
	}
	sort.SliceStable(out, func(i, j int) bool {
		ri := out[i].Rank
		rj := out[j].Rank
		if ri <= 0 && rj <= 0 {
			return i < j
		}
		if ri <= 0 {
			return false
		}
		if rj <= 0 {
			return true
		}
		return ri < rj
	})
	return out
}

// formatProjectLink возвращает кликабельную HTML-ссылку на проект.
func formatProjectLink(url string) string {
	if !isValidJobURL(url) {
		return escapeHTML(url)
	}
	return formatProjectLinkWithLabel(url, "Открыть проект")
}

func formatProjectLinkWithLabel(rawURL, label string) string {
	if !isValidJobURL(rawURL) {
		return escapeHTML(label)
	}
	return fmt.Sprintf(`<a href="%s">%s</a>`, escapeForAttr(rawURL), escapeHTML(label))
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

func normalizeTelegramText(s string) string {
	if s == "" {
		return ""
	}
	var b strings.Builder
	b.Grow(len(s))
	lastSpace := false
	for _, r := range s {
		switch r {
		case '\u00a0', '\u2007', '\u202f':
			r = ' '
		case '\u200b', '\u200c', '\u200d', '\ufeff':
			continue
		}
		if unicode.IsSpace(r) {
			if !lastSpace {
				b.WriteByte(' ')
				lastSpace = true
			}
			continue
		}
		b.WriteRune(r)
		lastSpace = false
	}
	return strings.TrimSpace(b.String())
}

func trimRepeatedTitlePrefix(desc, title string) string {
	normalizedDesc := normalizeTelegramText(desc)
	normalizedTitle := normalizeTelegramText(title)
	if normalizedDesc == "" || normalizedTitle == "" {
		return normalizedDesc
	}
	if !strings.HasPrefix(normalizedDesc, normalizedTitle) {
		return normalizedDesc
	}
	trimmed := strings.TrimLeftFunc(normalizedDesc[len(normalizedTitle):], func(r rune) bool {
		return unicode.IsSpace(r) || strings.ContainsRune(":;,.-!?", r)
	})
	if trimmed == "" {
		return normalizedDesc
	}
	return trimmed
}

// Проверка, что Notifier реализует port.Notifier.
var _ port.Notifier = (*Notifier)(nil)

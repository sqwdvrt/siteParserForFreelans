package telegram

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"testing"
	"time"
	"unicode/utf8"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

type mockTransport struct {
	status int
}

func (m *mockTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	return &http.Response{
		StatusCode: m.status,
		Body:       io.NopCloser(strings.NewReader("")),
		Header:     make(http.Header),
	}, nil
}

type captureTransport struct {
	status   int
	lastBody string
}

func (m *captureTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	if req != nil && req.Body != nil {
		raw, _ := io.ReadAll(req.Body)
		m.lastBody = string(raw)
	}
	return &http.Response{
		StatusCode: m.status,
		Body:       io.NopCloser(strings.NewReader("")),
		Header:     make(http.Header),
	}, nil
}

type captureBatchSessionStore struct {
	key   string
	value []byte
	ttl   time.Duration
}

func (s *captureBatchSessionStore) Set(_ context.Context, key string, value []byte, ttl time.Duration) error {
	s.key = key
	s.value = append([]byte(nil), value...)
	s.ttl = ttl
	return nil
}

func TestNotifier_Send_Success(t *testing.T) {
	n := NewNotifierWithClient("test-token", &http.Client{
		Transport: &mockTransport{status: 200},
		Timeout:   5 * time.Second,
	})
	ctx := context.Background()
	job := &domain.Job{
		ID:          1,
		Title:       "Test Job",
		Description: "Desc",
		URL:         "https://kwork.ru/projects/1",
	}
	err := n.Send(ctx, 123456, port.NotifyPayload{Job: job, Score: 0.85})
	if err != nil {
		t.Fatalf("Send: %v", err)
	}
}

func TestNotifier_Send_429_Retry(t *testing.T) {
	attempts := 0
	transport := &mockTransportWithCount{statusFirst: 429, statusLater: 200, attempts: &attempts}
	n := NewNotifierWithClient("token", &http.Client{
		Transport: transport,
		Timeout:   5 * time.Second,
	})
	ctx := context.Background()
	job := &domain.Job{ID: 1, Title: "T", URL: "https://kwork.ru/p/1"}
	err := n.Send(ctx, 999, port.NotifyPayload{Job: job, Score: 0.9})
	if err != nil {
		t.Fatalf("Send after retry: %v", err)
	}
	if attempts < 2 {
		t.Errorf("want at least 2 attempts (retry), got %d", attempts)
	}
}

func TestNotifier_Send_5xx_RetryThenFail(t *testing.T) {
	n := NewNotifierWithClient("token", &http.Client{
		Transport: &mockTransport{status: 500},
		Timeout:   5 * time.Second,
	})
	ctx := context.Background()
	job := &domain.Job{ID: 1, Title: "T", URL: "https://kwork.ru/p/1"}
	err := n.Send(ctx, 999, port.NotifyPayload{Job: job, Score: 0.9})
	if err == nil {
		t.Fatal("want error on 500 after retries")
	}
	if err.Error() != "telegram api: http 500" {
		t.Errorf("want 'telegram api: http 500', got %v", err)
	}
}

func TestNotifier_Send_NilJob(t *testing.T) {
	n := NewNotifierWithClient("token", &http.Client{Transport: &mockTransport{status: 200}})
	err := n.Send(context.Background(), 123, port.NotifyPayload{Job: nil, Score: 0.5})
	if err == nil {
		t.Fatal("want error on nil job")
	}
	if err.Error() != "job is nil" {
		t.Errorf("want 'job is nil', got %v", err)
	}
}

func TestNotifier_Send_BatchPayload_UsesSingleCardAndNavigationKeyboard(t *testing.T) {
	transport := &captureTransport{status: 200}
	store := &captureBatchSessionStore{}
	n := NewNotifierWithClient("token", &http.Client{
		Transport: transport,
		Timeout:   5 * time.Second,
	})
	n.ConfigureBatchSessionStore(store, "")
	err := n.Send(context.Background(), 123456, port.NotifyPayload{
		Batch: []port.BatchNotifyItem{
			{
				Job: &domain.Job{
					ID:          2,
					Title:       "Second",
					Description: "Detailed second description",
					Budget:      "2000₽",
					URL:         "https://kwork.ru/projects/2",
				},
				WhyItFits:  "Second reason",
				Rank:       2,
				FinalScore: 0.88,
			},
			{
				Job: &domain.Job{
					ID:          1,
					Title:       "First",
					Description: "Detailed first description",
					Budget:      "1000₽",
					URL:         "https://kwork.ru/projects/1",
				},
				WhyItFits:  "First reason",
				Rank:       1,
				FinalScore: 0.91,
			},
		},
		CriticScore: 8.2,
	})
	if err != nil {
		t.Fatalf("Send batch: %v", err)
	}

	var body map[string]any
	if err := json.Unmarshal([]byte(transport.lastBody), &body); err != nil {
		t.Fatalf("decode request body: %v", err)
	}
	text, _ := body["text"].(string)
	if strings.Contains(text, "Подборка для вас") || strings.Contains(text, "Открыть #1") || strings.Contains(text, "Открыть #2") {
		t.Fatalf("batch text still looks like a list: %q", text)
	}
	if !strings.Contains(text, "<b>First</b>") || !strings.Contains(text, "Detailed first description") || !strings.Contains(text, "1000₽") {
		t.Fatalf("first card text missing required fields: %q", text)
	}
	if previewDisabled, ok := body["disable_web_page_preview"].(bool); !ok || !previewDisabled {
		t.Fatalf("disable_web_page_preview must be true, got: %#v", body["disable_web_page_preview"])
	}
	replyMarkup, ok := body["reply_markup"].(map[string]any)
	if !ok {
		t.Fatalf("reply_markup missing: %#v", body["reply_markup"])
	}
	inlineKeyboard, ok := replyMarkup["inline_keyboard"].([]any)
	if !ok || len(inlineKeyboard) != 3 {
		t.Fatalf("inline_keyboard invalid: %#v", replyMarkup["inline_keyboard"])
	}
	firstRow, ok := inlineKeyboard[0].([]any)
	if !ok || len(firstRow) != 3 {
		t.Fatalf("navigation row invalid: %#v", inlineKeyboard[0])
	}
	secondRow, ok := inlineKeyboard[1].([]any)
	if !ok || len(secondRow) != 2 {
		t.Fatalf("feedback row invalid: %#v", inlineKeyboard[1])
	}
	thirdRow, ok := inlineKeyboard[2].([]any)
	if !ok || len(thirdRow) != 1 {
		t.Fatalf("url row invalid: %#v", inlineKeyboard[2])
	}
	currentButton, _ := firstRow[1].(map[string]any)
	currentData, _ := currentButton["callback_data"].(string)
	if !strings.HasPrefix(currentData, "nav:i:") {
		t.Fatalf("current callback must use nav:i, got %q", currentData)
	}
	upButton, _ := secondRow[0].(map[string]any)
	downButton, _ := secondRow[1].(map[string]any)
	upData, _ := upButton["callback_data"].(string)
	downData, _ := downButton["callback_data"].(string)
	if !strings.HasPrefix(upData, "fb:g:") || !strings.HasPrefix(downData, "fb:b:") {
		t.Fatalf("feedback callbacks invalid: up=%q down=%q", upData, downData)
	}
	if urlButton, _ := thirdRow[0].(map[string]any); urlButton["text"] != "Открыть проект" {
		t.Fatalf("url button invalid: %#v", thirdRow[0])
	}
}

func TestNotifier_Send_BatchPayload_StoresSessionPayload(t *testing.T) {
	transport := &captureTransport{status: 200}
	store := &captureBatchSessionStore{}
	n := NewNotifierWithClient("token", &http.Client{
		Transport: transport,
		Timeout:   5 * time.Second,
	})
	n.ConfigureBatchSessionStore(store, "")

	err := n.Send(context.Background(), 123456, port.NotifyPayload{
		Batch: []port.BatchNotifyItem{
			{
				Job: &domain.Job{
					ID:          99,
					Title:       "Storage first",
					Description: "Storage description",
					Budget:      "3000₽",
					URL:         "https://kwork.ru/projects/99",
				},
				WhyItFits:  "Storage reason",
				Rank:       1,
				FinalScore: 0.73,
			},
		},
	})
	if err != nil {
		t.Fatalf("Send batch: %v", err)
	}
	if store.key == "" {
		t.Fatal("batch session was not stored")
	}
	if store.ttl != 24*time.Hour {
		t.Fatalf("batch session ttl = %v, want 24h", store.ttl)
	}
	if !strings.HasPrefix(store.key, "telegram-bot:batch-session:") {
		t.Fatalf("unexpected batch session key: %q", store.key)
	}

	var session struct {
		Version      int   `json:"version"`
		TelegramID   int64 `json:"telegram_id"`
		CurrentIndex int   `json:"current_index"`
		Items        []struct {
			JobID         int64   `json:"job_id"`
			Title         string  `json:"title"`
			Description   string  `json:"description"`
			Budget        string  `json:"budget"`
			URL           string  `json:"url"`
			WhyItFits     string  `json:"why_it_fits"`
			ScorePercent  float64 `json:"score_percent"`
			PostedAtUnix  int64   `json:"posted_at_unix"`
			CreatedAtUnix int64   `json:"created_at_unix"`
		} `json:"items"`
	}
	if err := json.Unmarshal(store.value, &session); err != nil {
		t.Fatalf("decode stored session: %v", err)
	}
	if session.Version != 1 {
		t.Fatalf("session version = %d, want 1", session.Version)
	}
	if session.TelegramID != 123456 {
		t.Fatalf("session telegram_id = %d, want 123456", session.TelegramID)
	}
	if session.CurrentIndex != 0 {
		t.Fatalf("session current_index = %d, want 0", session.CurrentIndex)
	}
	if len(session.Items) != 1 {
		t.Fatalf("session items = %d, want 1", len(session.Items))
	}
	if session.Items[0].JobID != 99 || session.Items[0].Title != "Storage first" {
		t.Fatalf("stored item mismatch: %+v", session.Items[0])
	}
	if session.Items[0].Description != "Storage description" || session.Items[0].Budget != "3000₽" {
		t.Fatalf("stored item data mismatch: %+v", session.Items[0])
	}
	if session.Items[0].ScorePercent != 73 {
		t.Fatalf("score_percent = %v, want 73", session.Items[0].ScorePercent)
	}

	var body map[string]any
	if err := json.Unmarshal([]byte(transport.lastBody), &body); err != nil {
		t.Fatalf("decode request body: %v", err)
	}
	replyMarkup, ok := body["reply_markup"].(map[string]any)
	if !ok {
		t.Fatalf("reply_markup missing: %#v", body["reply_markup"])
	}
	inlineKeyboard, ok := replyMarkup["inline_keyboard"].([]any)
	if !ok || len(inlineKeyboard) != 3 {
		t.Fatalf("inline_keyboard invalid: %#v", replyMarkup["inline_keyboard"])
	}
	row1, _ := inlineKeyboard[0].([]any)
	row2, _ := inlineKeyboard[1].([]any)
	if len(row1) != 3 || len(row2) != 2 {
		t.Fatalf("keyboard rows invalid: %#v", replyMarkup["inline_keyboard"])
	}
	currentButton, _ := row1[1].(map[string]any)
	upButton, _ := row2[0].(map[string]any)
	downButton, _ := row2[1].(map[string]any)
	currentData, _ := currentButton["callback_data"].(string)
	upData, _ := upButton["callback_data"].(string)
	downData, _ := downButton["callback_data"].(string)
	sessionID := strings.TrimPrefix(store.key, "telegram-bot:batch-session:")
	if !strings.Contains(currentData, sessionID) || !strings.Contains(upData, sessionID) || !strings.Contains(downData, sessionID) {
		t.Fatalf("callbacks must reference session id %q: current=%q up=%q down=%q", sessionID, currentData, upData, downData)
	}
}

func TestNotifier_Send_DisablesWebPagePreview(t *testing.T) {
	transport := &captureTransport{status: 200}
	n := NewNotifierWithClient("test-token", &http.Client{
		Transport: transport,
		Timeout:   5 * time.Second,
	})

	job := &domain.Job{
		ID:          1,
		Title:       "Test Job",
		Description: "Desc",
		URL:         "https://fl.ru/projects/1",
	}
	if err := n.Send(context.Background(), 123456, port.NotifyPayload{Job: job, Score: 0.85}); err != nil {
		t.Fatalf("Send: %v", err)
	}

	var body map[string]any
	if err := json.Unmarshal([]byte(transport.lastBody), &body); err != nil {
		t.Fatalf("decode request body: %v", err)
	}
	if previewDisabled, ok := body["disable_web_page_preview"].(bool); !ok || !previewDisabled {
		t.Fatalf("disable_web_page_preview must be true, got: %#v", body["disable_web_page_preview"])
	}
}

func TestBuildFeedbackKeyboard_ForSingleJob(t *testing.T) {
	keyboard := buildFeedbackKeyboard(port.NotifyPayload{
		Job: &domain.Job{ID: 7},
	})
	if len(keyboard) != 1 || len(keyboard[0]) != 2 {
		t.Fatalf("single keyboard shape invalid: %#v", keyboard)
	}
	if keyboard[0][0]["callback_data"] != "fb:g:7" || keyboard[0][1]["callback_data"] != "fb:b:7" {
		t.Fatalf("single keyboard callback data invalid: %#v", keyboard)
	}
}

func TestFormatBatchMessage_SortsByRank(t *testing.T) {
	msg := formatBatchMessage(port.NotifyPayload{
		Batch: []port.BatchNotifyItem{
			{
				Job: &domain.Job{
					Title: "Later",
					URL:   "https://kwork.ru/projects/2",
				},
				Rank: 2,
			},
			{
				Job: &domain.Job{
					Title: "Earlier",
					URL:   "https://kwork.ru/projects/1",
				},
				Rank: 1,
			},
		},
		CriticScore: 7.7,
	})
	if !strings.Contains(msg, "🎯 <b>Подборка для вас</b> (оценка: 7.7/10)") {
		t.Fatalf("batch header not found: %q", msg)
	}
	firstIdx := strings.Index(msg, "<b>1. Earlier</b>")
	secondIdx := strings.Index(msg, "<b>2. Later</b>")
	if firstIdx == -1 || secondIdx == -1 {
		t.Fatalf("sorted items not found: %q", msg)
	}
	if firstIdx > secondIdx {
		t.Fatalf("items order invalid: %q", msg)
	}
}

func TestNotifier_Send_ErrorDoesNotContainToken(t *testing.T) {
	n := NewNotifierWithClient("secret-bot-token-12345", &http.Client{
		Transport: &mockTransport{status: 401},
		Timeout:   5 * time.Second,
	})
	job := &domain.Job{ID: 1, Title: "T", URL: "https://kwork.ru/p/1"}
	err := n.Send(context.Background(), 1, port.NotifyPayload{Job: job, Score: 0.5})
	if err == nil {
		t.Fatal("want error")
	}
	errStr := err.Error()
	if strings.Contains(errStr, "secret") || strings.Contains(errStr, "token") || strings.Contains(errStr, "12345") {
		t.Errorf("error must not contain token, got: %s", errStr)
	}
}

func TestFormatMessage_TruncateDescription_UTF8Safe(t *testing.T) {
	desc := strings.Repeat("🙂", maxDescLen+1)
	job := &domain.Job{
		ID:          1,
		Title:       "Emoji job",
		Description: desc,
		URL:         "https://kwork.ru/projects/1",
	}

	msg := formatMessage(port.NotifyPayload{Job: job, Score: 0.85})
	wantDesc := strings.Repeat("🙂", maxDescLen) + "..."

	if !utf8.ValidString(msg) {
		t.Fatal("message must be valid UTF-8")
	}
	if strings.Contains(msg, "�") {
		t.Fatalf("message contains broken UTF-8 replacement rune: %q", msg)
	}
	if !strings.Contains(msg, wantDesc) {
		t.Fatalf("want truncated description by runes, got: %q", msg)
	}
}

func TestFormatMessage_NormalizesWhitespaceAndDropsRepeatedTitlePrefix(t *testing.T) {
	job := &domain.Job{
		ID:          1,
		Title:       "Разработчик Telegram-бота (Solana / Node.js)",
		Description: "Разработчик Telegram-бота (Solana / Node.js):\n\n\nпроект в категории Блокчейн-решения,\u00a0 07.03.2026 в 14:36\n\n\n   Описание   задачи",
		URL:         "https://kwork.ru/projects/1",
	}

	msg := formatMessage(port.NotifyPayload{Job: job, Score: 0.4925})

	if strings.Count(msg, job.Title) != 1 {
		t.Fatalf("title duplicated in message: %q", msg)
	}
	if strings.Contains(msg, "\n\n\n") {
		t.Fatalf("message contains excessive blank lines: %q", msg)
	}
	if !strings.Contains(msg, "проект в категории Блокчейн-решения, 07.03.2026 в 14:36 Описание задачи") {
		t.Fatalf("normalized description not found: %q", msg)
	}
}

type mockTransportWithCount struct {
	statusFirst int
	statusLater int
	attempts    *int
}

func (m *mockTransportWithCount) RoundTrip(req *http.Request) (*http.Response, error) {
	*m.attempts++
	status := m.statusFirst
	if *m.attempts > 1 {
		status = m.statusLater
	}
	return &http.Response{
		StatusCode: status,
		Body:       io.NopCloser(strings.NewReader("")),
		Header:     make(http.Header),
	}, nil
}

type mockTransportWithBodyAndCount struct {
	status   int
	body     string
	attempts *int
}

func (m *mockTransportWithBodyAndCount) RoundTrip(req *http.Request) (*http.Response, error) {
	*m.attempts++
	return &http.Response{
		StatusCode: m.status,
		Body:       io.NopCloser(strings.NewReader(m.body)),
		Header:     make(http.Header),
	}, nil
}

type errorTransport struct {
	err error
}

func (m *errorTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	if m.err == nil {
		m.err = errors.New("network down")
	}
	return nil, m.err
}

func TestNotifier_Send_RespectsContextCancel_DuringRetryOnHTTPStatus(t *testing.T) {
	n := NewNotifierWithClient("token", &http.Client{
		Transport: &mockTransport{status: 500},
		Timeout:   5 * time.Second,
	})
	job := &domain.Job{ID: 1, Title: "T", URL: "https://kwork.ru/p/1"}

	ctx, cancel := context.WithCancel(context.Background())
	time.AfterFunc(50*time.Millisecond, cancel)

	start := time.Now()
	err := n.Send(ctx, 999, port.NotifyPayload{Job: job, Score: 0.9})
	elapsed := time.Since(start)

	if !errors.Is(err, context.Canceled) {
		t.Fatalf("want context canceled, got %v", err)
	}
	if elapsed >= retryBaseWait {
		t.Fatalf("Send waited too long after cancel: %v", elapsed)
	}
}

func TestNotifier_Send_RespectsContextCancel_DuringRetryOnRequestError(t *testing.T) {
	n := NewNotifierWithClient("token", &http.Client{
		Transport: &errorTransport{},
		Timeout:   5 * time.Second,
	})
	job := &domain.Job{ID: 1, Title: "T", URL: "https://kwork.ru/p/1"}

	ctx, cancel := context.WithCancel(context.Background())
	time.AfterFunc(50*time.Millisecond, cancel)

	start := time.Now()
	err := n.Send(ctx, 999, port.NotifyPayload{Job: job, Score: 0.9})
	elapsed := time.Since(start)

	if !errors.Is(err, context.Canceled) {
		t.Fatalf("want context canceled, got %v", err)
	}
	if elapsed >= retryBaseWait {
		t.Fatalf("Send waited too long after cancel: %v", elapsed)
	}
}

func TestNotifier_Send_429_OpensCircuitWithRetryAfter(t *testing.T) {
	attempts := 0
	n := NewNotifierWithClient("token", &http.Client{
		Transport: &mockTransportWithBodyAndCount{
			status:   429,
			body:     `{"ok":false,"parameters":{"retry_after":2}}`,
			attempts: &attempts,
		},
		Timeout: 5 * time.Second,
	})
	n.maxRetries = 1
	n.retryBaseWait = 10 * time.Millisecond
	n.breaker = newCircuitBreaker(3, 100*time.Millisecond, 0)

	job := &domain.Job{ID: 1, Title: "T", URL: "https://kwork.ru/p/1"}
	err := n.Send(context.Background(), 999, port.NotifyPayload{Job: job, Score: 0.9})
	if err == nil {
		t.Fatal("want error on 429")
	}
	delay, ok := RetryAfter(err)
	if !ok {
		t.Fatalf("want retryable error on 429, got %v", err)
	}
	if delay != 2*time.Second {
		t.Fatalf("retry delay = %v, want 2s", delay)
	}

	err = n.Send(context.Background(), 999, port.NotifyPayload{Job: job, Score: 0.9})
	if err == nil {
		t.Fatal("want circuit-open error on immediate second send")
	}
	if !strings.Contains(err.Error(), "circuit open") {
		t.Fatalf("want circuit-open error, got %v", err)
	}
	if attempts != 1 {
		t.Fatalf("circuit-open call must not hit transport, attempts=%d", attempts)
	}
	if !ShouldRequeueWithoutRetry(err) {
		t.Fatal("circuit-open error must be requeued without retry increment")
	}
}

func TestNotifier_Send_5xx_OpensCircuitAfterThreshold(t *testing.T) {
	attempts := 0
	n := NewNotifierWithClient("token", &http.Client{
		Transport: &mockTransportWithBodyAndCount{
			status:   500,
			body:     "",
			attempts: &attempts,
		},
		Timeout: 5 * time.Second,
	})
	n.maxRetries = 1
	n.retryBaseWait = 10 * time.Millisecond
	n.breaker = newCircuitBreaker(2, 150*time.Millisecond, 0)

	job := &domain.Job{ID: 1, Title: "T", URL: "https://kwork.ru/p/1"}

	if err := n.Send(context.Background(), 999, port.NotifyPayload{Job: job, Score: 0.9}); err == nil {
		t.Fatal("want first 5xx error")
	}
	if err := n.Send(context.Background(), 999, port.NotifyPayload{Job: job, Score: 0.9}); err == nil {
		t.Fatal("want second 5xx error")
	}
	err := n.Send(context.Background(), 999, port.NotifyPayload{Job: job, Score: 0.9})
	if err == nil {
		t.Fatal("want circuit-open error")
	}
	if !strings.Contains(err.Error(), "circuit open") {
		t.Fatalf("want circuit-open error, got %v", err)
	}
	if attempts != 2 {
		t.Fatalf("circuit-open call must not hit transport, attempts=%d", attempts)
	}
}

func TestNotifier_Send_5xx_ThresholdCountsPerSendNotPerInternalRetry(t *testing.T) {
	attempts := 0
	n := NewNotifierWithClient("token", &http.Client{
		Transport: &mockTransportWithBodyAndCount{
			status:   500,
			body:     "",
			attempts: &attempts,
		},
		Timeout: 5 * time.Second,
	})
	n.maxRetries = 3
	n.retryBaseWait = 1 * time.Millisecond
	n.breaker = newCircuitBreaker(3, 100*time.Millisecond, 0)

	job := &domain.Job{ID: 1, Title: "T", URL: "https://kwork.ru/p/1"}

	if err := n.Send(context.Background(), 999, port.NotifyPayload{Job: job, Score: 0.9}); err == nil {
		t.Fatal("want first 5xx error")
	}
	if err := n.Send(context.Background(), 999, port.NotifyPayload{Job: job, Score: 0.9}); err == nil {
		t.Fatal("want second 5xx error")
	}
	err := n.Send(context.Background(), 999, port.NotifyPayload{Job: job, Score: 0.9})
	if err == nil {
		t.Fatal("want third 5xx error")
	}
	if strings.Contains(err.Error(), "circuit open") {
		t.Fatalf("third send must still perform request retries, got %v", err)
	}
	if attempts != 9 {
		t.Fatalf("want 9 real HTTP attempts before opening breaker, got %d", attempts)
	}

	err = n.Send(context.Background(), 999, port.NotifyPayload{Job: job, Score: 0.9})
	if err == nil {
		t.Fatal("want circuit-open error after threshold reached")
	}
	if !strings.Contains(err.Error(), "circuit open") {
		t.Fatalf("want circuit-open error, got %v", err)
	}
	if attempts != 9 {
		t.Fatalf("circuit-open call must not hit transport, attempts=%d", attempts)
	}
}

func TestRetryAfter_DetectsWrappedRetryableError(t *testing.T) {
	err := fmt.Errorf("wrapped: %w", newRetryableError("retryable", 3*time.Second))
	delay, ok := RetryAfter(err)
	if !ok {
		t.Fatal("want wrapped retryable error to be detected")
	}
	if delay != 3*time.Second {
		t.Fatalf("retry delay = %v, want 3s", delay)
	}
}

func TestShouldRequeueWithoutRetry_OnlyForCircuitOpen(t *testing.T) {
	if !ShouldRequeueWithoutRetry(newCircuitOpenError(2 * time.Second)) {
		t.Fatal("circuit-open should request requeue without retry increment")
	}
	if ShouldRequeueWithoutRetry(newRetryableError("telegram api: http 500", time.Second)) {
		t.Fatal("regular retryable errors must consume retry budget")
	}
}

func TestNotifier_Configure_AppliesRetryBreakerConfig(t *testing.T) {
	n := NewNotifierWithClient("token", &http.Client{Transport: &mockTransport{status: 200}})

	n.Configure(Config{
		MaxRetries:              7,
		RetryBaseWait:           250 * time.Millisecond,
		BreakerFailureThreshold: 9,
		BreakerOpenInterval:     45 * time.Second,
		BreakerOpenJitter:       0.35,
	})

	if n.maxRetries != 7 {
		t.Fatalf("maxRetries = %d, want 7", n.maxRetries)
	}
	if n.retryBaseWait != 250*time.Millisecond {
		t.Fatalf("retryBaseWait = %v, want 250ms", n.retryBaseWait)
	}
	if n.breakerFailureThreshold != 9 {
		t.Fatalf("breakerFailureThreshold = %d, want 9", n.breakerFailureThreshold)
	}
	if n.breakerOpenInterval != 45*time.Second {
		t.Fatalf("breakerOpenInterval = %v, want 45s", n.breakerOpenInterval)
	}
	if n.breakerOpenJitter != 0.35 {
		t.Fatalf("breakerOpenJitter = %v, want 0.35", n.breakerOpenJitter)
	}
	if n.breaker == nil {
		t.Fatal("breaker must be rebuilt after Configure")
	}
	if n.breaker.failureThreshold != 9 {
		t.Fatalf("breaker.failureThreshold = %d, want 9", n.breaker.failureThreshold)
	}
	if n.breaker.openInterval != 45*time.Second {
		t.Fatalf("breaker.openInterval = %v, want 45s", n.breaker.openInterval)
	}
	if n.breaker.openJitter != 0.35 {
		t.Fatalf("breaker.openJitter = %v, want 0.35", n.breaker.openJitter)
	}
}

func TestCircuitBreaker_Open_UsesPositiveJitter(t *testing.T) {
	now := time.Unix(100, 0)
	b := newCircuitBreaker(1, 10*time.Second, 0.5)
	b.randFloat64 = func() float64 { return 1 } // max jitter

	b.markTransientFailure(now, 10*time.Second, false)

	if b.state != breakerOpen {
		t.Fatalf("state = %v, want open", b.state)
	}
	got := b.openedUntil.Sub(now)
	if got != 15*time.Second {
		t.Fatalf("open window = %v, want 15s", got)
	}
}

func TestCircuitBreaker_HalfOpenInFlight_UsesPositiveJitterOnWait(t *testing.T) {
	now := time.Unix(200, 0)
	b := newCircuitBreaker(1, 10*time.Second, 0.5)
	b.randFloat64 = func() float64 { return 1 } // max jitter
	b.state = breakerHalfOpen
	b.halfOpenInFlight = true
	b.openedUntil = now.Add(10 * time.Second)

	wait, allowed := b.beforeRequest(now)
	if allowed {
		t.Fatal("half-open in-flight must reject concurrent probe")
	}
	if wait != 15*time.Second {
		t.Fatalf("wait = %v, want 15s", wait)
	}
}

func TestFormatJobAge_JustNow(t *testing.T) {
	now := time.Now()
	posted := now.Add(-30 * time.Second)
	job := &domain.Job{PostedAt: &posted}
	got := formatJobAge(job, now)
	if !strings.Contains(got, "🟢") || !strings.Contains(got, "только что") {
		t.Errorf("want 🟢 только что, got %q", got)
	}
}

func TestFormatJobAge_Minutes(t *testing.T) {
	now := time.Now()
	posted := now.Add(-12 * time.Minute)
	job := &domain.Job{PostedAt: &posted}
	got := formatJobAge(job, now)
	if !strings.Contains(got, "🟢") || !strings.Contains(got, "12 мин назад") {
		t.Errorf("want 🟢 12 мин назад, got %q", got)
	}
}

func TestFormatJobAge_Hours_Yellow(t *testing.T) {
	now := time.Now()
	posted := now.Add(-3 * time.Hour)
	job := &domain.Job{PostedAt: &posted}
	got := formatJobAge(job, now)
	if !strings.Contains(got, "🟡") || !strings.Contains(got, "3 ч назад") {
		t.Errorf("want 🟡 3 ч назад, got %q", got)
	}
}

func TestFormatJobAge_Hours_Orange(t *testing.T) {
	now := time.Now()
	posted := now.Add(-8 * time.Hour)
	job := &domain.Job{PostedAt: &posted}
	got := formatJobAge(job, now)
	if !strings.Contains(got, "🟠") || !strings.Contains(got, "8 ч назад") {
		t.Errorf("want 🟠 8 ч назад, got %q", got)
	}
}

func TestFormatJobAge_Days(t *testing.T) {
	now := time.Now()
	posted := now.Add(-49 * time.Hour)
	job := &domain.Job{PostedAt: &posted}
	got := formatJobAge(job, now)
	if !strings.Contains(got, "🔴") || !strings.Contains(got, "2 дн назад") {
		t.Errorf("want 🔴 2 дн назад, got %q", got)
	}
}

func TestFormatJobAge_FallbackToCreatedAt(t *testing.T) {
	now := time.Now()
	job := &domain.Job{PostedAt: nil, CreatedAt: now.Add(-5 * time.Minute)}
	got := formatJobAge(job, now)
	if !strings.Contains(got, "🟢") || !strings.Contains(got, "5 мин назад") {
		t.Errorf("want 🟢 5 мин назад from CreatedAt, got %q", got)
	}
}

func TestFormatJobAge_ZeroCreatedAt_ReturnsEmpty(t *testing.T) {
	job := &domain.Job{PostedAt: nil}
	got := formatJobAge(job, time.Now())
	if got != "" {
		t.Errorf("want empty string for zero time, got %q", got)
	}
}

func TestFormatMessage_ContainsAge(t *testing.T) {
	now := time.Now()
	posted := now.Add(-20 * time.Minute)
	job := &domain.Job{
		ID:          1,
		Title:       "Python разработчик",
		Description: "Нужен Python backend разработчик.",
		Budget:      "5000₽",
		URL:         "https://kwork.ru/projects/1",
		PostedAt:    &posted,
	}
	text := formatMessage(port.NotifyPayload{Job: job, Score: 0.85})
	if !strings.Contains(text, "🟢") || !strings.Contains(text, "мин назад") {
		t.Errorf("formatMessage should contain age indicator, got:\n%s", text)
	}
}

func TestFormatBatchMessage_ContainsAge(t *testing.T) {
	now := time.Now()
	posted := now.Add(-2 * time.Hour)
	job := &domain.Job{
		ID:       1,
		Title:    "Go разработчик",
		URL:      "https://kwork.ru/projects/1",
		PostedAt: &posted,
	}
	text := formatBatchMessage(port.NotifyPayload{
		CriticScore: 7.5,
		Batch: []port.BatchNotifyItem{
			{Job: job, WhyItFits: "Отличное совпадение", Rank: 1},
		},
	})
	if !strings.Contains(text, "🟡") || !strings.Contains(text, "ч назад") {
		t.Errorf("formatBatchMessage should contain age indicator, got:\n%s", text)
	}
}

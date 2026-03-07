// Package browser реализует port.Fetcher через внешний Browser Render Service
// (browser-service/main.py). Вместо прямого HTTP-запроса к целевому сайту
// отправляет URL в сервис, который рендерит страницу через Playwright и
// возвращает полностью отрисованный HTML — включая контент, загружаемый через JS.
package browser

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

const defaultTimeout = 60 * time.Second
const maxBodyBytes = 10 << 20 // 10 MB
const (
	defaultRetryMaxAttempts = 3
	defaultRetryBaseBackoff = 500 * time.Millisecond
	defaultRetryMaxBackoff  = 5 * time.Second
)

type sleepFunc func(ctx context.Context, d time.Duration) error

// Fetcher выполняет рендер страниц через Browser Render Service.
// Реализует port.Fetcher.
type Fetcher struct {
	serviceURL       string
	client           *http.Client
	retryMaxAttempts int
	retryBaseBackoff time.Duration
	retryMaxBackoff  time.Duration
	sleep            sleepFunc
}

// NewFetcher создаёт Fetcher, который проксирует запросы в Browser Render Service.
// serviceURL — базовый URL сервиса, например "http://browser-service:8090".
func NewFetcher(serviceURL string) *Fetcher {
	return newFetcher(serviceURL, &http.Client{Timeout: defaultTimeout}, nil)
}

func newFetcher(serviceURL string, client *http.Client, sleep sleepFunc) *Fetcher {
	if client == nil {
		client = &http.Client{Timeout: defaultTimeout}
	}
	if sleep == nil {
		sleep = sleepWithContext
	}
	return &Fetcher{
		serviceURL:       strings.TrimSuffix(serviceURL, "/"),
		client:           client,
		retryMaxAttempts: defaultRetryMaxAttempts,
		retryBaseBackoff: defaultRetryBaseBackoff,
		retryMaxBackoff:  defaultRetryMaxBackoff,
		sleep:            sleep,
	}
}

type renderResponse struct {
	HTML string `json:"html"`
	URL  string `json:"url"`
}

// Fetch запрашивает у Browser Render Service полностью отрендеренный HTML страницы.
func (f *Fetcher) Fetch(ctx context.Context, rawURL string) ([]byte, error) {
	endpoint := f.serviceURL + "/render?url=" + url.QueryEscape(rawURL)

	for attempt := 1; attempt <= f.retryMaxAttempts; attempt++ {
		if attempt > 1 {
			if err := f.sleep(ctx, f.retryBackoff(attempt-1)); err != nil {
				return nil, err
			}
		}

		req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
		if err != nil {
			return nil, fmt.Errorf("browser fetcher: build request: %w", err)
		}

		resp, err := f.client.Do(req)
		if err != nil {
			if attempt < f.retryMaxAttempts && ctx.Err() == nil {
				continue
			}
			return nil, fmt.Errorf("browser fetcher: request: %w", err)
		}

		body, readErr := io.ReadAll(io.LimitReader(resp.Body, maxBodyBytes))
		_ = resp.Body.Close()
		if readErr != nil {
			if attempt < f.retryMaxAttempts && ctx.Err() == nil {
				continue
			}
			return nil, fmt.Errorf("browser fetcher: read body: %w", readErr)
		}

		if resp.StatusCode != http.StatusOK {
			if shouldRetryStatus(resp.StatusCode) && attempt < f.retryMaxAttempts {
				continue
			}
			return nil, fmt.Errorf("browser fetcher: http %d: %s", resp.StatusCode, truncate(string(body), 200))
		}

		var r renderResponse
		if err := json.Unmarshal(body, &r); err != nil {
			return nil, fmt.Errorf("browser fetcher: decode response: %w", err)
		}
		if r.HTML == "" {
			return nil, fmt.Errorf("browser fetcher: empty html returned for %s", rawURL)
		}
		return []byte(r.HTML), nil
	}
	return nil, fmt.Errorf("browser fetcher: retries exhausted")
}

func (f *Fetcher) Ping(ctx context.Context) error {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, f.serviceURL+"/healthz", nil)
	if err != nil {
		return fmt.Errorf("browser fetcher ping: build request: %w", err)
	}
	resp, err := f.client.Do(req)
	if err != nil {
		return fmt.Errorf("browser fetcher ping: request: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("browser fetcher ping: http %d", resp.StatusCode)
	}
	return nil
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}

func shouldRetryStatus(code int) bool {
	return code == http.StatusTooManyRequests || code >= http.StatusInternalServerError
}

func (f *Fetcher) retryBackoff(attempt int) time.Duration {
	if attempt <= 0 {
		attempt = 1
	}
	delay := f.retryBaseBackoff
	for i := 1; i < attempt; i++ {
		if delay >= f.retryMaxBackoff/2 {
			return f.retryMaxBackoff
		}
		delay *= 2
	}
	if delay > f.retryMaxBackoff {
		return f.retryMaxBackoff
	}
	return delay
}

func sleepWithContext(ctx context.Context, d time.Duration) error {
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

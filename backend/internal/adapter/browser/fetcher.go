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

// Fetcher выполняет рендер страниц через Browser Render Service.
// Реализует port.Fetcher.
type Fetcher struct {
	serviceURL string
	client     *http.Client
}

// NewFetcher создаёт Fetcher, который проксирует запросы в Browser Render Service.
// serviceURL — базовый URL сервиса, например "http://browser-service:8090".
func NewFetcher(serviceURL string) *Fetcher {
	return &Fetcher{
		serviceURL: strings.TrimSuffix(serviceURL, "/"),
		client: &http.Client{
			Timeout: defaultTimeout,
		},
	}
}

type renderResponse struct {
	HTML string `json:"html"`
	URL  string `json:"url"`
}

// Fetch запрашивает у Browser Render Service полностью отрендеренный HTML страницы.
func (f *Fetcher) Fetch(ctx context.Context, rawURL string) ([]byte, error) {
	endpoint := f.serviceURL + "/render?url=" + url.QueryEscape(rawURL)

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return nil, fmt.Errorf("browser fetcher: build request: %w", err)
	}

	resp, err := f.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("browser fetcher: request: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(io.LimitReader(resp.Body, maxBodyBytes))
	if err != nil {
		return nil, fmt.Errorf("browser fetcher: read body: %w", err)
	}

	if resp.StatusCode != http.StatusOK {
		// Сервис возвращает {"detail": "..."} при ошибках (FastAPI-стандарт).
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

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}

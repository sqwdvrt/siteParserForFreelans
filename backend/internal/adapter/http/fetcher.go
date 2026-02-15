package http

import (
	"context"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"
)

const (
	defaultTimeout   = 30 * time.Second
	defaultRateLimit = 15 * time.Second
	maxBodySize      = 1 << 20 // 1 MB
)

var blockedHosts = map[string]bool{
	"localhost":       true,
	"127.0.0.1":       true,
	"::1":             true,
	"0.0.0.0":         true,
}

type Fetcher struct {
	client    *http.Client
	rateLimit time.Duration
	lastFetch map[string]time.Time
	mu        sync.Mutex
}

type Config struct {
	Timeout    time.Duration
	RateLimit  time.Duration
	Transport  http.RoundTripper // для тестов: мок RoundTripper
}

func NewFetcher(cfg Config) *Fetcher {
	if cfg.Timeout == 0 {
		cfg.Timeout = defaultTimeout
	}
	if cfg.RateLimit == 0 {
		cfg.RateLimit = defaultRateLimit
	}
	transport := cfg.Transport
	if transport == nil {
		transport = http.DefaultTransport
	}
	return &Fetcher{
		client: &http.Client{
			Timeout:   cfg.Timeout,
			Transport: transport,
		},
		rateLimit: cfg.RateLimit,
		lastFetch: make(map[string]time.Time),
	}
}

func (f *Fetcher) Fetch(ctx context.Context, rawURL string) ([]byte, error) {
	if err := f.validateURL(rawURL); err != nil {
		return nil, err
	}

	domain := f.extractDomain(rawURL)
	f.mu.Lock()
	if last, ok := f.lastFetch[domain]; ok {
		elapsed := time.Since(last)
		if elapsed < f.rateLimit {
			f.mu.Unlock()
			time.Sleep(f.rateLimit - elapsed)
			f.mu.Lock()
		}
	}
	f.lastFetch[domain] = time.Now()
	f.mu.Unlock()

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, rawURL, nil)
	if err != nil {
		return nil, fmt.Errorf("create request: %w", err)
	}
	req.Header.Set("User-Agent", "Mozilla/5.0 (compatible; SiteParser/1.0)")

	resp, err := f.client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("fetch: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 400 {
		return nil, fmt.Errorf("http %d", resp.StatusCode)
	}

	body := io.LimitReader(resp.Body, maxBodySize)
	data, err := io.ReadAll(body)
	if err != nil {
		return nil, fmt.Errorf("read body: %w", err)
	}
	return data, nil
}

func (f *Fetcher) validateURL(rawURL string) error {
	u, err := url.Parse(rawURL)
	if err != nil {
		return fmt.Errorf("invalid url: %w", err)
	}
	if u.Scheme != "http" && u.Scheme != "https" {
		return fmt.Errorf("invalid scheme: %s", u.Scheme)
	}
	host := strings.ToLower(strings.TrimSpace(u.Hostname()))
	if blockedHosts[host] {
		return fmt.Errorf("host not allowed: %s", host)
	}
	if strings.HasPrefix(host, "127.") || strings.HasPrefix(host, "10.") ||
		strings.HasPrefix(host, "192.168.") || strings.HasPrefix(host, "172.") {
		return fmt.Errorf("private network not allowed: %s", host)
	}
	return nil
}

func (f *Fetcher) extractDomain(rawURL string) string {
	u, err := url.Parse(rawURL)
	if err != nil {
		return rawURL
	}
	return u.Hostname()
}

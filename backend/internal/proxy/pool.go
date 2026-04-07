// Package proxy implements a proxy pool with rotation, health checks, and ban detection.
package proxy

import (
	"context"
	"fmt"
	"math/rand"
	"net/http"
	"net/url"
	"sync"
	"time"
)

// Proxy represents a single proxy server.
type Proxy struct {
	URL          *url.URL
	LastUsed     time.Time
	SuccessCount int
	FailureCount int
	IsBanned     bool
	BannedUntil  time.Time
	mu           sync.Mutex
}

func (p *Proxy) successRate() float64 {
	p.mu.Lock()
	defer p.mu.Unlock()
	total := p.SuccessCount + p.FailureCount
	if total == 0 {
		return 1.0
	}
	return float64(p.SuccessCount) / float64(total)
}

// RotationStrategy defines how proxies are rotated.
type RotationStrategy int

const (
	// RoundRobin rotates proxies in order.
	RoundRobin RotationStrategy = iota
	// Random picks a random available proxy.
	Random
	// LeastUsed picks the proxy that was used least recently.
	LeastUsed
	// SuccessRate picks the proxy with highest success rate.
	SuccessRate
)

// Config for the proxy pool.
type Config struct {
	ProxyURLs           []string
	Strategy            RotationStrategy
	BanDuration         time.Duration
	HealthCheckInterval time.Duration
	HealthCheckURL      string
	MaxFailures         int
}

// Pool manages a pool of proxies with rotation.
type Pool struct {
	mu           sync.RWMutex
	proxies      []*Proxy
	currentIndex int
	strategy     RotationStrategy
	banDuration  time.Duration
	maxFailures  int
}

// ErrPoolExhausted is returned when all proxies are unavailable.
var ErrPoolExhausted = fmt.Errorf("proxy pool exhausted: all proxies banned or unavailable")

// NewPool creates a new proxy pool.
func NewPool(cfg Config) (*Pool, error) {
	if len(cfg.ProxyURLs) == 0 {
		return nil, fmt.Errorf("no proxies configured")
	}

	p := &Pool{
		strategy:    cfg.Strategy,
		banDuration: cfg.BanDuration,
		maxFailures: cfg.MaxFailures,
	}

	if p.banDuration == 0 {
		p.banDuration = 30 * time.Minute
	}
	if p.maxFailures == 0 {
		p.maxFailures = 3
	}

	for _, proxyURL := range cfg.ProxyURLs {
		u, err := url.Parse(proxyURL)
		if err != nil {
			return nil, fmt.Errorf("invalid proxy URL %q: %w", proxyURL, err)
		}
		p.proxies = append(p.proxies, &Proxy{URL: u})
	}

	// Start health check loop
	if cfg.HealthCheckInterval > 0 && cfg.HealthCheckURL != "" {
		go p.healthCheckLoop(cfg.HealthCheckInterval, cfg.HealthCheckURL)
	}

	return p, nil
}

// Next returns the next proxy to use based on rotation strategy.
func (p *Pool) Next() (*Proxy, error) {
	p.mu.Lock()
	defer p.mu.Unlock()

	available := p.availableProxiesLocked()
	if len(available) == 0 {
		return nil, ErrPoolExhausted
	}

	switch p.strategy {
	case RoundRobin:
		p.currentIndex = (p.currentIndex + 1) % len(available)
		pr := available[p.currentIndex]
		pr.LastUsed = time.Now()
		return pr, nil
	case Random:
		pr := available[rand.Intn(len(available))]
		pr.LastUsed = time.Now()
		return pr, nil
	case LeastUsed:
		least := available[0]
		for _, pr := range available[1:] {
			pr.mu.Lock()
			least.mu.Lock()
			if pr.LastUsed.Before(least.LastUsed) {
				least = pr
			}
			least.mu.Unlock()
			pr.mu.Unlock()
		}
		least.mu.Lock()
		least.LastUsed = time.Now()
		least.mu.Unlock()
		return least, nil
	case SuccessRate:
		best := available[0]
		bestRate := best.successRate()
		for _, pr := range available[1:] {
			if rate := pr.successRate(); rate > bestRate {
				best = pr
				bestRate = rate
			}
		}
		best.mu.Lock()
		best.LastUsed = time.Now()
		best.mu.Unlock()
		return best, nil
	default:
		available[0].mu.Lock()
		available[0].LastUsed = time.Now()
		available[0].mu.Unlock()
		return available[0], nil
	}
}

// RecordSuccess marks a proxy as successful.
func (p *Pool) RecordSuccess(pr *Proxy) {
	pr.mu.Lock()
	defer pr.mu.Unlock()
	pr.SuccessCount++
	if pr.FailureCount > 0 {
		pr.FailureCount--
	}
	pr.LastUsed = time.Now()
}

// RecordFailure marks a proxy as failed. May ban it.
func (p *Pool) RecordFailure(pr *Proxy, isBanned bool) {
	pr.mu.Lock()
	defer pr.mu.Unlock()
	pr.FailureCount++
	pr.LastUsed = time.Now()

	if isBanned || pr.FailureCount >= p.maxFailures {
		pr.IsBanned = true
		pr.BannedUntil = time.Now().Add(p.banDuration)
	}
}

// Stats returns pool statistics.
func (p *Pool) Stats() map[string]any {
	p.mu.RLock()
	defer p.mu.RUnlock()

	total := len(p.proxies)
	available := p.availableProxiesLocked()
	totalSuccess := 0
	totalFailure := 0
	for _, pr := range p.proxies {
		pr.mu.Lock()
		totalSuccess += pr.SuccessCount
		totalFailure += pr.FailureCount
		pr.mu.Unlock()
	}

	banned := total - len(available)
	banRate := float64(banned) / float64(max(total, 1))

	return map[string]any{
		"total":     total,
		"available": len(available),
		"banned":    banned,
		"successes": totalSuccess,
		"failures":  totalFailure,
		"ban_rate":  banRate,
		"strategy":  p.strategyName(),
	}
}

func (p *Pool) strategyName() string {
	switch p.strategy {
	case RoundRobin:
		return "round_robin"
	case Random:
		return "random"
	case LeastUsed:
		return "least_used"
	case SuccessRate:
		return "success_rate"
	default:
		return "unknown"
	}
}

func (p *Pool) unbanExpiredLocked() {
	now := time.Now()
	for _, pr := range p.proxies {
		pr.mu.Lock()
		if pr.IsBanned && now.After(pr.BannedUntil) {
			pr.IsBanned = false
			pr.FailureCount = 0
		}
		pr.mu.Unlock()
	}
}

func (p *Pool) availableProxiesLocked() []*Proxy {
	p.unbanExpiredLocked()
	available := make([]*Proxy, 0, len(p.proxies))
	for _, pr := range p.proxies {
		pr.mu.Lock()
		if !pr.IsBanned {
			available = append(available, pr)
		}
		pr.mu.Unlock()
	}
	return available
}

func (p *Pool) healthCheckLoop(interval time.Duration, checkURL string) {
	ticker := time.NewTicker(interval)
	defer ticker.Stop()

	for range ticker.C {
		p.healthCheckAll(checkURL)
	}
}

func (p *Pool) healthCheckAll(checkURL string) {
	if checkURL == "" {
		checkURL = "https://httpbin.org/ip"
	}

	p.mu.RLock()
	proxies := make([]*Proxy, len(p.proxies))
	copy(proxies, p.proxies)
	p.mu.RUnlock()

	for _, pr := range proxies {
		if err := p.healthCheckSingle(pr, checkURL); err != nil {
			p.RecordFailure(pr, false)
		} else {
			p.RecordSuccess(pr)
		}
	}
}

func (p *Pool) healthCheckSingle(pr *Proxy, checkURL string) error {
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	transport := &http.Transport{
		Proxy: http.ProxyURL(pr.URL),
	}
	client := &http.Client{
		Transport: transport,
		Timeout:   5 * time.Second,
	}

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, checkURL, nil)
	if err != nil {
		return err
	}

	resp, err := client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close() //nolint:errcheck // close error is non-critical in health check

	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return fmt.Errorf("health check failed: status %d", resp.StatusCode)
	}
	return nil
}

// HTTPTransport returns an http.RoundTripper that uses the pool for proxy selection.
func (p *Pool) HTTPTransport(base *http.Transport) *PoolTransport {
	if base == nil {
		base = &http.Transport{
			MaxIdleConns:        100,
			MaxIdleConnsPerHost: 10,
			IdleConnTimeout:     90 * time.Second,
		}
	}
	return &PoolTransport{
		pool: p,
		base: base,
	}
}

// PoolTransport implements http.RoundTripper with proxy pool rotation.
type PoolTransport struct {
	pool *Pool
	base *http.Transport
}

// RoundTrip selects a proxy from the pool and executes the request.
func (pt *PoolTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	pr, err := pt.pool.Next()
	if err != nil {
		return nil, err
	}

	transport := pt.base.Clone()
	transport.Proxy = http.ProxyURL(pr.URL)

	resp, err := transport.RoundTrip(req)
	if err != nil {
		pt.pool.RecordFailure(pr, false)
		return nil, err
	}

	pt.pool.RecordSuccess(pr)
	return resp, nil
}

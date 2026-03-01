package http

import (
	"context"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"
)

const (
	defaultTimeout                 = 30 * time.Second
	defaultRateLimit               = 15 * time.Second
	defaultBreakerFailureThreshold = 3
	defaultBreakerOpenInterval     = 1 * time.Minute
	defaultRetryMaxAttempts        = 1
	defaultRetryBaseBackoff        = 1 * time.Second
	defaultRetryMaxBackoff         = 30 * time.Second
	maxBodySize                    = 1 << 20 // 1 MB
)

var blockedHosts = map[string]bool{
	"localhost": true,
	"127.0.0.1": true,
	"::1":       true,
	"0.0.0.0":   true,
}

var (
	cgnatNet, cgnatNetErr         = parseCIDR("100.64.0.0/10")
	benchmarkNet, benchmarkNetErr = parseCIDR("198.18.0.0/15")
)

type ResolveIPFunc func(ctx context.Context, host string) ([]net.IPAddr, error)
type DialContextFunc func(ctx context.Context, network, addr string) (net.Conn, error)
type SleepFunc func(ctx context.Context, d time.Duration) error

type pinnedHostsContextKey struct{}

type pinnedHosts map[string][]net.IP

type Fetcher struct {
	client                  *http.Client
	rateLimit               time.Duration
	lastFetch               map[string]time.Time
	breakerByDomain         map[string]breakerState
	breakerFailureThreshold int
	breakerOpenInterval     time.Duration
	retryMaxAttempts        int
	retryBaseBackoff        time.Duration
	retryMaxBackoff         time.Duration
	resolveIP               ResolveIPFunc
	dial                    DialContextFunc
	sleep                   SleepFunc
	mu                      sync.Mutex
}

type breakerState struct {
	consecutiveFailures int
	openUntil           time.Time
}

type Config struct {
	Timeout                 time.Duration
	RateLimit               time.Duration
	BreakerFailureThreshold int
	BreakerOpenInterval     time.Duration
	RetryMaxAttempts        int
	RetryBaseBackoff        time.Duration
	RetryMaxBackoff         time.Duration
	Transport               http.RoundTripper // для тестов: мок RoundTripper
	ResolveIP               ResolveIPFunc     // для тестов: мок DNS-резолвера
	Dial                    DialContextFunc   // для тестов: мок dialer
	Sleep                   SleepFunc         // для тестов: мок ожидания backoff
}

func NewFetcher(cfg Config) *Fetcher {
	if cfg.Timeout == 0 {
		cfg.Timeout = defaultTimeout
	}
	if cfg.RateLimit == 0 {
		cfg.RateLimit = defaultRateLimit
	}
	if cfg.BreakerFailureThreshold <= 0 {
		cfg.BreakerFailureThreshold = defaultBreakerFailureThreshold
	}
	if cfg.BreakerOpenInterval <= 0 {
		cfg.BreakerOpenInterval = defaultBreakerOpenInterval
	}
	if cfg.RetryMaxAttempts <= 0 {
		cfg.RetryMaxAttempts = defaultRetryMaxAttempts
	}
	if cfg.RetryBaseBackoff <= 0 {
		cfg.RetryBaseBackoff = defaultRetryBaseBackoff
	}
	if cfg.RetryMaxBackoff <= 0 {
		cfg.RetryMaxBackoff = defaultRetryMaxBackoff
	}
	if cfg.RetryMaxBackoff < cfg.RetryBaseBackoff {
		cfg.RetryMaxBackoff = cfg.RetryBaseBackoff
	}
	resolveIP := cfg.ResolveIP
	if resolveIP == nil {
		resolveIP = net.DefaultResolver.LookupIPAddr
	}
	sleep := cfg.Sleep
	if sleep == nil {
		sleep = sleepWithContext
	}
	transport := cfg.Transport
	f := &Fetcher{
		client: &http.Client{
			Timeout: cfg.Timeout,
		},
		rateLimit:               cfg.RateLimit,
		lastFetch:               make(map[string]time.Time),
		breakerByDomain:         make(map[string]breakerState),
		breakerFailureThreshold: cfg.BreakerFailureThreshold,
		breakerOpenInterval:     cfg.BreakerOpenInterval,
		retryMaxAttempts:        cfg.RetryMaxAttempts,
		retryBaseBackoff:        cfg.RetryBaseBackoff,
		retryMaxBackoff:         cfg.RetryMaxBackoff,
		resolveIP:               resolveIP,
		sleep:                   sleep,
	}
	dial := cfg.Dial
	if dial == nil {
		dialer := &net.Dialer{Timeout: cfg.Timeout, KeepAlive: 30 * time.Second}
		dial = dialer.DialContext
	}
	f.dial = dial
	if transport == nil {
		base, ok := http.DefaultTransport.(*http.Transport)
		if !ok {
			transport = http.DefaultTransport
		} else {
			cloned := base.Clone()
			cloned.DialContext = f.dialPinnedContext
			transport = cloned
		}
	}
	f.client.CheckRedirect = func(req *http.Request, via []*http.Request) error {
		if len(via) >= 10 {
			return fmt.Errorf("stopped after 10 redirects")
		}
		nextCtx, err := f.validateParsedURL(req.Context(), req.URL)
		if err != nil {
			return err
		}
		*req = *req.WithContext(nextCtx)
		return nil
	}
	f.client.Transport = transport
	return f
}

func (f *Fetcher) Fetch(ctx context.Context, rawURL string) ([]byte, error) {
	nextCtx, err := f.validateURL(ctx, rawURL)
	if err != nil {
		return nil, err
	}
	ctx = nextCtx

	domain := normalizeHost(f.extractDomain(rawURL))
	if err := f.waitForRateLimitAndOpenCircuitCheck(ctx, domain); err != nil {
		return nil, err
	}

	for attempt := 1; attempt <= f.retryMaxAttempts; attempt++ {
		if attempt > 1 {
			if err := f.sleep(ctx, f.retryBackoff(attempt-1)); err != nil {
				return nil, err
			}
		}

		req, err := http.NewRequestWithContext(ctx, http.MethodGet, rawURL, nil)
		if err != nil {
			return nil, fmt.Errorf("create request: %w", err)
		}
		req.Header.Set("User-Agent", "Mozilla/5.0 (compatible; SiteParser/1.0)")

		resp, err := f.client.Do(req)
		if err != nil {
			if ctx.Err() == nil {
				f.recordFailure(domain)
			}
			return nil, fmt.Errorf("fetch: %w", err)
		}
		if resp.StatusCode >= 400 {
			statusCode := resp.StatusCode
			_ = resp.Body.Close()
			if shouldRetryOnStatus(statusCode) && attempt < f.retryMaxAttempts {
				continue
			}
			if shouldTripCircuitOnStatus(statusCode) {
				f.recordFailure(domain)
			} else {
				f.recordSuccess(domain)
			}
			return nil, fmt.Errorf("http %d", statusCode)
		}

		body := io.LimitReader(resp.Body, maxBodySize)
		data, err := io.ReadAll(body)
		_ = resp.Body.Close()
		if err != nil {
			f.recordFailure(domain)
			return nil, fmt.Errorf("read body: %w", err)
		}
		f.recordSuccess(domain)
		return data, nil
	}
	return nil, fmt.Errorf("fetch retries exhausted")
}

func shouldTripCircuitOnStatus(code int) bool {
	return code == http.StatusRequestTimeout || code == http.StatusTooManyRequests || code >= http.StatusInternalServerError
}

func shouldRetryOnStatus(code int) bool {
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
		delay = f.retryMaxBackoff
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

func (f *Fetcher) waitForRateLimitAndOpenCircuitCheck(ctx context.Context, domain string) error {
	for {
		now := time.Now()
		wait := time.Duration(0)

		f.mu.Lock()
		if err := f.checkCircuitOpenLocked(domain, now); err != nil {
			f.mu.Unlock()
			return err
		}
		if last, ok := f.lastFetch[domain]; ok {
			elapsed := now.Sub(last)
			if elapsed < f.rateLimit {
				wait = f.rateLimit - elapsed
			}
		}
		if wait <= 0 {
			f.lastFetch[domain] = now
			f.mu.Unlock()
			return nil
		}
		f.mu.Unlock()

		timer := time.NewTimer(wait)
		select {
		case <-ctx.Done():
			timer.Stop()
			return ctx.Err()
		case <-timer.C:
		}
	}
}

func (f *Fetcher) checkCircuitOpenLocked(domain string, now time.Time) error {
	state, ok := f.breakerByDomain[domain]
	if !ok {
		return nil
	}
	if state.openUntil.IsZero() {
		return nil
	}
	if !now.Before(state.openUntil) {
		state.openUntil = time.Time{}
		state.consecutiveFailures = 0
		f.breakerByDomain[domain] = state
		return nil
	}
	retryIn := time.Until(state.openUntil).Round(100 * time.Millisecond)
	if retryIn < 0 {
		retryIn = 0
	}
	return fmt.Errorf("circuit open for domain %s (retry in %s)", domain, retryIn)
}

func (f *Fetcher) recordFailure(domain string) {
	if domain == "" {
		return
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	state := f.breakerByDomain[domain]
	state.consecutiveFailures++
	if state.consecutiveFailures >= f.breakerFailureThreshold {
		state.openUntil = time.Now().Add(f.breakerOpenInterval)
		state.consecutiveFailures = 0
	}
	f.breakerByDomain[domain] = state
}

func (f *Fetcher) recordSuccess(domain string) {
	if domain == "" {
		return
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	delete(f.breakerByDomain, domain)
}

func (f *Fetcher) validateURL(ctx context.Context, rawURL string) (context.Context, error) {
	u, err := url.Parse(rawURL)
	if err != nil {
		return ctx, fmt.Errorf("invalid url: %w", err)
	}
	return f.validateParsedURL(ctx, u)
}

func (f *Fetcher) validateParsedURL(ctx context.Context, u *url.URL) (context.Context, error) {
	if u == nil {
		return ctx, fmt.Errorf("invalid url: empty")
	}
	if u.Scheme != "http" && u.Scheme != "https" {
		return ctx, fmt.Errorf("invalid scheme: %s", u.Scheme)
	}
	if u.Hostname() == "" {
		return ctx, fmt.Errorf("empty host")
	}
	host := strings.ToLower(strings.TrimSpace(u.Hostname()))
	if blockedHosts[host] {
		return ctx, fmt.Errorf("host not allowed: %s", host)
	}
	ips, err := f.resolveAllowedIPs(ctx, host)
	if err != nil {
		return ctx, err
	}
	return withPinnedHost(ctx, host, ips), nil
}

func (f *Fetcher) resolveAllowedIPs(ctx context.Context, host string) ([]net.IP, error) {
	if ip := net.ParseIP(host); ip != nil {
		if isDisallowedIP(ip) {
			return nil, fmt.Errorf("private network not allowed: %s", host)
		}
		return []net.IP{cloneIP(ip)}, nil
	}
	addrs, err := f.resolveIP(ctx, host)
	if err != nil {
		return nil, fmt.Errorf("resolve host %s: %w", host, err)
	}
	if len(addrs) == 0 {
		return nil, fmt.Errorf("resolve host %s: no addresses", host)
	}
	uniq := make(map[string]struct{}, len(addrs))
	ips := make([]net.IP, 0, len(addrs))
	for _, addr := range addrs {
		ip := cloneIP(addr.IP)
		if isDisallowedIP(ip) {
			return nil, fmt.Errorf("private network not allowed: %s", ip.String())
		}
		key := ip.String()
		if _, ok := uniq[key]; ok {
			continue
		}
		uniq[key] = struct{}{}
		ips = append(ips, ip)
	}
	if len(ips) == 0 {
		return nil, fmt.Errorf("resolve host %s: no addresses", host)
	}
	return ips, nil
}

func (f *Fetcher) dialPinnedContext(ctx context.Context, network, addr string) (net.Conn, error) {
	host, port, err := net.SplitHostPort(addr)
	if err != nil {
		return nil, fmt.Errorf("split host port: %w", err)
	}
	host = normalizeHost(host)
	pinned := pinnedHostIPsFromContext(ctx, host)
	if len(pinned) == 0 {
		return nil, fmt.Errorf("host %s has no pinned addresses", host)
	}
	var lastErr error
	for _, ip := range pinned {
		target := net.JoinHostPort(ip.String(), port)
		conn, err := f.dial(ctx, network, target)
		if err == nil {
			return conn, nil
		}
		lastErr = err
	}
	if lastErr == nil {
		lastErr = fmt.Errorf("all pinned addresses failed")
	}
	return nil, fmt.Errorf("dial pinned host %s:%s: %w", host, port, lastErr)
}

func isDisallowedIP(ip net.IP) bool {
	if ip == nil {
		return true
	}
	if ip.IsLoopback() || ip.IsPrivate() || ip.IsMulticast() || ip.IsUnspecified() ||
		ip.IsLinkLocalUnicast() || ip.IsLinkLocalMulticast() {
		return true
	}
	return ipInNetwork(ip, cgnatNet, cgnatNetErr) || ipInNetwork(ip, benchmarkNet, benchmarkNetErr)
}

func parseCIDR(raw string) (*net.IPNet, error) {
	_, n, err := net.ParseCIDR(raw)
	if err != nil {
		return nil, fmt.Errorf("parse cidr %q: %w", raw, err)
	}
	return n, nil
}

func ipInNetwork(ip net.IP, network *net.IPNet, parseErr error) bool {
	if parseErr != nil || network == nil {
		// Fail closed: malformed CIDR constant must not disable SSRF protections.
		return true
	}
	return network.Contains(ip)
}

func cloneIP(ip net.IP) net.IP {
	if ip == nil {
		return nil
	}
	cp := make(net.IP, len(ip))
	copy(cp, ip)
	return cp
}

func normalizeHost(host string) string {
	host = strings.TrimSpace(strings.ToLower(host))
	host = strings.Trim(host, "[]")
	return host
}

func cloneIPs(ips []net.IP) []net.IP {
	out := make([]net.IP, len(ips))
	for i, ip := range ips {
		out[i] = cloneIP(ip)
	}
	return out
}

func withPinnedHost(ctx context.Context, host string, ips []net.IP) context.Context {
	host = normalizeHost(host)
	if host == "" || len(ips) == 0 {
		return ctx
	}
	existing, _ := ctx.Value(pinnedHostsContextKey{}).(pinnedHosts)
	book := make(pinnedHosts, len(existing)+1)
	for k, v := range existing {
		book[k] = cloneIPs(v)
	}
	book[host] = cloneIPs(ips)
	return context.WithValue(ctx, pinnedHostsContextKey{}, book)
}

func pinnedHostIPsFromContext(ctx context.Context, host string) []net.IP {
	host = normalizeHost(host)
	if host == "" || ctx == nil {
		return nil
	}
	book, _ := ctx.Value(pinnedHostsContextKey{}).(pinnedHosts)
	if book == nil {
		return nil
	}
	return cloneIPs(book[host])
}

func (f *Fetcher) extractDomain(rawURL string) string {
	u, err := url.Parse(rawURL)
	if err != nil {
		return rawURL
	}
	return u.Hostname()
}

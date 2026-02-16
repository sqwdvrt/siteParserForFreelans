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
	defaultTimeout   = 30 * time.Second
	defaultRateLimit = 15 * time.Second
	maxBodySize      = 1 << 20 // 1 MB
)

var blockedHosts = map[string]bool{
	"localhost": true,
	"127.0.0.1": true,
	"::1":       true,
	"0.0.0.0":   true,
}

var (
	cgnatNet     = mustCIDR("100.64.0.0/10")
	benchmarkNet = mustCIDR("198.18.0.0/15")
)

type ResolveIPFunc func(ctx context.Context, host string) ([]net.IPAddr, error)
type DialContextFunc func(ctx context.Context, network, addr string) (net.Conn, error)

type pinnedHostsContextKey struct{}

type pinnedHosts map[string][]net.IP

type Fetcher struct {
	client    *http.Client
	rateLimit time.Duration
	lastFetch map[string]time.Time
	resolveIP ResolveIPFunc
	dial      DialContextFunc
	mu        sync.Mutex
}

type Config struct {
	Timeout   time.Duration
	RateLimit time.Duration
	Transport http.RoundTripper // для тестов: мок RoundTripper
	ResolveIP ResolveIPFunc     // для тестов: мок DNS-резолвера
	Dial      DialContextFunc   // для тестов: мок dialer
}

func NewFetcher(cfg Config) *Fetcher {
	if cfg.Timeout == 0 {
		cfg.Timeout = defaultTimeout
	}
	if cfg.RateLimit == 0 {
		cfg.RateLimit = defaultRateLimit
	}
	resolveIP := cfg.ResolveIP
	if resolveIP == nil {
		resolveIP = net.DefaultResolver.LookupIPAddr
	}
	transport := cfg.Transport
	f := &Fetcher{
		client: &http.Client{
			Timeout: cfg.Timeout,
		},
		rateLimit: cfg.RateLimit,
		lastFetch: make(map[string]time.Time),
		resolveIP: resolveIP,
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
	return cgnatNet.Contains(ip) || benchmarkNet.Contains(ip)
}

func mustCIDR(raw string) *net.IPNet {
	_, n, err := net.ParseCIDR(raw)
	if err != nil {
		panic(err)
	}
	return n
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

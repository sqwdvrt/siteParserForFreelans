package http

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"strings"
	"testing"
	"time"
)

// mockTransport возвращает заданные ответы, не делая реальных запросов.
type mockTransport struct {
	status int
	body   []byte
}

func (m *mockTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	var body io.ReadCloser = http.NoBody
	if len(m.body) > 0 {
		body = &mockReadCloser{data: m.body}
	}
	return &http.Response{
		StatusCode: m.status,
		Body:       body,
		Header:     make(http.Header),
	}, nil
}

type mockReadCloser struct {
	data []byte
	pos  int
}

func (m *mockReadCloser) Read(p []byte) (n int, err error) {
	if m.pos >= len(m.data) {
		return 0, io.EOF
	}
	n = copy(p, m.data[m.pos:])
	m.pos += n
	return n, nil
}

func (m *mockReadCloser) Close() error { return nil }

type redirectTransport struct{}

func (m *redirectTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	if req.URL.Hostname() == "example.com" {
		return &http.Response{
			StatusCode: http.StatusFound,
			Header:     http.Header{"Location": []string{"http://127.0.0.1/secret"}},
			Body:       http.NoBody,
			Request:    req,
		}, nil
	}
	return &http.Response{
		StatusCode: http.StatusOK,
		Body:       http.NoBody,
		Header:     make(http.Header),
		Request:    req,
	}, nil
}

func testResolveIP(_ context.Context, host string) ([]net.IPAddr, error) {
	switch host {
	case "kwork.ru":
		return []net.IPAddr{{IP: net.ParseIP("93.184.216.34")}}, nil
	case "example.com":
		return []net.IPAddr{{IP: net.ParseIP("93.184.216.34")}}, nil
	default:
		if ip := net.ParseIP(host); ip != nil {
			return []net.IPAddr{{IP: ip}}, nil
		}
		return nil, fmt.Errorf("host not found: %s", host)
	}
}

func newTestFetcher(cfg Config) *Fetcher {
	if cfg.RateLimit == 0 {
		cfg.RateLimit = time.Millisecond
	}
	if cfg.ResolveIP == nil {
		cfg.ResolveIP = testResolveIP
	}
	return NewFetcher(cfg)
}

func TestFetcher_Fetch_Success(t *testing.T) {
	body := []byte("<html>ok</html>")
	f := newTestFetcher(Config{
		Timeout:   5 * time.Second,
		RateLimit: time.Millisecond,
		Transport: &mockTransport{status: 200, body: body},
	})
	ctx := context.Background()
	data, err := f.Fetch(ctx, "https://kwork.ru/projects")
	if err != nil {
		t.Fatalf("Fetch: %v", err)
	}
	if string(data) != string(body) {
		t.Errorf("body: want %q, got %q", body, data)
	}
}

func TestFetcher_Fetch_4xx(t *testing.T) {
	f := newTestFetcher(Config{
		RateLimit: time.Millisecond,
		Transport: &mockTransport{status: 404},
	})
	_, err := f.Fetch(context.Background(), "https://kwork.ru/projects")
	if err == nil {
		t.Fatal("want error on 404")
	}
	if err.Error() != "http 404" {
		t.Errorf("want 'http 404', got %v", err)
	}
}

func TestFetcher_Fetch_5xx(t *testing.T) {
	f := newTestFetcher(Config{
		RateLimit: time.Millisecond,
		Transport: &mockTransport{status: 500},
	})
	_, err := f.Fetch(context.Background(), "https://kwork.ru/projects")
	if err == nil {
		t.Fatal("want error on 500")
	}
}

func TestFetcher_ValidateURL_Localhost(t *testing.T) {
	f := newTestFetcher(Config{RateLimit: time.Millisecond})
	_, err := f.Fetch(context.Background(), "http://localhost/test")
	if err == nil {
		t.Fatal("want error for localhost")
	}
	if err.Error() != "host not allowed: localhost" {
		t.Errorf("want host not allowed, got %v", err)
	}
}

func TestFetcher_ValidateURL_127(t *testing.T) {
	f := newTestFetcher(Config{RateLimit: time.Millisecond})
	_, err := f.Fetch(context.Background(), "http://127.0.0.1/test")
	if err == nil {
		t.Fatal("want error for 127.0.0.1")
	}
}

func TestFetcher_ValidateURL_PrivateNetwork(t *testing.T) {
	f := newTestFetcher(Config{RateLimit: time.Millisecond})
	for _, u := range []string{
		"http://192.168.1.1/test",
		"http://10.0.0.1/test",
		"http://172.16.0.1/test",
	} {
		_, err := f.Fetch(context.Background(), u)
		if err == nil {
			t.Errorf("want error for %s", u)
		}
	}
}

func TestFetcher_BodySizeLimit(t *testing.T) {
	largeBody := make([]byte, 2*1024*1024)
	for i := range largeBody {
		largeBody[i] = 'x'
	}
	f := newTestFetcher(Config{
		RateLimit: time.Millisecond,
		Transport: &mockTransport{status: 200, body: largeBody},
	})
	data, err := f.Fetch(context.Background(), "https://kwork.ru/large")
	if err != nil {
		t.Fatalf("Fetch: %v", err)
	}
	maxSize := 1 << 20
	if len(data) > maxSize {
		t.Errorf("body size %d exceeds limit %d", len(data), maxSize)
	}
}

func TestFetcher_ValidateURL_ResolvedPrivateIP(t *testing.T) {
	f := newTestFetcher(Config{
		ResolveIP: func(_ context.Context, host string) ([]net.IPAddr, error) {
			if host == "evil.example" {
				return []net.IPAddr{{IP: net.ParseIP("10.10.10.10")}}, nil
			}
			return nil, errors.New("unexpected host")
		},
	})
	_, err := f.Fetch(context.Background(), "https://evil.example/projects")
	if err == nil {
		t.Fatal("want error for host resolved to private IP")
	}
	if !strings.Contains(err.Error(), "private network not allowed") {
		t.Errorf("want private network error, got %v", err)
	}
}

func TestFetcher_Fetch_BlocksRedirectToPrivateHost(t *testing.T) {
	f := newTestFetcher(Config{
		Transport: &redirectTransport{},
	})
	_, err := f.Fetch(context.Background(), "http://example.com/start")
	if err == nil {
		t.Fatal("want error for redirect to private host")
	}
	if !strings.Contains(err.Error(), "not allowed") {
		t.Errorf("want redirect host blocked, got %v", err)
	}
}

func TestFetcher_DialPinnedContext_UsesPinnedIP(t *testing.T) {
	var dialed []string
	f := newTestFetcher(Config{
		Dial: func(ctx context.Context, network, addr string) (net.Conn, error) {
			dialed = append(dialed, addr)
			return nil, errors.New("dial blocked in test")
		},
	})
	ctx := withPinnedHost(context.Background(), "example.com", []net.IP{net.ParseIP("93.184.216.34")})

	_, err := f.dialPinnedContext(ctx, "tcp", "example.com:443")
	if err == nil {
		t.Fatal("want dial error")
	}
	if len(dialed) != 1 {
		t.Fatalf("want 1 dial attempt, got %d", len(dialed))
	}
	if dialed[0] != "93.184.216.34:443" {
		t.Fatalf("want dial to pinned ip, got %s", dialed[0])
	}
}

func TestFetcher_DialPinnedContext_DNSRebindingMitigatedByPin(t *testing.T) {
	var (
		resolveCalls int
		dialed       []string
	)
	f := newTestFetcher(Config{
		ResolveIP: func(_ context.Context, host string) ([]net.IPAddr, error) {
			resolveCalls++
			if resolveCalls == 1 {
				return []net.IPAddr{{IP: net.ParseIP("93.184.216.34")}}, nil
			}
			// DNS "rebinding" simulation: later resolve would return different IP.
			return []net.IPAddr{{IP: net.ParseIP("203.0.113.10")}}, nil
		},
		Dial: func(ctx context.Context, network, addr string) (net.Conn, error) {
			dialed = append(dialed, addr)
			return nil, errors.New("dial blocked in test")
		},
	})

	ctx, err := f.validateURL(context.Background(), "https://rebind.example/path")
	if err != nil {
		t.Fatalf("validateURL: %v", err)
	}

	_, err = f.dialPinnedContext(ctx, "tcp", "rebind.example:443")
	if err == nil {
		t.Fatal("want dial error")
	}
	if len(dialed) != 1 {
		t.Fatalf("want 1 dial attempt, got %d", len(dialed))
	}
	if dialed[0] != "93.184.216.34:443" {
		t.Fatalf("want dial to first pinned ip, got %s", dialed[0])
	}
	if resolveCalls != 1 {
		t.Fatalf("want single resolve during validation, got %d", resolveCalls)
	}
}

func TestFetcher_DialPinnedContext_RejectsUnpinnedHost(t *testing.T) {
	f := newTestFetcher(Config{
		Dial: func(ctx context.Context, network, addr string) (net.Conn, error) {
			t.Fatalf("dial should not be called for unpinned host")
			return nil, nil
		},
	})

	_, err := f.dialPinnedContext(context.Background(), "tcp", "unknown.example:443")
	if err == nil {
		t.Fatal("want error for unpinned host")
	}
	if !strings.Contains(err.Error(), "no pinned addresses") {
		t.Fatalf("unexpected error: %v", err)
	}
}

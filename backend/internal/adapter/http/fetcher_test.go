package http

import (
	"context"
	"io"
	"net/http"
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

func TestFetcher_Fetch_Success(t *testing.T) {
	body := []byte("<html>ok</html>")
	f := NewFetcher(Config{
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
	f := NewFetcher(Config{
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
	f := NewFetcher(Config{
		RateLimit: time.Millisecond,
		Transport: &mockTransport{status: 500},
	})
	_, err := f.Fetch(context.Background(), "https://kwork.ru/projects")
	if err == nil {
		t.Fatal("want error on 500")
	}
}

func TestFetcher_ValidateURL_Localhost(t *testing.T) {
	f := NewFetcher(Config{RateLimit: time.Millisecond})
	_, err := f.Fetch(context.Background(), "http://localhost/test")
	if err == nil {
		t.Fatal("want error for localhost")
	}
	if err.Error() != "host not allowed: localhost" {
		t.Errorf("want host not allowed, got %v", err)
	}
}

func TestFetcher_ValidateURL_127(t *testing.T) {
	f := NewFetcher(Config{RateLimit: time.Millisecond})
	_, err := f.Fetch(context.Background(), "http://127.0.0.1/test")
	if err == nil {
		t.Fatal("want error for 127.0.0.1")
	}
}

func TestFetcher_ValidateURL_PrivateNetwork(t *testing.T) {
	f := NewFetcher(Config{RateLimit: time.Millisecond})
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
	f := NewFetcher(Config{
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

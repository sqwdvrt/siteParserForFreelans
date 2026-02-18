package api

import (
	"bytes"
	"context"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
)

type mockUserEmbedQueue struct {
	enqueueFunc func(ctx context.Context, userID int64) error
}

func (m *mockUserEmbedQueue) Enqueue(ctx context.Context, userID int64) error {
	if m.enqueueFunc != nil {
		return m.enqueueFunc(ctx, userID)
	}
	return nil
}

type mockNonceStore struct {
	useFunc func(ctx context.Context, key string, ttl time.Duration) (bool, error)
	used    map[string]bool
}

func (m *mockNonceStore) Use(ctx context.Context, key string, ttl time.Duration) (bool, error) {
	if m.useFunc != nil {
		return m.useFunc(ctx, key, ttl)
	}
	if m.used == nil {
		m.used = make(map[string]bool)
	}
	if m.used[key] {
		return false, nil
	}
	m.used[key] = true
	return true, nil
}

type mockRateLimiter struct {
	allowFunc func(ctx context.Context, key string, limit int, window time.Duration) (bool, error)
}

func (m *mockRateLimiter) Allow(ctx context.Context, key string, limit int, window time.Duration) (bool, error) {
	if m.allowFunc != nil {
		return m.allowFunc(ctx, key, limit, window)
	}
	return true, nil
}

type mockUserRepo struct {
	saveFunc          func(ctx context.Context, telegramID int64) (int64, error)
	getByIDFunc       func(ctx context.Context, userID int64) (*domain.User, error)
	updateProfileFunc func(ctx context.Context, userID int64, profileText string) error
}

func (m *mockUserRepo) Save(ctx context.Context, telegramID int64) (int64, error) {
	if m.saveFunc != nil {
		return m.saveFunc(ctx, telegramID)
	}
	return 42, nil
}

func (m *mockUserRepo) GetByID(ctx context.Context, userID int64) (*domain.User, error) {
	if m.getByIDFunc != nil {
		return m.getByIDFunc(ctx, userID)
	}
	return &domain.User{ID: userID, TelegramID: 123456789}, nil
}

func (m *mockUserRepo) GetByTelegramID(ctx context.Context, telegramID int64) (*domain.User, error) {
	return nil, nil
}

func (m *mockUserRepo) UpdateProfile(ctx context.Context, userID int64, profileText string) error {
	if m.updateProfileFunc != nil {
		return m.updateProfileFunc(ctx, userID, profileText)
	}
	return nil
}

const testAuthToken = "test-api-token"
const testTelegramIDHeader = "123456789"
const testUserHMACSecret = "test-hmac-secret"

func newAuthHeaders() map[string]string {
	return map[string]string{
		"Authorization": "Bearer " + testAuthToken,
	}
}

func newAuthAndTelegramHeaders() map[string]string {
	return map[string]string{
		"Authorization": "Bearer " + testAuthToken,
		"X-Telegram-ID": testTelegramIDHeader,
	}
}

// newAuthHeadersWithUserSign добавляет X-Telegram-ID, X-Request-Timestamp, X-Request-Nonce, X-Request-Signature для user-level auth.
func newAuthHeadersWithUserSign(method, path string, telegramID int64, body []byte) map[string]string {
	nonce := "nonce" + strconv.FormatInt(time.Now().UnixNano(), 10)
	return newAuthHeadersWithUserSignAndNonce(method, path, telegramID, nonce, body)
}

func newAuthHeadersWithUserSignAndNonce(method, path string, telegramID int64, nonce string, body []byte) map[string]string {
	ts := time.Now().Unix()
	tsStr := strconv.FormatInt(ts, 10)
	sig := signUserRequest(testUserHMACSecret, method, path, telegramID, tsStr, nonce, body)
	return map[string]string{
		"Authorization":       "Bearer " + testAuthToken,
		"X-Telegram-ID":       strconv.FormatInt(telegramID, 10),
		"X-Request-Timestamp": tsStr,
		"X-Request-Nonce":     nonce,
		"X-Request-Signature": hex.EncodeToString(sig),
	}
}

func newJSONRequest(method, target string, body []byte, headers map[string]string) *http.Request {
	req := httptest.NewRequest(method, target, bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	for k, v := range headers {
		req.Header.Set(k, v)
	}
	return req
}

func attachRouteUserID(req *http.Request, userID string) *http.Request {
	rctx := chi.NewRouteContext()
	rctx.URLParams.Add("id", userID)
	return req.WithContext(context.WithValue(req.Context(), chi.RouteCtxKey, rctx))
}

func TestHandlers_PostUsers_Success(t *testing.T) {
	repo := &mockUserRepo{
		saveFunc: func(ctx context.Context, telegramID int64) (int64, error) {
			if telegramID != 123456789 {
				return 0, errors.New("unexpected telegram_id")
			}
			return 1, nil
		},
	}
	h := &Handlers{UserRepo: repo, AuthToken: testAuthToken, UserHMACSecret: testUserHMACSecret}

	body, _ := json.Marshal(PostUsersRequest{TelegramID: 123456789})
	req := newJSONRequest(http.MethodPost, "/users", body, newAuthHeadersWithUserSign(http.MethodPost, "/users", 123456789, body))
	rr := httptest.NewRecorder()

	h.PostUsers(rr, req)

	if rr.Code != http.StatusOK {
		t.Errorf("status = %d, want 200", rr.Code)
	}
	var resp PostUsersResponse
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if resp.UserID != 1 {
		t.Errorf("user_id = %d, want 1", resp.UserID)
	}
}

func TestHandlers_PostUsers_InvalidJSON(t *testing.T) {
	h := &Handlers{UserRepo: &mockUserRepo{}, AuthToken: testAuthToken, UserHMACSecret: testUserHMACSecret}
	body := []byte("not json")
	req := newJSONRequest(http.MethodPost, "/users", body, newAuthHeadersWithUserSign(http.MethodPost, "/users", 123456789, body))
	rr := httptest.NewRecorder()

	h.PostUsers(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("status = %d, want 400", rr.Code)
	}
}

func TestHandlers_PostUsers_BodyTooLarge(t *testing.T) {
	h := &Handlers{UserRepo: &mockUserRepo{}, AuthToken: testAuthToken, UserHMACSecret: testUserHMACSecret}

	padding := strings.Repeat("a", int(maxJSONBodyBytes))
	body := []byte(`{"telegram_id":123,"padding":"` + padding + `"}`)
	req := newJSONRequest(http.MethodPost, "/users", body, newAuthHeadersWithUserSign(http.MethodPost, "/users", 123, body))
	rr := httptest.NewRecorder()

	h.PostUsers(rr, req)

	if rr.Code != http.StatusRequestEntityTooLarge {
		t.Errorf("status = %d, want 413", rr.Code)
	}
}

func TestHandlers_PostUsers_InvalidTelegramID(t *testing.T) {
	h := &Handlers{UserRepo: &mockUserRepo{}, AuthToken: testAuthToken, UserHMACSecret: testUserHMACSecret}

	tests := []struct {
		name string
		body string
	}{
		{"zero", `{"telegram_id":0}`},
		{"negative", `{"telegram_id":-1}`},
		{"string", `{"telegram_id":"123"}`},
		{"float", `{"telegram_id":123.45}`},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			body := []byte(tt.body)
			req := newJSONRequest(http.MethodPost, "/users", body, newAuthHeadersWithUserSign(http.MethodPost, "/users", 123, body))
			rr := httptest.NewRecorder()

			h.PostUsers(rr, req)

			if rr.Code != http.StatusBadRequest {
				t.Errorf("status = %d, want 400", rr.Code)
			}
		})
	}
}

func TestHandlers_PostUsers_RepoError(t *testing.T) {
	repo := &mockUserRepo{
		saveFunc: func(ctx context.Context, telegramID int64) (int64, error) {
			return 0, errors.New("db error")
		},
	}
	h := &Handlers{UserRepo: repo, AuthToken: testAuthToken, UserHMACSecret: testUserHMACSecret}

	body, _ := json.Marshal(PostUsersRequest{TelegramID: 123})
	req := newJSONRequest(http.MethodPost, "/users", body, newAuthHeadersWithUserSign(http.MethodPost, "/users", 123, body))
	rr := httptest.NewRecorder()

	h.PostUsers(rr, req)

	if rr.Code != http.StatusInternalServerError {
		t.Errorf("status = %d, want 500", rr.Code)
	}
	if strings.Contains(rr.Body.String(), "db error") {
		t.Errorf("response leaks internal details: %q", rr.Body.String())
	}
	if !strings.Contains(rr.Body.String(), "internal error") {
		t.Errorf("response = %q, want generic internal error", rr.Body.String())
	}
}

func TestHandlers_PutUserProfile_Success(t *testing.T) {
	var gotUserID int64
	var gotProfile string
	repo := &mockUserRepo{
		updateProfileFunc: func(ctx context.Context, userID int64, profileText string) error {
			gotUserID = userID
			gotProfile = profileText
			return nil
		},
	}
	h := &Handlers{UserRepo: repo, AuthToken: testAuthToken, UserHMACSecret: testUserHMACSecret}

	body := []byte(`{"profile_text":"I am a developer"}`)
	req := newJSONRequest(http.MethodPut, "/users/1/profile", body, newAuthHeadersWithUserSign(http.MethodPut, "/users/1/profile", 123456789, body))
	req = attachRouteUserID(req, "1")
	rr := httptest.NewRecorder()

	h.PutUserProfile(rr, req)

	if rr.Code != http.StatusNoContent {
		t.Errorf("status = %d, want 204", rr.Code)
	}
	if gotUserID != 1 {
		t.Errorf("user_id = %d, want 1", gotUserID)
	}
	if gotProfile != "I am a developer" {
		t.Errorf("profile_text = %q, want %q", gotProfile, "I am a developer")
	}
}

func TestHandlers_PutUserProfile_InvalidID(t *testing.T) {
	h := &Handlers{UserRepo: &mockUserRepo{}, AuthToken: testAuthToken, UserHMACSecret: testUserHMACSecret}

	tests := []struct {
		name string
		id   string
	}{
		{"nan", "abc"},
		{"zero", "0"},
		{"negative", "-1"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			body := []byte(`{"profile_text":"x"}`)
			path := "/users/" + tt.id + "/profile"
			req := newJSONRequest(http.MethodPut, path, body, newAuthHeadersWithUserSign(http.MethodPut, path, 123456789, body))
			req = attachRouteUserID(req, tt.id)
			rr := httptest.NewRecorder()

			h.PutUserProfile(rr, req)

			if rr.Code != http.StatusBadRequest {
				t.Errorf("status = %d, want 400", rr.Code)
			}
		})
	}
}

func TestHandlers_PutUserProfile_InvalidJSON(t *testing.T) {
	h := &Handlers{UserRepo: &mockUserRepo{}, AuthToken: testAuthToken, UserHMACSecret: testUserHMACSecret}

	body := []byte("not json")
	req := newJSONRequest(http.MethodPut, "/users/1/profile", body, newAuthHeadersWithUserSign(http.MethodPut, "/users/1/profile", 123456789, body))
	req = attachRouteUserID(req, "1")
	rr := httptest.NewRecorder()

	h.PutUserProfile(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("status = %d, want 400", rr.Code)
	}
}

func TestHandlers_PutUserProfile_BodyTooLarge(t *testing.T) {
	h := &Handlers{UserRepo: &mockUserRepo{}, AuthToken: testAuthToken, UserHMACSecret: testUserHMACSecret}

	padding := strings.Repeat("a", int(maxJSONBodyBytes))
	body := []byte(`{"profile_text":"` + padding + `"}`)
	req := newJSONRequest(http.MethodPut, "/users/1/profile", body, newAuthHeadersWithUserSign(http.MethodPut, "/users/1/profile", 123456789, body))
	req = attachRouteUserID(req, "1")
	rr := httptest.NewRecorder()

	h.PutUserProfile(rr, req)

	if rr.Code != http.StatusRequestEntityTooLarge {
		t.Errorf("status = %d, want 413", rr.Code)
	}
}

func TestHandlers_PutUserProfile_RepoError(t *testing.T) {
	repo := &mockUserRepo{
		updateProfileFunc: func(ctx context.Context, userID int64, profileText string) error {
			return errors.New("db error")
		},
	}
	h := &Handlers{UserRepo: repo, AuthToken: testAuthToken, UserHMACSecret: testUserHMACSecret}

	body := []byte(`{"profile_text":"x"}`)
	req := newJSONRequest(http.MethodPut, "/users/1/profile", body, newAuthHeadersWithUserSign(http.MethodPut, "/users/1/profile", 123456789, body))
	req = attachRouteUserID(req, "1")
	rr := httptest.NewRecorder()

	h.PutUserProfile(rr, req)

	if rr.Code != http.StatusInternalServerError {
		t.Errorf("status = %d, want 500", rr.Code)
	}
	if strings.Contains(rr.Body.String(), "db error") {
		t.Errorf("response leaks internal details: %q", rr.Body.String())
	}
	if !strings.Contains(rr.Body.String(), "internal error") {
		t.Errorf("response = %q, want generic internal error", rr.Body.String())
	}
}

func TestHandlers_PutUserProfile_ProfileTooLong(t *testing.T) {
	h := &Handlers{UserRepo: &mockUserRepo{}, AuthToken: testAuthToken, UserHMACSecret: testUserHMACSecret}

	longProfile := strings.Repeat("a", 5001)
	body, _ := json.Marshal(map[string]string{"profile_text": longProfile})
	req := newJSONRequest(http.MethodPut, "/users/1/profile", body, newAuthHeadersWithUserSign(http.MethodPut, "/users/1/profile", 123456789, body))
	req = attachRouteUserID(req, "1")
	rr := httptest.NewRecorder()

	h.PutUserProfile(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("status = %d, want 400", rr.Code)
	}
}

func TestHandlers_PutUserProfile_EnqueuesUserEmbed(t *testing.T) {
	var enqueuedUserID int64
	queue := &mockUserEmbedQueue{
		enqueueFunc: func(ctx context.Context, userID int64) error {
			enqueuedUserID = userID
			return nil
		},
	}
	repo := &mockUserRepo{
		updateProfileFunc: func(ctx context.Context, userID int64, profileText string) error {
			return nil
		},
	}
	h := &Handlers{UserRepo: repo, UserEmbedQueue: queue, AuthToken: testAuthToken, UserHMACSecret: testUserHMACSecret}

	body := []byte(`{"profile_text":"hello"}`)
	req := newJSONRequest(http.MethodPut, "/users/42/profile", body, newAuthHeadersWithUserSign(http.MethodPut, "/users/42/profile", 123456789, body))
	req = attachRouteUserID(req, "42")
	rr := httptest.NewRecorder()

	h.PutUserProfile(rr, req)

	if rr.Code != http.StatusNoContent {
		t.Errorf("status = %d, want 204", rr.Code)
	}
	if enqueuedUserID != 42 {
		t.Errorf("enqueued user_id = %d, want 42", enqueuedUserID)
	}
}

func TestHandlers_PostUsers_Unauthorized(t *testing.T) {
	h := &Handlers{UserRepo: &mockUserRepo{}, AuthToken: testAuthToken, UserHMACSecret: testUserHMACSecret}

	body, _ := io.ReadAll(strings.NewReader(`{"telegram_id":123}`))
	req := newJSONRequest(http.MethodPost, "/users", body, nil)
	rr := httptest.NewRecorder()

	h.PostUsers(rr, req)

	if rr.Code != http.StatusUnauthorized {
		t.Errorf("status = %d, want 401", rr.Code)
	}
}

func TestHandlers_PostUsers_InvalidSignature(t *testing.T) {
	h := &Handlers{UserRepo: &mockUserRepo{}, AuthToken: testAuthToken, UserHMACSecret: testUserHMACSecret}
	body := []byte(`{"telegram_id":123456789}`)
	headers := newAuthHeadersWithUserSign(http.MethodPost, "/users", 123456789, body)
	headers["X-Request-Signature"] = strings.Repeat("0", 64)
	req := newJSONRequest(http.MethodPost, "/users", body, headers)
	rr := httptest.NewRecorder()

	h.PostUsers(rr, req)

	if rr.Code != http.StatusUnauthorized {
		t.Errorf("status = %d, want 401", rr.Code)
	}
}

func TestHandlers_PutUserProfile_OwnerMismatch(t *testing.T) {
	repo := &mockUserRepo{
		getByIDFunc: func(ctx context.Context, userID int64) (*domain.User, error) {
			return &domain.User{ID: userID, TelegramID: 777}, nil
		},
	}
	h := &Handlers{UserRepo: repo, AuthToken: testAuthToken, UserHMACSecret: testUserHMACSecret}

	body := []byte(`{"profile_text":"x"}`)
	req := newJSONRequest(
		http.MethodPut,
		"/users/1/profile",
		body,
		newAuthHeadersWithUserSign(http.MethodPut, "/users/1/profile", 123456789, body),
	)
	req = attachRouteUserID(req, "1")
	rr := httptest.NewRecorder()

	h.PutUserProfile(rr, req)

	if rr.Code != http.StatusForbidden {
		t.Errorf("status = %d, want 403", rr.Code)
	}
}

func TestHandlers_PutUserProfile_InvalidTelegramHeader(t *testing.T) {
	h := &Handlers{UserRepo: &mockUserRepo{}, AuthToken: testAuthToken, UserHMACSecret: testUserHMACSecret}

	headers := newAuthHeaders()
	headers["X-Telegram-ID"] = "bad"
	req := newJSONRequest(
		http.MethodPut,
		"/users/1/profile",
		[]byte(`{"profile_text":"x"}`),
		headers,
	)
	req = attachRouteUserID(req, "1")
	rr := httptest.NewRecorder()

	h.PutUserProfile(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("status = %d, want 400", rr.Code)
	}
}

func TestHandlers_PutUserProfile_GetByIDError_DoesNotLeak(t *testing.T) {
	repo := &mockUserRepo{
		getByIDFunc: func(ctx context.Context, userID int64) (*domain.User, error) {
			return nil, errors.New("postgres timeout")
		},
	}
	h := &Handlers{UserRepo: repo, AuthToken: testAuthToken, UserHMACSecret: testUserHMACSecret}

	body := []byte(`{"profile_text":"x"}`)
	req := newJSONRequest(
		http.MethodPut,
		"/users/1/profile",
		body,
		newAuthHeadersWithUserSign(http.MethodPut, "/users/1/profile", 123456789, body),
	)
	req = attachRouteUserID(req, "1")
	rr := httptest.NewRecorder()

	h.PutUserProfile(rr, req)

	if rr.Code != http.StatusInternalServerError {
		t.Errorf("status = %d, want 500", rr.Code)
	}
	if strings.Contains(rr.Body.String(), "postgres timeout") {
		t.Errorf("response leaks internal details: %q", rr.Body.String())
	}
	if !strings.Contains(rr.Body.String(), "internal error") {
		t.Errorf("response = %q, want generic internal error", rr.Body.String())
	}
}

func TestHandlers_PutUserProfile_ExpiredTimestamp(t *testing.T) {
	h := &Handlers{UserRepo: &mockUserRepo{}, AuthToken: testAuthToken, UserHMACSecret: testUserHMACSecret}
	body := []byte(`{"profile_text":"x"}`)
	nonce := "nonce1234567890abcd"
	oldTS := strconv.FormatInt(time.Now().Add(-maxRequestSkew-time.Second).Unix(), 10)
	req := newJSONRequest(
		http.MethodPut,
		"/users/1/profile",
		body,
		newAuthHeadersWithUserSignAndNonce(http.MethodPut, "/users/1/profile", 123456789, nonce, body),
	)
	req = attachRouteUserID(req, "1")
	req.Header.Set("X-Request-Timestamp", oldTS)
	req.Header.Set("X-Request-Signature", hex.EncodeToString(signUserRequest(testUserHMACSecret, http.MethodPut, "/users/1/profile", 123456789, oldTS, nonce, body)))
	rr := httptest.NewRecorder()

	h.PutUserProfile(rr, req)

	if rr.Code != http.StatusUnauthorized {
		t.Errorf("status = %d, want 401", rr.Code)
	}
}

func TestHandlers_PostUsers_MissingNonce(t *testing.T) {
	h := &Handlers{UserRepo: &mockUserRepo{}, AuthToken: testAuthToken, UserHMACSecret: testUserHMACSecret}
	body := []byte(`{"telegram_id":123456789}`)
	headers := newAuthHeadersWithUserSign(http.MethodPost, "/users", 123456789, body)
	delete(headers, "X-Request-Nonce")
	req := newJSONRequest(http.MethodPost, "/users", body, headers)
	rr := httptest.NewRecorder()

	h.PostUsers(rr, req)

	if rr.Code != http.StatusUnauthorized {
		t.Errorf("status = %d, want 401", rr.Code)
	}
}

func TestHandlers_PostUsers_ReplayedNonce(t *testing.T) {
	h := &Handlers{
		UserRepo:       &mockUserRepo{},
		AuthToken:      testAuthToken,
		UserHMACSecret: testUserHMACSecret,
		NonceStore:     &mockNonceStore{},
	}
	body := []byte(`{"telegram_id":123456789}`)
	headers := newAuthHeadersWithUserSignAndNonce(http.MethodPost, "/users", 123456789, "nonce1234567890abcd", body)

	req1 := newJSONRequest(http.MethodPost, "/users", body, headers)
	rr1 := httptest.NewRecorder()
	h.PostUsers(rr1, req1)
	if rr1.Code != http.StatusOK {
		t.Fatalf("first status = %d, want 200", rr1.Code)
	}

	req2 := newJSONRequest(http.MethodPost, "/users", body, headers)
	rr2 := httptest.NewRecorder()
	h.PostUsers(rr2, req2)
	if rr2.Code != http.StatusUnauthorized {
		t.Errorf("second status = %d, want 401", rr2.Code)
	}
}

func TestHandlers_PostUsers_RateLimitedByIP(t *testing.T) {
	h := &Handlers{
		UserRepo:       &mockUserRepo{},
		AuthToken:      testAuthToken,
		UserHMACSecret: testUserHMACSecret,
		RateLimiter: &mockRateLimiter{
			allowFunc: func(ctx context.Context, key string, limit int, window time.Duration) (bool, error) {
				if strings.HasPrefix(key, "ip:") {
					return false, nil
				}
				return true, nil
			},
		},
	}
	body := []byte(`{"telegram_id":123456789}`)
	req := newJSONRequest(http.MethodPost, "/users", body, newAuthHeadersWithUserSign(http.MethodPost, "/users", 123456789, body))
	req.RemoteAddr = "203.0.113.10:34567"
	rr := httptest.NewRecorder()

	h.PostUsers(rr, req)

	if rr.Code != http.StatusTooManyRequests {
		t.Errorf("status = %d, want 429", rr.Code)
	}
}

func TestHandlers_PostUsers_RateLimitedByTelegramID(t *testing.T) {
	h := &Handlers{
		UserRepo:       &mockUserRepo{},
		AuthToken:      testAuthToken,
		UserHMACSecret: testUserHMACSecret,
		RateLimiter: &mockRateLimiter{
			allowFunc: func(ctx context.Context, key string, limit int, window time.Duration) (bool, error) {
				if strings.HasPrefix(key, "tg:") {
					return false, nil
				}
				return true, nil
			},
		},
	}
	body := []byte(`{"telegram_id":123456789}`)
	req := newJSONRequest(http.MethodPost, "/users", body, newAuthHeadersWithUserSign(http.MethodPost, "/users", 123456789, body))
	req.RemoteAddr = "203.0.113.10:34567"
	rr := httptest.NewRecorder()

	h.PostUsers(rr, req)

	if rr.Code != http.StatusTooManyRequests {
		t.Errorf("status = %d, want 429", rr.Code)
	}
}

func mustCIDRForTest(t *testing.T, raw string) *net.IPNet {
	t.Helper()
	_, n, err := net.ParseCIDR(raw)
	if err != nil {
		t.Fatalf("parse cidr %q: %v", raw, err)
	}
	return n
}

func TestClientIP_UntrustedRemote_IgnoresForwardedHeaders(t *testing.T) {
	h := &Handlers{}
	req := httptest.NewRequest(http.MethodGet, "/users", nil)
	req.RemoteAddr = "203.0.113.10:34567"
	req.Header.Set("X-Forwarded-For", "198.51.100.200")
	req.Header.Set("X-Real-IP", "198.51.100.201")

	ip := h.clientIP(req)
	if ip != "203.0.113.10" {
		t.Fatalf("client ip = %q, want remote addr ip", ip)
	}
}

func TestClientIP_TrustedProxy_UsesForwardedFor(t *testing.T) {
	h := &Handlers{
		TrustedProxyCIDRs: []*net.IPNet{
			mustCIDRForTest(t, "203.0.113.0/24"),
		},
	}
	req := httptest.NewRequest(http.MethodGet, "/users", nil)
	req.RemoteAddr = "203.0.113.10:34567"
	req.Header.Set("X-Forwarded-For", "198.51.100.200, 10.0.0.1")

	ip := h.clientIP(req)
	if ip != "198.51.100.200" {
		t.Fatalf("client ip = %q, want first forwarded ip", ip)
	}
}

func TestHandlers_PostUsers_IPRateLimit_IgnoresSpoofedForwardedFor(t *testing.T) {
	var gotKey string
	h := &Handlers{
		UserRepo:       &mockUserRepo{},
		AuthToken:      testAuthToken,
		UserHMACSecret: testUserHMACSecret,
		RateLimiter: &mockRateLimiter{
			allowFunc: func(ctx context.Context, key string, limit int, window time.Duration) (bool, error) {
				gotKey = key
				return false, nil
			},
		},
	}

	body := []byte(`{"telegram_id":123456789}`)
	req := newJSONRequest(http.MethodPost, "/users", body, newAuthHeadersWithUserSign(http.MethodPost, "/users", 123456789, body))
	req.RemoteAddr = "203.0.113.10:34567"
	req.Header.Set("X-Forwarded-For", "198.51.100.200")
	rr := httptest.NewRecorder()

	h.PostUsers(rr, req)

	if rr.Code != http.StatusTooManyRequests {
		t.Fatalf("status = %d, want 429", rr.Code)
	}
	if gotKey != "ip:203.0.113.10" {
		t.Fatalf("rate-limit key = %q, want remote addr key", gotKey)
	}
}

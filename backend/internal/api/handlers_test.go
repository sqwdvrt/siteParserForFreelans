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
	saveFunc              func(ctx context.Context, telegramID int64) (int64, error)
	getByIDFunc           func(ctx context.Context, userID int64) (*domain.User, error)
	updateProfileFunc     func(ctx context.Context, userID int64, profileText string) error
	updateNotifyHourFunc  func(ctx context.Context, userID int64, hour int) error
	getPreferencesFunc    func(ctx context.Context, userID int64) (*domain.UserPreferences, error)
	upsertPreferencesFunc func(ctx context.Context, userID int64, prefs domain.UserPreferences) error
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

func (m *mockUserRepo) UpdateProfileScoped(ctx context.Context, userID int64, profileText string) error {
	return m.UpdateProfile(ctx, userID, profileText)
}

func (m *mockUserRepo) UpdateNotifyHourScoped(ctx context.Context, userID int64, hour int) error {
	if m.updateNotifyHourFunc != nil {
		return m.updateNotifyHourFunc(ctx, userID, hour)
	}
	return nil
}

func (m *mockUserRepo) GetPreferencesScoped(ctx context.Context, userID int64) (*domain.UserPreferences, error) {
	if m.getPreferencesFunc != nil {
		return m.getPreferencesFunc(ctx, userID)
	}
	return &domain.UserPreferences{}, nil
}

func (m *mockUserRepo) UpsertPreferencesScoped(ctx context.Context, userID int64, prefs domain.UserPreferences) error {
	if m.upsertPreferencesFunc != nil {
		return m.upsertPreferencesFunc(ctx, userID, prefs)
	}
	return nil
}
func (m *mockUserRepo) GetProUsersWithNotifyHour(ctx context.Context, hour int) ([]int64, error) {
	return nil, nil
}

const testAuthToken = "test-api-token"
const testTelegramIDHeader = "123456789"
const testUserHMACSecret = "test-hmac-secret"
const validProfileText = "Python backend разработчик, 4 года опыта. Делаю API, PostgreSQL, Redis, Docker, интеграции и автоматизацию."

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
	var enqueuedUserID int64
	queue := &mockUserEmbedQueue{
		enqueueFunc: func(ctx context.Context, userID int64) error {
			enqueuedUserID = userID
			return nil
		},
	}
	repo := &mockUserRepo{
		updateProfileFunc: func(ctx context.Context, userID int64, profileText string) error {
			gotUserID = userID
			gotProfile = profileText
			return nil
		},
	}
	h := &Handlers{UserRepo: repo, UserEmbedQueue: queue, AuthToken: testAuthToken, UserHMACSecret: testUserHMACSecret}

	body := []byte(`{"profile_text":"` + validProfileText + `"}`)
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
	if gotProfile != validProfileText {
		t.Errorf("profile_text = %q, want %q", gotProfile, validProfileText)
	}
	if enqueuedUserID != 1 {
		t.Errorf("enqueued user_id = %d, want 1", enqueuedUserID)
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
	queue := &mockUserEmbedQueue{
		enqueueFunc: func(ctx context.Context, userID int64) error {
			return nil
		},
	}
	repo := &mockUserRepo{
		updateProfileFunc: func(ctx context.Context, userID int64, profileText string) error {
			return errors.New("db error")
		},
	}
	h := &Handlers{UserRepo: repo, UserEmbedQueue: queue, AuthToken: testAuthToken, UserHMACSecret: testUserHMACSecret}

	body := []byte(`{"profile_text":"` + validProfileText + `"}`)
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

func TestHandlers_PutUserProfile_ProfileTooShort(t *testing.T) {
	h := &Handlers{UserRepo: &mockUserRepo{}, AuthToken: testAuthToken, UserHMACSecret: testUserHMACSecret}

	body := []byte(`{"profile_text":"short profile"}`)
	req := newJSONRequest(http.MethodPut, "/users/1/profile", body, newAuthHeadersWithUserSign(http.MethodPut, "/users/1/profile", 123456789, body))
	req = attachRouteUserID(req, "1")
	rr := httptest.NewRecorder()

	h.PutUserProfile(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400", rr.Code)
	}
	if !strings.Contains(rr.Body.String(), "profile_text too short") {
		t.Fatalf("response = %q, want too short hint", rr.Body.String())
	}
}

func TestHandlers_PutUserProfile_ProfilePlaceholderRejected(t *testing.T) {
	h := &Handlers{UserRepo: &mockUserRepo{}, AuthToken: testAuthToken, UserHMACSecret: testUserHMACSecret}

	body := []byte(`{"profile_text":"staging-user-embed-gate-d1ef4b90 with smoke placeholder words inside profile text"}`)
	req := newJSONRequest(http.MethodPut, "/users/1/profile", body, newAuthHeadersWithUserSign(http.MethodPut, "/users/1/profile", 123456789, body))
	req = attachRouteUserID(req, "1")
	rr := httptest.NewRecorder()

	h.PutUserProfile(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400", rr.Code)
	}
	if !strings.Contains(rr.Body.String(), "placeholder") {
		t.Fatalf("response = %q, want placeholder hint", rr.Body.String())
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

	body := []byte(`{"profile_text":"` + validProfileText + `"}`)
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

func TestHandlers_PutUserProfile_QueueUnavailable(t *testing.T) {
	var updateCalled bool
	repo := &mockUserRepo{
		updateProfileFunc: func(ctx context.Context, userID int64, profileText string) error {
			updateCalled = true
			return nil
		},
	}
	h := &Handlers{UserRepo: repo, AuthToken: testAuthToken, UserHMACSecret: testUserHMACSecret}

	body := []byte(`{"profile_text":"` + validProfileText + `"}`)
	req := newJSONRequest(http.MethodPut, "/users/42/profile", body, newAuthHeadersWithUserSign(http.MethodPut, "/users/42/profile", 123456789, body))
	req = attachRouteUserID(req, "42")
	rr := httptest.NewRecorder()

	h.PutUserProfile(rr, req)

	if rr.Code != http.StatusServiceUnavailable {
		t.Errorf("status = %d, want 503", rr.Code)
	}
	if updateCalled {
		t.Error("update profile must not be called when queue is unavailable")
	}
}

func TestHandlers_PutUserProfile_EnqueueError_RollsBackAndReturns503(t *testing.T) {
	profiles := make([]string, 0, 2)
	oldProfileText := validProfileText
	queue := &mockUserEmbedQueue{
		enqueueFunc: func(ctx context.Context, userID int64) error {
			return errors.New("redis down")
		},
	}
	repo := &mockUserRepo{
		updateProfileFunc: func(ctx context.Context, userID int64, profileText string) error {
			profiles = append(profiles, profileText)
			return nil
		},
		getByIDFunc: func(ctx context.Context, userID int64) (*domain.User, error) {
			return &domain.User{ID: userID, TelegramID: 123456789, ProfileText: &oldProfileText}, nil
		},
	}
	h := &Handlers{UserRepo: repo, UserEmbedQueue: queue, AuthToken: testAuthToken, UserHMACSecret: testUserHMACSecret}

	newProfile := "Go backend разработчик, 5 лет опыта. Делаю REST API, очереди, Redis, PostgreSQL, Docker и интеграции."
	body := []byte(`{"profile_text":"` + newProfile + `"}`)
	req := newJSONRequest(http.MethodPut, "/users/1/profile", body, newAuthHeadersWithUserSign(http.MethodPut, "/users/1/profile", 123456789, body))
	req = attachRouteUserID(req, "1")
	rr := httptest.NewRecorder()

	h.PutUserProfile(rr, req)

	if rr.Code != http.StatusServiceUnavailable {
		t.Errorf("status = %d, want 503", rr.Code)
	}
	if len(profiles) != 2 {
		t.Fatalf("update profile calls = %d, want 2 (update + rollback)", len(profiles))
	}
	if profiles[0] != newProfile {
		t.Errorf("first update profile = %q, want %q", profiles[0], newProfile)
	}
	if profiles[1] != validProfileText {
		t.Errorf("rollback profile = %q, want %q", profiles[1], validProfileText)
	}
}

func TestHandlers_PutUserNotifyHour_Success(t *testing.T) {
	var gotUserID int64
	var gotHour int
	repo := &mockUserRepo{
		getByIDFunc: func(ctx context.Context, userID int64) (*domain.User, error) {
			return &domain.User{ID: userID, TelegramID: 123456789, IsPro: true}, nil
		},
		updateNotifyHourFunc: func(ctx context.Context, userID int64, hour int) error {
			gotUserID = userID
			gotHour = hour
			return nil
		},
	}
	h := &Handlers{UserRepo: repo, AuthToken: testAuthToken, UserHMACSecret: testUserHMACSecret}

	body := []byte(`{"hour":9}`)
	req := newJSONRequest(
		http.MethodPut,
		"/users/1/notify-hour",
		body,
		newAuthHeadersWithUserSign(http.MethodPut, "/users/1/notify-hour", 123456789, body),
	)
	req = attachRouteUserID(req, "1")
	rr := httptest.NewRecorder()

	h.PutUserNotifyHour(rr, req)

	if rr.Code != http.StatusNoContent {
		t.Errorf("status = %d, want 204", rr.Code)
	}
	if gotUserID != 1 {
		t.Errorf("user_id = %d, want 1", gotUserID)
	}
	if gotHour != 9 {
		t.Errorf("hour = %d, want 9", gotHour)
	}
}

func TestHandlers_PutUserNotifyHour_ForbiddenForNonPro(t *testing.T) {
	h := &Handlers{UserRepo: &mockUserRepo{}, AuthToken: testAuthToken, UserHMACSecret: testUserHMACSecret}

	body := []byte(`{"hour":8}`)
	req := newJSONRequest(
		http.MethodPut,
		"/users/1/notify-hour",
		body,
		newAuthHeadersWithUserSign(http.MethodPut, "/users/1/notify-hour", 123456789, body),
	)
	req = attachRouteUserID(req, "1")
	rr := httptest.NewRecorder()

	h.PutUserNotifyHour(rr, req)

	if rr.Code != http.StatusForbidden {
		t.Errorf("status = %d, want 403", rr.Code)
	}
}

func TestHandlers_PutUserNotifyHour_InvalidHour(t *testing.T) {
	h := &Handlers{UserRepo: &mockUserRepo{}, AuthToken: testAuthToken, UserHMACSecret: testUserHMACSecret}

	body := []byte(`{"hour":24}`)
	req := newJSONRequest(
		http.MethodPut,
		"/users/1/notify-hour",
		body,
		newAuthHeadersWithUserSign(http.MethodPut, "/users/1/notify-hour", 123456789, body),
	)
	req = attachRouteUserID(req, "1")
	rr := httptest.NewRecorder()

	h.PutUserNotifyHour(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("status = %d, want 400", rr.Code)
	}
}

func TestHandlers_PutUserNotifyHour_UpdateError(t *testing.T) {
	repo := &mockUserRepo{
		getByIDFunc: func(ctx context.Context, userID int64) (*domain.User, error) {
			return &domain.User{ID: userID, TelegramID: 123456789, IsPro: true}, nil
		},
		updateNotifyHourFunc: func(ctx context.Context, userID int64, hour int) error {
			return errors.New("db error")
		},
	}
	h := &Handlers{UserRepo: repo, AuthToken: testAuthToken, UserHMACSecret: testUserHMACSecret}

	body := []byte(`{"hour":10}`)
	req := newJSONRequest(
		http.MethodPut,
		"/users/1/notify-hour",
		body,
		newAuthHeadersWithUserSign(http.MethodPut, "/users/1/notify-hour", 123456789, body),
	)
	req = attachRouteUserID(req, "1")
	rr := httptest.NewRecorder()

	h.PutUserNotifyHour(rr, req)

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

func TestHandlers_GetUserPreferences_Success(t *testing.T) {
	repo := &mockUserRepo{
		getByIDFunc: func(ctx context.Context, userID int64) (*domain.User, error) {
			return &domain.User{ID: userID, TelegramID: 123456789, IsPro: true}, nil
		},
		getPreferencesFunc: func(ctx context.Context, userID int64) (*domain.UserPreferences, error) {
			minBudget := 1000.0
			maxBudget := 5000.0
			return &domain.UserPreferences{
				UserID:           userID,
				IncludeKeywords:  []string{"Python", "python", "Go"},
				ExcludeKeywords:  []string{"PHP"},
				MinBudget:        &minBudget,
				MaxBudget:        &maxBudget,
				PreferredSources: []string{"KWORK", "freelancehunt"},
			}, nil
		},
	}
	h := &Handlers{UserRepo: repo, AuthToken: testAuthToken, UserHMACSecret: testUserHMACSecret}

	req := newJSONRequest(
		http.MethodGet,
		"/users/7/preferences",
		nil,
		newAuthHeadersWithUserSign(http.MethodGet, "/users/7/preferences", 123456789, nil),
	)
	req = attachRouteUserID(req, "7")
	rr := httptest.NewRecorder()

	h.GetUserPreferences(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("status = %d, want 200", rr.Code)
	}
	var resp UserPreferencesResponse
	if err := json.NewDecoder(rr.Body).Decode(&resp); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if len(resp.IncludeKeywords) != 2 || resp.IncludeKeywords[0] != "python" || resp.IncludeKeywords[1] != "go" {
		t.Fatalf("include_keywords = %#v, want [python go]", resp.IncludeKeywords)
	}
	if len(resp.PreferredSources) != 2 || resp.PreferredSources[0] != "kwork" || resp.PreferredSources[1] != "freelancehunt" {
		t.Fatalf("preferred_sources = %#v, want normalized sources", resp.PreferredSources)
	}
	if resp.MinBudget == nil || *resp.MinBudget != 1000 {
		t.Fatalf("min_budget = %#v, want 1000", resp.MinBudget)
	}
	if resp.MaxBudget == nil || *resp.MaxBudget != 5000 {
		t.Fatalf("max_budget = %#v, want 5000", resp.MaxBudget)
	}
	if !resp.IsPro {
		t.Fatal("is_pro = false, want true")
	}
}

func TestHandlers_PutUserPreferences_Success(t *testing.T) {
	var captured domain.UserPreferences
	repo := &mockUserRepo{
		getByIDFunc: func(ctx context.Context, userID int64) (*domain.User, error) {
			return &domain.User{ID: userID, TelegramID: 123456789, IsPro: true}, nil
		},
		upsertPreferencesFunc: func(ctx context.Context, userID int64, prefs domain.UserPreferences) error {
			captured = prefs
			return nil
		},
	}
	h := &Handlers{UserRepo: repo, AuthToken: testAuthToken, UserHMACSecret: testUserHMACSecret}

	body := []byte(`{
		"include_keywords":["Python"," python ","Go"],
		"exclude_keywords":["PHP","php"],
		"min_budget":1500,
		"max_budget":4000,
		"preferred_sources":["KWORK","FreelanceHunt"]
	}`)
	req := newJSONRequest(
		http.MethodPut,
		"/users/8/preferences",
		body,
		newAuthHeadersWithUserSign(http.MethodPut, "/users/8/preferences", 123456789, body),
	)
	req = attachRouteUserID(req, "8")
	rr := httptest.NewRecorder()

	h.PutUserPreferences(rr, req)

	if rr.Code != http.StatusNoContent {
		t.Fatalf("status = %d, want 204", rr.Code)
	}
	if captured.UserID != 8 {
		t.Fatalf("captured user_id = %d, want 8", captured.UserID)
	}
	if len(captured.IncludeKeywords) != 2 || captured.IncludeKeywords[0] != "python" || captured.IncludeKeywords[1] != "go" {
		t.Fatalf("include_keywords = %#v, want normalized unique list", captured.IncludeKeywords)
	}
	if len(captured.ExcludeKeywords) != 1 || captured.ExcludeKeywords[0] != "php" {
		t.Fatalf("exclude_keywords = %#v, want [php]", captured.ExcludeKeywords)
	}
	if captured.MinBudget == nil || *captured.MinBudget != 1500 {
		t.Fatalf("min_budget = %#v, want 1500", captured.MinBudget)
	}
	if captured.MaxBudget == nil || *captured.MaxBudget != 4000 {
		t.Fatalf("max_budget = %#v, want 4000", captured.MaxBudget)
	}
	if len(captured.PreferredSources) != 2 || captured.PreferredSources[0] != "kwork" || captured.PreferredSources[1] != "freelancehunt" {
		t.Fatalf("preferred_sources = %#v, want normalized unique list", captured.PreferredSources)
	}
}

func TestHandlers_PutUserPreferences_InvalidBudgetRange(t *testing.T) {
	h := &Handlers{
		UserRepo:       &mockUserRepo{},
		AuthToken:      testAuthToken,
		UserHMACSecret: testUserHMACSecret,
	}

	body := []byte(`{"min_budget":5000,"max_budget":1000}`)
	req := newJSONRequest(
		http.MethodPut,
		"/users/8/preferences",
		body,
		newAuthHeadersWithUserSign(http.MethodPut, "/users/8/preferences", 123456789, body),
	)
	req = attachRouteUserID(req, "8")
	rr := httptest.NewRecorder()

	h.PutUserPreferences(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400", rr.Code)
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

	body := []byte(`{"profile_text":"` + validProfileText + `"}`)
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
		[]byte(`{"profile_text":"` + validProfileText + `"}`),
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

	body := []byte(`{"profile_text":"` + validProfileText + `"}`)
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
	body := []byte(`{"profile_text":"` + validProfileText + `"}`)
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

func TestClientIP_TrustedProxy_UsesClosestUntrustedFromXForwardedFor(t *testing.T) {
	h := &Handlers{
		TrustedProxyCIDRs: []*net.IPNet{
			mustCIDRForTest(t, "203.0.113.0/24"),
			mustCIDRForTest(t, "10.0.0.0/8"),
		},
	}
	req := httptest.NewRequest(http.MethodGet, "/users", nil)
	req.RemoteAddr = "203.0.113.10:34567"
	req.Header.Set("X-Forwarded-For", "198.51.100.200, 10.0.0.1")

	ip := h.clientIP(req)
	if ip != "198.51.100.200" {
		t.Fatalf("client ip = %q, want closest untrusted forwarded ip", ip)
	}
}

func TestClientIP_TrustedProxy_IgnoresSpoofedLeftMostXForwardedFor(t *testing.T) {
	h := &Handlers{
		TrustedProxyCIDRs: []*net.IPNet{
			mustCIDRForTest(t, "203.0.113.0/24"),
		},
	}
	req := httptest.NewRequest(http.MethodGet, "/users", nil)
	req.RemoteAddr = "203.0.113.10:34567"
	// Simulate a trusted proxy appending the real client after a spoofed left-most entry.
	req.Header.Set("X-Forwarded-For", "198.51.100.250, 198.51.100.200")

	ip := h.clientIP(req)
	if ip != "198.51.100.200" {
		t.Fatalf("client ip = %q, want right-most untrusted forwarded ip", ip)
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

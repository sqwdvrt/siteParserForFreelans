package api

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"

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

type mockUserRepo struct {
	saveFunc         func(ctx context.Context, telegramID int64) (int64, error)
	updateProfileFunc func(ctx context.Context, userID int64, profileText string) error
}

func (m *mockUserRepo) Save(ctx context.Context, telegramID int64) (int64, error) {
	if m.saveFunc != nil {
		return m.saveFunc(ctx, telegramID)
	}
	return 42, nil
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

func TestHandlers_PostUsers_Success(t *testing.T) {
	repo := &mockUserRepo{
		saveFunc: func(ctx context.Context, telegramID int64) (int64, error) {
			if telegramID != 123456789 {
				return 0, errors.New("unexpected telegram_id")
			}
			return 1, nil
		},
	}
	h := &Handlers{UserRepo: repo}

	body, _ := json.Marshal(PostUsersRequest{TelegramID: 123456789})
	req := httptest.NewRequest(http.MethodPost, "/users", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
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
	h := &Handlers{UserRepo: &mockUserRepo{}}

	req := httptest.NewRequest(http.MethodPost, "/users", bytes.NewReader([]byte("not json")))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()

	h.PostUsers(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("status = %d, want 400", rr.Code)
	}
}

func TestHandlers_PostUsers_InvalidTelegramID(t *testing.T) {
	h := &Handlers{UserRepo: &mockUserRepo{}}

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
			req := httptest.NewRequest(http.MethodPost, "/users", bytes.NewReader([]byte(tt.body)))
			req.Header.Set("Content-Type", "application/json")
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
	h := &Handlers{UserRepo: repo}

	body, _ := json.Marshal(PostUsersRequest{TelegramID: 123})
	req := httptest.NewRequest(http.MethodPost, "/users", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()

	h.PostUsers(rr, req)

	if rr.Code != http.StatusInternalServerError {
		t.Errorf("status = %d, want 500", rr.Code)
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
	h := &Handlers{UserRepo: repo}

	body := []byte(`{"profile_text":"I am a developer"}`)
	req := httptest.NewRequest(http.MethodPut, "/users/1/profile", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	rctx := chi.NewRouteContext()
	rctx.URLParams.Add("id", "1")
	req = req.WithContext(context.WithValue(req.Context(), chi.RouteCtxKey, rctx))
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
	h := &Handlers{UserRepo: &mockUserRepo{}}

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
			req := httptest.NewRequest(http.MethodPut, "/users/"+tt.id+"/profile", bytes.NewReader(body))
			req.Header.Set("Content-Type", "application/json")
			rctx := chi.NewRouteContext()
			rctx.URLParams.Add("id", tt.id)
			req = req.WithContext(context.WithValue(req.Context(), chi.RouteCtxKey, rctx))
			rr := httptest.NewRecorder()

			h.PutUserProfile(rr, req)

			if rr.Code != http.StatusBadRequest {
				t.Errorf("status = %d, want 400", rr.Code)
			}
		})
	}
}

func TestHandlers_PutUserProfile_InvalidJSON(t *testing.T) {
	h := &Handlers{UserRepo: &mockUserRepo{}}

	req := httptest.NewRequest(http.MethodPut, "/users/1/profile", bytes.NewReader([]byte("not json")))
	req.Header.Set("Content-Type", "application/json")
	rctx := chi.NewRouteContext()
	rctx.URLParams.Add("id", "1")
	req = req.WithContext(context.WithValue(req.Context(), chi.RouteCtxKey, rctx))
	rr := httptest.NewRecorder()

	h.PutUserProfile(rr, req)

	if rr.Code != http.StatusBadRequest {
		t.Errorf("status = %d, want 400", rr.Code)
	}
}

func TestHandlers_PutUserProfile_RepoError(t *testing.T) {
	repo := &mockUserRepo{
		updateProfileFunc: func(ctx context.Context, userID int64, profileText string) error {
			return errors.New("db error")
		},
	}
	h := &Handlers{UserRepo: repo}

	body := []byte(`{"profile_text":"x"}`)
	req := httptest.NewRequest(http.MethodPut, "/users/1/profile", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	rctx := chi.NewRouteContext()
	rctx.URLParams.Add("id", "1")
	req = req.WithContext(context.WithValue(req.Context(), chi.RouteCtxKey, rctx))
	rr := httptest.NewRecorder()

	h.PutUserProfile(rr, req)

	if rr.Code != http.StatusInternalServerError {
		t.Errorf("status = %d, want 500", rr.Code)
	}
}

func TestHandlers_PutUserProfile_ProfileTooLong(t *testing.T) {
	h := &Handlers{UserRepo: &mockUserRepo{}}

	longProfile := string(make([]byte, 5001))
	body, _ := json.Marshal(map[string]string{"profile_text": longProfile})
	req := httptest.NewRequest(http.MethodPut, "/users/1/profile", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	rctx := chi.NewRouteContext()
	rctx.URLParams.Add("id", "1")
	req = req.WithContext(context.WithValue(req.Context(), chi.RouteCtxKey, rctx))
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
	h := &Handlers{UserRepo: repo, UserEmbedQueue: queue}

	body := []byte(`{"profile_text":"hello"}`)
	req := httptest.NewRequest(http.MethodPut, "/users/42/profile", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	rctx := chi.NewRouteContext()
	rctx.URLParams.Add("id", "42")
	req = req.WithContext(context.WithValue(req.Context(), chi.RouteCtxKey, rctx))
	rr := httptest.NewRecorder()

	h.PutUserProfile(rr, req)

	if rr.Code != http.StatusNoContent {
		t.Errorf("status = %d, want 204", rr.Code)
	}
	if enqueuedUserID != 42 {
		t.Errorf("enqueued user_id = %d, want 42", enqueuedUserID)
	}
}

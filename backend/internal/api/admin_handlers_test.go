package api

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

type mockAdminRepo struct {
	getStatsFunc          func(ctx context.Context) (*port.AdminStats, error)
	listUsersFunc         func(ctx context.Context, limit, offset int) ([]port.AdminUser, int64, error)
	getUserFunc           func(ctx context.Context, userID int64) (*port.AdminUser, error)
	getUserByTelegramFunc func(ctx context.Context, telegramID int64) (*port.AdminUser, error)
	deleteUserFn          func(ctx context.Context, userID int64) (bool, error)
	listJobsFunc          func(ctx context.Context, source string, limit, offset int) ([]port.AdminJob, int64, error)
	setProFunc            func(ctx context.Context, userID int64, enabled bool, days int) (bool, *time.Time, error)
	setProByTelegramFunc  func(ctx context.Context, telegramID int64, enabled bool, days int) (bool, *time.Time, error)
}

type mockAdminDebugClient struct {
	getMatchDebugFunc func(ctx context.Context, userID int64, jobURL string) (map[string]any, error)
}

func (m *mockAdminDebugClient) GetMatchDebug(ctx context.Context, userID int64, jobURL string) (map[string]any, error) {
	if m.getMatchDebugFunc != nil {
		return m.getMatchDebugFunc(ctx, userID, jobURL)
	}
	return map[string]any{}, nil
}

func (m *mockAdminRepo) GetStats(ctx context.Context) (*port.AdminStats, error) {
	if m.getStatsFunc != nil {
		return m.getStatsFunc(ctx)
	}
	return &port.AdminStats{}, nil
}

func (m *mockAdminRepo) ListUsers(ctx context.Context, limit, offset int) ([]port.AdminUser, int64, error) {
	if m.listUsersFunc != nil {
		return m.listUsersFunc(ctx, limit, offset)
	}
	return nil, 0, nil
}

func (m *mockAdminRepo) GetUser(ctx context.Context, userID int64) (*port.AdminUser, error) {
	if m.getUserFunc != nil {
		return m.getUserFunc(ctx, userID)
	}
	return nil, nil
}

func (m *mockAdminRepo) GetUserByTelegramID(ctx context.Context, telegramID int64) (*port.AdminUser, error) {
	if m.getUserByTelegramFunc != nil {
		return m.getUserByTelegramFunc(ctx, telegramID)
	}
	return nil, nil
}

func (m *mockAdminRepo) DeleteUser(ctx context.Context, userID int64) (bool, error) {
	if m.deleteUserFn != nil {
		return m.deleteUserFn(ctx, userID)
	}
	return false, nil
}

func (m *mockAdminRepo) ListJobs(ctx context.Context, source string, limit, offset int) ([]port.AdminJob, int64, error) {
	if m.listJobsFunc != nil {
		return m.listJobsFunc(ctx, source, limit, offset)
	}
	return nil, 0, nil
}

func (m *mockAdminRepo) SetUserPro(ctx context.Context, userID int64, enabled bool, days int) (bool, *time.Time, error) {
	if m.setProFunc != nil {
		return m.setProFunc(ctx, userID, enabled, days)
	}
	return false, nil, nil
}

func (m *mockAdminRepo) SetUserProByTelegramID(ctx context.Context, telegramID int64, enabled bool, days int) (bool, *time.Time, error) {
	if m.setProByTelegramFunc != nil {
		return m.setProByTelegramFunc(ctx, telegramID, enabled, days)
	}
	return false, nil, nil
}

func newAdminHandlers(repo port.AdminRepository) *AdminHandlers {
	return &AdminHandlers{
		AdminRepo:  repo,
		AdminToken: "super-secret-admin-token",
	}
}

func newAdminRequest(method, target string) *http.Request {
	req := httptest.NewRequest(method, target, nil)
	req.Header.Set("Authorization", "Bearer super-secret-admin-token")
	return req
}

func withURLParam(req *http.Request, key, value string) *http.Request {
	rctx := chi.NewRouteContext()
	rctx.URLParams.Add(key, value)
	return req.WithContext(context.WithValue(req.Context(), chi.RouteCtxKey, rctx))
}

func TestAdminHandlersAuthorizeAdmin(t *testing.T) {
	t.Run("missing admin token", func(t *testing.T) {
		h := &AdminHandlers{}
		rr := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodGet, "/admin/stats", nil)
		if h.authorizeAdmin(rr, req) {
			t.Fatal("authorizeAdmin=true want false")
		}
		if rr.Code != http.StatusInternalServerError {
			t.Fatalf("status=%d", rr.Code)
		}
	})

	t.Run("missing bearer token", func(t *testing.T) {
		h := newAdminHandlers(&mockAdminRepo{})
		rr := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodGet, "/admin/stats", nil)
		if h.authorizeAdmin(rr, req) {
			t.Fatal("authorizeAdmin=true want false")
		}
		if rr.Code != http.StatusUnauthorized {
			t.Fatalf("status=%d", rr.Code)
		}
	})

	t.Run("wrong token", func(t *testing.T) {
		h := newAdminHandlers(&mockAdminRepo{})
		rr := httptest.NewRecorder()
		req := httptest.NewRequest(http.MethodGet, "/admin/stats", nil)
		req.Header.Set("Authorization", "Bearer wrong")
		if h.authorizeAdmin(rr, req) {
			t.Fatal("authorizeAdmin=true want false")
		}
		if rr.Code != http.StatusUnauthorized {
			t.Fatalf("status=%d", rr.Code)
		}
	})
}

func TestAdminHandlersGetStats(t *testing.T) {
	h := newAdminHandlers(&mockAdminRepo{
		getStatsFunc: func(context.Context) (*port.AdminStats, error) {
			return &port.AdminStats{TotalUsers: 7}, nil
		},
	})
	rr := httptest.NewRecorder()
	h.GetStats(rr, newAdminRequest(http.MethodGet, "/admin/stats"))
	if rr.Code != http.StatusOK {
		t.Fatalf("status=%d", rr.Code)
	}
	var body port.AdminStats
	if err := json.NewDecoder(rr.Body).Decode(&body); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if body.TotalUsers != 7 {
		t.Fatalf("TotalUsers=%d", body.TotalUsers)
	}

	h = newAdminHandlers(&mockAdminRepo{
		getStatsFunc: func(context.Context) (*port.AdminStats, error) { return nil, errors.New("repo failed") },
	})
	rr = httptest.NewRecorder()
	h.GetStats(rr, newAdminRequest(http.MethodGet, "/admin/stats"))
	if rr.Code != http.StatusInternalServerError {
		t.Fatalf("status=%d", rr.Code)
	}
}

func TestAdminHandlersListUsers(t *testing.T) {
	var gotLimit, gotOffset int
	h := newAdminHandlers(&mockAdminRepo{
		listUsersFunc: func(ctx context.Context, limit, offset int) ([]port.AdminUser, int64, error) {
			gotLimit, gotOffset = limit, offset
			return []port.AdminUser{{ID: 1}}, 1, nil
		},
	})
	rr := httptest.NewRecorder()
	h.ListUsers(rr, newAdminRequest(http.MethodGet, "/admin/users?page=-5&limit=999"))
	if rr.Code != http.StatusOK {
		t.Fatalf("status=%d", rr.Code)
	}
	if gotLimit != adminMaxLimit || gotOffset != 0 {
		t.Fatalf("limit=%d offset=%d", gotLimit, gotOffset)
	}

	h = newAdminHandlers(&mockAdminRepo{
		listUsersFunc: func(ctx context.Context, limit, offset int) ([]port.AdminUser, int64, error) {
			gotLimit, gotOffset = limit, offset
			return nil, 0, errors.New("repo failed")
		},
	})
	rr = httptest.NewRecorder()
	h.ListUsers(rr, newAdminRequest(http.MethodGet, "/admin/users?page=2&limit=10"))
	if rr.Code != http.StatusInternalServerError {
		t.Fatalf("status=%d", rr.Code)
	}
}

func TestAdminHandlersGetUser(t *testing.T) {
	h := newAdminHandlers(&mockAdminRepo{
		getUserFunc: func(ctx context.Context, userID int64) (*port.AdminUser, error) {
			return &port.AdminUser{ID: userID}, nil
		},
	})
	rr := httptest.NewRecorder()
	h.GetUser(rr, withURLParam(newAdminRequest(http.MethodGet, "/admin/users/5"), "id", "5"))
	if rr.Code != http.StatusOK {
		t.Fatalf("status=%d", rr.Code)
	}

	rr = httptest.NewRecorder()
	h.GetUser(rr, withURLParam(newAdminRequest(http.MethodGet, "/admin/users/bad"), "id", "bad"))
	if rr.Code != http.StatusBadRequest {
		t.Fatalf("status=%d", rr.Code)
	}

	h = newAdminHandlers(&mockAdminRepo{
		getUserFunc: func(ctx context.Context, userID int64) (*port.AdminUser, error) { return nil, nil },
	})
	rr = httptest.NewRecorder()
	h.GetUser(rr, withURLParam(newAdminRequest(http.MethodGet, "/admin/users/7"), "id", "7"))
	if rr.Code != http.StatusNotFound {
		t.Fatalf("status=%d", rr.Code)
	}

	h = newAdminHandlers(&mockAdminRepo{
		getUserFunc: func(ctx context.Context, userID int64) (*port.AdminUser, error) {
			return nil, errors.New("repo failed")
		},
	})
	rr = httptest.NewRecorder()
	h.GetUser(rr, withURLParam(newAdminRequest(http.MethodGet, "/admin/users/7"), "id", "7"))
	if rr.Code != http.StatusInternalServerError {
		t.Fatalf("status=%d", rr.Code)
	}
}

func TestAdminHandlersDeleteUserAndListJobs(t *testing.T) {
	var gotSource string
	var gotLimit, gotOffset int
	h := newAdminHandlers(&mockAdminRepo{
		deleteUserFn: func(ctx context.Context, userID int64) (bool, error) { return true, nil },
		listJobsFunc: func(ctx context.Context, source string, limit, offset int) ([]port.AdminJob, int64, error) {
			gotSource, gotLimit, gotOffset = source, limit, offset
			return []port.AdminJob{{ID: 9, Source: source}}, 1, nil
		},
	})

	rr := httptest.NewRecorder()
	h.DeleteUser(rr, withURLParam(newAdminRequest(http.MethodDelete, "/admin/users/7"), "id", "7"))
	if rr.Code != http.StatusNoContent {
		t.Fatalf("status=%d", rr.Code)
	}

	rr = httptest.NewRecorder()
	h.ListJobs(rr, newAdminRequest(http.MethodGet, "/admin/jobs"))
	if rr.Code != http.StatusOK {
		t.Fatalf("status=%d", rr.Code)
	}
	if gotSource != "" || gotLimit != adminDefaultLimit || gotOffset != 0 {
		t.Fatalf("ListJobs default query mismatch: source=%q limit=%d offset=%d", gotSource, gotLimit, gotOffset)
	}

	req := newAdminRequest(http.MethodGet, "/admin/jobs?page=3&limit=20&source=kwork")
	req.URL.RawQuery = "page=3&limit=20&source=kwork"
	rr = httptest.NewRecorder()
	h.ListJobs(rr, req)
	if rr.Code != http.StatusOK {
		t.Fatalf("status=%d", rr.Code)
	}
	if gotSource != "kwork" || gotLimit != 20 || gotOffset != 40 {
		t.Fatalf("ListJobs query mismatch: source=%q limit=%d offset=%d", gotSource, gotLimit, gotOffset)
	}

	h = newAdminHandlers(&mockAdminRepo{
		deleteUserFn: func(ctx context.Context, userID int64) (bool, error) { return false, nil },
		listJobsFunc: func(ctx context.Context, source string, limit, offset int) ([]port.AdminJob, int64, error) {
			return nil, 0, errors.New("repo failed")
		},
	})
	rr = httptest.NewRecorder()
	h.DeleteUser(rr, withURLParam(newAdminRequest(http.MethodDelete, "/admin/users/7"), "id", "7"))
	if rr.Code != http.StatusNotFound {
		t.Fatalf("status=%d", rr.Code)
	}

	req = newAdminRequest(http.MethodGet, "/admin/jobs?source=flru")
	req.URL.RawQuery = "source=flru"
	rr = httptest.NewRecorder()
	h.ListJobs(rr, req)
	if rr.Code != http.StatusInternalServerError {
		t.Fatalf("status=%d", rr.Code)
	}
}

func TestAdminHandlersSetUserPro(t *testing.T) {
	t.Run("on", func(t *testing.T) {
		var gotUserID int64
		var gotEnabled bool
		var gotDays int
		expiresAt := time.Now().UTC().Add(30 * 24 * time.Hour)
		h := newAdminHandlers(&mockAdminRepo{
			setProFunc: func(ctx context.Context, userID int64, enabled bool, days int) (bool, *time.Time, error) {
				gotUserID = userID
				gotEnabled = enabled
				gotDays = days
				return true, &expiresAt, nil
			},
		})

		req := withURLParam(newAdminRequest(http.MethodPut, "/admin/users/7/pro"), "id", "7")
		req.Body = io.NopCloser(strings.NewReader(`{"enabled":true,"days":30}`))
		rr := httptest.NewRecorder()
		h.SetUserPro(rr, req)

		if rr.Code != http.StatusNoContent {
			t.Fatalf("status=%d want=204", rr.Code)
		}
		if gotUserID != 7 || !gotEnabled || gotDays != 30 {
			t.Fatalf("SetUserPro got user=%d enabled=%v days=%d", gotUserID, gotEnabled, gotDays)
		}
	})

	t.Run("off", func(t *testing.T) {
		var gotEnabled bool
		h := newAdminHandlers(&mockAdminRepo{
			setProFunc: func(ctx context.Context, userID int64, enabled bool, days int) (bool, *time.Time, error) {
				gotEnabled = enabled
				return true, nil, nil
			},
		})

		req := withURLParam(newAdminRequest(http.MethodPut, "/admin/users/7/pro"), "id", "7")
		req.Body = io.NopCloser(strings.NewReader(`{"enabled":false}`))
		rr := httptest.NewRecorder()
		h.SetUserPro(rr, req)

		if rr.Code != http.StatusNoContent {
			t.Fatalf("status=%d want=204", rr.Code)
		}
		if gotEnabled {
			t.Fatal("enabled=true, want false")
		}
	})

	t.Run("not found", func(t *testing.T) {
		h := newAdminHandlers(&mockAdminRepo{
			setProFunc: func(ctx context.Context, userID int64, enabled bool, days int) (bool, *time.Time, error) {
				return false, nil, nil
			},
		})

		req := withURLParam(newAdminRequest(http.MethodPut, "/admin/users/404/pro"), "id", "404")
		req.Body = io.NopCloser(strings.NewReader(`{"enabled":true,"days":30}`))
		rr := httptest.NewRecorder()
		h.SetUserPro(rr, req)

		if rr.Code != http.StatusNotFound {
			t.Fatalf("status=%d want=404", rr.Code)
		}
	})
}

func TestAdminHandlersSetUserPro_RecordsLifecycleEvent(t *testing.T) {
	getUserCalls := 0
	eventRepo := &mockProductEventRepo{}
	h := newAdminHandlers(&mockAdminRepo{
		getUserFunc: func(ctx context.Context, userID int64) (*port.AdminUser, error) {
			getUserCalls++
			if getUserCalls == 1 {
				return &port.AdminUser{ID: userID, TelegramID: 123456789, IsPro: false}, nil
			}
			expiresAt := time.Now().UTC().Add(30 * 24 * time.Hour)
			return &port.AdminUser{ID: userID, TelegramID: 123456789, IsPro: true, ProExpiresAt: &expiresAt}, nil
		},
		setProFunc: func(ctx context.Context, userID int64, enabled bool, days int) (bool, *time.Time, error) {
			expiresAt := time.Now().UTC().Add(30 * 24 * time.Hour)
			return true, &expiresAt, nil
		},
	})
	h.ProductEventRepo = eventRepo

	req := withURLParam(newAdminRequest(http.MethodPut, "/admin/users/7/pro"), "id", "7")
	req.Body = io.NopCloser(strings.NewReader(`{"enabled":true,"days":30}`))
	rr := httptest.NewRecorder()

	h.SetUserPro(rr, req)

	if rr.Code != http.StatusNoContent {
		t.Fatalf("status=%d want=204", rr.Code)
	}
	if len(eventRepo.events) != 1 {
		t.Fatalf("events len=%d want=1", len(eventRepo.events))
	}
	if eventRepo.events[0].Type != port.ProductEventProActivated {
		t.Fatalf("event type=%q want=%q", eventRepo.events[0].Type, port.ProductEventProActivated)
	}
}

func TestParseHelpers(t *testing.T) {
	req := httptest.NewRequest(http.MethodGet, "/admin/users?page=0&limit=-1", nil)
	limit, offset := parsePagination(req)
	if limit != adminDefaultLimit || offset != 0 {
		t.Fatalf("limit=%d offset=%d", limit, offset)
	}

	rr := httptest.NewRecorder()
	req = withURLParam(httptest.NewRequest(http.MethodGet, "/admin/users/0", nil), "id", "0")
	if _, ok := parseIDParam(rr, req, "id"); ok {
		t.Fatal("parseIDParam ok want false")
	}

	rr = httptest.NewRecorder()
	writeJSON(rr, map[string]string{"ok": "true"})
	if ct := rr.Header().Get("Content-Type"); !strings.Contains(ct, "application/json") {
		t.Fatalf("content-type=%q", ct)
	}
}

func TestAdminHandlersGetDebugMatch(t *testing.T) {
	var gotUserID int64
	var gotJobURL string
	h := newAdminHandlers(&mockAdminRepo{})
	h.DebugMatchClient = &mockAdminDebugClient{
		getMatchDebugFunc: func(ctx context.Context, userID int64, jobURL string) (map[string]any, error) {
			gotUserID = userID
			gotJobURL = jobURL
			return map[string]any{"conclusion": "ok"}, nil
		},
	}

	req := newAdminRequest(http.MethodGet, "/admin/debug/match?user_id=7&job_url=https://kwork.ru/projects/7")
	req.URL.RawQuery = "user_id=7&job_url=https://kwork.ru/projects/7"
	rr := httptest.NewRecorder()
	h.GetDebugMatch(rr, req)
	if rr.Code != http.StatusOK {
		t.Fatalf("status=%d", rr.Code)
	}
	if gotUserID != 7 || gotJobURL != "https://kwork.ru/projects/7" {
		t.Fatalf("unexpected proxy args: user_id=%d job_url=%q", gotUserID, gotJobURL)
	}

	req = newAdminRequest(http.MethodGet, "/admin/debug/match?user_id=bad")
	req.URL.RawQuery = "user_id=bad"
	rr = httptest.NewRecorder()
	h.GetDebugMatch(rr, req)
	if rr.Code != http.StatusBadRequest {
		t.Fatalf("status=%d", rr.Code)
	}

	h.DebugMatchClient = &mockAdminDebugClient{
		getMatchDebugFunc: func(ctx context.Context, userID int64, jobURL string) (map[string]any, error) {
			return nil, errors.New("upstream failed")
		},
	}
	req = newAdminRequest(http.MethodGet, "/admin/debug/match?user_id=9&job_url=https://kwork.ru/projects/9")
	req.URL.RawQuery = "user_id=9&job_url=https://kwork.ru/projects/9"
	rr = httptest.NewRecorder()
	h.GetDebugMatch(rr, req)
	if rr.Code != http.StatusBadGateway {
		t.Fatalf("status=%d", rr.Code)
	}
}

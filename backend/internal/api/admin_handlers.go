package api

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"log/slog"
	"net/http"
	"strconv"
	"strings"

	"github.com/go-chi/chi/v5"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

const adminDefaultLimit = 50
const adminMaxLimit = 200

// AdminHandlers — HTTP handlers для /admin/* эндпоинтов.
type AdminHandlers struct {
	AdminRepo        port.AdminRepository
	UserRepo         port.UserRepository // new: needed to update plans
	DebugMatchClient AdminDebugMatchClient
	AdminToken       string
	Logger           *slog.Logger
}

type AdminDebugMatchClient interface {
	GetMatchDebug(ctx context.Context, userID int64, jobURL string) (map[string]any, error)
}

func (h *AdminHandlers) logger() *slog.Logger {
	if h != nil && h.Logger != nil {
		return h.Logger
	}
	return slog.Default()
}

// authorizeAdmin проверяет Bearer token для admin-запросов.
func (h *AdminHandlers) authorizeAdmin(w http.ResponseWriter, r *http.Request) bool {
	if h.AdminToken == "" {
		http.Error(w, "admin not configured", http.StatusInternalServerError)
		return false
	}
	auth := strings.TrimSpace(r.Header.Get("Authorization"))
	if !strings.HasPrefix(auth, authHeaderPrefix) {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return false
	}
	token := strings.TrimSpace(strings.TrimPrefix(auth, authHeaderPrefix))
	if token == "" || subtle.ConstantTimeCompare([]byte(token), []byte(h.AdminToken)) != 1 {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return false
	}
	return true
}

// GetStats возвращает агрегированную статистику системы.
// GET /admin/stats
func (h *AdminHandlers) GetStats(w http.ResponseWriter, r *http.Request) {
	if !h.authorizeAdmin(w, r) {
		return
	}
	stats, err := h.AdminRepo.GetStats(r.Context())
	if err != nil {
		h.logger().Error("admin get stats failed", "err", err)
		http.Error(w, internalErrorMessage, http.StatusInternalServerError)
		return
	}
	writeJSON(w, stats)
}

// ListUsers возвращает постраничный список пользователей.
// GET /admin/users?page=1&limit=50
func (h *AdminHandlers) ListUsers(w http.ResponseWriter, r *http.Request) {
	if !h.authorizeAdmin(w, r) {
		return
	}
	limit, offset := parsePagination(r)
	users, total, err := h.AdminRepo.ListUsers(r.Context(), limit, offset)
	if err != nil {
		h.logger().Error("admin list users failed", "err", err)
		http.Error(w, internalErrorMessage, http.StatusInternalServerError)
		return
	}
	writeJSON(w, map[string]any{
		"total": total,
		"items": users,
	})
}

// GetUser возвращает одного пользователя.
// GET /admin/users/{id}
func (h *AdminHandlers) GetUser(w http.ResponseWriter, r *http.Request) {
	if !h.authorizeAdmin(w, r) {
		return
	}
	userID, ok := parseIDParam(w, r, "id")
	if !ok {
		return
	}
	user, err := h.AdminRepo.GetUser(r.Context(), userID)
	if err != nil {
		h.logger().Error("admin get user failed", "user_id", userID, "err", err)
		http.Error(w, internalErrorMessage, http.StatusInternalServerError)
		return
	}
	if user == nil {
		http.Error(w, "user not found", http.StatusNotFound)
		return
	}
	writeJSON(w, user)
}

// PutUserPlan обновляет тарифный план пользователя.
// PUT /admin/users/{id}/plan
type PutUserPlanRequest struct {
	PlanID string `json:"plan_id"`
}

func (h *AdminHandlers) PutUserPlan(w http.ResponseWriter, r *http.Request) {
	if !h.authorizeAdmin(w, r) {
		return
	}
	userID, ok := parseIDParam(w, r, "id")
	if !ok {
		return
	}
	var req PutUserPlanRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid JSON", http.StatusBadRequest)
		return
	}
	if req.PlanID == "" {
		http.Error(w, "plan_id is required", http.StatusBadRequest)
		return
	}

	if err := h.UserRepo.UpdatePlan(r.Context(), userID, req.PlanID); err != nil {
		h.logger().Error("admin update user plan failed", "user_id", userID, "plan_id", req.PlanID, "err", err)
		http.Error(w, internalErrorMessage, http.StatusInternalServerError)
		return
	}

	h.logger().Info("admin updated user plan", "user_id", userID, "plan_id", req.PlanID)
	w.WriteHeader(http.StatusNoContent)
}

// DeleteUser удаляет пользователя и все связанные данные (каскад).
// DELETE /admin/users/{id}
func (h *AdminHandlers) DeleteUser(w http.ResponseWriter, r *http.Request) {
	if !h.authorizeAdmin(w, r) {
		return
	}
	userID, ok := parseIDParam(w, r, "id")
	if !ok {
		return
	}
	found, err := h.AdminRepo.DeleteUser(r.Context(), userID)
	if err != nil {
		h.logger().Error("admin delete user failed", "user_id", userID, "err", err)
		http.Error(w, internalErrorMessage, http.StatusInternalServerError)
		return
	}
	if !found {
		http.Error(w, "user not found", http.StatusNotFound)
		return
	}
	h.logger().Info("admin deleted user", "user_id", userID)
	w.WriteHeader(http.StatusNoContent)
}

// ListJobs возвращает постраничный список задач.
// GET /admin/jobs?page=1&limit=50&source=kwork
func (h *AdminHandlers) ListJobs(w http.ResponseWriter, r *http.Request) {
	if !h.authorizeAdmin(w, r) {
		return
	}
	source := strings.TrimSpace(r.URL.Query().Get("source"))
	limit, offset := parsePagination(r)
	jobs, total, err := h.AdminRepo.ListJobs(r.Context(), source, limit, offset)
	if err != nil {
		h.logger().Error("admin list jobs failed", "err", err)
		http.Error(w, internalErrorMessage, http.StatusInternalServerError)
		return
	}
	writeJSON(w, map[string]any{
		"total": total,
		"items": jobs,
	})
}

// GetDebugMatch проксирует admin debug-match запрос во внутренний ai-service.
// GET /admin/debug/match?user_id=1&job_url=https://...
func (h *AdminHandlers) GetDebugMatch(w http.ResponseWriter, r *http.Request) {
	if !h.authorizeAdmin(w, r) {
		return
	}
	if h.DebugMatchClient == nil {
		http.Error(w, "debug match not configured", http.StatusServiceUnavailable)
		return
	}
	rawUserID := strings.TrimSpace(r.URL.Query().Get("user_id"))
	rawJobURL := strings.TrimSpace(r.URL.Query().Get("job_url"))
	userID, err := strconv.ParseInt(rawUserID, 10, 64)
	if err != nil || userID <= 0 {
		http.Error(w, "invalid user_id", http.StatusBadRequest)
		return
	}
	if rawJobURL == "" {
		http.Error(w, "job_url is required", http.StatusBadRequest)
		return
	}

	payload, err := h.DebugMatchClient.GetMatchDebug(r.Context(), userID, rawJobURL)
	if err != nil {
		h.logger().Error("admin debug match failed", "user_id", userID, "job_url", rawJobURL, "err", err)
		http.Error(w, "debug match unavailable", http.StatusBadGateway)
		return
	}
	writeJSON(w, payload)
}

// --- helpers ---

func parsePagination(r *http.Request) (limit, offset int) {
	page, _ := strconv.Atoi(r.URL.Query().Get("page"))
	if page < 1 {
		page = 1
	}
	limit, _ = strconv.Atoi(r.URL.Query().Get("limit"))
	if limit <= 0 {
		limit = adminDefaultLimit
	}
	if limit > adminMaxLimit {
		limit = adminMaxLimit
	}
	offset = (page - 1) * limit
	return limit, offset
}

func parseIDParam(w http.ResponseWriter, r *http.Request, param string) (int64, bool) {
	raw := chi.URLParam(r, param)
	id, err := strconv.ParseInt(raw, 10, 64)
	if err != nil || id <= 0 {
		http.Error(w, "invalid id", http.StatusBadRequest)
		return 0, false
	}
	return id, true
}

func writeJSON(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(v); err != nil {
		slog.Default().Error("admin write json failed", "err", err)
	}
}

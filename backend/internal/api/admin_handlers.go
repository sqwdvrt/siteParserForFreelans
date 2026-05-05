package api

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"log/slog"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

const adminDefaultLimit = 50
const adminMaxLimit = 200

// AdminHandlers — HTTP handlers для /admin/* эндпоинтов.
type AdminHandlers struct {
	AdminRepo        port.AdminRepository
	DebugMatchClient AdminDebugMatchClient
	ProductEventRepo port.ProductEventRepository
	AdminToken       string
	Logger           *slog.Logger
}

type AdminDebugMatchClient interface {
	GetMatchDebug(ctx context.Context, userID int64, jobURL string) (map[string]any, error)
}

type SetUserProRequest struct {
	Enabled bool `json:"enabled"`
	Days    int  `json:"days,omitempty"`
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

// SetUserPro включает, продлевает или выключает Pro по внутреннему user id.
// PUT /admin/users/{id}/pro
func (h *AdminHandlers) SetUserPro(w http.ResponseWriter, r *http.Request) {
	if !h.authorizeAdmin(w, r) {
		return
	}
	userID, ok := parseIDParam(w, r, "id")
	if !ok {
		return
	}
	var req SetUserProRequest
	if _, err := decodeJSONBody(w, r, &req); err != nil {
		http.Error(w, "invalid JSON", http.StatusBadRequest)
		return
	}
	before, err := h.AdminRepo.GetUser(r.Context(), userID)
	if err != nil {
		h.logger().Error("admin get user before pro update failed", "user_id", userID, "err", err)
		http.Error(w, internalErrorMessage, http.StatusInternalServerError)
		return
	}
	found, _, err := h.AdminRepo.SetUserPro(r.Context(), userID, req.Enabled, req.Days)
	if err != nil {
		h.logger().Error("admin set user pro failed", "user_id", userID, "enabled", req.Enabled, "err", err)
		http.Error(w, internalErrorMessage, http.StatusInternalServerError)
		return
	}
	if !found {
		http.Error(w, "user not found", http.StatusNotFound)
		return
	}
	after, err := h.AdminRepo.GetUser(r.Context(), userID)
	if err != nil {
		h.logger().Warn("admin get user after pro update failed", "user_id", userID, "err", err)
	} else {
		h.recordProLifecycleEvent(r.Context(), before, after, req.Enabled, req.Days)
	}
	w.WriteHeader(http.StatusNoContent)
}

// SetTelegramUserPro включает, продлевает или выключает Pro по telegram_id.
// PUT /admin/telegram-users/{telegram_id}/pro
func (h *AdminHandlers) SetTelegramUserPro(w http.ResponseWriter, r *http.Request) {
	if !h.authorizeAdmin(w, r) {
		return
	}
	telegramID, ok := parseIDParam(w, r, "telegram_id")
	if !ok {
		return
	}
	var req SetUserProRequest
	if _, err := decodeJSONBody(w, r, &req); err != nil {
		http.Error(w, "invalid JSON", http.StatusBadRequest)
		return
	}
	before, err := h.AdminRepo.GetUserByTelegramID(r.Context(), telegramID)
	if err != nil {
		h.logger().Error("admin get telegram user before pro update failed", "telegram_id", telegramID, "err", err)
		http.Error(w, internalErrorMessage, http.StatusInternalServerError)
		return
	}
	found, _, err := h.AdminRepo.SetUserProByTelegramID(r.Context(), telegramID, req.Enabled, req.Days)
	if err != nil {
		h.logger().Error("admin set telegram user pro failed", "telegram_id", telegramID, "enabled", req.Enabled, "err", err)
		http.Error(w, internalErrorMessage, http.StatusInternalServerError)
		return
	}
	if !found {
		http.Error(w, "user not found", http.StatusNotFound)
		return
	}
	after, err := h.AdminRepo.GetUserByTelegramID(r.Context(), telegramID)
	if err != nil {
		h.logger().Warn("admin get telegram user after pro update failed", "telegram_id", telegramID, "err", err)
	} else {
		h.recordProLifecycleEvent(r.Context(), before, after, req.Enabled, req.Days)
	}
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

func (h *AdminHandlers) recordProLifecycleEvent(
	ctx context.Context,
	before *port.AdminUser,
	after *port.AdminUser,
	enabled bool,
	days int,
) {
	if h.ProductEventRepo == nil || after == nil || after.ID <= 0 {
		return
	}
	eventType := port.ProductEventProActivated
	if !enabled {
		eventType = port.ProductEventProExpired
	} else if before != nil && before.IsPro {
		eventType = port.ProductEventProRenewed
	} else if before != nil && before.ProExpiresAt != nil && before.ProExpiresAt.Before(time.Now()) {
		eventType = port.ProductEventProRenewed
	}
	properties := map[string]any{
		"days":          days,
		"telegram_id":   after.TelegramID,
		"previous_plan": "free",
	}
	if before != nil && before.IsPro {
		properties["previous_plan"] = "pro"
	} else if before != nil && before.ProExpiresAt != nil && before.ProExpiresAt.Before(time.Now()) {
		properties["previous_plan"] = "expired_pro"
	}
	if after.ProExpiresAt != nil {
		properties["pro_expires_at"] = after.ProExpiresAt.UTC().Format(time.RFC3339)
	}
	if err := h.ProductEventRepo.Record(ctx, port.ProductEvent{
		Type:       eventType,
		UserID:     after.ID,
		Properties: properties,
	}); err != nil {
		h.logger().Warn("admin record pro lifecycle event failed", "user_id", after.ID, "event_type", eventType, "err", err)
	}
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

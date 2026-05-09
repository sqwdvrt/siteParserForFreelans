package api

import (
	"bytes"
	"context"
	"crypto/hmac"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/go-chi/chi/v5"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/observability"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/telemetry"
)

const maxProfileTextLen = 5000
const minProfileTextLen = 50
const maxJSONBodyBytes int64 = 16 << 10 // 16 KiB
const authHeaderPrefix = "Bearer "
const headerTelegramID = "X-Telegram-ID"
const headerRequestTimestamp = "X-Request-Timestamp"
const headerRequestNonce = "X-Request-Nonce"
const headerRequestSignature = "X-Request-Signature"
const internalErrorMessage = "internal error"
const maxRequestSkew = 5 * time.Minute
const defaultNonceTTL = 10 * time.Minute
const defaultRateLimitWindow = time.Minute
const defaultIPRateLimit = 120
const defaultTelegramRateLimit = 60
const defaultUserStatsRequestWindow = 7 * 24 * time.Hour
const maxPreferenceKeywords = 32
const maxPreferenceSources = 16
const maxPreferenceValueLen = 64
const defaultFreePlanDailyCap = 5
const defaultProPlanDailyCap = 25

var errTelegramIDInvalid = errors.New("telegram_id must be positive integer")
var errProfileTextTooShort = errors.New("profile_text too short")
var errProfileTextLooksPlaceholder = errors.New("profile_text looks like placeholder")

var profilePlaceholderMarkers = []string{
	"smoke",
	"staging",
	"dummy",
	"profile check",
	"example profile",
}

func validateProfileText(profileText string) error {
	trimmed := strings.TrimSpace(profileText)
	if utf8.RuneCountInString(trimmed) < minProfileTextLen {
		return errProfileTextTooShort
	}
	wordCount := 0
	for _, part := range strings.Fields(trimmed) {
		if utf8.RuneCountInString(part) >= 2 {
			wordCount++
		}
	}
	if wordCount < 5 {
		return errProfileTextTooShort
	}
	lower := strings.ToLower(trimmed)
	for _, marker := range profilePlaceholderMarkers {
		if strings.Contains(lower, marker) {
			return errProfileTextLooksPlaceholder
		}
	}
	return nil
}

// NonceStore — хранилище одноразовых nonce для anti-replay.
type NonceStore interface {
	Use(ctx context.Context, key string, ttl time.Duration) (bool, error)
}

// RequestRateLimiter — rate limiter для ключей (IP, telegram id).
type RequestRateLimiter interface {
	Allow(ctx context.Context, key string, limit int, window time.Duration) (bool, error)
}

type UserEmbedDispatcher interface {
	Flush(ctx context.Context, limit int) (int, error)
}

// Handlers — HTTP handlers для API.
type Handlers struct {
	UserRepo              port.UserRepository
	UserStatsRepo         port.UserStatsRepository // optional: user-facing stats for /users/{id}/stats
	UserEmbedDispatchRepo port.UserEmbedDispatchRepository
	UserEmbedDispatcher   UserEmbedDispatcher     // optional: best-effort low-latency flush after staging
	FeedbackRepo          port.FeedbackRepository // nil — feedback не сохраняется
	ProductEventRepo      port.ProductEventRepository
	ProductMetrics        *telemetry.ProductMetrics // optional: prometheus metrics for product events
	AuthToken             string                    // обязательный bearer token для API
	UserHMACSecret        string                    // обязательный секрет подписи user-level запросов
	Logger                *slog.Logger              // optional structured logger; defaults to slog.Default()
	NonceStore            NonceStore                // optional: anti-replay (nonce)
	RateLimiter           RequestRateLimiter        // optional: rate limit (per ip/per telegram id)
	TrustedProxyCIDRs     []*net.IPNet              // optional: trusted reverse proxies for forwarded headers
	NonceTTL              time.Duration
	RateLimitWindow       time.Duration
	IPRateLimit           int
	TelegramRateLimit     int
}

// PostUsersRequest — тело POST /users.
type PostUsersRequest struct {
	TelegramID telegramID `json:"telegram_id"`
}

// telegramID — только целые числа (строки и дроби отклоняются).
type telegramID int64

func (t *telegramID) UnmarshalJSON(data []byte) error {
	var v interface{}
	if err := json.Unmarshal(data, &v); err != nil {
		return err
	}
	switch val := v.(type) {
	case float64:
		if val != float64(int64(val)) {
			return errors.New("telegram_id must be integer")
		}
		i := int64(val)
		if i <= 0 {
			return errTelegramIDInvalid
		}
		*t = telegramID(i)
		return nil
	case string:
		return errors.New("telegram_id must be number, not string")
	default:
		return errors.New("telegram_id must be number")
	}
}

// PostUsersResponse — ответ POST /users.
type PostUsersResponse struct {
	UserID int64 `json:"user_id"`
}

// PostUsers создаёт пользователя по telegram_id.
func (h *Handlers) PostUsers(w http.ResponseWriter, r *http.Request) {
	if !h.authorize(w, r) {
		return
	}
	var req PostUsersRequest
	rawBody, err := decodeJSONBody(w, r, &req)
	if err != nil {
		if errors.Is(err, errBodyTooLarge) {
			http.Error(w, "request body too large", http.StatusRequestEntityTooLarge)
			return
		}
		http.Error(w, "invalid JSON", http.StatusBadRequest)
		return
	}
	callerTelegramID, err := parseTelegramIDHeader(r.Header.Get(headerTelegramID))
	if err != nil {
		http.Error(w, "invalid x-telegram-id header", http.StatusBadRequest)
		return
	}
	if callerTelegramID != int64(req.TelegramID) {
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}
	if !h.enforceIPRateLimit(w, r) {
		return
	}
	if !h.authorizeUserRequest(w, r, callerTelegramID, rawBody) {
		return
	}
	if !h.enforceTelegramRateLimit(w, r, callerTelegramID) {
		return
	}
	userID, created, err := h.UserRepo.Save(r.Context(), int64(req.TelegramID))
	if err != nil {
		h.logger().Error("post users save failed", "err", err)
		http.Error(w, internalErrorMessage, http.StatusInternalServerError)
		return
	}
	if created {
		h.recordProductEvent(r.Context(), port.ProductEvent{
			Type:   port.ProductEventUserRegistered,
			UserID: userID,
		})
	}
	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(PostUsersResponse{UserID: userID}); err != nil {
		h.logger().Error("post users encode response failed", "user_id", userID, "err", err)
	}
}

// PutUserProfileRequest — тело PUT /users/:id/profile.
type PutUserProfileRequest struct {
	ProfileText string `json:"profile_text"`
}

// PutUserNotifyHourRequest — тело PUT /users/:id/notify-hour.
type PutUserNotifyHourRequest struct {
	Hour int `json:"hour"`
}

// PutUserPauseRequest — тело PUT /users/:id/pause.
type PutUserPauseRequest struct {
	Until *time.Time `json:"until"`
}

type PostUserProUpgradeIntentRequest struct {
	Source string `json:"source"`
}

// UserPreferencesRequest — тело PUT /users/:id/preferences.
type UserPreferencesRequest struct {
	IncludeKeywords  []string `json:"include_keywords"`
	ExcludeKeywords  []string `json:"exclude_keywords"`
	MinBudget        *float64 `json:"min_budget"`
	MaxBudget        *float64 `json:"max_budget"`
	PreferredSources []string `json:"preferred_sources"`
}

// UserPreferencesResponse — ответ GET /users/:id/preferences.
type UserPreferencesResponse struct {
	IncludeKeywords  []string   `json:"include_keywords"`
	ExcludeKeywords  []string   `json:"exclude_keywords"`
	MinBudget        *float64   `json:"min_budget,omitempty"`
	MaxBudget        *float64   `json:"max_budget,omitempty"`
	PreferredSources []string   `json:"preferred_sources"`
	IsPro            bool       `json:"is_pro"`
	ProExpiresAt     *time.Time `json:"pro_expires_at,omitempty"`
	NotifyHour       *int16     `json:"notify_hour,omitempty"`
}

// UserResponse — ответ GET /users/:id.
type UserResponse struct {
	ProfileText  string     `json:"profile_text"`
	IsPro        bool       `json:"is_pro"`
	ProExpiresAt *time.Time `json:"pro_expires_at,omitempty"`
	NotifyHour   *int16     `json:"notify_hour,omitempty"`
	PausedUntil  *time.Time `json:"paused_until,omitempty"`
}

// GetUser возвращает профиль пользователя для owner-scoped запросов.
func (h *Handlers) GetUser(w http.ResponseWriter, r *http.Request) {
	if !h.authorize(w, r) {
		return
	}
	userID, callerTelegramID, ok := h.authorizeOwnedUserRequest(w, r, nil)
	if !ok {
		return
	}
	user, err := h.UserRepo.GetByID(r.Context(), userID)
	if err != nil {
		h.logger().Error("get user failed", "user_id", userID, "err", err)
		http.Error(w, internalErrorMessage, http.StatusInternalServerError)
		return
	}
	if user == nil {
		http.Error(w, "user not found", http.StatusNotFound)
		return
	}
	if user.TelegramID != callerTelegramID {
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}
	writeJSON(w, UserResponse{
		ProfileText:  strings.TrimSpace(derefString(user.ProfileText)),
		IsPro:        user.EffectiveIsPro(time.Now()),
		ProExpiresAt: user.ProExpiresAt,
		NotifyHour:   user.NotifyHour,
		PausedUntil:  user.PausedUntil,
	})
}

// PutUserProfile обновляет profile_text пользователя.
func (h *Handlers) PutUserProfile(w http.ResponseWriter, r *http.Request) {
	if !h.authorize(w, r) {
		return
	}
	idStr := chi.URLParam(r, "id")
	userID, err := strconv.ParseInt(idStr, 10, 64)
	if err != nil || userID <= 0 {
		http.Error(w, "invalid user id", http.StatusBadRequest)
		return
	}
	callerTelegramID, err := parseTelegramIDHeader(r.Header.Get(headerTelegramID))
	if err != nil {
		http.Error(w, "invalid x-telegram-id header", http.StatusBadRequest)
		return
	}
	var req PutUserProfileRequest
	rawBody, err := decodeJSONBody(w, r, &req)
	if err != nil {
		if errors.Is(err, errBodyTooLarge) {
			http.Error(w, "request body too large", http.StatusRequestEntityTooLarge)
			return
		}
		http.Error(w, "invalid JSON", http.StatusBadRequest)
		return
	}
	if len(req.ProfileText) > maxProfileTextLen {
		http.Error(w, "profile_text too long", http.StatusBadRequest)
		return
	}
	if validateErr := validateProfileText(req.ProfileText); validateErr != nil {
		switch {
		case errors.Is(validateErr, errProfileTextTooShort):
			http.Error(w, "profile_text too short; include skills, stack, experience and target tasks", http.StatusBadRequest)
		case errors.Is(validateErr, errProfileTextLooksPlaceholder):
			http.Error(w, "profile_text looks like test placeholder; send a real freelancer profile", http.StatusBadRequest)
		default:
			http.Error(w, "invalid profile_text", http.StatusBadRequest)
		}
		return
	}
	if !h.enforceIPRateLimit(w, r) {
		return
	}
	if !h.authorizeUserRequest(w, r, callerTelegramID, rawBody) {
		return
	}
	if !h.enforceTelegramRateLimit(w, r, callerTelegramID) {
		return
	}
	user, err := h.UserRepo.GetByID(r.Context(), userID)
	if err != nil {
		h.logger().Error("put user profile get user failed", "user_id", userID, "err", err)
		http.Error(w, internalErrorMessage, http.StatusInternalServerError)
		return
	}
	if user == nil {
		http.Error(w, "user not found", http.StatusNotFound)
		return
	}
	if user.TelegramID != callerTelegramID {
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}
	if h.UserEmbedDispatchRepo == nil {
		h.logger().Error("put user profile dispatch repository unavailable", "user_id", userID)
		http.Error(w, internalErrorMessage, http.StatusInternalServerError)
		return
	}
	if err := h.UserEmbedDispatchRepo.UpdateProfileScopedAndStage(
		r.Context(),
		userID,
		req.ProfileText,
		observability.QueueDispatchTraceFromContext(r.Context()),
	); err != nil {
		h.logger().Error("put user profile update failed", "user_id", userID, "err", err)
		http.Error(w, internalErrorMessage, http.StatusInternalServerError)
		return
	}
	if h.UserEmbedDispatcher != nil {
		flushCtx, cancel := context.WithTimeout(r.Context(), 500*time.Millisecond)
		defer cancel()
		if _, err := h.UserEmbedDispatcher.Flush(flushCtx, 1); err != nil {
			h.logger().Warn("put user profile dispatch flush failed", "user_id", userID, "err", err)
		}
	}
	h.recordProductEvent(r.Context(), port.ProductEvent{
		Type:   port.ProductEventProfileUpdated,
		UserID: userID,
		Properties: map[string]any{
			"profile_length": len(strings.TrimSpace(req.ProfileText)),
		},
	})
	if !hasNonEmptyProfileText(user.ProfileText) {
		h.recordProductEvent(r.Context(), port.ProductEvent{
			Type:   port.ProductEventProfileCompleted,
			UserID: userID,
		})
	}
	w.WriteHeader(http.StatusNoContent)
}

// PutUserNotifyHour обновляет notify_hour пользователя (0-23, МСК); доступно только для Pro.
func (h *Handlers) PutUserNotifyHour(w http.ResponseWriter, r *http.Request) {
	if !h.authorize(w, r) {
		return
	}
	idStr := chi.URLParam(r, "id")
	userID, err := strconv.ParseInt(idStr, 10, 64)
	if err != nil || userID <= 0 {
		http.Error(w, "invalid user id", http.StatusBadRequest)
		return
	}
	callerTelegramID, err := parseTelegramIDHeader(r.Header.Get(headerTelegramID))
	if err != nil {
		http.Error(w, "invalid x-telegram-id header", http.StatusBadRequest)
		return
	}
	var req PutUserNotifyHourRequest
	rawBody, err := decodeJSONBody(w, r, &req)
	if err != nil {
		if errors.Is(err, errBodyTooLarge) {
			http.Error(w, "request body too large", http.StatusRequestEntityTooLarge)
			return
		}
		http.Error(w, "invalid JSON", http.StatusBadRequest)
		return
	}
	if req.Hour < 0 || req.Hour > 23 {
		http.Error(w, "hour must be between 0 and 23", http.StatusBadRequest)
		return
	}
	if !h.enforceIPRateLimit(w, r) {
		return
	}
	if !h.authorizeUserRequest(w, r, callerTelegramID, rawBody) {
		return
	}
	if !h.enforceTelegramRateLimit(w, r, callerTelegramID) {
		return
	}
	user, err := h.UserRepo.GetByID(r.Context(), userID)
	if err != nil {
		h.logger().Error("put user notify hour get user failed", "user_id", userID, "err", err)
		http.Error(w, internalErrorMessage, http.StatusInternalServerError)
		return
	}
	if user == nil {
		http.Error(w, "user not found", http.StatusNotFound)
		return
	}
	if user.TelegramID != callerTelegramID {
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}
	if !user.EffectiveIsPro(time.Now()) {
		http.Error(w, "pro subscription required", http.StatusForbidden)
		return
	}
	if err := h.UserRepo.UpdateNotifyHourScoped(r.Context(), userID, req.Hour); err != nil {
		h.logger().Error("put user notify hour update failed", "user_id", userID, "err", err)
		http.Error(w, internalErrorMessage, http.StatusInternalServerError)
		return
	}
	h.recordProductEvent(r.Context(), port.ProductEvent{
		Type:   port.ProductEventNotifyHourUpdated,
		UserID: userID,
		Properties: map[string]any{
			"notify_hour": req.Hour,
		},
	})
	w.WriteHeader(http.StatusNoContent)
}

// PutUserPause обновляет paused_until пользователя. until=null снимает паузу.
func (h *Handlers) PutUserPause(w http.ResponseWriter, r *http.Request) {
	if !h.authorize(w, r) {
		return
	}
	var req PutUserPauseRequest
	rawBody, err := decodeJSONBody(w, r, &req)
	if err != nil {
		if errors.Is(err, errBodyTooLarge) {
			http.Error(w, "request body too large", http.StatusRequestEntityTooLarge)
			return
		}
		http.Error(w, "invalid JSON", http.StatusBadRequest)
		return
	}
	if req.Until != nil && !req.Until.After(time.Now().UTC()) {
		http.Error(w, "until must be in the future or null", http.StatusBadRequest)
		return
	}

	userID, callerTelegramID, ok := h.authorizeOwnedUserRequest(w, r, rawBody)
	if !ok {
		return
	}
	user, err := h.UserRepo.GetByID(r.Context(), userID)
	if err != nil {
		h.logger().Error("put user pause get user failed", "user_id", userID, "err", err)
		http.Error(w, internalErrorMessage, http.StatusInternalServerError)
		return
	}
	if user == nil {
		http.Error(w, "user not found", http.StatusNotFound)
		return
	}
	if user.TelegramID != callerTelegramID {
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}
	if err := h.UserRepo.UpdatePauseScoped(r.Context(), userID, req.Until); err != nil {
		h.logger().Error("put user pause update failed", "user_id", userID, "err", err)
		http.Error(w, internalErrorMessage, http.StatusInternalServerError)
		return
	}
	h.recordProductEvent(r.Context(), port.ProductEvent{
		Type:   port.ProductEventPauseUpdated,
		UserID: userID,
		Properties: map[string]any{
			"paused": req.Until != nil,
			"until":  req.Until,
		},
	})
	w.WriteHeader(http.StatusNoContent)
}

// GetUserPreferences возвращает user_preferences для пользователя.
func (h *Handlers) GetUserPreferences(w http.ResponseWriter, r *http.Request) {
	if !h.authorize(w, r) {
		return
	}
	userID, callerTelegramID, ok := h.authorizeOwnedUserRequest(w, r, nil)
	if !ok {
		return
	}
	user, err := h.UserRepo.GetByID(r.Context(), userID)
	if err != nil {
		h.logger().Error("get user preferences get user failed", "user_id", userID, "err", err)
		http.Error(w, internalErrorMessage, http.StatusInternalServerError)
		return
	}
	if user == nil {
		http.Error(w, "user not found", http.StatusNotFound)
		return
	}
	if user.TelegramID != callerTelegramID {
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}
	prefs, err := h.UserRepo.GetPreferencesScoped(r.Context(), userID)
	if err != nil {
		h.logger().Error("get user preferences failed", "user_id", userID, "err", err)
		http.Error(w, internalErrorMessage, http.StatusInternalServerError)
		return
	}
	if prefs == nil {
		prefs = &domain.UserPreferences{}
	}
	writeJSON(w, UserPreferencesResponse{
		IncludeKeywords:  cloneAndNormalizePreferenceValues(prefs.IncludeKeywords, maxPreferenceKeywords),
		ExcludeKeywords:  cloneAndNormalizePreferenceValues(prefs.ExcludeKeywords, maxPreferenceKeywords),
		MinBudget:        prefs.MinBudget,
		MaxBudget:        prefs.MaxBudget,
		PreferredSources: cloneAndNormalizePreferenceValues(prefs.PreferredSources, maxPreferenceSources),
		IsPro:            user.EffectiveIsPro(time.Now()),
		ProExpiresAt:     user.ProExpiresAt,
		NotifyHour:       user.NotifyHour,
	})
}

// GetUserStats returns explainable matching stats for the user over last 7 days.
func (h *Handlers) GetUserStats(w http.ResponseWriter, r *http.Request) {
	if !h.authorize(w, r) {
		return
	}
	userID, callerTelegramID, ok := h.authorizeOwnedUserRequest(w, r, nil)
	if !ok {
		return
	}
	user, err := h.UserRepo.GetByID(r.Context(), userID)
	if err != nil {
		h.logger().Error("get user stats get user failed", "user_id", userID, "err", err)
		http.Error(w, internalErrorMessage, http.StatusInternalServerError)
		return
	}
	if user == nil {
		http.Error(w, "user not found", http.StatusNotFound)
		return
	}
	if user.TelegramID != callerTelegramID {
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}
	if h.UserStatsRepo == nil {
		stats := &port.UserStats{
			PeriodDays: int(defaultUserStatsRequestWindow.Hours() / 24),
		}
		enrichUserStats(stats, user)
		writeJSON(w, stats)
		return
	}
	stats, err := h.UserStatsRepo.GetUserStats(r.Context(), userID, defaultUserStatsRequestWindow)
	if err != nil {
		h.logger().Error("get user stats failed", "user_id", userID, "err", err)
		http.Error(w, internalErrorMessage, http.StatusInternalServerError)
		return
	}
	if stats == nil {
		stats = &port.UserStats{
			PeriodDays: int(defaultUserStatsRequestWindow.Hours() / 24),
		}
	}
	enrichUserStats(stats, user)
	writeJSON(w, stats)
}

// PostUserProUpgradeIntent records a self-serve upgrade request from Telegram UX.
func (h *Handlers) PostUserProUpgradeIntent(w http.ResponseWriter, r *http.Request) {
	if !h.authorize(w, r) {
		return
	}
	var req PostUserProUpgradeIntentRequest
	rawBody, err := decodeJSONBody(w, r, &req)
	if err != nil {
		http.Error(w, "invalid JSON", http.StatusBadRequest)
		return
	}
	userID, callerTelegramID, ok := h.authorizeOwnedUserRequest(w, r, rawBody)
	if !ok {
		return
	}
	user, err := h.UserRepo.GetByID(r.Context(), userID)
	if err != nil {
		h.logger().Error("post user pro upgrade intent get user failed", "user_id", userID, "err", err)
		http.Error(w, internalErrorMessage, http.StatusInternalServerError)
		return
	}
	if user == nil {
		http.Error(w, "user not found", http.StatusNotFound)
		return
	}
	if user.TelegramID != callerTelegramID {
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}
	h.recordProductEvent(r.Context(), port.ProductEvent{
		Type:   port.ProductEventProUpgradeRequested,
		UserID: userID,
		Properties: map[string]any{
			"source":       strings.TrimSpace(req.Source),
			"current_plan": currentPlanName(user, time.Now()),
		},
	})
	w.WriteHeader(http.StatusNoContent)
}

// PutUserPreferences обновляет user_preferences пользователя.
func (h *Handlers) PutUserPreferences(w http.ResponseWriter, r *http.Request) {
	if !h.authorize(w, r) {
		return
	}
	var req UserPreferencesRequest
	rawBody, err := decodeJSONBody(w, r, &req)
	if err != nil {
		if errors.Is(err, errBodyTooLarge) {
			http.Error(w, "request body too large", http.StatusRequestEntityTooLarge)
			return
		}
		http.Error(w, "invalid JSON", http.StatusBadRequest)
		return
	}
	if req.MinBudget != nil && *req.MinBudget < 0 {
		http.Error(w, "min_budget must be >= 0", http.StatusBadRequest)
		return
	}
	if req.MaxBudget != nil && *req.MaxBudget < 0 {
		http.Error(w, "max_budget must be >= 0", http.StatusBadRequest)
		return
	}
	if req.MinBudget != nil && req.MaxBudget != nil && *req.MinBudget > *req.MaxBudget {
		http.Error(w, "min_budget must be <= max_budget", http.StatusBadRequest)
		return
	}

	userID, callerTelegramID, ok := h.authorizeOwnedUserRequest(w, r, rawBody)
	if !ok {
		return
	}
	user, err := h.UserRepo.GetByID(r.Context(), userID)
	if err != nil {
		h.logger().Error("put user preferences get user failed", "user_id", userID, "err", err)
		http.Error(w, internalErrorMessage, http.StatusInternalServerError)
		return
	}
	if user == nil {
		http.Error(w, "user not found", http.StatusNotFound)
		return
	}
	if user.TelegramID != callerTelegramID {
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}

	prefs := domain.UserPreferences{
		UserID:           userID,
		IncludeKeywords:  cloneAndNormalizePreferenceValues(req.IncludeKeywords, maxPreferenceKeywords),
		ExcludeKeywords:  cloneAndNormalizePreferenceValues(req.ExcludeKeywords, maxPreferenceKeywords),
		MinBudget:        req.MinBudget,
		MaxBudget:        req.MaxBudget,
		PreferredSources: cloneAndNormalizePreferenceValues(req.PreferredSources, maxPreferenceSources),
	}
	if err := h.UserRepo.UpsertPreferencesScoped(r.Context(), userID, prefs); err != nil {
		h.logger().Error("put user preferences upsert failed", "user_id", userID, "err", err)
		http.Error(w, internalErrorMessage, http.StatusInternalServerError)
		return
	}
	h.recordProductEvent(r.Context(), port.ProductEvent{
		Type:   port.ProductEventPreferencesUpdated,
		UserID: userID,
		Properties: map[string]any{
			"include_keywords_count":  len(prefs.IncludeKeywords),
			"exclude_keywords_count":  len(prefs.ExcludeKeywords),
			"preferred_sources_count": len(prefs.PreferredSources),
			"preferred_sources":       append([]string(nil), prefs.PreferredSources...),
			"has_min_budget":          prefs.MinBudget != nil,
			"has_max_budget":          prefs.MaxBudget != nil,
		},
	})
	w.WriteHeader(http.StatusNoContent)
}

// PostUserFeedbackRequest — тело POST /users/{id}/feedback.
type PostUserFeedbackRequest struct {
	JobID    int64  `json:"job_id"`
	Feedback string `json:"feedback"`
}

// PostUserFeedback обрабатывает POST /users/{id}/feedback.
// Аутентификация: Bearer + HMAC (X-Telegram-ID, X-Request-Signature) — как у PutUserProfile.
func (h *Handlers) PostUserFeedback(w http.ResponseWriter, r *http.Request) {
	if !h.authorize(w, r) {
		return
	}
	idStr := chi.URLParam(r, "id")
	userID, err := strconv.ParseInt(idStr, 10, 64)
	if err != nil || userID <= 0 {
		http.Error(w, "invalid user id", http.StatusBadRequest)
		return
	}
	callerTelegramID, err := parseTelegramIDHeader(r.Header.Get(headerTelegramID))
	if err != nil {
		http.Error(w, "invalid x-telegram-id header", http.StatusBadRequest)
		return
	}
	var req PostUserFeedbackRequest
	rawBody, err := decodeJSONBody(w, r, &req)
	if err != nil {
		if errors.Is(err, errBodyTooLarge) {
			http.Error(w, "request body too large", http.StatusRequestEntityTooLarge)
			return
		}
		http.Error(w, "invalid JSON", http.StatusBadRequest)
		return
	}
	if req.JobID <= 0 {
		http.Error(w, "invalid job_id", http.StatusBadRequest)
		return
	}
	fb := domain.FeedbackType(req.Feedback)
	if !fb.IsValid() {
		http.Error(w, "feedback must be 'good' or 'bad'", http.StatusBadRequest)
		return
	}
	if !h.enforceIPRateLimit(w, r) {
		return
	}
	if !h.authorizeUserRequest(w, r, callerTelegramID, rawBody) {
		return
	}
	if !h.enforceTelegramRateLimit(w, r, callerTelegramID) {
		return
	}
	user, err := h.UserRepo.GetByID(r.Context(), userID)
	if err != nil {
		h.logger().Error("post user feedback get user failed", "user_id", userID, "err", err)
		http.Error(w, internalErrorMessage, http.StatusInternalServerError)
		return
	}
	if user == nil {
		http.Error(w, "user not found", http.StatusNotFound)
		return
	}
	if user.TelegramID != callerTelegramID {
		http.Error(w, "forbidden", http.StatusForbidden)
		return
	}
	if h.FeedbackRepo != nil {
		if err := h.FeedbackRepo.Upsert(r.Context(), userID, req.JobID, fb); err != nil {
			if errors.Is(err, port.ErrFeedbackNotAllowed) {
				http.Error(w, "forbidden", http.StatusForbidden)
				return
			}
			h.logger().Error("post user feedback upsert failed", "user_id", userID, "job_id", req.JobID, "err", err)
			http.Error(w, internalErrorMessage, http.StatusInternalServerError)
			return
		}
	}
	h.recordProductEvent(r.Context(), port.ProductEvent{
		Type:   port.ProductEventFeedbackSubmitted,
		UserID: userID,
		JobID:  req.JobID,
		Properties: map[string]any{
			"feedback": string(fb),
		},
	})
	h.logger().Info("feedback recorded", "user_id", userID, "job_id", req.JobID, "feedback", req.Feedback)
	w.WriteHeader(http.StatusNoContent)
}

var errBodyTooLarge = errors.New("body too large")
var errInvalidTelegramHeader = errors.New("invalid telegram header")

func decodeJSONBody(w http.ResponseWriter, r *http.Request, dst interface{}) ([]byte, error) {
	r.Body = http.MaxBytesReader(w, r.Body, maxJSONBodyBytes)
	body, err := io.ReadAll(r.Body)
	if err != nil {
		var maxErr *http.MaxBytesError
		if errors.As(err, &maxErr) {
			return nil, errBodyTooLarge
		}
		return nil, err
	}
	dec := json.NewDecoder(bytes.NewReader(body))
	if err := dec.Decode(dst); err != nil {
		return nil, err
	}
	if err := dec.Decode(&struct{}{}); err != nil && !errors.Is(err, io.EOF) {
		return nil, fmt.Errorf("trailing data: %w", err)
	}
	return body, nil
}

func (h *Handlers) authorizeOwnedUserRequest(w http.ResponseWriter, r *http.Request, rawBody []byte) (int64, int64, bool) {
	idStr := chi.URLParam(r, "id")
	userID, err := strconv.ParseInt(idStr, 10, 64)
	if err != nil || userID <= 0 {
		http.Error(w, "invalid user id", http.StatusBadRequest)
		return 0, 0, false
	}
	callerTelegramID, err := parseTelegramIDHeader(r.Header.Get(headerTelegramID))
	if err != nil {
		http.Error(w, "invalid x-telegram-id header", http.StatusBadRequest)
		return 0, 0, false
	}
	if !h.enforceIPRateLimit(w, r) {
		return 0, 0, false
	}
	if !h.authorizeUserRequest(w, r, callerTelegramID, rawBody) {
		return 0, 0, false
	}
	if !h.enforceTelegramRateLimit(w, r, callerTelegramID) {
		return 0, 0, false
	}
	return userID, callerTelegramID, true
}

func cloneAndNormalizePreferenceValues(values []string, maxItems int) []string {
	if maxItems <= 0 {
		return []string{}
	}
	seen := make(map[string]struct{}, len(values))
	out := make([]string, 0, len(values))
	for _, raw := range values {
		value := strings.ToLower(strings.TrimSpace(raw))
		if value == "" || len(value) > maxPreferenceValueLen {
			continue
		}
		if _, ok := seen[value]; ok {
			continue
		}
		seen[value] = struct{}{}
		out = append(out, value)
		if len(out) >= maxItems {
			break
		}
	}
	return out
}

func derefString(value *string) string {
	if value == nil {
		return ""
	}
	return *value
}

func (h *Handlers) authorize(w http.ResponseWriter, r *http.Request) bool {
	if h.AuthToken == "" {
		http.Error(w, "server auth is not configured", http.StatusInternalServerError)
		return false
	}
	auth := strings.TrimSpace(r.Header.Get("Authorization"))
	if !strings.HasPrefix(auth, authHeaderPrefix) {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return false
	}
	token := strings.TrimSpace(strings.TrimPrefix(auth, authHeaderPrefix))
	if token == "" || subtle.ConstantTimeCompare([]byte(token), []byte(h.AuthToken)) != 1 {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return false
	}
	return true
}

func (h *Handlers) authorizeUserRequest(w http.ResponseWriter, r *http.Request, telegramID int64, body []byte) bool {
	if h.UserHMACSecret == "" {
		http.Error(w, "server user auth is not configured", http.StatusInternalServerError)
		return false
	}
	tsRaw := strings.TrimSpace(r.Header.Get(headerRequestTimestamp))
	if tsRaw == "" {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return false
	}
	ts, err := strconv.ParseInt(tsRaw, 10, 64)
	if err != nil || ts <= 0 {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return false
	}
	reqTime := time.Unix(ts, 0)
	now := time.Now()
	if reqTime.Before(now.Add(-maxRequestSkew)) || reqTime.After(now.Add(maxRequestSkew)) {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return false
	}
	nonce := strings.TrimSpace(r.Header.Get(headerRequestNonce))
	if !isValidNonce(nonce) {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return false
	}

	givenHex := strings.TrimSpace(r.Header.Get(headerRequestSignature))
	givenSig, err := hex.DecodeString(givenHex)
	if err != nil || len(givenSig) != sha256.Size {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return false
	}
	expectedSig := signUserRequest(h.UserHMACSecret, r.Method, r.URL.EscapedPath(), telegramID, tsRaw, nonce, body)
	if subtle.ConstantTimeCompare(givenSig, expectedSig) != 1 {
		http.Error(w, "unauthorized", http.StatusUnauthorized)
		return false
	}
	if h.NonceStore != nil {
		nonceKey := fmt.Sprintf("tg:%d:%s", telegramID, nonce)
		ok, err := h.NonceStore.Use(r.Context(), nonceKey, h.nonceTTL())
		if err != nil {
			h.logger().Error("nonce store failed", "telegram_id", telegramID, "err", err)
			http.Error(w, internalErrorMessage, http.StatusInternalServerError)
			return false
		}
		if !ok {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return false
		}
	}
	return true
}

func signUserRequest(secret, method, path string, telegramID int64, timestamp, nonce string, body []byte) []byte {
	bodyHash := sha256.Sum256(body)
	payload := strings.Join([]string{
		strings.ToUpper(strings.TrimSpace(method)),
		strings.TrimSpace(path),
		strconv.FormatInt(telegramID, 10),
		strings.TrimSpace(timestamp),
		strings.TrimSpace(nonce),
		hex.EncodeToString(bodyHash[:]),
	}, "\n")
	mac := hmac.New(sha256.New, []byte(secret))
	_, _ = mac.Write([]byte(payload))
	return mac.Sum(nil)
}

func parseTelegramIDHeader(raw string) (int64, error) {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return 0, errInvalidTelegramHeader
	}
	id, err := strconv.ParseInt(raw, 10, 64)
	if err != nil || id <= 0 {
		return 0, errInvalidTelegramHeader
	}
	return id, nil
}

func (h *Handlers) nonceTTL() time.Duration {
	if h.NonceTTL > 0 {
		return h.NonceTTL
	}
	return defaultNonceTTL
}

func (h *Handlers) rateLimitWindow() time.Duration {
	if h.RateLimitWindow > 0 {
		return h.RateLimitWindow
	}
	return defaultRateLimitWindow
}

func (h *Handlers) ipRateLimit() int {
	if h.IPRateLimit > 0 {
		return h.IPRateLimit
	}
	return defaultIPRateLimit
}

func (h *Handlers) telegramRateLimit() int {
	if h.TelegramRateLimit > 0 {
		return h.TelegramRateLimit
	}
	return defaultTelegramRateLimit
}

func (h *Handlers) enforceIPRateLimit(w http.ResponseWriter, r *http.Request) bool {
	if h.RateLimiter == nil {
		return true
	}
	ip := h.clientIP(r)
	if ip == "" {
		return true
	}
	allowed, err := h.RateLimiter.Allow(r.Context(), "ip:"+ip, h.ipRateLimit(), h.rateLimitWindow())
	if err != nil {
		h.logger().Error("ip rate limit check failed", "ip", ip, "err", err)
		http.Error(w, internalErrorMessage, http.StatusInternalServerError)
		return false
	}
	if !allowed {
		http.Error(w, "too many requests", http.StatusTooManyRequests)
		return false
	}
	return true
}

func (h *Handlers) enforceTelegramRateLimit(w http.ResponseWriter, r *http.Request, telegramID int64) bool {
	if h.RateLimiter == nil {
		return true
	}
	allowed, err := h.RateLimiter.Allow(
		r.Context(),
		"tg:"+strconv.FormatInt(telegramID, 10),
		h.telegramRateLimit(),
		h.rateLimitWindow(),
	)
	if err != nil {
		h.logger().Error("telegram rate limit check failed", "telegram_id", telegramID, "err", err)
		http.Error(w, internalErrorMessage, http.StatusInternalServerError)
		return false
	}
	if !allowed {
		http.Error(w, "too many requests", http.StatusTooManyRequests)
		return false
	}
	return true
}

func isValidNonce(nonce string) bool {
	if len(nonce) < 16 || len(nonce) > 128 {
		return false
	}
	for _, r := range nonce {
		if (r >= 'a' && r <= 'z') || (r >= 'A' && r <= 'Z') || (r >= '0' && r <= '9') || r == '-' || r == '_' {
			continue
		}
		return false
	}
	return true
}

func (h *Handlers) clientIP(r *http.Request) string {
	if r == nil {
		return ""
	}
	remoteIP := parseRemoteIP(r.RemoteAddr)
	if remoteIP == nil {
		return ""
	}
	// Trust forwarded headers only from configured reverse proxies.
	// Without trusted proxies we always use direct RemoteAddr and ignore XFF/X-Real-IP.
	if !h.isTrustedProxy(remoteIP) {
		return remoteIP.String()
	}
	if xffIP := h.clientIPFromXForwardedFor(r.Header.Get("X-Forwarded-For"), remoteIP); xffIP != nil {
		return xffIP.String()
	}
	if realIP := strings.TrimSpace(r.Header.Get("X-Real-IP")); realIP != "" {
		if ip := parseRemoteIP(realIP); ip != nil && !h.isTrustedProxy(ip) {
			return ip.String()
		}
	}
	return remoteIP.String()
}

// clientIPFromXForwardedFor returns the closest untrusted hop from XFF chain.
// This prevents spoofing of left-most XFF entries when trusted proxies append client IPs.
func (h *Handlers) clientIPFromXForwardedFor(raw string, remoteIP net.IP) net.IP {
	raw = strings.TrimSpace(raw)
	if raw == "" || remoteIP == nil {
		return nil
	}
	parts := strings.Split(raw, ",")
	chain := make([]net.IP, 0, len(parts)+1)
	for _, p := range parts {
		if ip := parseRemoteIP(p); ip != nil {
			chain = append(chain, ip)
		}
	}
	chain = append(chain, remoteIP)
	for i := len(chain) - 1; i >= 0; i-- {
		ip := chain[i]
		if h.isTrustedProxy(ip) {
			continue
		}
		return ip
	}
	return nil
}

func (h *Handlers) isTrustedProxy(ip net.IP) bool {
	if ip == nil {
		return false
	}
	for _, n := range h.TrustedProxyCIDRs {
		if n != nil && n.Contains(ip) {
			return true
		}
	}
	return false
}

func parseRemoteIP(raw string) net.IP {
	raw = strings.TrimSpace(raw)
	if raw == "" {
		return nil
	}
	if ip := net.ParseIP(raw); ip != nil {
		return ip
	}
	host, _, err := net.SplitHostPort(raw)
	if err != nil {
		return nil
	}
	host = strings.TrimSpace(host)
	if ip := net.ParseIP(host); ip != nil {
		return ip
	}
	return nil
}

func (h *Handlers) logger() *slog.Logger {
	if h != nil && h.Logger != nil {
		return h.Logger
	}
	return slog.Default()
}

func (h *Handlers) recordProductEvent(ctx context.Context, event port.ProductEvent) {
	if h.ProductEventRepo == nil {
		return
	}
	if err := h.ProductEventRepo.Record(ctx, event); err != nil {
		h.logger().Warn("record product event failed", "event_type", event.Type, "user_id", event.UserID, "job_id", event.JobID, "err", err)
	}
	// Записываем в Prometheus metrics
	if h.ProductMetrics != nil {
		h.ProductMetrics.RecordEvent(string(event.Type), event.Source)
	}
}

func enrichUserStats(stats *port.UserStats, user *domain.User) {
	if stats == nil {
		return
	}
	stats.Plan = currentPlanName(user, time.Now())
	stats.DailyCap = defaultFreePlanDailyCap
	if user != nil {
		stats.ProExpiresAt = user.ProExpiresAt
		stats.NotifyHour = user.NotifyHour
		if user.EffectiveIsPro(time.Now()) {
			stats.DailyCap = defaultProPlanDailyCap
		}
	}
}

func currentPlanName(user *domain.User, now time.Time) string {
	if user == nil {
		return "free"
	}
	if user.EffectiveIsPro(now) {
		return "pro"
	}
	if user.IsPro && user.ProExpiresAt != nil && !user.ProExpiresAt.After(now) {
		return "expired_pro"
	}
	return "free"
}

func hasNonEmptyProfileText(profileText *string) bool {
	if profileText == nil {
		return false
	}
	return strings.TrimSpace(*profileText) != ""
}

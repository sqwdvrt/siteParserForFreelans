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
	"log"
	"net"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/go-chi/chi/v5"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

const maxProfileTextLen = 5000
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

var errTelegramIDInvalid = errors.New("telegram_id must be positive integer")

// NonceStore — хранилище одноразовых nonce для anti-replay.
type NonceStore interface {
	Use(ctx context.Context, key string, ttl time.Duration) (bool, error)
}

// RequestRateLimiter — rate limiter для ключей (IP, telegram id).
type RequestRateLimiter interface {
	Allow(ctx context.Context, key string, limit int, window time.Duration) (bool, error)
}

// Handlers — HTTP handlers для API.
type Handlers struct {
	UserRepo          port.UserRepository
	UserEmbedQueue    port.UserEmbedQueue // nil — очередь не используется
	AuthToken         string              // обязательный bearer token для API
	UserHMACSecret    string              // обязательный секрет подписи user-level запросов
	NonceStore        NonceStore          // optional: anti-replay (nonce)
	RateLimiter       RequestRateLimiter  // optional: rate limit (per ip/per telegram id)
	TrustedProxyCIDRs []*net.IPNet        // optional: trusted reverse proxies for forwarded headers
	NonceTTL          time.Duration
	RateLimitWindow   time.Duration
	IPRateLimit       int
	TelegramRateLimit int
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
	userID, err := h.UserRepo.Save(r.Context(), int64(req.TelegramID))
	if err != nil {
		log.Printf("POST /users error: %v", err)
		http.Error(w, internalErrorMessage, http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	if err := json.NewEncoder(w).Encode(PostUsersResponse{UserID: userID}); err != nil {
		log.Printf("POST /users encode response: %v", err)
	}
}

// PutUserProfileRequest — тело PUT /users/:id/profile.
type PutUserProfileRequest struct {
	ProfileText string `json:"profile_text"`
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
		log.Printf("PUT /users/%d/profile get user: %v", userID, err)
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
	if err := h.UserRepo.UpdateProfile(r.Context(), userID, req.ProfileText); err != nil {
		log.Printf("PUT /users/%d/profile update: %v", userID, err)
		http.Error(w, internalErrorMessage, http.StatusInternalServerError)
		return
	}
	if h.UserEmbedQueue != nil {
		if err := h.UserEmbedQueue.Enqueue(r.Context(), userID); err != nil {
			log.Printf("PUT /users/%d/profile enqueue user-embed: %v", userID, err)
		}
	}
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
			log.Printf("nonce store error: %v", err)
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
		log.Printf("ip rate-limit error: %v", err)
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
		log.Printf("telegram rate-limit error: %v", err)
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

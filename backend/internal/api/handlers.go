package api

import (
	"encoding/json"
	"errors"
	"log"
	"net/http"
	"strconv"

	"github.com/go-chi/chi/v5"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

const maxProfileTextLen = 5000

var errTelegramIDInvalid = errors.New("telegram_id must be positive integer")

// Handlers — HTTP handlers для API.
type Handlers struct {
	UserRepo       port.UserRepository
	UserEmbedQueue port.UserEmbedQueue // nil — очередь не используется
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
	var req PostUsersRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid JSON", http.StatusBadRequest)
		return
	}
	userID, err := h.UserRepo.Save(r.Context(), int64(req.TelegramID))
	if err != nil {
		log.Printf("POST /users error: %v", err)
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(PostUsersResponse{UserID: userID})
}

// PutUserProfileRequest — тело PUT /users/:id/profile.
type PutUserProfileRequest struct {
	ProfileText string `json:"profile_text"`
}

// PutUserProfile обновляет profile_text пользователя.
func (h *Handlers) PutUserProfile(w http.ResponseWriter, r *http.Request) {
	idStr := chi.URLParam(r, "id")
	userID, err := strconv.ParseInt(idStr, 10, 64)
	if err != nil || userID <= 0 {
		http.Error(w, "invalid user id", http.StatusBadRequest)
		return
	}
	var req PutUserProfileRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "invalid JSON", http.StatusBadRequest)
		return
	}
	if len(req.ProfileText) > maxProfileTextLen {
		http.Error(w, "profile_text too long", http.StatusBadRequest)
		return
	}
	if err := h.UserRepo.UpdateProfile(r.Context(), userID, req.ProfileText); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
		return
	}
	if h.UserEmbedQueue != nil {
		_ = h.UserEmbedQueue.Enqueue(r.Context(), userID)
	}
	w.WriteHeader(http.StatusNoContent)
}

package telegram

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	redisclient "github.com/redis/go-redis/v9"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

const (
	defaultBatchSessionPrefix = "telegram-bot"
	defaultBatchSessionTTL    = 24 * time.Hour
	batchSessionVersion       = 1
)

type batchSessionStore interface {
	Set(ctx context.Context, key string, value []byte, ttl time.Duration) error
}

type redisBatchSessionStore struct {
	client *redisclient.Client
}

func NewRedisBatchSessionStore(client *redisclient.Client) batchSessionStore {
	if client == nil {
		return nil
	}
	return &redisBatchSessionStore{client: client}
}

func (s *redisBatchSessionStore) Set(ctx context.Context, key string, value []byte, ttl time.Duration) error {
	if s == nil || s.client == nil {
		return fmt.Errorf("batch session store is nil")
	}
	return s.client.Set(ctx, key, value, ttl).Err()
}

type batchSession struct {
	Version      int                `json:"version"`
	TelegramID   int64              `json:"telegram_id"`
	CurrentIndex int                `json:"current_index"`
	Items        []batchSessionItem `json:"items"`
}

type batchSessionItem struct {
	JobID         int64   `json:"job_id"`
	Title         string  `json:"title"`
	Description   string  `json:"description"`
	Budget        string  `json:"budget"`
	URL           string  `json:"url"`
	WhyItFits     string  `json:"why_it_fits"`
	ScorePercent  float64 `json:"score_percent"`
	PostedAtUnix  int64   `json:"posted_at_unix"`
	CreatedAtUnix int64   `json:"created_at_unix"`
}

type batchSessionState struct {
	ID           string
	CurrentIndex int
	Total        int
	CurrentItem  batchSessionItem
	Prefix       string
	StoredKey    string
}

func normalizeBatchSessionPrefix(prefix string) string {
	prefix = strings.TrimSpace(prefix)
	if prefix == "" {
		return defaultBatchSessionPrefix
	}
	return prefix
}

func newBatchSessionID() (string, error) {
	var raw [6]byte
	if _, err := rand.Read(raw[:]); err != nil {
		return "", err
	}
	return hex.EncodeToString(raw[:]), nil
}

func buildBatchSession(telegramID int64, items []port.BatchNotifyItem) batchSession {
	sessionItems := make([]batchSessionItem, 0, len(items))
	for _, item := range items {
		if item.Job == nil {
			continue
		}
		sessionItems = append(sessionItems, batchSessionItem{
			JobID:         item.Job.ID,
			Title:         item.Job.Title,
			Description:   batchItemDescription(item),
			Budget:        item.Job.Budget,
			URL:           item.Job.URL,
			WhyItFits:     item.WhyItFits,
			ScorePercent:  scorePercent(item.FinalScore),
			PostedAtUnix:  timeToUnix(item.Job.PostedAt),
			CreatedAtUnix: timeValueToUnix(item.Job.CreatedAt),
		})
	}
	return batchSession{
		Version:      batchSessionVersion,
		TelegramID:   telegramID,
		CurrentIndex: 0,
		Items:        sessionItems,
	}
}

func scorePercent(score float64) float64 {
	if score < 0 {
		return 0
	}
	if score <= 1 {
		return score * 100
	}
	return score
}

func timeToUnix(ts *time.Time) int64 {
	if ts == nil || ts.IsZero() {
		return 0
	}
	return ts.Unix()
}

func timeValueToUnix(ts time.Time) int64 {
	if ts.IsZero() {
		return 0
	}
	return ts.Unix()
}

func batchItemDescription(item port.BatchNotifyItem) string {
	if item.Job == nil {
		return ""
	}
	if desc := strings.TrimSpace(item.Job.Description); desc != "" {
		return desc
	}
	return strings.TrimSpace(item.Job.RawHTML)
}

func (n *Notifier) configureBatchSessionStore(store batchSessionStore, prefix string) {
	n.batchSessionStore = store
	n.batchSessionPrefix = normalizeBatchSessionPrefix(prefix)
}

func (n *Notifier) storeBatchSession(ctx context.Context, telegramID int64, items []port.BatchNotifyItem) (*batchSessionState, error) {
	if n.batchSessionStore == nil {
		return nil, nil
	}
	sessionID, err := newBatchSessionID()
	if err != nil {
		return nil, fmt.Errorf("generate batch session id: %w", err)
	}
	session := buildBatchSession(telegramID, items)
	raw, err := json.Marshal(session)
	if err != nil {
		return nil, fmt.Errorf("marshal batch session: %w", err)
	}
	prefix := normalizeBatchSessionPrefix(n.batchSessionPrefix)
	key := fmt.Sprintf("%s:batch-session:%s", prefix, sessionID)
	ttl := n.batchSessionTTL
	if ttl <= 0 {
		ttl = defaultBatchSessionTTL
	}
	if err := n.batchSessionStore.Set(ctx, key, raw, ttl); err != nil {
		return nil, fmt.Errorf("store batch session: %w", err)
	}
	current := batchSessionItem{}
	if len(session.Items) > 0 {
		current = session.Items[0]
	}
	return &batchSessionState{
		ID:           sessionID,
		CurrentIndex: session.CurrentIndex,
		Total:        len(session.Items),
		CurrentItem:  current,
		Prefix:       prefix,
		StoredKey:    key,
	}, nil
}

func buildBatchNavigationKeyboard(state *batchSessionState) [][]map[string]interface{} {
	if state == nil || state.Total <= 0 {
		return nil
	}
	currentIndex := state.CurrentIndex
	if currentIndex < 0 {
		currentIndex = 0
	}
	if currentIndex >= state.Total {
		currentIndex = state.Total - 1
	}
	prevIndex := currentIndex - 1
	if prevIndex < 0 {
		prevIndex = 0
	}
	nextIndex := currentIndex + 1
	if nextIndex >= state.Total {
		nextIndex = state.Total - 1
	}
	currentLabel := fmt.Sprintf("%d/%d", currentIndex+1, state.Total)
	row1 := []map[string]interface{}{
		{"text": "←", "callback_data": fmt.Sprintf("nav:p:%s:%d", state.ID, prevIndex)},
		{"text": currentLabel, "callback_data": fmt.Sprintf("nav:i:%s:%d", state.ID, currentIndex)},
		{"text": "→", "callback_data": fmt.Sprintf("nav:n:%s:%d", state.ID, nextIndex)},
	}
	row2 := []map[string]interface{}{
		{"text": "👍", "callback_data": fmt.Sprintf("fb:g:%s:%d:%d", state.ID, currentIndex, state.CurrentItem.JobID)},
		{"text": "👎", "callback_data": fmt.Sprintf("fb:b:%s:%d:%d", state.ID, currentIndex, state.CurrentItem.JobID)},
	}
	rows := [][]map[string]interface{}{row1, row2}
	if isValidJobURL(state.CurrentItem.URL) {
		row3 := []map[string]interface{}{
			{"text": "Открыть проект", "url": state.CurrentItem.URL},
		}
		rows = append(rows, row3)
	}
	return rows
}

package api

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"time"
)

const defaultAIDebugMatchBaseURL = "http://ai-service:8093"

type HTTPAdminDebugMatchClient struct {
	BaseURL string
	Client  *http.Client
}

func (c *HTTPAdminDebugMatchClient) GetMatchDebug(ctx context.Context, userID int64, jobURL string) (map[string]any, error) {
	baseURL := strings.TrimSpace(c.BaseURL)
	if baseURL == "" {
		baseURL = defaultAIDebugMatchBaseURL
	}
	client := c.Client
	if client == nil {
		client = &http.Client{Timeout: 15 * time.Second}
	}
	endpoint := fmt.Sprintf(
		"%s/internal/debug/match?user_id=%d&job_url=%s",
		strings.TrimRight(baseURL, "/"),
		userID,
		url.QueryEscape(jobURL),
	)
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return nil, err
	}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer func() {
		_ = resp.Body.Close()
	}()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("upstream status=%d", resp.StatusCode)
	}
	var payload map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&payload); err != nil {
		return nil, err
	}
	return payload, nil
}

package api

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
)

func loadReleaseSmokeProfilePayload(t *testing.T) string {
	t.Helper()

	payloadPath := filepath.Join("..", "..", "..", "scripts", "testdata", "release_smoke_profile.txt")
	raw, err := os.ReadFile(payloadPath)
	if err != nil {
		t.Fatalf("read release smoke profile payload: %v", err)
	}
	payload := strings.TrimSpace(string(raw))
	if payload == "" {
		t.Fatal("release smoke profile payload is empty")
	}
	return payload
}

func TestReleaseSmokeProfilePayload_MatchesCurrentAPIContract(t *testing.T) {
	profileText := loadReleaseSmokeProfilePayload(t)

	if err := validateProfileText(profileText); err != nil {
		t.Fatalf("release smoke profile payload rejected by validateProfileText: %v", err)
	}
}

func TestReleaseSmokeProfilePayload_PutUserProfileAcceptsSharedFixture(t *testing.T) {
	profileText := loadReleaseSmokeProfilePayload(t)

	var updatedProfile string
	var flushed bool
	repo := &mockUserRepo{
		getByIDFunc: func(ctx context.Context, userID int64) (*domain.User, error) {
			return &domain.User{ID: userID, TelegramID: 123456789}, nil
		},
		updateProfileAndStage: func(ctx context.Context, userID int64, text string) error {
			updatedProfile = text
			return nil
		},
	}
	h := &Handlers{
		UserRepo:              repo,
		UserEmbedDispatchRepo: repo,
		UserEmbedDispatcher: &mockUserEmbedDispatcher{
			flushFunc: func(ctx context.Context, limit int) (int, error) {
				flushed = true
				return 1, nil
			},
		},
		AuthToken:      testAuthToken,
		UserHMACSecret: testUserHMACSecret,
	}

	body, err := json.Marshal(map[string]string{"profile_text": profileText})
	if err != nil {
		t.Fatalf("marshal body: %v", err)
	}
	req := newJSONRequest(http.MethodPut, "/users/1/profile", body, newAuthHeadersWithUserSign(http.MethodPut, "/users/1/profile", 123456789, body))
	req = attachRouteUserID(req, "1")
	rr := httptest.NewRecorder()

	h.PutUserProfile(rr, req)

	if rr.Code != http.StatusNoContent {
		t.Fatalf("status = %d, want 204, body=%q", rr.Code, rr.Body.String())
	}
	if updatedProfile != profileText {
		t.Fatalf("updatedProfile = %q, want %q", updatedProfile, profileText)
	}
	if !flushed {
		t.Fatal("expected best-effort flush to run")
	}
}

func TestReleaseSmokeScripts_UseSharedProfilePayloadFixture(t *testing.T) {
	scriptPaths := []string{
		filepath.Join("..", "..", "..", "scripts", "e2e_test.sh"),
		filepath.Join("..", "..", "..", "scripts", "staging_smoke_e2e_gate.sh"),
	}

	for _, scriptPath := range scriptPaths {
		raw, err := os.ReadFile(scriptPath)
		if err != nil {
			t.Fatalf("read script %s: %v", scriptPath, err)
		}
		content := string(raw)
		if !strings.Contains(content, "release_smoke_payloads.sh") {
			t.Fatalf("%s does not source release_smoke_payloads.sh", scriptPath)
		}
		if !strings.Contains(content, "load_release_smoke_profile_text") {
			t.Fatalf("%s does not load the shared release smoke profile payload", scriptPath)
		}
	}
}

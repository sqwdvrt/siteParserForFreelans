//go:build integration
// +build integration

package postgres

import (
	"context"
	"testing"
	"time"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

func TestDispatchRepository_SaveAndStageJobForEmbedding_UpdatesExistingJobFields(t *testing.T) {
	pool := setupTestDB(t)
	repo := NewDispatchRepository(pool)
	ctx := context.Background()

	url := "https://kwork.ru/projects/dispatch-update-" + time.Now().Format("20060102150405.000000000") + "/view"
	initialPostedAt := time.Date(2026, 3, 21, 8, 0, 0, 0, time.UTC)
	updatedPostedAt := initialPostedAt.Add(6 * time.Hour)
	initial := &domain.Job{
		Source:      "kwork",
		URL:         url,
		ExternalID:  "old-123",
		Title:       "Old title",
		Description: "Old description",
		Budget:      "1000 руб",
		Skills:      []string{"Go"},
		PostedAt:    &initialPostedAt,
		RawHTML:     "<html>old</html>",
	}
	trace1 := port.QueueDispatchTrace{TraceID: "trace-old", Traceparent: "parent-old"}

	id, inserted, err := repo.SaveAndStageJobForEmbedding(ctx, initial, trace1)
	if err != nil {
		t.Fatalf("SaveAndStageJobForEmbedding initial: %v", err)
	}
	if !inserted {
		t.Fatalf("expected first save to insert new job")
	}

	updated := &domain.Job{
		Source:      "kwork",
		URL:         url,
		ExternalID:  "new-456",
		Title:       "New title",
		Description: "New description",
		Budget:      "2500 руб",
		Skills:      []string{"Python", "PostgreSQL"},
		PostedAt:    &updatedPostedAt,
		RawHTML:     "<html>new</html>",
	}
	trace2 := port.QueueDispatchTrace{TraceID: "trace-new", Traceparent: "parent-new"}

	gotID, inserted, err := repo.SaveAndStageJobForEmbedding(ctx, updated, trace2)
	if err != nil {
		t.Fatalf("SaveAndStageJobForEmbedding update: %v", err)
	}
	if inserted {
		t.Fatalf("expected second save to reuse existing job")
	}
	if gotID != id {
		t.Fatalf("expected same job id %d, got %d", id, gotID)
	}

	var (
		source      string
		externalID  string
		title       string
		description string
		budget      string
		skills      []string
		postedAt    *time.Time
		rawHTML     string
		status      string
		traceID     string
		traceparent string
	)
	err = pool.QueryRow(ctx, `
		SELECT j.source,
		       COALESCE(j.external_id, ''),
		       j.title,
		       COALESCE(j.description, ''),
		       COALESCE(j.budget, ''),
		       j.skills,
		       j.posted_at,
		       j.raw_html,
		       j.status,
		       COALESCE(p.trace_id, ''),
		       COALESCE(p.traceparent, '')
		FROM jobs j
		LEFT JOIN pending_job_embeds p ON p.job_id = j.id
		WHERE j.id = $1
	`, id).Scan(
		&source,
		&externalID,
		&title,
		&description,
		&budget,
		&skills,
		&postedAt,
		&rawHTML,
		&status,
		&traceID,
		&traceparent,
	)
	if err != nil {
		t.Fatalf("query updated job: %v", err)
	}

	if source != updated.Source {
		t.Fatalf("source = %q, want %q", source, updated.Source)
	}
	if externalID != updated.ExternalID {
		t.Fatalf("external_id = %q, want %q", externalID, updated.ExternalID)
	}
	if title != updated.Title {
		t.Fatalf("title = %q, want %q", title, updated.Title)
	}
	if description != updated.Description {
		t.Fatalf("description = %q, want %q", description, updated.Description)
	}
	if budget != updated.Budget {
		t.Fatalf("budget = %q, want %q", budget, updated.Budget)
	}
	if len(skills) != len(updated.Skills) || skills[0] != updated.Skills[0] || skills[1] != updated.Skills[1] {
		t.Fatalf("skills = %#v, want %#v", skills, updated.Skills)
	}
	if postedAt == nil || !postedAt.Equal(updatedPostedAt) {
		t.Fatalf("posted_at = %v, want %v", postedAt, updatedPostedAt)
	}
	if rawHTML != updated.RawHTML {
		t.Fatalf("raw_html = %q, want %q", rawHTML, updated.RawHTML)
	}
	if status != "active" {
		t.Fatalf("status = %q, want active", status)
	}
	if traceID != trace2.TraceID {
		t.Fatalf("trace_id = %q, want %q", traceID, trace2.TraceID)
	}
	if traceparent != trace2.Traceparent {
		t.Fatalf("traceparent = %q, want %q", traceparent, trace2.Traceparent)
	}
}

package usecase_test

import (
	"testing"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/usecase"
)

func TestValidateJob_NilJob(t *testing.T) {
	errs := usecase.ValidateJob(nil)
	if len(errs) == 0 {
		t.Fatal("expected validation errors for nil job")
	}
}

func TestValidateJob_ValidJob(t *testing.T) {
	job := &domain.Job{
		Source:      "kwork",
		URL:         "https://kwork.ru/projects/123",
		Title:       "Разработка веб-приложения на Python",
		Description: "Нужен опытный разработчик для создания CRM системы",
		RawHTML:     "<html><body>test</body></html>",
	}
	errs := usecase.ValidateJob(job)
	if len(errs) > 0 {
		t.Fatalf("expected no errors for valid job, got: %v", errs)
	}
}

func TestValidateJob_EmptyTitle(t *testing.T) {
	job := &domain.Job{
		Source:  "kwork",
		URL:     "https://kwork.ru/projects/123",
		Title:   "",
		RawHTML: "<html>test</html>",
	}
	errs := usecase.ValidateJob(job)
	found := false
	for _, e := range errs {
		if containsStr(e, "title") {
			found = true
			break
		}
	}
	if !found {
		t.Fatalf("expected title validation error, got: %v", errs)
	}
}

func TestValidateJob_ShortTitle(t *testing.T) {
	job := &domain.Job{
		Source:  "kwork",
		URL:     "https://kwork.ru/projects/123",
		Title:   "Hi",
		RawHTML: "<html>test</html>",
	}
	errs := usecase.ValidateJob(job)
	found := false
	for _, e := range errs {
		if containsStr(e, "too short") {
			found = true
			break
		}
	}
	if !found {
		t.Fatalf("expected 'too short' title error, got: %v", errs)
	}
}

func TestValidateJob_NoRawHTML(t *testing.T) {
	job := &domain.Job{
		Source:      "kwork",
		URL:         "https://kwork.ru/projects/123",
		Title:       "Valid title here",
		Description: "Some description",
		RawHTML:     "",
	}
	errs := usecase.ValidateJob(job)
	found := false
	for _, e := range errs {
		if containsStr(e, "raw_html") {
			found = true
			break
		}
	}
	if !found {
		t.Fatalf("expected raw_html validation error, got: %v", errs)
	}
}

func TestValidateJob_NoSource(t *testing.T) {
	job := &domain.Job{
		URL:     "https://kwork.ru/projects/123",
		Title:   "Valid title here",
		RawHTML: "<html>test</html>",
	}
	errs := usecase.ValidateJob(job)
	found := false
	for _, e := range errs {
		if containsStr(e, "source") {
			found = true
			break
		}
	}
	if !found {
		t.Fatalf("expected source validation error, got: %v", errs)
	}
}

func TestValidateJob_NoURL(t *testing.T) {
	job := &domain.Job{
		Source:  "kwork",
		Title:   "Valid title here",
		RawHTML: "<html>test</html>",
	}
	errs := usecase.ValidateJob(job)
	found := false
	for _, e := range errs {
		if containsStr(e, "url") {
			found = true
			break
		}
	}
	if !found {
		t.Fatalf("expected url validation error, got: %v", errs)
	}
}

func TestValidationFailureRate(t *testing.T) {
	rate := &usecase.ValidationFailureRate{Total: 100, Rejected: 30}
	if got := rate.Rate(); got != 0.3 {
		t.Fatalf("expected 0.3, got %f", got)
	}

	emptyRate := &usecase.ValidationFailureRate{}
	if got := emptyRate.Rate(); got != 0.0 {
		t.Fatalf("expected 0.0 for empty rate, got %f", got)
	}
}

func containsStr(s, substr string) bool {
	return len(s) >= len(substr) && (s == substr || len(s) > 0 && containsStrHelper(s, substr))
}

func containsStrHelper(s, substr string) bool {
	for i := 0; i <= len(s)-len(substr); i++ {
		if s[i:i+len(substr)] == substr {
			return true
		}
	}
	return false
}

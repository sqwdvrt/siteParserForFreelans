package weblancer

import "testing"

func TestExtractList(t *testing.T) {
	ext := NewExtractor()
	html := []byte(`
		<html><body>
			<a href="/jobs/web-development/123-api-task/?utm=1">one</a>
			<a href="https://www.weblancer.net/jobs/web-development/123-api-task/?ref=dup">dup</a>
			<a href="https://jobs.weblancer.net/jobs/parsing/456-parser-task/">two</a>
			<a href="https://evil.example/jobs/web-development/999-bad/">bad-host</a>
			<a href="/jobs/">list</a>
		</body></html>
	`)

	urls, err := ext.ExtractList(html)
	if err != nil {
		t.Fatalf("ExtractList: %v", err)
	}

	want := []string{
		"https://www.weblancer.net/jobs/web-development/123-api-task/",
		"https://jobs.weblancer.net/jobs/parsing/456-parser-task/",
	}
	if len(urls) != len(want) {
		t.Fatalf("len(urls)=%d want=%d urls=%v", len(urls), len(want), urls)
	}
	for i := range want {
		if urls[i] != want[i] {
			t.Fatalf("urls[%d]=%q want=%q", i, urls[i], want[i])
		}
	}
}

func TestExtractDetail(t *testing.T) {
	ext := NewExtractor()
	job, err := ext.ExtractDetail([]byte(`
		<html><body>
			<h1 class="page-h">Сделать интеграцию каталога</h1>
			<div class="descr">Нужен backend на Go с очередями и PostgreSQL.</div>
			<div class="cost">€700</div>
			<div class="tags"><a>Go</a><a>Queues</a></div>
			<time datetime="2026-03-04T11:22:33"></time>
		</body></html>
	`), "https://www.weblancer.net/jobs/web-development/123-api-task/")
	if err != nil {
		t.Fatalf("ExtractDetail: %v", err)
	}
	if job == nil {
		t.Fatal("job is nil")
	}
	if job.Source != "weblancer" {
		t.Fatalf("Source=%q", job.Source)
	}
	if job.ExternalID != "123" {
		t.Fatalf("ExternalID=%q", job.ExternalID)
	}
	if job.Title != "Сделать интеграцию каталога" {
		t.Fatalf("Title=%q", job.Title)
	}
	if job.Description != "Нужен backend на Go с очередями и PostgreSQL." {
		t.Fatalf("Description=%q", job.Description)
	}
	if job.Budget != "€700" {
		t.Fatalf("Budget=%q", job.Budget)
	}
	if len(job.Skills) != 2 || job.Skills[0] != "Go" || job.Skills[1] != "Queues" {
		t.Fatalf("Skills=%v", job.Skills)
	}
	if job.PostedAt == nil || job.PostedAt.Format("2006-01-02T15:04:05") != "2026-03-04T11:22:33" {
		t.Fatalf("PostedAt=%v", job.PostedAt)
	}
}

func TestExtractorHelpers(t *testing.T) {
	if got, err := resolveURL("/jobs/web-development/321-test/?x=1"); err != nil || got != "https://www.weblancer.net/jobs/web-development/321-test/?x=1" {
		t.Fatalf("resolveURL relative: got=%q err=%v", got, err)
	}
	if _, err := resolveURL("https://evil.example/jobs/web-development/321-test/"); err == nil {
		t.Fatal("resolveURL external host: want error")
	}
	if got := stripQuery("https://www.weblancer.net/jobs/web-development/321-test/?x=1#frag"); got != "https://www.weblancer.net/jobs/web-development/321-test/" {
		t.Fatalf("stripQuery=%q", got)
	}
	if got := stripQuery("://bad-url"); got != "://bad-url" {
		t.Fatalf("stripQuery invalid=%q", got)
	}
	if !isAllowedHost("jobs.weblancer.net.") || isAllowedHost("evil.example") {
		t.Fatal("isAllowedHost mismatch")
	}
	if got := extractExternalID("https://www.weblancer.net/jobs/web-development/321-test/"); got != "321" {
		t.Fatalf("extractExternalID=%q", got)
	}
	if got := extractExternalID("https://www.weblancer.net/jobs/web-development/test/"); got != "" {
		t.Fatalf("extractExternalID invalid=%q", got)
	}
	if ts := parseDateTime("2026-03-05"); ts == nil || ts.Format("2006-01-02") != "2026-03-05" {
		t.Fatalf("parseDateTime=%v", ts)
	}
	if ts := parseDateTime("not-a-date"); ts != nil {
		t.Fatalf("parseDateTime invalid=%v", ts)
	}
}

func TestExtractDetail_DefaultTitle(t *testing.T) {
	ext := NewExtractor()
	job, err := ext.ExtractDetail([]byte(`<div class="task__description">Поддержка проекта.</div>`), "https://www.weblancer.net/jobs/web-development/777-test/")
	if err != nil {
		t.Fatalf("ExtractDetail: %v", err)
	}
	if job == nil {
		t.Fatal("job is nil")
	}
	if job.Title != "(без названия)" {
		t.Fatalf("Title=%q", job.Title)
	}
}

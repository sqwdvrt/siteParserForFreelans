package freelancehunt

import "testing"

func TestExtractList(t *testing.T) {
	ext := NewExtractor()
	html := []byte(`
		<html><body>
			<a href="/project/123/backend-api.html?utm=1">one</a>
			<a href="https://freelancehunt.com/project/123/backend-api.html?ref=dup">dup</a>
			<a href="https://jobs.freelancehunt.com/project/456/parser.html">two</a>
			<a href="https://evil.example/project/777/nope.html">bad-host</a>
			<a href="/projects/">list</a>
		</body></html>
	`)

	urls, err := ext.ExtractList(html)
	if err != nil {
		t.Fatalf("ExtractList: %v", err)
	}

	want := []string{
		"https://freelancehunt.com/project/123/backend-api.html",
		"https://jobs.freelancehunt.com/project/456/parser.html",
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
			<h1 itemprop="name">Подобрать подрядчика на интеграцию</h1>
			<div itemprop="description">Нужен сервис на Go и PostgreSQL.</div>
			<div class="budget"><strong>$500</strong></div>
			<ul class="tags-list"><li><a>Go</a></li><li><a>Redis</a></li></ul>
			<time itemprop="datePosted" datetime="2026-03-03"></time>
		</body></html>
	`), "https://freelancehunt.com/project/123/backend-api.html")
	if err != nil {
		t.Fatalf("ExtractDetail: %v", err)
	}
	if job == nil {
		t.Fatal("job is nil")
	}
	if job.Source != "freelancehunt" {
		t.Fatalf("Source=%q", job.Source)
	}
	if job.ExternalID != "123" {
		t.Fatalf("ExternalID=%q", job.ExternalID)
	}
	if job.Title != "Подобрать подрядчика на интеграцию" {
		t.Fatalf("Title=%q", job.Title)
	}
	if job.Description != "Нужен сервис на Go и PostgreSQL." {
		t.Fatalf("Description=%q", job.Description)
	}
	if job.Budget != "$500" {
		t.Fatalf("Budget=%q", job.Budget)
	}
	if len(job.Skills) != 2 || job.Skills[0] != "Go" || job.Skills[1] != "Redis" {
		t.Fatalf("Skills=%v", job.Skills)
	}
	if job.PostedAt == nil || job.PostedAt.Format("2006-01-02") != "2026-03-03" {
		t.Fatalf("PostedAt=%v", job.PostedAt)
	}
}

func TestExtractorHelpers(t *testing.T) {
	if got, err := resolveURL("/project/321/test.html?x=1"); err != nil || got != "https://freelancehunt.com/project/321/test.html?x=1" {
		t.Fatalf("resolveURL relative: got=%q err=%v", got, err)
	}
	if _, err := resolveURL("https://evil.example/project/321/test.html"); err == nil {
		t.Fatal("resolveURL external host: want error")
	}
	if got := stripQuery("https://freelancehunt.com/project/321/test.html?x=1#frag"); got != "https://freelancehunt.com/project/321/test.html" {
		t.Fatalf("stripQuery=%q", got)
	}
	if got := stripQuery("://bad-url"); got != "://bad-url" {
		t.Fatalf("stripQuery invalid=%q", got)
	}
	if !isAllowedHost("jobs.freelancehunt.com.") || isAllowedHost("evil.example") {
		t.Fatal("isAllowedHost mismatch")
	}
	if got := extractExternalID("https://freelancehunt.com/project/321/test.html"); got != "321" {
		t.Fatalf("extractExternalID=%q", got)
	}
	if got := extractExternalID("https://freelancehunt.com/project/test.html"); got != "" {
		t.Fatalf("extractExternalID invalid=%q", got)
	}
	if ts := parseDateTime("2.3.2026"); ts == nil || ts.Format("2006-01-02") != "2026-03-02" {
		t.Fatalf("parseDateTime=%v", ts)
	}
	if ts := parseDateTime("not-a-date"); ts != nil {
		t.Fatalf("parseDateTime invalid=%v", ts)
	}
}

func TestExtractDetail_DefaultTitle(t *testing.T) {
	ext := NewExtractor()
	job, err := ext.ExtractDetail([]byte(`<div class="project-description">Поддержка проекта.</div>`), "https://freelancehunt.com/project/777/test.html")
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

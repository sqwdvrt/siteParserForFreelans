package flru

import "testing"

func TestExtractList(t *testing.T) {
	ext := NewExtractor()
	html := []byte(`
		<html><body>
			<a href="/projects/123/task/?utm=1#top">one</a>
			<a href="https://www.fl.ru/projects/123/task/?ref=dup">dup</a>
			<a href="https://sub.fl.ru/projects/456/spec/">two</a>
			<a href="https://evil.example/projects/789/task/">bad-host</a>
			<a href="/projects/">list</a>
		</body></html>
	`)

	urls, err := ext.ExtractList(html)
	if err != nil {
		t.Fatalf("ExtractList: %v", err)
	}

	want := []string{
		"https://www.fl.ru/projects/123/task/",
		"https://sub.fl.ru/projects/456/spec/",
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
			<h1 class="project-name">Сделать API для каталога</h1>
			<div class="project-description">Нужен Go backend с PostgreSQL и Redis.</div>
			<div class="project-price">120 000 руб.</div>
			<ul class="project-tags"><li>Go</li><li>PostgreSQL</li></ul>
			<time itemprop="datePosted" datetime="2026-03-01T10:20:30"></time>
		</body></html>
	`), "https://www.fl.ru/projects/123/task/")
	if err != nil {
		t.Fatalf("ExtractDetail: %v", err)
	}
	if job == nil {
		t.Fatal("job is nil")
	}
	if job.Source != "flru" {
		t.Fatalf("Source=%q want=flru", job.Source)
	}
	if job.ExternalID != "123" {
		t.Fatalf("ExternalID=%q want=123", job.ExternalID)
	}
	if job.Title != "Сделать API для каталога" {
		t.Fatalf("Title=%q", job.Title)
	}
	if job.Description != "Нужен Go backend с PostgreSQL и Redis." {
		t.Fatalf("Description=%q", job.Description)
	}
	if job.Budget != "120 000 руб." {
		t.Fatalf("Budget=%q", job.Budget)
	}
	if len(job.Skills) != 2 || job.Skills[0] != "Go" || job.Skills[1] != "PostgreSQL" {
		t.Fatalf("Skills=%v", job.Skills)
	}
	if job.PostedAt == nil || job.PostedAt.Format("2006-01-02T15:04:05") != "2026-03-01T10:20:30" {
		t.Fatalf("PostedAt=%v", job.PostedAt)
	}
}

func TestExtractorHelpers(t *testing.T) {
	if got, err := resolveURL("/projects/321/task/?x=1"); err != nil || got != "https://www.fl.ru/projects/321/task/?x=1" {
		t.Fatalf("resolveURL relative: got=%q err=%v", got, err)
	}
	if _, err := resolveURL("https://evil.example/projects/321/task/"); err == nil {
		t.Fatal("resolveURL external host: want error")
	}
	if got := stripQuery("https://www.fl.ru/projects/321/task/?x=1#frag"); got != "https://www.fl.ru/projects/321/task/" {
		t.Fatalf("stripQuery=%q", got)
	}
	if got := stripQuery("://bad-url"); got != "://bad-url" {
		t.Fatalf("stripQuery invalid=%q", got)
	}
	if !isAllowedHost("sub.fl.ru.") || isAllowedHost("evil.example") {
		t.Fatal("isAllowedHost mismatch")
	}
	if got := extractExternalID("https://www.fl.ru/projects/321/task/"); got != "321" {
		t.Fatalf("extractExternalID=%q", got)
	}
	if got := extractExternalID("https://www.fl.ru/projects/task/"); got != "" {
		t.Fatalf("extractExternalID invalid=%q", got)
	}
	if ts := parseDateTime("02.03.2026"); ts == nil || ts.Format("2006-01-02") != "2026-03-02" {
		t.Fatalf("parseDateTime=%v", ts)
	}
	if ts := parseDateTime("not-a-date"); ts != nil {
		t.Fatalf("parseDateTime invalid=%v", ts)
	}
}

func TestExtractDetail_DefaultTitle(t *testing.T) {
	ext := NewExtractor()
	job, err := ext.ExtractDetail([]byte(`<div class="posting-description">Поддержка существующего сервиса.</div>`), "https://www.fl.ru/projects/777/task/")
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

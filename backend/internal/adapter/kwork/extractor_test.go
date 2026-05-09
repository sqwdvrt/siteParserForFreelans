package kwork

import (
	"os"
	"strings"
	"testing"
)

func TestExtractList(t *testing.T) {
	html, err := os.ReadFile("../../../testdata/kwork_list.html")
	if err != nil {
		t.Fatalf("read fixture: %v", err)
	}
	ext := NewExtractor()
	urls, err := ext.ExtractList(html)
	if err != nil {
		t.Fatalf("ExtractList: %v", err)
	}
	if len(urls) != 3 {
		t.Errorf("want 3 URLs, got %d: %v", len(urls), urls)
	}
	expected := []string{
		"https://kwork.ru/projects/12345",
		"https://kwork.ru/projects/67890",
		"https://kwork.ru/projects/11111",
	}
	for i, u := range urls {
		if i >= len(expected) {
			break
		}
		if u != expected[i] {
			t.Errorf("urls[%d]: want %q, got %q", i, expected[i], u)
		}
	}
}

func TestExtractList_EmptyHTML(t *testing.T) {
	ext := NewExtractor()
	urls, err := ext.ExtractList([]byte{})
	if err != nil {
		t.Fatalf("ExtractList: %v", err)
	}
	if urls != nil {
		t.Errorf("want nil, got %v", urls)
	}
}

func TestExtractList_BrokenHTML(t *testing.T) {
	ext := NewExtractor()
	urls, err := ext.ExtractList([]byte("<html><body>broken"))
	if err != nil {
		t.Fatalf("ExtractList: %v", err)
	}
	if urls == nil {
		t.Log("broken HTML: returned nil (acceptable)")
	}
}

func TestExtractList_SkipsExternalAbsoluteURLs(t *testing.T) {
	html := []byte(`
		<html><body>
			<a href="https://evil.example/projects/123/view">bad</a>
			<a href="https://kwork.ru/projects/456/view">ok-abs</a>
			<a href="/projects/789/view">ok-rel</a>
		</body></html>
	`)
	ext := NewExtractor()
	urls, err := ext.ExtractList(html)
	if err != nil {
		t.Fatalf("ExtractList: %v", err)
	}
	if len(urls) != 2 {
		t.Fatalf("want 2 kwork URLs, got %d: %v", len(urls), urls)
	}
	if urls[0] != "https://kwork.ru/projects/456/view" {
		t.Errorf("urls[0]: got %q", urls[0])
	}
	if urls[1] != "https://kwork.ru/projects/789/view" {
		t.Errorf("urls[1]: got %q", urls[1])
	}
	// Убеждаемся что /projects/list/... отфильтрован
	for _, u := range urls {
		if strings.Contains(u, "/list/") {
			t.Errorf("list URL должен быть отфильтрован: %q", u)
		}
	}
}

func TestExtractDetail(t *testing.T) {
	html, err := os.ReadFile("../../../testdata/kwork_detail.html")
	if err != nil {
		t.Fatalf("read fixture: %v", err)
	}
	ext := NewExtractor()
	job, err := ext.ExtractDetail(html, "https://kwork.ru/projects/12345/view")
	if err != nil {
		t.Fatalf("ExtractDetail: %v", err)
	}
	if job == nil {
		t.Fatal("want job, got nil")
	}
	if job.Title != "Нужен парсер для сайта" {
		t.Errorf("title: want %q, got %q", "Нужен парсер для сайта", job.Title)
	}
	if job.Description != "Требуется написать парсер на Python для сбора данных." {
		t.Errorf("description: want %q, got %q", "Требуется написать парсер на Python для сбора данных.", job.Description)
	}
	if job.Budget == "" {
		t.Errorf("budget: want non-empty, got %q", job.Budget)
	}
	if job.ExternalID != "12345" {
		t.Errorf("external_id: want 12345, got %q", job.ExternalID)
	}
}

func TestExtractDetail_EmptyHTML(t *testing.T) {
	ext := NewExtractor()
	job, err := ext.ExtractDetail([]byte{}, "https://kwork.ru/projects/1/view")
	if err != nil {
		t.Fatalf("ExtractDetail: %v", err)
	}
	if job != nil {
		t.Errorf("want nil, got %v", job)
	}
}

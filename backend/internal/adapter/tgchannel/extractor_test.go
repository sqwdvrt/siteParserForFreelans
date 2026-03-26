package tgchannel

import "testing"

func TestExtractList(t *testing.T) {
	ext := NewExtractor("freelance_ru")
	html := []byte(`
		<html><body>
			<a class="tgme_widget_message_date" href="https://t.me/freelance_ru/1001">post1</a>
			<a class="tgme_widget_message_date" href="https://t.me/freelance_ru/1002">post2</a>
			<a class="tgme_widget_message_date" href="https://t.me/freelance_ru/1001">dup</a>
			<a class="tgme_widget_message_date" href="https://t.me/freelance_ru/">no-id</a>
			<a href="https://t.me/freelance_ru/1003">no-class</a>
		</body></html>
	`)

	urls, err := ext.ExtractList(html)
	if err != nil {
		t.Fatalf("ExtractList: %v", err)
	}

	want := []string{
		"https://t.me/freelance_ru/1001?embed=1&mode=tme",
		"https://t.me/freelance_ru/1002?embed=1&mode=tme",
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

func TestExtractList_Empty(t *testing.T) {
	ext := NewExtractor("freelance_ru")
	urls, err := ext.ExtractList(nil)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(urls) != 0 {
		t.Fatalf("expected empty, got %v", urls)
	}
}

func TestExtractDetail(t *testing.T) {
	ext := NewExtractor("freelance_ru")
	pageURL := "https://t.me/freelance_ru/1001?embed=1&mode=tme"
	html := []byte(`
		<html><body>
			<div class="tgme_widget_message_text">Нужен Go разработчик для работы над микросервисами.
Стек: Go, PostgreSQL, Redis.
Бюджет: 80 000 руб/мес.</div>
			<time class="time" datetime="2026-03-01T10:00:00+00:00">01 Mar</time>
		</body></html>
	`)

	job, err := ext.ExtractDetail(html, pageURL)
	if err != nil {
		t.Fatalf("ExtractDetail: %v", err)
	}
	if job == nil {
		t.Fatal("job is nil")
	}
	if job.Source != "tgchannel:freelance_ru" {
		t.Fatalf("Source=%q want=tgchannel:freelance_ru", job.Source)
	}
	if job.ExternalID != "1001" {
		t.Fatalf("ExternalID=%q want=1001", job.ExternalID)
	}
	if job.URL != pageURL {
		t.Fatalf("URL=%q want=%q", job.URL, pageURL)
	}
	if job.Title != "Нужен Go разработчик для работы над микросервисами." {
		t.Fatalf("Title=%q", job.Title)
	}
	if job.Description == "" {
		t.Fatal("Description is empty")
	}
	if job.PostedAt == nil || job.PostedAt.Format("2006-01-02") != "2026-03-01" {
		t.Fatalf("PostedAt=%v", job.PostedAt)
	}
}

func TestExtractDetail_LongTitle(t *testing.T) {
	ext := NewExtractor("test_chan")
	// текст без переноса строки, длиннее 100 символов
	longText := "А" + string(make([]byte, 200)) // 201 символ
	html := []byte(`<div class="tgme_widget_message_text">` + longText + `</div>`)
	job, err := ext.ExtractDetail(html, "https://t.me/test_chan/42?embed=1&mode=tme")
	if err != nil {
		t.Fatalf("ExtractDetail: %v", err)
	}
	if job == nil {
		t.Fatal("job is nil")
	}
	runes := []rune(job.Title)
	if len(runes) > 100 {
		t.Fatalf("Title too long: %d runes", len(runes))
	}
}

func TestExtractDetail_NoText(t *testing.T) {
	ext := NewExtractor("test_chan")
	html := []byte(`<html><body><div class="tgme_widget_message_wrap"></div></body></html>`)
	job, err := ext.ExtractDetail(html, "https://t.me/test_chan/99?embed=1&mode=tme")
	if err != nil {
		t.Fatalf("ExtractDetail: %v", err)
	}
	if job == nil {
		t.Fatal("job is nil")
	}
	if job.Title != "(без текста)" {
		t.Fatalf("Title=%q want=(без текста)", job.Title)
	}
}

func TestExtractDetail_EmptyHTML(t *testing.T) {
	ext := NewExtractor("test_chan")
	job, err := ext.ExtractDetail(nil, "https://t.me/test_chan/1?embed=1&mode=tme")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if job != nil {
		t.Fatalf("expected nil job, got %+v", job)
	}
}

func TestExtractMsgID(t *testing.T) {
	cases := []struct {
		url  string
		want string
	}{
		{"https://t.me/freelance_ru/1001?embed=1&mode=tme", "1001"},
		{"https://t.me/golang_jobs/42", "42"},
		{"https://t.me/chan/", ""},
		{"https://t.me/chan", ""},
	}
	for _, c := range cases {
		got := extractMsgID(c.url)
		if got != c.want {
			t.Fatalf("extractMsgID(%q)=%q want=%q", c.url, got, c.want)
		}
	}
}

func TestNewExtractor_StripsAt(t *testing.T) {
	ext := NewExtractor("@freelance_ru")
	if ext.channelUsername != "freelance_ru" {
		t.Fatalf("channelUsername=%q want=freelance_ru", ext.channelUsername)
	}
}

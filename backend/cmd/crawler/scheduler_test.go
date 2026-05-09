package main

import (
	"context"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/robfig/cron/v3"
)

// TestScheduler_CronRunsOnSchedule проверяет, что cron вызывает job по расписанию.
func TestScheduler_CronRunsOnSchedule(t *testing.T) {
	var callCount int64
	job := func() {
		atomic.AddInt64(&callCount, 1)
	}

	c := cron.New()
	_, err := c.AddFunc("@every 1s", job)
	if err != nil {
		t.Fatalf("AddFunc: %v", err)
	}
	c.Start()
	defer c.Stop()

	// Ждём 2.5s — @every 1s даёт минимум 2 вызова
	time.Sleep(2500 * time.Millisecond)
	n := atomic.LoadInt64(&callCount)
	if n < 2 {
		t.Errorf("want >= 2 calls in 2.5s, got %d", n)
	}
}

// TestScheduler_StopDrainsRunningJob проверяет, что Stop() ждёт завершения текущего job.
func TestScheduler_StopDrainsRunningJob(t *testing.T) {
	jobStarted := make(chan struct{})
	jobDone := make(chan struct{})
	var startOnce, doneOnce sync.Once

	c := cron.New()
	_, err := c.AddFunc("@every 1s", func() {
		startOnce.Do(func() { close(jobStarted) })
		time.Sleep(200 * time.Millisecond)
		doneOnce.Do(func() { close(jobDone) })
	})
	if err != nil {
		t.Fatalf("AddFunc: %v", err)
	}
	c.Start()

	<-jobStarted
	drainCtx := c.Stop()
	select {
	case <-drainCtx.Done():
		// OK — drain завершился
	case <-time.After(500 * time.Millisecond):
		t.Fatal("Stop() did not drain within 500ms")
	}
	<-jobDone
}

func TestCrawlerRunJob_SkipsOverlappingRuns(t *testing.T) {
	lock := &stubCrawlerRunLock{}
	firstStarted := make(chan struct{})
	releaseFirst := make(chan struct{})
	var runs int32

	job := newCrawlerRunJob(lock, "crawler:test", time.Minute, func() {
		if atomic.AddInt32(&runs, 1) == 1 {
			close(firstStarted)
			<-releaseFirst
		}
	})

	go job()
	<-firstStarted

	job()

	if got := atomic.LoadInt32(&runs); got != 1 {
		t.Fatalf("runs after overlapping invocation = %d, want 1", got)
	}
	if got := lock.acquireCalls(); got != 2 {
		t.Fatalf("acquire calls = %d, want 2", got)
	}

	close(releaseFirst)

	deadline := time.After(time.Second)
	for !lock.isReleased() {
		select {
		case <-deadline:
			t.Fatal("expected first lease to be released")
		default:
			time.Sleep(10 * time.Millisecond)
		}
	}

	job()

	if got := atomic.LoadInt32(&runs); got != 2 {
		t.Fatalf("runs after released invocation = %d, want 2", got)
	}
}

func TestCrawlerRunJob_ReleasesLeaseAfterLongRun(t *testing.T) {
	lock := &deadlineAwareCrawlerRunLock{}
	job := newCrawlerRunJob(lock, "crawler:test", time.Minute, func() {
		time.Sleep(crawlerRunLockReleaseTimeout + 100*time.Millisecond)
	})

	job()

	if !lock.isReleased() {
		t.Fatal("expected lease to be released after long-running job")
	}
}

type stubCrawlerRunLock struct {
	mu           sync.Mutex
	held         bool
	acquireCount int
	released     bool
}

func (l *stubCrawlerRunLock) Acquire(_ context.Context, _ string, _ time.Duration) (crawlerRunLease, bool, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.acquireCount++
	if l.held {
		return nil, false, nil
	}
	l.held = true
	l.released = false
	return &stubCrawlerRunLease{parent: l}, true, nil
}

func (l *stubCrawlerRunLock) acquireCalls() int {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.acquireCount
}

func (l *stubCrawlerRunLock) isReleased() bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.released
}

type stubCrawlerRunLease struct {
	parent *stubCrawlerRunLock
}

func (l *stubCrawlerRunLease) Release(_ context.Context) (bool, error) {
	l.parent.mu.Lock()
	defer l.parent.mu.Unlock()
	l.parent.held = false
	l.parent.released = true
	return true, nil
}

type deadlineAwareCrawlerRunLock struct {
	released atomic.Bool
}

func (l *deadlineAwareCrawlerRunLock) Acquire(_ context.Context, _ string, _ time.Duration) (crawlerRunLease, bool, error) {
	return &deadlineAwareCrawlerRunLease{released: &l.released}, true, nil
}

func (l *deadlineAwareCrawlerRunLock) isReleased() bool {
	return l.released.Load()
}

type deadlineAwareCrawlerRunLease struct {
	released *atomic.Bool
}

func (l *deadlineAwareCrawlerRunLease) Release(ctx context.Context) (bool, error) {
	if err := ctx.Err(); err != nil {
		return false, err
	}
	l.released.Store(true)
	return true, nil
}

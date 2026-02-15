package main

import (
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

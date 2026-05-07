package usecase

import (
	"context"
	"testing"
	"time"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

type reminderUserRepo struct {
	getUsersWithProExpiryBetweenFunc func(ctx context.Context, from, to time.Time) ([]*domain.User, error)
}

func (m *reminderUserRepo) Save(ctx context.Context, telegramID int64) (int64, bool, error) {
	return 0, false, nil
}
func (m *reminderUserRepo) GetByID(ctx context.Context, userID int64) (*domain.User, error) {
	return nil, nil
}
func (m *reminderUserRepo) GetByTelegramID(ctx context.Context, telegramID int64) (*domain.User, error) {
	return nil, nil
}
func (m *reminderUserRepo) GetPreferencesScoped(ctx context.Context, userID int64) (*domain.UserPreferences, error) {
	return &domain.UserPreferences{}, nil
}
func (m *reminderUserRepo) UpdateProfile(ctx context.Context, userID int64, profileText string) error {
	return nil
}
func (m *reminderUserRepo) UpdateProfileScoped(ctx context.Context, userID int64, profileText string) error {
	return nil
}
func (m *reminderUserRepo) UpdateNotifyHourScoped(ctx context.Context, userID int64, hour int) error {
	return nil
}
func (m *reminderUserRepo) UpdatePauseScoped(ctx context.Context, userID int64, until *time.Time) error {
	return nil
}
func (m *reminderUserRepo) UpsertPreferencesScoped(ctx context.Context, userID int64, prefs domain.UserPreferences) error {
	return nil
}
func (m *reminderUserRepo) GetProUsersWithNotifyHour(ctx context.Context, hour int) ([]int64, error) {
	return nil, nil
}
func (m *reminderUserRepo) GetUsersWithProExpiryBetween(ctx context.Context, from, to time.Time) ([]*domain.User, error) {
	if m.getUsersWithProExpiryBetweenFunc != nil {
		return m.getUsersWithProExpiryBetweenFunc(ctx, from, to)
	}
	return nil, nil
}

func TestProRenewalReminders_ExecuteSendsThreeDayReminderOnce(t *testing.T) {
	now := time.Date(2026, 5, 5, 12, 0, 0, 0, time.UTC)
	expiresAt := time.Date(2026, 5, 8, 10, 0, 0, 0, time.UTC)
	notifier := &mockNotifier{}
	eventRepo := &mockEventRepo{}
	repo := &reminderUserRepo{
		getUsersWithProExpiryBetweenFunc: func(ctx context.Context, from, to time.Time) ([]*domain.User, error) {
			if from.Equal(time.Date(2026, 5, 8, 0, 0, 0, 0, time.UTC)) && to.Equal(time.Date(2026, 5, 9, 0, 0, 0, 0, time.UTC)) {
				return []*domain.User{{ID: 7, TelegramID: 700, IsPro: true, ProExpiresAt: &expiresAt}}, nil
			}
			return nil, nil
		},
	}

	err := NewProRenewalReminders(repo, notifier, eventRepo).
		WithNow(func() time.Time { return now }).
		Execute(context.Background())
	if err != nil {
		t.Fatalf("Execute err=%v", err)
	}
	if len(notifier.payloads) != 1 {
		t.Fatalf("payloads=%d want 1", len(notifier.payloads))
	}
	if notifier.payloads[0].Source != "pro-renewal" {
		t.Fatalf("source=%q want pro-renewal", notifier.payloads[0].Source)
	}
	if len(eventRepo.recorded) != 1 {
		t.Fatalf("events=%d want 1", len(eventRepo.recorded))
	}
	if eventRepo.recorded[0].Type != port.ProductEventProRenewalReminderSent {
		t.Fatalf("event=%q want %q", eventRepo.recorded[0].Type, port.ProductEventProRenewalReminderSent)
	}

	eventRepo.existsFunc = func(ctx context.Context, userID int64, eventType port.ProductEventType, since time.Time, propertyKey, propertyValue string) (bool, error) {
		return true, nil
	}
	notifier.payloads = nil
	eventRepo.recorded = nil
	err = NewProRenewalReminders(repo, notifier, eventRepo).
		WithNow(func() time.Time { return now }).
		Execute(context.Background())
	if err != nil {
		t.Fatalf("second Execute err=%v", err)
	}
	if len(notifier.payloads) != 0 {
		t.Fatalf("payloads=%d want 0 after dedupe", len(notifier.payloads))
	}
}

func TestProRenewalReminders_ExecuteRecordsExpiredEvent(t *testing.T) {
	now := time.Date(2026, 5, 5, 12, 0, 0, 0, time.UTC)
	expiresAt := time.Date(2026, 5, 4, 10, 0, 0, 0, time.UTC)
	notifier := &mockNotifier{}
	eventRepo := &mockEventRepo{}
	repo := &reminderUserRepo{
		getUsersWithProExpiryBetweenFunc: func(ctx context.Context, from, to time.Time) ([]*domain.User, error) {
			startYesterday := time.Date(2026, 5, 4, 0, 0, 0, 0, time.UTC)
			startToday := time.Date(2026, 5, 5, 0, 0, 0, 0, time.UTC)
			if from.Equal(startYesterday) && to.Equal(startToday) {
				return []*domain.User{{ID: 11, TelegramID: 1100, IsPro: true, ProExpiresAt: &expiresAt}}, nil
			}
			return nil, nil
		},
	}

	err := NewProRenewalReminders(repo, notifier, eventRepo).
		WithNow(func() time.Time { return now }).
		Execute(context.Background())
	if err != nil {
		t.Fatalf("Execute err=%v", err)
	}
	if len(notifier.payloads) != 1 {
		t.Fatalf("payloads=%d want 1", len(notifier.payloads))
	}
	if got := notifier.payloads[0].InlineKeyboard[0][0].CallbackData; got != "pro:upgrade" {
		t.Fatalf("callback=%q want pro:upgrade", got)
	}
	if len(eventRepo.recorded) != 2 {
		t.Fatalf("events=%d want 2", len(eventRepo.recorded))
	}
	if eventRepo.recorded[0].Type != port.ProductEventProRenewalReminderSent {
		t.Fatalf("event[0]=%q want %q", eventRepo.recorded[0].Type, port.ProductEventProRenewalReminderSent)
	}
	if eventRepo.recorded[1].Type != port.ProductEventProExpired {
		t.Fatalf("event[1]=%q want %q", eventRepo.recorded[1].Type, port.ProductEventProExpired)
	}
}

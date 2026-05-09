package usecase

import (
	"context"
	"fmt"
	"time"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/port"
)

type reminderWindow struct {
	kind string
	from time.Time
	to   time.Time
}

type ProRenewalReminders struct {
	userRepo  port.UserRepository
	notifier  port.Notifier
	eventRepo port.ProductEventRepository
	nowFunc   func() time.Time
}

func NewProRenewalReminders(
	userRepo port.UserRepository,
	notifier port.Notifier,
	eventRepo port.ProductEventRepository,
) *ProRenewalReminders {
	return &ProRenewalReminders{
		userRepo:  userRepo,
		notifier:  notifier,
		eventRepo: eventRepo,
		nowFunc:   time.Now,
	}
}

func (u *ProRenewalReminders) WithNow(nowFunc func() time.Time) *ProRenewalReminders {
	if nowFunc != nil {
		u.nowFunc = nowFunc
	}
	return u
}

func (u *ProRenewalReminders) Execute(ctx context.Context) error {
	if u.userRepo == nil || u.notifier == nil || u.eventRepo == nil {
		return nil
	}
	now := u.nowFunc().UTC()
	for _, window := range u.reminderWindows(now) {
		users, err := u.userRepo.GetUsersWithProExpiryBetween(ctx, window.from, window.to)
		if err != nil {
			return err
		}
		for _, user := range users {
			if err := u.sendReminder(ctx, user, window.kind, now); err != nil {
				return err
			}
		}
	}
	return nil
}

func (u *ProRenewalReminders) reminderWindows(now time.Time) []reminderWindow {
	startToday := startOfUTCDay(now)
	return []reminderWindow{
		{
			kind: "expires_in_3_days",
			from: startToday.Add(72 * time.Hour),
			to:   startToday.Add(96 * time.Hour),
		},
		{
			kind: "expires_today",
			from: startToday,
			to:   startToday.Add(24 * time.Hour),
		},
		{
			kind: "expired",
			from: startToday.Add(-24 * time.Hour),
			to:   startToday,
		},
	}
}

func (u *ProRenewalReminders) sendReminder(ctx context.Context, user *domain.User, kind string, now time.Time) error {
	if user == nil || user.ID <= 0 || user.TelegramID <= 0 || user.ProExpiresAt == nil {
		return nil
	}
	exists, err := u.eventRepo.ExistsSince(
		ctx,
		user.ID,
		port.ProductEventProRenewalReminderSent,
		startOfUTCDay(now),
		"reminder_kind",
		kind,
	)
	if err != nil {
		return err
	}
	if exists {
		return nil
	}
	if err := u.notifier.Send(ctx, user.TelegramID, port.NotifyPayload{
		Source: "pro-renewal",
		Text:   formatRenewalReminderMessage(kind, user.ProExpiresAt.UTC()),
		InlineKeyboard: [][]port.InlineButton{{
			{Text: "Хочу Pro", CallbackData: "pro:upgrade"},
		}},
	}); err != nil {
		return err
	}
	if err := u.eventRepo.Record(ctx, port.ProductEvent{
		Type:   port.ProductEventProRenewalReminderSent,
		UserID: user.ID,
		Properties: map[string]any{
			"reminder_kind":  kind,
			"pro_expires_at": user.ProExpiresAt.UTC().Format(time.RFC3339),
		},
	}); err != nil {
		return err
	}
	if kind == "expired" {
		return u.eventRepo.Record(ctx, port.ProductEvent{
			Type:   port.ProductEventProExpired,
			UserID: user.ID,
			Properties: map[string]any{
				"pro_expires_at": user.ProExpiresAt.UTC().Format(time.RFC3339),
			},
		})
	}
	return nil
}

func formatRenewalReminderMessage(kind string, expiresAt time.Time) string {
	date := expiresAt.Format("2006-01-02")
	switch kind {
	case "expires_in_3_days":
		return fmt.Sprintf("Pro закончится через 3 дня, %s.\n\nЧтобы не вернуться к лимиту Free 5 в день, оставь запрос на продление заранее.", date)
	case "expires_today":
		return fmt.Sprintf("Сегодня последний день Pro: %s.\n\nПосле expiry снова включится лимит Free 5 в день. Если хочешь продлить доступ, отправь запрос сейчас.", date)
	default:
		return fmt.Sprintf("Срок Pro закончился %s.\n\nЧасть подходящих лидов снова будет скрываться лимитом Free. Оставь запрос, и я включу Pro снова.", date)
	}
}

package domain

import "time"

// User — пользователь из таблицы users.
type User struct {
	ID           int64
	TelegramID   int64
	ProfileText  *string
	IsPro        bool
	ProExpiresAt *time.Time
	NotifyHour   *int16
	PausedUntil  *time.Time
}

func (u *User) IsPaused(now time.Time) bool {
	if u == nil || u.PausedUntil == nil {
		return false
	}
	return u.PausedUntil.After(now)
}

func (u *User) EffectiveIsPro(now time.Time) bool {
	if u == nil || !u.IsPro || u.ProExpiresAt == nil {
		return false
	}
	return u.ProExpiresAt.After(now)
}

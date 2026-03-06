package domain

// User — пользователь из таблицы users.
type User struct {
	ID          int64
	TelegramID  int64
	ProfileText *string
	IsPro       bool
	NotifyHour  *int16
}

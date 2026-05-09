package domain

// FeedbackType представляет реакцию пользователя на уведомление о проекте.
type FeedbackType string

const (
	FeedbackGood FeedbackType = "good" // 👍
	FeedbackBad  FeedbackType = "bad"  // 👎
)

// IsValid сообщает, является ли f известным типом обратной связи.
func (f FeedbackType) IsValid() bool {
	return f == FeedbackGood || f == FeedbackBad
}

// UserFeedback — обратная связь пользователя на уведомление о проекте.
type UserFeedback struct {
	UserID   int64
	JobID    int64
	Feedback FeedbackType
}

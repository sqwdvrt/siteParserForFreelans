package domain

import (
	"errors"
	"fmt"
)

// HttpStatusError возвращается fetcher'ом при неуспешном HTTP-статусе.
type HttpStatusError struct {
	StatusCode int
	URL        string
}

func (e *HttpStatusError) Error() string {
	return fmt.Sprintf("http %d: %s", e.StatusCode, e.URL)
}

// IsGone возвращает true если ошибка означает что ресурс недоступен (404/410).
func IsGone(err error) bool {
	var httpErr *HttpStatusError
	if errors.As(err, &httpErr) {
		return httpErr.StatusCode == 404 || httpErr.StatusCode == 410
	}
	return false
}

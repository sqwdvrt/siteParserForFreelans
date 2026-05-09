package domain

import "testing"

func TestFeedbackTypeIsValid(t *testing.T) {
	if !FeedbackGood.IsValid() {
		t.Fatal("FeedbackGood should be valid")
	}
	if !FeedbackBad.IsValid() {
		t.Fatal("FeedbackBad should be valid")
	}
	if FeedbackType("maybe").IsValid() {
		t.Fatal("unexpected valid feedback")
	}
}

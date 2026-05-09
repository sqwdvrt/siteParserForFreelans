package port

import "testing"

func TestMatchNotifyPayloadEffectiveBatchScore(t *testing.T) {
	if got := (MatchNotifyPayload{BatchScore: 0.91, CriticScore: 0.4}).EffectiveBatchScore(); got != 0.91 {
		t.Fatalf("batch score=%v want 0.91", got)
	}
	if got := (MatchNotifyPayload{CriticScore: 0.73}).EffectiveBatchScore(); got != 0.73 {
		t.Fatalf("fallback critic score=%v want 0.73", got)
	}
}

func TestNotifyPayloadEffectiveBatchScore(t *testing.T) {
	if got := (NotifyPayload{BatchScore: 0.82, CriticScore: 0.3}).EffectiveBatchScore(); got != 0.82 {
		t.Fatalf("batch score=%v want 0.82", got)
	}
	if got := (NotifyPayload{CriticScore: 0.61}).EffectiveBatchScore(); got != 0.61 {
		t.Fatalf("fallback critic score=%v want 0.61", got)
	}
}

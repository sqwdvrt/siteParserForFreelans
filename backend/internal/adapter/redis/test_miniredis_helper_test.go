package redis

import (
	"strings"
	"testing"

	"github.com/alicebob/miniredis/v2"
)

func mustRunMiniRedis(t *testing.T) *miniredis.Miniredis {
	t.Helper()

	mr, err := miniredis.Run()
	if err != nil {
		if isSandboxBindRestriction(err) {
			t.Skipf("miniredis unavailable in this environment: %v", err)
		}
		t.Fatalf("miniredis: %v", err)
	}
	t.Cleanup(mr.Close)
	return mr
}

func isSandboxBindRestriction(err error) bool {
	if err == nil {
		return false
	}
	msg := strings.ToLower(err.Error())
	return strings.Contains(msg, "listen tcp") &&
		(strings.Contains(msg, "operation not permitted") || strings.Contains(msg, "permission denied"))
}

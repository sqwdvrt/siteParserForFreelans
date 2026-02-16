package security

import (
	"fmt"
	"math"
	"net/url"
	"strings"
)

var forbiddenSecretPrefixes = []string{
	"change_me",
	"changeme",
	"replace_me",
	"replace_with",
	"your_",
	"example_",
	"dummy_",
	"test_",
}

// ValidateSecret проверяет базовую policy секрета: placeholder-паттерны,
// минимальная длина и приближённая энтропия.
func ValidateSecret(name, secret string, minLen int) error {
	name = strings.TrimSpace(name)
	if name == "" {
		name = "secret"
	}
	secret = strings.TrimSpace(secret)
	if secret == "" {
		return fmt.Errorf("%s is empty", name)
	}
	if minLen <= 0 {
		minLen = 24
	}
	lower := strings.ToLower(secret)
	for _, p := range forbiddenSecretPrefixes {
		if strings.HasPrefix(lower, p) {
			return fmt.Errorf("%s uses placeholder prefix %q", name, p)
		}
	}
	if len(secret) < minLen {
		return fmt.Errorf("%s must be at least %d chars", name, minLen)
	}
	minEntropyBits := math.Max(80, float64(minLen)*3.0)
	if entropy := shannonEntropyBits(secret); entropy < minEntropyBits {
		return fmt.Errorf("%s is too weak (entropy %.1f < %.1f bits)", name, entropy, minEntropyBits)
	}
	return nil
}

// ValidateURLPassword извлекает пароль из URL и проверяет его как секрет.
func ValidateURLPassword(urlName, rawURL string, minLen int) error {
	u, err := url.Parse(strings.TrimSpace(rawURL))
	if err != nil {
		return fmt.Errorf("%s is invalid: %w", urlName, err)
	}
	if u.User == nil {
		return fmt.Errorf("%s must include credentials", urlName)
	}
	pw, ok := u.User.Password()
	if !ok || strings.TrimSpace(pw) == "" {
		return fmt.Errorf("%s must include password", urlName)
	}
	return ValidateSecret(urlName+" password", pw, minLen)
}

// IsProductionEnv сообщает, следует ли применять production policy для транспорта.
func IsProductionEnv(raw string) bool {
	switch strings.ToLower(strings.TrimSpace(raw)) {
	case "prod", "production":
		return true
	default:
		return false
	}
}

// ValidatePostgresTLSForProduction проверяет, что Postgres URL использует sslmode=require|verify-ca|verify-full.
func ValidatePostgresTLSForProduction(urlName, rawURL string) error {
	u, err := url.Parse(strings.TrimSpace(rawURL))
	if err != nil {
		return fmt.Errorf("%s is invalid: %w", urlName, err)
	}
	scheme := strings.ToLower(strings.TrimSpace(u.Scheme))
	if scheme != "postgres" && scheme != "postgresql" {
		return fmt.Errorf("%s must use postgres:// or postgresql:// scheme", urlName)
	}
	sslMode := strings.ToLower(strings.TrimSpace(u.Query().Get("sslmode")))
	switch sslMode {
	case "require", "verify-ca", "verify-full":
		return nil
	case "":
		return fmt.Errorf("%s must include sslmode=require (or verify-ca/verify-full) in production", urlName)
	default:
		return fmt.Errorf("%s uses insecure sslmode=%q in production", urlName, sslMode)
	}
}

// ValidateRedisTLSForProduction проверяет, что Redis URL использует rediss:// и защищён паролем.
func ValidateRedisTLSForProduction(urlName, rawURL string, passwordMinLen int) error {
	u, err := url.Parse(strings.TrimSpace(rawURL))
	if err != nil {
		return fmt.Errorf("%s is invalid: %w", urlName, err)
	}
	scheme := strings.ToLower(strings.TrimSpace(u.Scheme))
	if scheme != "rediss" {
		return fmt.Errorf("%s must use rediss:// in production", urlName)
	}
	if u.User == nil {
		return fmt.Errorf("%s must include credentials in production", urlName)
	}
	pw, ok := u.User.Password()
	if !ok || strings.TrimSpace(pw) == "" {
		return fmt.Errorf("%s must include password in production", urlName)
	}
	if passwordMinLen <= 0 {
		passwordMinLen = 16
	}
	if err := ValidateSecret(urlName+" password", pw, passwordMinLen); err != nil {
		return err
	}
	return nil
}

// ValidateHTTPSURLForProduction проверяет, что URL использует HTTPS в production.
func ValidateHTTPSURLForProduction(urlName, rawURL string) error {
	u, err := url.Parse(strings.TrimSpace(rawURL))
	if err != nil {
		return fmt.Errorf("%s is invalid: %w", urlName, err)
	}
	if strings.ToLower(strings.TrimSpace(u.Scheme)) != "https" {
		return fmt.Errorf("%s must use https:// in production", urlName)
	}
	if strings.TrimSpace(u.Host) == "" {
		return fmt.Errorf("%s must include host", urlName)
	}
	return nil
}

func shannonEntropyBits(s string) float64 {
	if s == "" {
		return 0
	}
	counts := make(map[rune]int, len(s))
	n := 0
	for _, r := range s {
		counts[r]++
		n++
	}
	if n == 0 {
		return 0
	}
	var entropyPerRune float64
	total := float64(n)
	for _, c := range counts {
		p := float64(c) / total
		entropyPerRune += -p * math.Log2(p)
	}
	return entropyPerRune * total
}

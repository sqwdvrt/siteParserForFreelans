package security

import "testing"

func TestValidateSecret_RejectsPlaceholderPrefix(t *testing.T) {
	err := ValidateSecret("API_AUTH_TOKEN", "change_me_long_random_token_123456", 24)
	if err == nil {
		t.Fatal("want error for placeholder secret")
	}
}

func TestValidateSecret_RejectsShort(t *testing.T) {
	err := ValidateSecret("API_AUTH_TOKEN", "abc123", 24)
	if err == nil {
		t.Fatal("want error for short secret")
	}
}

func TestValidateSecret_RejectsLowEntropy(t *testing.T) {
	err := ValidateSecret("API_AUTH_TOKEN", "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", 24)
	if err == nil {
		t.Fatal("want error for weak secret")
	}
}

func TestValidateSecret_AcceptsStrong(t *testing.T) {
	strong := "9f2caea2db0362da10583f9b0dc8e0feb6e1d7b28469a31ca9e0c2f339a3f2b1"
	if err := ValidateSecret("API_AUTH_TOKEN", strong, 24); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestValidateURLPassword(t *testing.T) {
	raw := "postgres://user:5e15709d34a5405d84c6f2e2457c7e8a@localhost:55432/db?sslmode=disable"
	if err := ValidateURLPassword("DATABASE_URL", raw, 16); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestIsProductionEnv(t *testing.T) {
	if !IsProductionEnv("production") {
		t.Fatal("production must be production env")
	}
	if !IsProductionEnv("prod") {
		t.Fatal("prod must be production env")
	}
	if IsProductionEnv("development") {
		t.Fatal("development must not be production env")
	}
}

func TestValidatePostgresTLSForProduction(t *testing.T) {
	okURL := "postgres://user:pass@db.example.com:5432/site?sslmode=require"
	if err := ValidatePostgresTLSForProduction("DATABASE_URL", okURL); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	badURL := "postgres://user:pass@db.example.com:5432/site?sslmode=disable"
	if err := ValidatePostgresTLSForProduction("DATABASE_URL", badURL); err == nil {
		t.Fatal("want error for sslmode=disable")
	}
}

func TestValidateRedisTLSForProduction(t *testing.T) {
	okURL := "rediss://default:a16e9c0dbf67aa50831ed2a1bc4e9f0f@redis.example.com:6380/0"
	if err := ValidateRedisTLSForProduction("REDIS_URL", okURL, 16); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	badURL := "redis://:a16e9c0dbf67aa50831ed2a1bc4e9f0f@redis.example.com:6379/0"
	if err := ValidateRedisTLSForProduction("REDIS_URL", badURL, 16); err == nil {
		t.Fatal("want error for non-TLS redis scheme")
	}
}

func TestValidateHTTPSURLForProduction(t *testing.T) {
	if err := ValidateHTTPSURLForProduction("API_URL", "https://api.example.com"); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if err := ValidateHTTPSURLForProduction("API_URL", "http://api.example.com"); err == nil {
		t.Fatal("want error for non-https url")
	}
}

func TestValidateHTTPOrHTTPSURL(t *testing.T) {
	if err := ValidateHTTPOrHTTPSURL("BROWSER_SERVICE_URL", "http://browser-service:8090"); err != nil {
		t.Fatalf("unexpected error for http url: %v", err)
	}
	if err := ValidateHTTPOrHTTPSURL("BROWSER_SERVICE_URL", "https://browser.example.com"); err != nil {
		t.Fatalf("unexpected error for https url: %v", err)
	}
	if err := ValidateHTTPOrHTTPSURL("BROWSER_SERVICE_URL", "tcp://browser-service:8090"); err == nil {
		t.Fatal("want error for non-http scheme")
	}
	if err := ValidateHTTPOrHTTPSURL("BROWSER_SERVICE_URL", "http:///render"); err == nil {
		t.Fatal("want error for missing host")
	}
}

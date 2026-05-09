package postgres

import (
	"os"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

func TestLoadPoolRuntimeSettings_Defaults(t *testing.T) {
	t.Setenv(envPoolMaxConns, "")
	t.Setenv(envPoolMinConns, "")
	t.Setenv(envPoolAcquireTimeout, "")
	t.Setenv(envQueryTimeout, "")
	t.Setenv(envConnectTimeout, "")
	t.Setenv(envQueryExecMode, "")

	settings, err := loadPoolRuntimeSettings(os.Getenv)
	if err != nil {
		t.Fatalf("load defaults: %v", err)
	}

	if settings.maxConns != defaultPoolMaxConns {
		t.Fatalf("maxConns=%d want=%d", settings.maxConns, defaultPoolMaxConns)
	}
	if settings.minConns != defaultPoolMinConns {
		t.Fatalf("minConns=%d want=%d", settings.minConns, defaultPoolMinConns)
	}
	if settings.acquireTimeout != defaultPoolAcquireTimeout {
		t.Fatalf("acquireTimeout=%v want=%v", settings.acquireTimeout, defaultPoolAcquireTimeout)
	}
	if settings.queryTimeout != defaultQueryTimeout {
		t.Fatalf("queryTimeout=%v want=%v", settings.queryTimeout, defaultQueryTimeout)
	}
	if settings.connectTimeout != defaultConnectTimeout {
		t.Fatalf("connectTimeout=%v want=%v", settings.connectTimeout, defaultConnectTimeout)
	}
}

func TestLoadPoolRuntimeSettings_ParsesExplicitValues(t *testing.T) {
	t.Setenv(envPoolMaxConns, "12")
	t.Setenv(envPoolMinConns, "3")
	t.Setenv(envPoolAcquireTimeout, "750ms")
	t.Setenv(envQueryTimeout, "9s")
	t.Setenv(envConnectTimeout, "2s")
	t.Setenv(envQueryExecMode, "exec")

	settings, err := loadPoolRuntimeSettings(os.Getenv)
	if err != nil {
		t.Fatalf("load settings: %v", err)
	}

	if settings.maxConns != 12 {
		t.Fatalf("maxConns=%d want=12", settings.maxConns)
	}
	if settings.minConns != 3 {
		t.Fatalf("minConns=%d want=3", settings.minConns)
	}
	if settings.acquireTimeout != 750*time.Millisecond {
		t.Fatalf("acquireTimeout=%v want=750ms", settings.acquireTimeout)
	}
	if settings.queryTimeout != 9*time.Second {
		t.Fatalf("queryTimeout=%v want=9s", settings.queryTimeout)
	}
	if settings.connectTimeout != 2*time.Second {
		t.Fatalf("connectTimeout=%v want=2s", settings.connectTimeout)
	}
	if settings.queryExecMode == nil || *settings.queryExecMode != pgx.QueryExecModeExec {
		t.Fatalf("queryExecMode=%v want=%v", settings.queryExecMode, pgx.QueryExecModeExec)
	}
}

func TestLoadPoolRuntimeSettings_InvalidValue(t *testing.T) {
	t.Setenv(envPoolMaxConns, "bad")

	if _, err := loadPoolRuntimeSettings(os.Getenv); err == nil {
		t.Fatal("expected error for invalid PG_POOL_MAX_CONNS")
	}
}

func TestApplyPoolRuntimeSettings_AssignsPoolAndTimeouts(t *testing.T) {
	cfg, err := pgxpool.ParseConfig("postgres://user:pass@localhost:5432/app")
	if err != nil {
		t.Fatalf("parse config: %v", err)
	}

	settings := poolRuntimeSettings{
		maxConns:       15,
		minConns:       4,
		acquireTimeout: 3 * time.Second,
		queryTimeout:   17 * time.Second,
		connectTimeout: 4 * time.Second,
	}
	if err := applyPoolRuntimeSettings(cfg, settings); err != nil {
		t.Fatalf("apply settings: %v", err)
	}

	if cfg.MaxConns != 15 {
		t.Fatalf("cfg.MaxConns=%d want=15", cfg.MaxConns)
	}
	if cfg.MinConns != 4 {
		t.Fatalf("cfg.MinConns=%d want=4", cfg.MinConns)
	}
	if cfg.ConnConfig.ConnectTimeout != 4*time.Second {
		t.Fatalf("cfg.ConnConfig.ConnectTimeout=%v want=4s", cfg.ConnConfig.ConnectTimeout)
	}
	if got := cfg.ConnConfig.RuntimeParams["statement_timeout"]; got != "17000" {
		t.Fatalf("statement_timeout=%q want=17000", got)
	}
}

func TestApplyPoolRuntimeSettings_AssignsQueryExecMode(t *testing.T) {
	cfg, err := pgxpool.ParseConfig("postgres://user:pass@localhost:6543/app")
	if err != nil {
		t.Fatalf("parse config: %v", err)
	}

	mode := pgx.QueryExecModeExec
	settings := poolRuntimeSettings{
		maxConns:       2,
		minConns:       0,
		acquireTimeout: time.Second,
		queryTimeout:   5 * time.Second,
		connectTimeout: time.Second,
		queryExecMode:  &mode,
	}
	if err := applyPoolRuntimeSettings(cfg, settings); err != nil {
		t.Fatalf("apply settings: %v", err)
	}

	if cfg.ConnConfig.DefaultQueryExecMode != pgx.QueryExecModeExec {
		t.Fatalf("cfg.ConnConfig.DefaultQueryExecMode=%v want=%v", cfg.ConnConfig.DefaultQueryExecMode, pgx.QueryExecModeExec)
	}
}

func TestApplyPoolRuntimeSettings_MinConnsCannotExceedMaxConns(t *testing.T) {
	cfg, err := pgxpool.ParseConfig("postgres://user:pass@localhost:5432/app")
	if err != nil {
		t.Fatalf("parse config: %v", err)
	}
	settings := poolRuntimeSettings{
		maxConns:       2,
		minConns:       3,
		acquireTimeout: time.Second,
		queryTimeout:   time.Second,
		connectTimeout: time.Second,
	}
	if err := applyPoolRuntimeSettings(cfg, settings); err == nil {
		t.Fatal("expected error when PG_POOL_MIN_CONNS > PG_POOL_MAX_CONNS")
	}
}

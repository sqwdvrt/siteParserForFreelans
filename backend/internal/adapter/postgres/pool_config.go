package postgres

import (
	"context"
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

const (
	envPoolMaxConns       = "PG_POOL_MAX_CONNS"
	envPoolMinConns       = "PG_POOL_MIN_CONNS"
	envPoolAcquireTimeout = "PG_POOL_ACQUIRE_TIMEOUT"
	envQueryTimeout       = "PG_QUERY_TIMEOUT"
	envConnectTimeout     = "PG_CONNECT_TIMEOUT"
	envQueryExecMode      = "PG_DEFAULT_QUERY_EXEC_MODE"
)

const (
	defaultPoolMaxConns       int32 = 8
	defaultPoolMinConns       int32 = 0
	defaultPoolAcquireTimeout       = 5 * time.Second
	defaultQueryTimeout             = 30 * time.Second
	defaultConnectTimeout           = 5 * time.Second
)

type poolRuntimeSettings struct {
	maxConns       int32
	minConns       int32
	acquireTimeout time.Duration
	queryTimeout   time.Duration
	connectTimeout time.Duration
	queryExecMode  *pgx.QueryExecMode
}

// NewConfiguredPool создает pgxpool с явными лимитами и таймаутами.
func NewConfiguredPool(ctx context.Context, dbURL string) (*pgxpool.Pool, error) {
	cfg, err := pgxpool.ParseConfig(dbURL)
	if err != nil {
		return nil, fmt.Errorf("parse pgxpool config: %w", err)
	}

	settings, err := loadPoolRuntimeSettings(os.Getenv)
	if err != nil {
		return nil, err
	}
	if err := applyPoolRuntimeSettings(cfg, settings); err != nil {
		return nil, err
	}

	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		return nil, err
	}

	acquireCtx, cancel := context.WithTimeout(ctx, settings.acquireTimeout)
	defer cancel()
	conn, err := pool.Acquire(acquireCtx)
	if err != nil {
		pool.Close()
		return nil, fmt.Errorf("pgxpool acquire within %s: %w", settings.acquireTimeout, err)
	}
	conn.Release()
	return pool, nil
}

func applyPoolRuntimeSettings(cfg *pgxpool.Config, settings poolRuntimeSettings) error {
	if cfg == nil {
		return fmt.Errorf("pgxpool config is nil")
	}
	if settings.minConns > settings.maxConns {
		return fmt.Errorf("%s (%d) must be <= %s (%d)", envPoolMinConns, settings.minConns, envPoolMaxConns, settings.maxConns)
	}

	cfg.MaxConns = settings.maxConns
	cfg.MinConns = settings.minConns
	cfg.ConnConfig.ConnectTimeout = settings.connectTimeout
	if settings.queryExecMode != nil {
		cfg.ConnConfig.DefaultQueryExecMode = *settings.queryExecMode
	}

	if cfg.ConnConfig.RuntimeParams == nil {
		cfg.ConnConfig.RuntimeParams = map[string]string{}
	}
	// PostgreSQL statement_timeout is specified in milliseconds.
	cfg.ConnConfig.RuntimeParams["statement_timeout"] = strconv.FormatInt(settings.queryTimeout.Milliseconds(), 10)
	return nil
}

func loadPoolRuntimeSettings(getenv func(string) string) (poolRuntimeSettings, error) {
	maxConns, err := parsePositiveInt32Env(getenv, envPoolMaxConns, defaultPoolMaxConns)
	if err != nil {
		return poolRuntimeSettings{}, err
	}
	minConns, err := parseNonNegativeInt32Env(getenv, envPoolMinConns, defaultPoolMinConns)
	if err != nil {
		return poolRuntimeSettings{}, err
	}
	acquireTimeout, err := parsePositiveDurationEnv(getenv, envPoolAcquireTimeout, defaultPoolAcquireTimeout)
	if err != nil {
		return poolRuntimeSettings{}, err
	}
	queryTimeout, err := parsePositiveDurationEnv(getenv, envQueryTimeout, defaultQueryTimeout)
	if err != nil {
		return poolRuntimeSettings{}, err
	}
	connectTimeout, err := parsePositiveDurationEnv(getenv, envConnectTimeout, defaultConnectTimeout)
	if err != nil {
		return poolRuntimeSettings{}, err
	}
	queryExecMode, err := parseOptionalQueryExecMode(getenv(envQueryExecMode))
	if err != nil {
		return poolRuntimeSettings{}, err
	}

	return poolRuntimeSettings{
		maxConns:       maxConns,
		minConns:       minConns,
		acquireTimeout: acquireTimeout,
		queryTimeout:   queryTimeout,
		connectTimeout: connectTimeout,
		queryExecMode:  queryExecMode,
	}, nil
}

func parsePositiveInt32Env(getenv func(string) string, key string, fallback int32) (int32, error) {
	raw := strings.TrimSpace(getenv(key))
	if raw == "" {
		return fallback, nil
	}
	v, err := strconv.ParseInt(raw, 10, 32)
	if err != nil {
		return 0, fmt.Errorf("invalid %s=%q: %w", key, raw, err)
	}
	if v <= 0 {
		return 0, fmt.Errorf("invalid %s=%q: must be > 0", key, raw)
	}
	return int32(v), nil
}

func parseNonNegativeInt32Env(getenv func(string) string, key string, fallback int32) (int32, error) {
	raw := strings.TrimSpace(getenv(key))
	if raw == "" {
		return fallback, nil
	}
	v, err := strconv.ParseInt(raw, 10, 32)
	if err != nil {
		return 0, fmt.Errorf("invalid %s=%q: %w", key, raw, err)
	}
	if v < 0 {
		return 0, fmt.Errorf("invalid %s=%q: must be >= 0", key, raw)
	}
	return int32(v), nil
}

func parsePositiveDurationEnv(getenv func(string) string, key string, fallback time.Duration) (time.Duration, error) {
	raw := strings.TrimSpace(getenv(key))
	if raw == "" {
		return fallback, nil
	}
	v, err := time.ParseDuration(raw)
	if err != nil {
		return 0, fmt.Errorf("invalid %s=%q: %w", key, raw, err)
	}
	if v <= 0 {
		return 0, fmt.Errorf("invalid %s=%q: must be > 0", key, raw)
	}
	return v, nil
}

func parseOptionalQueryExecMode(raw string) (*pgx.QueryExecMode, error) {
	switch strings.ToLower(strings.TrimSpace(raw)) {
	case "":
		return nil, nil
	case "cache_statement":
		mode := pgx.QueryExecModeCacheStatement
		return &mode, nil
	case "cache_describe":
		mode := pgx.QueryExecModeCacheDescribe
		return &mode, nil
	case "describe_exec":
		mode := pgx.QueryExecModeDescribeExec
		return &mode, nil
	case "exec":
		mode := pgx.QueryExecModeExec
		return &mode, nil
	case "simple_protocol":
		mode := pgx.QueryExecModeSimpleProtocol
		return &mode, nil
	default:
		return nil, fmt.Errorf("invalid %s=%q: expected one of cache_statement, cache_describe, describe_exec, exec, simple_protocol", envQueryExecMode, strings.TrimSpace(raw))
	}
}

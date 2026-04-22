package config

import (
	"log"
	"os"
	"path/filepath"
	"sync"
	"time"

	"gopkg.in/yaml.v3"
)

const (
	plansConfigPath     = "config/plans.yaml"
	configReloadInterval = 5 * time.Minute
)

type Plan struct {
	Name     string             `yaml:"name"`
	Model    string             `yaml:"model"`
	Quotas   PlanQuotas         `yaml:"quotas"`
	Features PlanFeatures       `yaml:"features"`
}

type PlanQuotas struct {
	OrdersPerDay  int `yaml:"orders_per_day"`
	TokensPerDay  int `yaml:"tokens_per_day"`
}

type PlanFeatures struct {
	Sources  []string `yaml:"sources"`
	Pipeline []string `yaml:"pipeline"`
	FeedbackLoop bool `yaml:"feedback_loop"`
}

type SubscriptionConfig struct {
	Plans map[string]Plan `yaml:"plans"`
}

var (
	subscriptionConfig     *SubscriptionConfig
	subscriptionConfigLock sync.RWMutex
)

func LoadSubscriptionConfig() error {
	absPath, err := filepath.Abs(plansConfigPath)
	if err != nil {
		return err
	}

	data, err := os.ReadFile(absPath)
	if err != nil {
		return err
	}

	var cfg SubscriptionConfig
	if err := yaml.Unmarshal(data, &cfg); err != nil {
		return err
	}

	subscriptionConfigLock.Lock()
	subscriptionConfig = &cfg
	subscriptionConfigLock.Unlock()

	return nil
}

func GetSubscriptionConfig() *SubscriptionConfig {
	subscriptionConfigLock.RLock()
	defer subscriptionConfigLock.RUnlock()
	return subscriptionConfig
}

func StartSubscriptionConfigReloader() {
	ticker := time.NewTicker(configReloadInterval)
	defer ticker.Stop()

	for range ticker.C {
		if err := LoadSubscriptionConfig(); err != nil {
			log.Printf("ERROR: Failed to reload subscription config: %v", err)
		} else {
			log.Println("Subscription config reloaded successfully.")
		}
	}
}

func init() {
	if err := LoadSubscriptionConfig(); err != nil {
		log.Fatalf("FATAL: Failed to load initial subscription config: %v", err)
	}
}
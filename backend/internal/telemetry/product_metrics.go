package telemetry

import (
	"strconv"

	"github.com/prometheus/client_golang/prometheus"
)

const (
	productEventsTotalName = "siteparser_product_events_total"
	productConversionName  = "siteparser_product_conversion_rate"
)

// ProductMetrics хранит Prometheus-метрики для продуктовой аналитики.
type ProductMetrics struct {
	eventsTotal            *prometheus.CounterVec
	conversionRate         *prometheus.GaugeVec
	featureUsageTotal      *prometheus.CounterVec // New: total feature usage by plan and feature name
	quotaExceededTotal     *prometheus.CounterVec // New: total quota exceeded events by plan and quota type
	llmTokenEstimateCost   *prometheus.CounterVec // New: estimated LLM token cost by model, plan, and consumed tokens
}

// NewProductMetrics создаёт метрики для отслеживания продуктовых событий.
func NewProductMetrics(reg prometheus.Registerer) *ProductMetrics {
	m := &ProductMetrics{
		eventsTotal: prometheus.NewCounterVec(
			prometheus.CounterOpts{
				Name: productEventsTotalName,
				Help: "Total number of product events by type.",
			},
			[]string{"event_type", "source"},
		),
		conversionRate: prometheus.NewGaugeVec(
			prometheus.GaugeOpts{
				Name: productConversionName,
				Help: "Conversion rate for key funnels (registration→profile completion, profile→notification).",
			},
			[]string{"funnel"},
		),
	}

	reg.MustRegister(m.eventsTotal, m.conversionRate, m.featureUsageTotal, m.quotaExceededTotal, m.llmTokenEstimateCost)

	// Инициализируем лейблы с нулевыми значениями для visibility
	m.eventsTotal.WithLabelValues("user_registered", "").Add(0)
	m.eventsTotal.WithLabelValues("profile_updated", "").Add(0)
	m.eventsTotal.WithLabelValues("profile_completed", "").Add(0)
	m.eventsTotal.WithLabelValues("preferences_updated", "").Add(0)
	m.eventsTotal.WithLabelValues("notify_hour_updated", "").Add(0)
	m.eventsTotal.WithLabelValues("pause_updated", "").Add(0)
	m.eventsTotal.WithLabelValues("feedback_submitted", "").Add(0)
	m.eventsTotal.WithLabelValues("notification_sent", "").Add(0)

	m.conversionRate.WithLabelValues("registration_to_profile").Set(0)
	m.conversionRate.WithLabelValues("profile_to_notification").Set(0)

	// Инициализируем лейблы для новых метрик с нулевыми значениями
	m.featureUsageTotal.WithLabelValues("free", "sites").Add(0)
	m.featureUsageTotal.WithLabelValues("free", "keyword_search").Add(0)
	m.quotaExceededTotal.WithLabelValues("free", "orders_per_day").Add(0)
	m.llmTokenEstimateCost.WithLabelValues("Gemini flash lite", "free", "0").Add(0)

	return m
}

// RecordEvent записывает продуктовое событие в Prometheus.
func (m *ProductMetrics) RecordEvent(eventType, source string) {
	m.eventsTotal.WithLabelValues(eventType, source).Inc()
}

// SetConversionRate устанавливает конверсию для воронки.
func (m *ProductMetrics) SetConversionRate(funnel string, rate float64) {
	m.conversionRate.WithLabelValues(funnel).Set(rate)
}

// RecordFeatureUsage записывает использование фичи в Prometheus.
func (m *ProductMetrics) RecordFeatureUsage(planID, featureName string) {
	m.featureUsageTotal.WithLabelValues(planID, featureName).Inc()
}

// RecordQuotaExceeded записывает превышение квоты в Prometheus.
func (m *ProductMetrics) RecordQuotaExceeded(planID, quotaType string) {
	m.quotaExceededTotal.WithLabelValues(planID, quotaType).Inc()
}

// RecordLLMTokenEstimateCost записывает оценку стоимости токенов LLM в Prometheus.
func (m *ProductMetrics) RecordLLMTokenEstimateCost(modelID, planID string, tokensConsumed float64) {
	m.llmTokenEstimateCost.WithLabelValues(modelID, planID, strconv.FormatFloat(tokensConsumed, 'f', 0, 64)).Add(tokensConsumed)
}

// Funnel constants
const (
	FunnelRegToProfile    = "registration_to_profile"
	FunnelProfileToNotify = "profile_to_notification"
)

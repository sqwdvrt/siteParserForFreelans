package telemetry

import (
	"github.com/prometheus/client_golang/prometheus"
)

const (
	productEventsTotalName = "siteparser_product_events_total"
	productConversionName  = "siteparser_product_conversion_rate"
)

// ProductMetrics хранит Prometheus-метрики для продуктовой аналитики.
type ProductMetrics struct {
	eventsTotal    *prometheus.CounterVec
	conversionRate *prometheus.GaugeVec
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

	reg.MustRegister(m.eventsTotal, m.conversionRate)

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

// Funnel constants
const (
	FunnelRegToProfile    = "registration_to_profile"
	FunnelProfileToNotify = "profile_to_notification"
)

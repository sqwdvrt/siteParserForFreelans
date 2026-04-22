package port

import (
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/config"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
)

// SubscriptionPolicy — политика подписок, абстракция для получения инфы о тарифах.
type SubscriptionPolicy interface {
	GetPlan(planID string) (*config.Plan, error)
	CanAccessFeature(userPlanID string, feature string) (bool, error)
	GetOrdersPerDayLimit(userPlanID string) (int, error)
	GetTokensPerDayLimit(userPlanID string) (int, error)
	GetModelForPipelineStage(userPlanID string, stage string) (string, error)
}

// DailyCounter — интерфейс для дневного счётчика заказов.
type DailyCounter interface {
	Increment(ctx context.Context, userID int64) (int, error)
	Get(ctx context.Context, userID int64) (int, error)
}

// Ensure the domain.User has a PlanID for the policy.
var _ = domain.User{PlanID: "test"}

// Ensure the config.Plan exists for policy.
var _ = config.Plan{}

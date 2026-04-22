package subscription

import (
	"fmt"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
)

type Guard struct {
	policy *Policy
}

func NewGuard(policy *Policy) *Guard {
	return &Guard{
		policy: policy,
	}
}

func (g *Guard) CanUserAccessFeature(user *domain.User, feature string) (bool, error) {
	if user == nil {
		return false, fmt.Errorf("user is nil")
	}
	if user.PlanID == "" {
		// Default to 'free' plan if not explicitly set
		user.PlanID = "free"
	}
	return g.policy.CanAccessFeature(user.PlanID, feature)
}

func (g *Guard) GetUserOrdersPerDayLimit(user *domain.User) (int, error) {
	if user == nil {
		return 0, fmt.Errorf("user is nil")
	}
	if user.PlanID == "" {
		user.PlanID = "free"
	}
	return g.policy.GetOrdersPerDayLimit(user.PlanID)
}

func (g *Guard) GetUserTokensPerDayLimit(user *domain.User) (int, error) {
	if user == nil {
		return 0, fmt.Errorf("user is nil")
	}
	if user.PlanID == "" {
		user.PlanID = "free"
	}
	return g.policy.GetTokensPerDayLimit(user.PlanID)
}

func (g *Guard) GetUserModelForPipelineStage(user *domain.User, stage string) (string, error) {
	if user == nil {
		return "", fmt.Errorf("user is nil")
	}
	if user.PlanID == "" {
		user.PlanID = "free"
	}
	return g.policy.GetModelForPipelineStage(user.PlanID, stage)
}

package subscription

import (
	"fmt"

	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/config"
	"github.com/sqwdvrt/siteParserForFreelans/backend/internal/domain"
)

type Policy struct {
	cfg *config.SubscriptionConfig
}

func NewPolicy() *Policy {
	return &Policy{
		cfg: config.GetSubscriptionConfig(),
	}
}

func (p *Policy) GetPlan(planID string) (*config.Plan, error) {
	plan, ok := p.cfg.Plans[planID]
	if !ok {
		return nil, fmt.Errorf("plan with ID %s not found", planID)
	}
	return &plan, nil
}

func (p *Policy) CanAccessFeature(userPlanID string, feature string) (bool, error) {
	plan, err := p.GetPlan(userPlanID)
	if err != nil {
		return false, err
	}

	for _, f := range plan.Features.Sources {
		if f == feature {
			return true, nil
		}
	}
	for _, f := range plan.Features.Pipeline {
		if f == feature {
			return true, nil
		}
	}
	if feature == "feedback_loop" && plan.Features.FeedbackLoop {
		return true,
	}
	
	return false, nil
}

func (p *Policy) GetOrdersPerDayLimit(userPlanID string) (int, error) {
	plan, err := p.GetPlan(userPlanID)
	if err != nil {
		return 0, err
	}
	return plan.Quotas.OrdersPerDay, nil
}

func (p *Policy) GetTokensPerDayLimit(userPlanID string) (int, error) {
	plan, err := p.GetPlan(userPlanID)
	if err != nil {
		return 0, err
	}
	return plan.Quotas.TokensPerDay, nil
}

func (p *Policy) GetModelForPipelineStage(userPlanID string, stage string) (string, error) {
	plan, err := p.GetPlan(userPlanID)
	if err != nil {
		return "", err
	}
	// Simplified logic for now, assumes model is defined per plan, not per pipeline stage.
	// This can be extended later if needed.
	if plan.Model != "" {
		return plan.Model, nil
	}
	return "", fmt.Errorf("no specific model defined for pipeline stage %s in plan %s", stage, userPlanID)
}

// Ensure the user struct in domain has PlanID field
var _ = domain.User{PlanID: "test"}

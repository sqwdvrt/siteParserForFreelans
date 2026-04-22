import logging
from typing import Optional

from ai_service.config.subscription import get_subscription_config, Plan
from ai_service.domain.user import User

logger = logging.getLogger(__name__)

class SubscriptionGuard:
    def __init__(self):
        pass

    def _get_plan(self, plan_id: str) -> Optional[Plan]:
        cfg = get_subscription_config()
        return cfg.plans.get(plan_id)

    def can_access_feature(self, user: User, feature: str) -> bool:
        if user is None:
            logger.warning("Attempted to check feature access for a None user.")
            return False

        plan_id = user.plan_id if user.plan_id else "free"
        plan = self._get_plan(plan_id)
        if not plan:
            logger.error(f"Plan with ID {plan_id} not found for user {user.id}. Denying access to feature {feature}.")
            return False

        if feature in plan.features.sources:
            return True
        if feature in plan.features.pipeline:
            return True
        if feature == "feedback_loop" and plan.features.feedback_loop:
            return True
        
        return False

    def get_orders_per_day_limit(self, user: User) -> int:
        if user is None:
            logger.warning("Attempted to get orders per day limit for a None user. Returning 0.")
            return 0
        plan_id = user.plan_id if user.plan_id else "free"
        plan = self._get_plan(plan_id)
        if not plan:
            logger.error(f"Plan with ID {plan_id} not found for user {user.id}. Returning 0 for orders_per_day.")
            return 0
        return plan.quotas.orders_per_day

    def get_tokens_per_day_limit(self, user: User) -> int:
        if user is None:
            logger.warning("Attempted to get tokens per day limit for a None user. Returning 0.")
            return 0
        plan_id = user.plan_id if user.plan_id else "free"
        plan = self._get_plan(plan_id)
        if not plan:
            logger.error(f"Plan with ID {plan_id} not found for user {user.id}. Returning 0 for tokens_per_day.")
            return 0
        return plan.quotas.tokens_per_day

    def get_model_for_pipeline_stage(self, user: User, stage: str) -> Optional[str]:
        if user is None:
            logger.warning("Attempted to get model for pipeline stage for a None user. Returning None.")
            return None
        plan_id = user.plan_id if user.plan_id else "free"
        plan = self._get_plan(plan_id)
        if not plan:
            logger.error(f"Plan with ID {plan_id} not found for user {user.id}. Returning None for model.")
            return None
        # Simplified logic for now, assumes model is defined per plan, not per pipeline stage.
        # This can be extended later if needed.
        return plan.model
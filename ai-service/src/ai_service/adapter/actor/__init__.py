"""Actor adapters."""

from ai_service.adapter.actor.fallback_actor import FallbackActorAgent
from ai_service.adapter.actor.rule_based_actor import RuleBasedActorAgent

__all__ = ["FallbackActorAgent", "RuleBasedActorAgent"]

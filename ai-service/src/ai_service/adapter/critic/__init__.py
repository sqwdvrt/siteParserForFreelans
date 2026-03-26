"""Critic adapters."""

from ai_service.adapter.critic.fallback_critic import FallbackCriticAgent
from ai_service.adapter.critic.rule_based_critic import RuleBasedCriticAgent

__all__ = ["FallbackCriticAgent", "RuleBasedCriticAgent"]

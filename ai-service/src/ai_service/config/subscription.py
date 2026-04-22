import yaml
import os
import threading
import time
import logging

logger = logging.getLogger(__name__)

PLANS_CONFIG_PATH = "config/plans.yaml"
CONFIG_RELOAD_INTERVAL = 300  # 5 minutes

class PlanQuotas:
    def __init__(self, orders_per_day: int, tokens_per_day: int):
        self.orders_per_day = orders_per_day
        self.tokens_per_day = tokens_per_day

class PlanFeatures:
    def __init__(self, sources: list[str], pipeline: list[str], feedback_loop: bool = False):
        self.sources = sources
        self.pipeline = pipeline
        self.feedback_loop = feedback_loop

class Plan:
    def __init__(self, name: str, model: str, quotas: dict, features: dict):
        self.name = name
        self.model = model
        self.quotas = PlanQuotas(**quotas)
        self.features = PlanFeatures(**features)

class SubscriptionConfig:
    def __init__(self, plans: dict):
        self.plans = {k: Plan(name=v['name'], model=v['model'], quotas=v['quotas'], features=v['features']) for k, v in plans.items()}

_subscription_config: SubscriptionConfig = None
_subscription_config_lock = threading.RLock()

def load_subscription_config():
    global _subscription_config
    try:
        with open(PLANS_CONFIG_PATH, 'r') as f:
            data = yaml.safe_load(f)
        with _subscription_config_lock:
            _subscription_config = SubscriptionConfig(**data)
        logger.info("Subscription config loaded successfully.")
    except Exception as e:
        logger.error(f"Failed to load subscription config: {e}")
        raise

def get_subscription_config() -> SubscriptionConfig:
    with _subscription_config_lock:
        return _subscription_config

def start_subscription_config_reloader():
    def reloader_loop():
        while True:
            time.sleep(CONFIG_RELOAD_INTERVAL)
            try:
                load_subscription_config()
                logger.info("Subscription config reloaded successfully.")
            except Exception as e:
                logger.error(f"Failed to reload subscription config: {e}")

    reloader_thread = threading.Thread(target=reloader_loop, daemon=True)
    reloader_thread.start()

# Initial load
load_subscription_config()

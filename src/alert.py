from __future__ import annotations

import logging
import os
from typing import Any

import requests

from .config import EnvironmentConfig


class WeChatAlert:
    def __init__(self, config: EnvironmentConfig, logger: logging.Logger) -> None:
        settings = config.section("alert")
        env_name = str(settings.get("webhook_env", "WECHAT_WEBHOOK_URL"))
        self.webhook_url = os.getenv(env_name, "").strip()
        self.logger = logger
        self.environment = config.name

    @property
    def enabled(self) -> bool:
        return bool(self.webhook_url)

    def send(self, title: str, detail: str, *, level: str = "warning") -> bool:
        if not self.enabled:
            self.logger.debug("Enterprise WeChat alert is disabled")
            return False
        content = (
            f"### {title}\n"
            f"> Environment: `{self.environment}`\n"
            f"> Level: `{level}`\n"
            f"> Detail: {detail[:1500]}"
        )
        payload: dict[str, Any] = {
            "msgtype": "markdown",
            "markdown": {"content": content},
        }
        try:
            response = requests.post(self.webhook_url, json=payload, timeout=5)
            response.raise_for_status()
            result = response.json()
            if int(result.get("errcode", -1)) != 0:
                raise RuntimeError(f"WeChat API returned: {result}")
            return True
        except Exception as exc:
            self.logger.error("Unable to send Enterprise WeChat alert: %s", exc)
            return False

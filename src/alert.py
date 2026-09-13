from __future__ import annotations

import logging
import os
from typing import Any

import smtplib
from email.message import EmailMessage

from .config import EnvironmentConfig


class EmailAlert:
    def __init__(self, config: EnvironmentConfig, logger: logging.Logger) -> None:
        settings = config.section("alert")
        self.smtp_host = os.getenv(str(settings.get("smtp_host_env", "SMTP_HOST")), "").strip()
        self.smtp_port = int(os.getenv(str(settings.get("smtp_port_env", "SMTP_PORT")), "587"))
        self.smtp_user = os.getenv(str(settings.get("smtp_user_env", "SMTP_USERNAME")), "").strip()
        self.smtp_password = os.getenv(str(settings.get("smtp_password_env", "SMTP_PASSWORD")), "")
        self.smtp_starttls = os.getenv(str(settings.get("smtp_starttls_env", "SMTP_STARTTLS")), "1") == "1"
        self.sender = os.getenv(str(settings.get("sender_env", "ALERT_EMAIL_FROM")), self.smtp_user).strip()
        self.recipient = "3324095959@qq.com"
        self.logger = logger
        self.environment = config.name

    @property
    def enabled(self) -> bool:
        return bool(self.smtp_host and self.sender and self.recipient)

    def send(self, title: str, detail: str, *, level: str = "warning") -> bool:
        if not self.enabled:
            self.logger.debug("Email alert is disabled")
            return False
        message = EmailMessage()
        message["Subject"] = f"[{level.upper()}] {title}"
        message["From"] = self.sender
        message["To"] = self.recipient
        message.set_content(f"Environment: {self.environment}\nLevel: {level}\nDetail: {detail[:4000]}")
        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=5) as smtp:
                if self.smtp_starttls:
                    smtp.starttls()
                if self.smtp_user:
                    smtp.login(self.smtp_user, self.smtp_password)
                smtp.send_message(message)
            return True
        except Exception as exc:
            self.logger.error("Unable to send email alert: %s", exc)
            return False


# Backward-compatible import name for integrations using the old class.
WeChatAlert = EmailAlert

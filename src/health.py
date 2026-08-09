from __future__ import annotations

import logging
import time

import requests


class HealthCheckError(RuntimeError):
    pass


def wait_for_http_health(
    url: str,
    *,
    timeout: int = 120,
    interval: float = 3.0,
    request_timeout: float = 3.0,
    logger: logging.Logger | None = None,
) -> None:
    deadline = time.monotonic() + timeout
    last_error = "no response"
    while time.monotonic() < deadline:
        try:
            response = requests.get(url, timeout=request_timeout)
            if 200 <= response.status_code < 300:
                if logger:
                    logger.info("Health check passed: %s", url)
                return
            last_error = f"HTTP {response.status_code}: {response.text[:200]}"
        except requests.RequestException as exc:
            last_error = str(exc)
        if logger:
            logger.debug("Health check pending: %s (%s)", url, last_error)
        time.sleep(interval)
    raise HealthCheckError(
        f"Health check did not pass within {timeout}s: {url}; last error: {last_error}"
    )


from __future__ import annotations

import logging
from pathlib import Path

from .audit import AuditLog, ReleaseStateStore
from .command import CommandResult, run_command
from .config import PROJECT_ROOT, EnvironmentConfig
from .health import wait_for_http_health


class ComposeDeploymentError(RuntimeError):
    pass


class ComposeDeployer:
    def __init__(
        self,
        config: EnvironmentConfig,
        logger: logging.Logger,
        audit: AuditLog,
        operator: str,
    ) -> None:
        self.config = config
        self.logger = logger
        self.audit = audit
        self.operator = operator
        self.settings = config.section("compose")
        self.compose_file = config.resolve_path(str(self.settings["file"]))
        self.project_name = str(self.settings["project_name"])
        self.service = str(self.settings.get("service", "web"))
        self.state = ReleaseStateStore(config.name, "compose")
        if not self.compose_file.is_file():
            raise ComposeDeploymentError(f"Compose file not found: {self.compose_file}")

    def deploy(self, image: str, *, version: str | None = None) -> None:
        previous = self.state.load().get("current_image")
        try:
            self._up(image)
            self._wait_healthy()
            self.state.mark_success(image)
            self._audit("deploy", "success", image, version)
            self.logger.info("Compose deployment succeeded: %s", image)
        except Exception as exc:
            rollback_detail = ""
            if previous and previous != image:
                self.logger.error("Deployment failed; rolling back to %s", previous)
                try:
                    self._up(str(previous))
                    self._wait_healthy()
                    rollback_detail = f"; rollback succeeded: {previous}"
                except Exception as rollback_exc:
                    rollback_detail = f"; rollback failed: {rollback_exc}"
            detail = f"{exc}{rollback_detail}"
            self._audit("deploy", "failed", image, version, detail)
            raise ComposeDeploymentError(detail) from exc

    def rollback(self) -> str:
        target = self.state.rollback_target()
        if not target:
            raise ComposeDeploymentError("No previous successful Compose release is available")
        try:
            self._up(target)
            self._wait_healthy()
            self.state.mark_success(target, reason="rollback")
            self._audit("rollback", "success", target)
            self.logger.info("Compose rollback succeeded: %s", target)
            return target
        except Exception as exc:
            self._audit("rollback", "failed", target, detail=str(exc))
            raise ComposeDeploymentError(f"Compose rollback failed: {exc}") from exc

    def restart(self) -> None:
        self._run(["restart", self.service])
        self._wait_healthy()
        self._audit("restart", "success", self.current_image)

    def stop(self) -> None:
        self._run(["stop"])
        self._audit("stop", "success", self.current_image)

    def scale(self, replicas: int) -> None:
        if replicas < 1 or replicas > 20:
            raise ComposeDeploymentError("Replica count must be between 1 and 20")
        image = self.current_image
        if not image:
            raise ComposeDeploymentError("Deploy an image before scaling the Compose service")
        self._run(["up", "-d", "--scale", f"{self.service}={replicas}"], image=image)
        self._run(["restart", "gateway"], image=image)
        self._wait_healthy()
        self._audit("scale", "success", image, detail=f"replicas={replicas}")

    def status(self) -> CommandResult:
        return self._run(["ps"], image=self.current_image, check=False)

    @property
    def current_image(self) -> str | None:
        value = self.state.load().get("current_image")
        return str(value) if value else None

    def _up(self, image: str) -> None:
        self._run(["up", "-d", "--remove-orphans"], image=image)

    def _wait_healthy(self) -> None:
        wait_for_http_health(
            str(self.settings["health_url"]),
            timeout=int(self.settings.get("timeout", 120)),
            logger=self.logger,
        )

    def _run(
        self,
        arguments: list[str],
        *,
        image: str | None = None,
        check: bool = True,
    ) -> CommandResult:
        resolved_image = image or self.current_image or "auto-deploy-placeholder:latest"
        environment = {"APP_IMAGE": resolved_image}
        env_file = PROJECT_ROOT / ".env"
        env_arguments = ["--env-file", str(env_file)] if env_file.is_file() else []
        return run_command(
            [
                "docker",
                "compose",
                *env_arguments,
                "--file",
                str(self.compose_file),
                "--project-name",
                self.project_name,
                *arguments,
            ],
            cwd=self.compose_file.parents[2],
            env=environment,
            check=check,
            logger=self.logger,
        )

    def _audit(
        self,
        action: str,
        result: str,
        image: str | None,
        version: str | None = None,
        detail: str | None = None,
    ) -> None:
        self.audit.record(
            action=action,
            environment=self.config.name,
            engine="compose",
            operator=self.operator,
            version=version,
            image=image,
            result=result,
            detail=detail,
        )

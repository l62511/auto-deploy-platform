from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import docker
from docker.errors import BuildError, DockerException

from .audit import AuditLog
from .command import run_command
from .config import EnvironmentConfig, validate_version


class ImageBuildError(RuntimeError):
    pass


class ImageBuilder:
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

    def prepare_source(self) -> tuple[Path, str | None]:
        source = self.config.section("source")
        directory = self.config.resolve_path(str(source.get("directory", "app_dockerfile")))
        repository = str(source.get("repository", "")).strip()
        branch = str(source.get("branch", "main"))
        target_commit = os.getenv("SOURCE_COMMIT", "").strip()
        if target_commit and not re.fullmatch(r"[0-9a-fA-F]{7,64}", target_commit):
            raise ImageBuildError("SOURCE_COMMIT must be a 7-64 character hexadecimal Git id")

        if repository:
            if (directory / ".git").is_dir():
                run_command(
                    ["git", "-C", str(directory), "fetch", "--prune", "origin"],
                    logger=self.logger,
                )
            elif directory.exists() and any(directory.iterdir()):
                raise ImageBuildError(
                    f"Source directory exists but is not a Git checkout: {directory}"
                )
            else:
                directory.parent.mkdir(parents=True, exist_ok=True)
                run_command(
                    ["git", "clone", "--branch", branch, "--single-branch", repository, str(directory)],
                    logger=self.logger,
                )
            if target_commit:
                run_command(
                    ["git", "-C", str(directory), "checkout", "--detach", target_commit],
                    logger=self.logger,
                )
            else:
                branch_exists = run_command(
                    [
                        "git",
                        "-C",
                        str(directory),
                        "show-ref",
                        "--verify",
                        "--quiet",
                        f"refs/heads/{branch}",
                    ],
                    check=False,
                )
                if branch_exists.returncode == 0:
                    run_command(
                        ["git", "-C", str(directory), "checkout", branch],
                        logger=self.logger,
                    )
                else:
                    run_command(
                        [
                            "git",
                            "-C",
                            str(directory),
                            "checkout",
                            "--track",
                            "-b",
                            branch,
                            f"origin/{branch}",
                        ],
                        logger=self.logger,
                    )
                run_command(
                    [
                        "git",
                        "-C",
                        str(directory),
                        "merge",
                        "--ff-only",
                        f"origin/{branch}",
                    ],
                    logger=self.logger,
                )

        if not directory.is_dir():
            raise ImageBuildError(f"Build context does not exist: {directory}")

        commit: str | None = None
        if (directory / ".git").is_dir():
            result = run_command(
                ["git", "-C", str(directory), "rev-parse", "HEAD"], logger=self.logger
            )
            commit = result.output
        return directory, commit

    def build(self, version: str, *, push: bool = False) -> str:
        normalized_version = validate_version(version)
        image_ref = self.config.image_ref(normalized_version)
        context, commit = self.prepare_source()
        source = self.config.section("source")
        build = self.config.section("build")
        dockerfile = str(source.get("dockerfile", "Dockerfile"))
        dockerfile_path = context / dockerfile
        if not dockerfile_path.is_file():
            raise ImageBuildError(f"Dockerfile does not exist: {dockerfile_path}")

        self.logger.info("Building image %s from %s", image_ref, context)
        try:
            client = docker.from_env()
            client.ping()
            build_args = {
                "APP_VERSION": normalized_version,
                "GIT_COMMIT": commit or "unknown",
            }
            if build.get("pip_index_url"):
                build_args["PIP_INDEX_URL"] = str(build["pip_index_url"])
            _, logs = client.images.build(
                path=str(context),
                dockerfile=dockerfile,
                tag=image_ref,
                rm=True,
                pull=bool(build.get("pull_base_images", True)),
                platform=build.get("platform"),
                buildargs=build_args,
            )
            for entry in logs:
                message = entry.get("stream") if isinstance(entry, dict) else None
                if message:
                    self.logger.debug(message.rstrip())
            if push:
                self._push(client, image_ref)
        except BuildError as exc:
            detail = "".join(
                str(item.get("stream", item)) for item in (exc.build_log or [])
            )[-4000:]
            self._audit(normalized_version, image_ref, "failed", detail or str(exc))
            raise ImageBuildError(f"Image build failed: {detail or exc}") from exc
        except DockerException as exc:
            self._audit(normalized_version, image_ref, "failed", str(exc))
            raise ImageBuildError(f"Docker operation failed: {exc}") from exc
        except ImageBuildError as exc:
            self._audit(normalized_version, image_ref, "failed", str(exc))
            raise

        self._audit(normalized_version, image_ref, "success", f"commit={commit or 'n/a'}")
        self.logger.info("Image ready: %s", image_ref)
        return image_ref

    def cleanup_dangling(self) -> dict[str, Any]:
        try:
            result = docker.from_env().images.prune(filters={"dangling": True})
        except DockerException as exc:
            raise ImageBuildError(f"Unable to prune dangling images: {exc}") from exc
        reclaimed = int(result.get("SpaceReclaimed", 0))
        self.logger.info("Removed dangling images; reclaimed %d bytes", reclaimed)
        return result

    def _push(self, client: docker.DockerClient, image_ref: str) -> None:
        repository, tag = image_ref.rsplit(":", 1)
        self.logger.info("Pushing image %s", image_ref)
        for event in client.images.push(repository, tag=tag, stream=True, decode=True):
            if "error" in event:
                raise ImageBuildError(f"Registry push failed: {event['error']}")
            status = event.get("status")
            if status:
                self.logger.debug("Registry: %s", status)

    def _audit(
        self, version: str, image: str, result: str, detail: str | None = None
    ) -> None:
        self.audit.record(
            action="build_push",
            environment=self.config.name,
            engine="docker",
            operator=self.operator,
            version=version,
            image=image,
            result=result,
            detail=detail,
        )

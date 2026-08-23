from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from typing import Any

from .alert import WeChatAlert
from .audit import AuditLog
from .builder import ImageBuilder
from .command import run_command
from .config import PROJECT_ROOT, EnvironmentConfig, load_environment
from .deploy_compose import ComposeDeployer
from .deploy_k8s import K8sDeployer
from .logger import setup_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="auto-deploy",
        description="Build images and deploy them with Docker Compose or K3s.",
    )
    parser.add_argument("--verbose", action="store_true", help="show debug logs")
    parser.add_argument(
        "--operator",
        default=getpass.getuser(),
        help="operator recorded in the audit log",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("check", help="run environment prechecks")
    _environment_argument(check)
    _engine_argument(check)

    build = subparsers.add_parser("build", help="build an application image")
    _environment_argument(build)
    build.add_argument("--version", required=True)
    build.add_argument("--push", action="store_true")

    deploy = subparsers.add_parser("deploy", help="deploy an existing image")
    _environment_argument(deploy)
    _engine_argument(deploy)
    image_group = deploy.add_mutually_exclusive_group(required=True)
    image_group.add_argument("--version", help="version mapped to the configured image tag")
    image_group.add_argument("--image", help="complete image reference")

    pipeline = subparsers.add_parser("pipeline", help="precheck, build, push and deploy")
    _environment_argument(pipeline)
    _engine_argument(pipeline)
    pipeline.add_argument("--version", required=True)
    pipeline.add_argument(
        "--no-push",
        action="store_true",
        help="do not push the image (Compose-only local debugging)",
    )

    rollback = subparsers.add_parser("rollback", help="roll back to the previous release")
    _environment_argument(rollback)
    _engine_argument(rollback)

    status = subparsers.add_parser("status", help="show deployment status")
    _environment_argument(status)
    _engine_argument(status)

    compose = subparsers.add_parser("compose", help="manage the Compose application")
    _environment_argument(compose)
    compose.add_argument("action", choices=("restart", "stop", "scale"))
    compose.add_argument("--replicas", type=int)

    cleanup = subparsers.add_parser("cleanup-images", help="remove dangling Docker images")
    _environment_argument(cleanup)

    backup = subparsers.add_parser("backup", help="back up MySQL and configuration")
    _environment_argument(backup)

    audit = subparsers.add_parser("audit", help="show recent audit events")
    audit.add_argument("--limit", type=int, default=20)

    webhook = subparsers.add_parser("webhook", help="run the Gitee webhook service")
    webhook.add_argument("--host", default=os.getenv("HOOK_HOST", "0.0.0.0"))
    webhook.add_argument("--port", type=int, default=int(os.getenv("HOOK_PORT", "9000")))
    return parser


def _environment_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--env", choices=("dev", "test"), required=True)


def _engine_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--engine", choices=("compose", "k8s"), required=True)


def run_precheck(
    config: EnvironmentConfig,
    engine: str,
    logger: Any,
    *,
    require_docker: bool | None = None,
) -> None:
    settings = config.section("precheck")
    ports = settings.get("required_free_ports", [])
    if not isinstance(ports, list):
        raise ValueError("precheck.required_free_ports must be a list")
    environment = {
        "DISK_THRESHOLD": str(settings.get("disk_threshold", 85)),
        "NETWORK_TARGET": str(settings.get("network_target", "")),
        "REQUIRED_FREE_PORTS": ",".join(str(port) for port in ports),
        "CHECK_DOCKER": "1"
        if (settings.get("check_docker", True) if require_docker is None else require_docker)
        else "0",
        "PROJECT_PATH": str(PROJECT_ROOT),
    }
    run_command(
        ["bash", str(PROJECT_ROOT / "scripts" / "env_check.sh"), engine],
        cwd=PROJECT_ROOT,
        env=environment,
        logger=logger,
    )


def _deployer(
    engine: str,
    config: EnvironmentConfig,
    logger: Any,
    audit: AuditLog,
    operator: str,
) -> ComposeDeployer | K8sDeployer:
    if engine == "compose":
        return ComposeDeployer(config, logger, audit, operator)
    return K8sDeployer(config, logger, audit, operator)


def _run_backup(config: EnvironmentConfig, logger: Any) -> None:
    settings = config.section("backup")
    environment = {
        "MYSQL_CONTAINER": str(settings.get("mysql_container", "")),
        "MYSQL_DATABASE": str(settings.get("mysql_database", "demo")),
        "CONFIG_PATH": str(config.resolve_path(str(settings.get("config_path", "config")))),
        "RETENTION_DAYS": str(settings.get("retention_days", 14)),
    }
    run_command(
        ["bash", str(PROJECT_ROOT / "scripts" / "backup.sh")],
        cwd=PROJECT_ROOT,
        env=environment,
        logger=logger,
    )


def _show_audit(limit: int) -> None:
    if limit < 1 or limit > 1000:
        raise ValueError("Audit limit must be between 1 and 1000")
    database_url = os.getenv("PLATFORM_DATABASE_URL", "").strip()
    if database_url and os.getenv("PLATFORM_FILE_AUDIT", "0") != "1":
        from .control_plane import ControlPlaneStore

        for event in ControlPlaneStore(database_url).list_audit(limit):
            print(json.dumps(event, ensure_ascii=False, indent=2))
        return
    path = PROJECT_ROOT / "releases" / "audit.jsonl"
    if not path.exists():
        print("No audit events recorded.")
        return
    with path.open("r", encoding="utf-8") as handle:
        lines = handle.readlines()[-limit:]
    for line in lines:
        print(json.dumps(json.loads(line), ensure_ascii=False, indent=2))


def execute(args: argparse.Namespace, logger: Any) -> None:
    if args.command == "audit":
        _show_audit(args.limit)
        return
    if args.command == "webhook":
        from .hook_server import create_app

        create_app(logger).run(host=args.host, port=args.port, debug=False)
        return

    config = load_environment(args.env)
    audit = AuditLog()
    builder = ImageBuilder(config, logger, audit, args.operator)

    if args.command == "check":
        run_precheck(config, args.engine, logger)
    elif args.command == "build":
        print(builder.build(args.version, push=args.push))
    elif args.command == "cleanup-images":
        print(json.dumps(builder.cleanup_dangling(), ensure_ascii=False, indent=2))
    elif args.command == "backup":
        _run_backup(config, logger)
    elif args.command == "deploy":
        run_precheck(config, args.engine, logger, require_docker=args.engine == "compose")
        image = args.image or config.image_ref(args.version)
        _deployer(args.engine, config, logger, audit, args.operator).deploy(
            image, version=args.version
        )
    elif args.command == "pipeline":
        if args.no_push and args.engine == "k8s":
            raise ValueError("Kubernetes deployments require an image pushed to the registry")
        run_precheck(config, args.engine, logger, require_docker=True)
        image = builder.build(args.version, push=not args.no_push)
        _deployer(args.engine, config, logger, audit, args.operator).deploy(
            image, version=args.version
        )
    elif args.command == "rollback":
        run_precheck(config, args.engine, logger, require_docker=args.engine == "compose")
        target = _deployer(args.engine, config, logger, audit, args.operator).rollback()
        print(target)
    elif args.command == "status":
        deployer = _deployer(args.engine, config, logger, audit, args.operator)
        result = deployer.status()
        if args.engine == "compose":
            print(result.output)
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == "compose":
        deployer = ComposeDeployer(config, logger, audit, args.operator)
        if args.action == "restart":
            deployer.restart()
        elif args.action == "stop":
            deployer.stop()
        elif args.action == "scale":
            if args.replicas is None:
                raise ValueError("--replicas is required for the scale action")
            deployer.scale(args.replicas)


def main() -> int:
    args = build_parser().parse_args()
    logger = setup_logging(args.verbose)
    config: EnvironmentConfig | None = None
    try:
        if hasattr(args, "env"):
            config = load_environment(args.env)
        execute(args, logger)
        return 0
    except KeyboardInterrupt:
        logger.error("Operation interrupted")
        return 130
    except Exception as exc:
        logger.exception("Operation failed: %s", exc) if args.verbose else logger.error(
            "Operation failed: %s", exc
        )
        if config:
            WeChatAlert(config, logger).send(
                "Auto deployment failed", f"{args.command}: {exc}", level="error"
            )
        return 1


if __name__ == "__main__":
    sys.exit(main())

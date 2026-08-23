from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from string import Template
from typing import Any

import yaml
from kubernetes import client
from kubernetes import config as kube_config
from kubernetes.client import ApiException

from .audit import AuditLog, ReleaseStateStore
from .config import EnvironmentConfig
from .health import wait_for_http_health


class K8sDeploymentError(RuntimeError):
    pass


class K8sDeployer:
    def __init__(
        self,
        config: EnvironmentConfig,
        logger: logging.Logger,
        audit: AuditLog,
        operator: str,
        *,
        connect: bool = True,
    ) -> None:
        self.config = config
        self.logger = logger
        self.audit = audit
        self.operator = operator
        self.settings = config.section("k8s")
        self.namespace = str(self.settings["namespace"])
        self.deployment_name = str(self.settings["deployment_name"])
        self.container_name = str(self.settings.get("container_name", config.data["project_name"]))
        self.state = ReleaseStateStore(config.name, "k8s")
        if connect:
            self._load_client_configuration()
            self.apps = client.AppsV1Api()
            self.core = client.CoreV1Api()

    def deploy(self, image: str, *, version: str | None = None) -> None:
        previous = self.state.load().get("current_image")
        try:
            self._ensure_namespace()
            resources = self.render_resources(image)
            self._apply_configmap(resources["configmap"])
            self._apply_service(resources["service"])
            self._apply_deployment(resources["deployment"])
            self._wait_for_rollout()
            self._wait_external_health()
            self.state.mark_success(image)
            self._audit("deploy", "success", image, version)
            self.logger.info("Kubernetes deployment succeeded: %s", image)
        except Exception as exc:
            rollback_detail = ""
            if previous and previous != image:
                self.logger.error("Deployment failed; rolling back to %s", previous)
                try:
                    self._set_deployment_image(str(previous))
                    self._wait_for_rollout()
                    self._wait_external_health()
                    rollback_detail = f"; rollback succeeded: {previous}"
                except Exception as rollback_exc:
                    rollback_detail = f"; rollback failed: {rollback_exc}"
            detail = f"{exc}{rollback_detail}"
            self._audit("deploy", "failed", image, version, detail)
            raise K8sDeploymentError(detail) from exc

    def rollback(self) -> str:
        target = self.state.rollback_target()
        if not target:
            raise K8sDeploymentError("No previous successful Kubernetes release is available")
        try:
            self._set_deployment_image(target)
            self._wait_for_rollout()
            self._wait_external_health()
            self.state.mark_success(target, reason="rollback")
            self._audit("rollback", "success", target)
            self.logger.info("Kubernetes rollback succeeded: %s", target)
            return target
        except Exception as exc:
            self._audit("rollback", "failed", target, detail=str(exc))
            raise K8sDeploymentError(f"Kubernetes rollback failed: {exc}") from exc

    def status(self) -> dict[str, Any]:
        try:
            deployment = self.apps.read_namespaced_deployment_status(
                self.deployment_name, self.namespace
            )
            pods = self.core.list_namespaced_pod(
                self.namespace,
                label_selector=(f"app.kubernetes.io/name={self.config.data['project_name']}"),
            )
        except ApiException as exc:
            raise K8sDeploymentError(f"Unable to read Kubernetes status: {exc}") from exc
        return {
            "deployment": {
                "name": self.deployment_name,
                "namespace": self.namespace,
                "replicas": deployment.status.replicas or 0,
                "ready_replicas": deployment.status.ready_replicas or 0,
                "updated_replicas": deployment.status.updated_replicas or 0,
                "available_replicas": deployment.status.available_replicas or 0,
            },
            "pods": [
                {
                    "name": pod.metadata.name,
                    "phase": pod.status.phase,
                    "pod_ip": pod.status.pod_ip,
                    "ready": bool(pod.status.container_statuses)
                    and all(status.ready for status in (pod.status.container_statuses or [])),
                }
                for pod in pods.items
            ],
        }

    def render_resources(self, image: str) -> dict[str, dict[str, Any]]:
        config_data = {
            str(key): str(value) for key, value in self.config.section("config_map").items()
        }
        config_hash = hashlib.sha256(
            json.dumps(config_data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:16]
        replacements: dict[str, Any] = {
            "NAMESPACE": self.namespace,
            "APP_NAME": str(self.config.data["project_name"]),
            "DEPLOYMENT_NAME": self.deployment_name,
            "SERVICE_NAME": str(
                self.settings.get("service_name", self.config.data["project_name"])
            ),
            "CONFIGMAP_NAME": str(self.settings["configmap_name"]),
            "CONTAINER_NAME": self.container_name,
            "SERVICE_ACCOUNT_NAME": str(
                self.settings.get("service_account_name", self.container_name)
            ),
            "CONTAINER_PORT": int(self.settings.get("container_port", 5000)),
            "NODE_PORT": int(self.settings["node_port"]),
            "REPLICAS": int(self.settings.get("replicas", 2)),
            "IMAGE": image,
            "CONFIG_HASH": config_hash,
        }
        templates = self.settings.get("templates", {})
        resources = {
            key: self._load_and_render_template(str(templates[key]), replacements)
            for key in ("deployment", "service", "configmap")
        }
        resources["configmap"]["data"] = config_data

        secret_name = str(self.settings.get("secret_name", "")).strip()
        if secret_name:
            containers = resources["deployment"]["spec"]["template"]["spec"]["containers"]
            containers[0].setdefault("envFrom", []).append(
                {"secretRef": {"name": secret_name, "optional": False}}
            )
        image_pull_secret = str(self.settings.get("image_pull_secret", "")).strip()
        if image_pull_secret:
            resources["deployment"]["spec"]["template"]["spec"]["imagePullSecrets"] = [
                {"name": image_pull_secret}
            ]
        return resources

    def _load_and_render_template(
        self, template_path: str, replacements: dict[str, Any]
    ) -> dict[str, Any]:
        path = self.config.resolve_path(template_path)
        if not path.is_file():
            raise K8sDeploymentError(f"Kubernetes template not found: {path}")
        with path.open("r", encoding="utf-8") as handle:
            document = yaml.safe_load(handle)
        if not isinstance(document, dict):
            raise K8sDeploymentError(f"Template must contain one YAML mapping: {path}")
        return self._substitute(document, replacements)

    def _substitute(self, value: Any, replacements: dict[str, Any]) -> Any:
        if isinstance(value, dict):
            return {key: self._substitute(item, replacements) for key, item in value.items()}
        if isinstance(value, list):
            return [self._substitute(item, replacements) for item in value]
        if isinstance(value, str):
            exact = re.fullmatch(r"\$\{([A-Z0-9_]+)\}", value)
            if exact:
                key = exact.group(1)
                if key not in replacements:
                    raise K8sDeploymentError(f"Missing template value: {key}")
                return replacements[key]
            try:
                return Template(value).substitute(
                    {key: str(item) for key, item in replacements.items()}
                )
            except KeyError as exc:
                raise K8sDeploymentError(f"Missing template value: {exc.args[0]}") from exc
        return value

    def _load_client_configuration(self) -> None:
        kubeconfig = str(self.settings.get("kubeconfig", "")).strip()
        try:
            if kubeconfig:
                kube_config.load_kube_config(config_file=str(Path(kubeconfig).expanduser()))
            else:
                kube_config.load_kube_config()
        except Exception as local_error:
            try:
                kube_config.load_incluster_config()
            except Exception as cluster_error:
                raise K8sDeploymentError(
                    f"Unable to load Kubernetes configuration: {local_error}; {cluster_error}"
                ) from cluster_error

    def _ensure_namespace(self) -> None:
        try:
            self.core.read_namespace(self.namespace)
        except ApiException as exc:
            if exc.status != 404:
                raise
            raise K8sDeploymentError(
                f"Namespace {self.namespace!r} is not provisioned; apply "
                "config/k8s/platform-namespace.yaml first"
            ) from exc

    def _apply_configmap(self, body: dict[str, Any]) -> None:
        name = body["metadata"]["name"]
        try:
            self.core.read_namespaced_config_map(name, self.namespace)
            self.core.patch_namespaced_config_map(name, self.namespace, body)
            self.logger.info("Updated ConfigMap %s", name)
        except ApiException as exc:
            if exc.status != 404:
                raise
            self.core.create_namespaced_config_map(self.namespace, body)
            self.logger.info("Created ConfigMap %s", name)

    def _apply_service(self, body: dict[str, Any]) -> None:
        name = body["metadata"]["name"]
        try:
            self.core.read_namespaced_service(name, self.namespace)
            self.core.patch_namespaced_service(name, self.namespace, body)
            self.logger.info("Updated Service %s", name)
        except ApiException as exc:
            if exc.status != 404:
                raise
            self.core.create_namespaced_service(self.namespace, body)
            self.logger.info("Created Service %s", name)

    def _apply_deployment(self, body: dict[str, Any]) -> None:
        name = body["metadata"]["name"]
        try:
            self.apps.read_namespaced_deployment(name, self.namespace)
            self.apps.patch_namespaced_deployment(name, self.namespace, body)
            self.logger.info("Updated Deployment %s", name)
        except ApiException as exc:
            if exc.status != 404:
                raise
            self.apps.create_namespaced_deployment(self.namespace, body)
            self.logger.info("Created Deployment %s", name)

    def _set_deployment_image(self, image: str) -> None:
        body = self.apps.read_namespaced_deployment(self.deployment_name, self.namespace)
        found = False
        for container in body.spec.template.spec.containers:
            if container.name == self.container_name:
                container.image = image
                found = True
        if not found:
            raise K8sDeploymentError(
                f"Container {self.container_name!r} not found in "
                f"Deployment {self.deployment_name!r}"
            )
        self.apps.patch_namespaced_deployment(self.deployment_name, self.namespace, body)

    def _wait_for_rollout(self) -> None:
        timeout = int(self.settings.get("timeout", 180))
        deadline = time.monotonic() + timeout
        last_status = "deployment not observed"
        while time.monotonic() < deadline:
            deployment = self.apps.read_namespaced_deployment_status(
                self.deployment_name, self.namespace
            )
            desired = deployment.spec.replicas or 0
            status = deployment.status
            last_status = (
                f"generation={status.observed_generation}/{deployment.metadata.generation}, "
                f"updated={status.updated_replicas or 0}/{desired}, "
                f"available={status.available_replicas or 0}/{desired}"
            )
            for condition in status.conditions or []:
                if (
                    condition.type == "Progressing"
                    and condition.status == "False"
                    and condition.reason == "ProgressDeadlineExceeded"
                ):
                    raise K8sDeploymentError(f"Rollout stopped progressing: {condition.message}")
            if (
                status.observed_generation == deployment.metadata.generation
                and (status.updated_replicas or 0) == desired
                and (status.available_replicas or 0) == desired
                and (status.unavailable_replicas or 0) == 0
            ):
                self.logger.info("Kubernetes rollout completed: %s", last_status)
                return
            self.logger.debug("Kubernetes rollout pending: %s", last_status)
            time.sleep(3)
        raise K8sDeploymentError(f"Rollout timed out after {timeout}s: {last_status}")

    def _wait_external_health(self) -> None:
        health_url = str(self.settings.get("health_url", "")).strip()
        if health_url:
            wait_for_http_health(
                health_url,
                timeout=int(self.settings.get("timeout", 180)),
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
            engine="k8s",
            operator=self.operator,
            version=version,
            image=image,
            result=result,
            detail=detail,
        )

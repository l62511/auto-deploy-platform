from __future__ import annotations

import logging

from src.audit import AuditLog
from src.config import load_environment
from src.deploy_k8s import K8sDeployer


def test_k8s_templates_render_typed_resources(tmp_path) -> None:
    config = load_environment("dev")
    deployer = K8sDeployer(
        config,
        logging.getLogger("test"),
        AuditLog(tmp_path),
        "tester",
        connect=False,
    )

    resources = deployer.render_resources("localhost:5000/demo-app:v1-dev")
    deployment = resources["deployment"]
    service = resources["service"]
    configmap = resources["configmap"]

    assert deployment["spec"]["replicas"] == 2
    assert deployment["spec"]["strategy"]["rollingUpdate"] == {
        "maxSurge": 1,
        "maxUnavailable": 0,
    }
    assert deployment["spec"]["template"]["spec"]["containers"][0]["image"].endswith(":v1-dev")
    assert service["spec"]["ports"][0]["nodePort"] == 30080
    assert configmap["data"]["APP_ENV"] == "dev"
    assert deployment["spec"]["template"]["metadata"]["annotations"]["auto-deploy/config-hash"]

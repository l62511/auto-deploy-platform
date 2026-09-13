# Auto Deploy Platform

面向单机实验和运维开发学习的自动发布平台。接收 Gitee Push WebHook，完成环境预检、源码准备、Docker 镜像构建、Registry 推送、Docker Compose/K3s 发布、健康检查、失败回滚、审计和邮件告警。

## 发布流程

开发提交代码后：

1. Flask WebHook 校验 Token、Push 事件、分支和提交 SHA。
2. 创建幂等任务；启用审批时进入 `pending_approval`。
3. Worker 从 Redis 消费任务，通过环境 Lease 防止并发发布。
4. `src.builder` 准备源码、构建镜像并推送私有 Registry。
5. `src.deploy_compose` 或 `src.deploy_k8s` 发布应用。
6. HTTP 健康检查通过后保存版本；失败自动回滚到上一成功版本。
7. 写入审计记录；失败时发送邮件到 `3324095959@qq.com`。

任务状态：`pending_approval -> queued -> running -> succeeded|failed|cancelled`，拒绝路径为 `pending_approval -> rejected`。

## 模块

| 模块 | 功能 |
| --- | --- |
| `src/main.py` | CLI：预检、构建、流水线、部署、回滚、状态、备份 |
| `src/config.py` | 环境 YAML 校验、路径和镜像引用生成 |
| `src/builder.py` | Git 源码、Docker 构建、Registry 推送、制品记录 |
| `src/deploy_compose.py` | Compose 启停、扩容、健康检查、回滚 |
| `src/deploy_k8s.py` | K3s 资源渲染、滚动更新、状态和回滚 |
| `src/hook_server.py` | WebHook、健康、任务、审批、指标接口 |
| `src/worker.py` | Redis 队列消费、任务恢复、串行发布 |
| `src/control_plane.py` | 任务、审批、Lease、审计和发布状态持久化 |
| `src/audit.py` | 审计事件、镜像制品和版本历史 |
| `src/auth.py` | JWT/OIDC 认证和角色权限 |
| `src/health.py` | HTTP 健康探测和重试 |
| `src/alert.py` | SMTP 邮件告警 |
| `src/metrics.py` | Prometheus 指标 |
| `scripts/` | 预检、备份、日志清理、K3s/Registry 初始化 |
| `config/` | 环境、Compose、Nginx、K8s 和 Registry 配置 |
| `migrations/` | Alembic 数据库迁移 |
| `tests/` | 单元和集成测试 |

## 快速开始

```bash
cp .env.example .env
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
python -m src.main check --env dev --engine compose
python -m src.main pipeline --env dev --engine compose --version v1.0.0
curl http://127.0.0.1:18080/health
```

K3s：

```bash
./scripts/setup_k3s.sh
REGISTRY_ADDRESS=localhost:5000 ./scripts/configure_k3s_registry.sh
python -m src.main pipeline --env dev --engine k8s --version v1.0.0
```

## 常用命令

```bash
python -m src.main status --env dev --engine compose
python -m src.main rollback --env dev --engine compose
python -m src.main compose --env dev scale --replicas 3
python -m src.main backup --env dev
python -m src.main audit --limit 20
```

## 邮件告警

在 `.env` 配置 SMTP（QQ 邮箱通常使用授权码）：

```dotenv
SMTP_HOST=smtp.qq.com
SMTP_PORT=587
SMTP_USERNAME=your@qq.com
SMTP_PASSWORD=your-smtp-authorization-code
SMTP_STARTTLS=1
ALERT_EMAIL_FROM=your@qq.com
```

收件人固定为 `3324095959@qq.com`。SMTP 未配置时自动禁用，发送失败只记录日志，不阻塞发布。

## 控制面与安全

```bash
docker compose --env-file .env -f config/compose/docker-compose-platform.yml up -d --build
```

生产控制面使用 PostgreSQL、Redis 和 Gunicorn。详情接口需要 `X-Health-Token`，审批需要 JWT 的 `approver` 或 `admin` 角色；生产建议使用 OIDC/JWKS，并通过受限 Docker Socket Proxy 访问 Docker。

## 备份、日志和测试

- `scripts/backup.sh` 备份 MySQL、配置并生成 SHA-256 校验文件。
- `scripts/log_clean.sh` 压缩和清理旧日志。
- 生产审计/状态写入 PostgreSQL，本地回退文件位于 `releases/`。

```bash
python -m pytest -q
bash -n scripts/*.sh
```

## 目录结构

```text
app_dockerfile/       示例业务及镜像 Dockerfile
config/               环境、Compose、K8s、Registry 配置
migrations/           数据库迁移
platform_dockerfile/  平台 API/Worker 镜像
scripts/              运维脚本
src/                  调度、构建、发布、控制面和告警
tests/                单元测试和集成测试
releases/             本地审计、制品和状态文件
```

## 配置参考

每个环境由 `config/env_<name>.yaml` 描述：

- `source`：源码仓库、分支、检出目录和 Dockerfile。
- `registry`：镜像仓库地址、仓库名及是否使用非 TLS Registry。
- `build`：构建平台、是否拉取基础镜像和 pip 源。
- `compose`：Compose 文件、Project Name、服务名和健康检查地址。
- `k8s`：Namespace、Deployment、Service、ConfigMap、NodePort、副本数和模板路径。
- `config_map`：注入应用的非敏感环境变量。
- `precheck`：磁盘阈值、网络探测地址、端口和 Docker 检查开关。
- `backup`：MySQL 容器、数据库、配置目录和保留天数。
- `release.approval_required`：是否要求人工审批。

敏感值只放在 `.env` 或 Kubernetes Secret 中，不要写入 YAML、镜像标签、日志和 Git。生产控制面至少应设置 `PLATFORM_DATABASE_URL`、`PLATFORM_REDIS_URL`、`JWT_SECRET`、`GITEE_WEBHOOK_TOKEN`、`PLATFORM_HEALTH_TOKEN` 和数据库密码。

## 运维检查清单

发布前：

- 检查 Docker Desktop、K3s API 和私有 Registry 状态。
- 确认目标端口、磁盘空间和网络连通性。
- 确认源码仓库凭据、Webhook Token 和当前分支配置。
- 确认目标环境的数据库、Redis 和持久化卷正常。

发布中：

- 观察 `logs/platform.log`、任务状态和 `/metrics`。
- Compose 使用 `docker compose logs`；K3s 使用 `kubectl describe`、Pod 事件和 Deployment 状态。
- 健康检查超时会触发自动回滚，回滚失败必须人工介入。

发布后：

- 执行 `status` 和业务 `/health` 检查。
- 核对审计事件、镜像 Digest 和实际运行镜像。
- 确认备份任务和日志清理定时任务仍在运行。

## 故障排查

| 现象 | 排查方向 |
| --- | --- |
| WebHook 返回 `401` | 检查 `GITEE_WEBHOOK_TOKEN` 和请求头 `X-Gitee-Token` |
| WebHook 返回 `409` | 同一环境已有任务运行，检查 Redis 队列和 Lease |
| 镜像构建失败 | 检查 Docker daemon、源码目录、Dockerfile 和基础镜像网络 |
| Registry 推送失败 | 检查 Registry 容器、地址、认证和 K3s `registries.yaml` |
| Compose unhealthy | 查看 `docker compose logs`，确认 MySQL/Redis 健康检查和密码 |
| K3s ImagePullBackOff | 检查 Registry 地址、containerd 信任配置和镜像 Digest |
| 发布超时 | 查看 Pod 事件、就绪探针、NodePort 访问和外部健康 URL |
| 邮件未收到 | 检查 SMTP 授权码、端口、防火墙、STARTTLS 和发件人地址 |

## 当前不足与后续建议

项目适合单机和学习场景，生产化时建议按以下优先级完善：

1. **高优先级安全项**：增加 WebHook 请求时间戳/重放保护、反向代理 HTTPS、SMTP TLS 强制校验、密钥轮换和 Secret 管理规范。
2. **高优先级可靠性**：为 Redis/PostgreSQL 增加连接重试和故障告警；补充数据库、Registry 和发布状态的异地备份及恢复演练。
3. **发布能力**：增加金丝雀/分批发布、人工暂停、超时任务取消和更细粒度的并发策略。
4. **可观测性**：扩展构建耗时、队列积压、回滚次数、健康检查失败原因等指标，并提供 Grafana Dashboard 和告警规则。
5. **测试覆盖**：补充 SMTP、OIDC/JWKS、Redis 故障、数据库并发、Webhook 重放、K3s API 异常和回滚失败场景测试。
6. **工程质量**：在 CI 中固定执行 lint、类型检查、ShellCheck、依赖漏洞扫描和 Docker 镜像扫描；为 API 增加 OpenAPI 文档。

这些项目不影响当前 Compose/K3s 单机发布链路，但会影响多节点、高并发和公网生产环境的安全性与可运维性。

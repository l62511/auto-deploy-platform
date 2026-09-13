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

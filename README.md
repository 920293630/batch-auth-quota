# batch-auth-quota

[![CI](https://github.com/920293630/batch-auth-quota/actions/workflows/ci.yml/badge.svg)](https://github.com/920293630/batch-auth-quota/actions/workflows/ci.yml)

`batch-auth-quota` 是一个独立的终端脚本工具，专门用于批量检查认证文件额度，开箱即可单独运行。

---

## 核心能力

- 批量检查 `~/.cli-proxy-api` 认证文件额度
- 支持 `.env`、环境变量、配置文件、命令行四层配置
- 支持 8 档剩余额度分类与剩余总量总览
- 支持 401 失效账号一键删除
- 支持额度耗尽账号一键隔离到隔离目录
- 支持隔离目录账号复查后，一键恢复回主认证目录
- 支持输出 `summary.json`、最新索引、分类清单与诊断结果

---

## 默认路径

- 配置文件：`~/.batch-auth-quota/config.json`
- 输出目录：`~/.batch-auth-quota/results/latest`
- 最新索引：`~/.batch-auth-quota/results/latest.json`
- 默认认证目录：`~/.cli-proxy-api`
- 默认隔离目录：`~/.cli-proxy-api/.quota_isolated`

---

## 安装方式

### 方式 1：直接运行

```bash
cd ~/batch-auth-quota
bash run.sh --type codex
```

### 方式 2：可编辑安装

```bash
cd ~/batch-auth-quota
python3 -m pip install -e . --no-deps
batch-auth-quota --type codex
```

---

## 快速开始

### 1. 进入目录

```bash
cd ~/batch-auth-quota
```

### 2. 准备 `.env`

```bash
cp .env.example .env
```

然后写入管理密钥：

```bash
CPA_MANAGEMENT_KEY=你的管理密钥
```

### 3. 准备配置文件

```bash
mkdir -p ~/.batch-auth-quota
cp config.example.json ~/.batch-auth-quota/config.json
```

### 4. 运行检查

```bash
bash run.sh --type codex
```

或：

```bash
python3 batch_auth_quota.py --type codex --auth-dir ~/.cli-proxy-api --api-base http://127.0.0.1:8317
```

---

## 配置优先级

```text
CLI 参数 > 环境变量 > config.json > 内置默认值
```

敏感信息如管理密钥不要写入 `config.json`，建议放在 `.env` 中。

---

## 常用环境变量

- `CPA_MANAGEMENT_KEY`
- `MANAGEMENT_PASSWORD`
- `BATCH_AUTH_QUOTA_CONFIG`
- `BATCH_AUTH_QUOTA_ENV_FILE`
- `BATCH_AUTH_QUOTA_AUTH_DIR`
- `BATCH_AUTH_QUOTA_API_BASE`
- `BATCH_AUTH_QUOTA_CONCURRENCY`
- `BATCH_AUTH_QUOTA_ISOLATION_DIR`

---

## 持续集成

仓库已内置最小 GitHub Actions 校验流程：`.github/workflows/ci.yml`

每次推送到 `main` 或提交 Pull Request 到 `main` 时，会自动执行：

- `python3 -m py_compile batch_auth_quota.py`
- `python3 batch_auth_quota.py --version`
- `python3 batch_auth_quota.py -h`

---

## 文档说明

- `README.md`：项目概览与快速开始
- `USAGE.md`：完整使用手册
- `config.example.json`：配置模板
- `.env.example`：环境变量模板
- `run.sh`：轻量启动包装脚本

如需查看完整参数、统计口径、隔离/恢复流程与输出文件说明，请阅读 `USAGE.md`。


---

## 仓库维护

- 当前版本：`0.1.0`
- 版本文件：`VERSION`
- 变更记录：`CHANGELOG.md`
- 发布清单：`RELEASE.md`
- 设计文档：`docs/plans/2026-03-06-repo-skeleton-design.md`
- 实施计划：`docs/plans/2026-03-06-repo-skeleton-implementation.md`

`LICENSE` 已采用 `MIT License`，允许商用、修改、分发与再授权，但需保留许可证声明。

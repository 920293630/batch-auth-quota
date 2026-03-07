# batch-auth-quota 使用手册

本文档是独立工具 `batch-auth-quota` 的完整使用说明，专注“批量检查认证文件额度”这一项能力。

---

## 目录

- [1. 工具定位](#1-工具定位)
- [2. 目录结构](#2-目录结构)
- [3. 环境准备](#3-环境准备)
- [4. 快速上手](#4-快速上手)
- [5. 命令行参数](#5-命令行参数)
- [6. 配置文件](#6-配置文件)
- [7. `.env` 自动加载](#7-env-自动加载)
- [8. 隔离目录机制](#8-隔离目录机制)
- [9. 统计口径说明](#9-统计口径说明)
- [10. 输出产物说明](#10-输出产物说明)
- [11. 常用示例](#11-常用示例)
- [12. 常见问题](#12-常见问题)

---

## 1. 工具定位

这个工具只做一件事：

- 批量读取认证文件
- 调用 CLI Proxy API 管理接口查询额度
- 统计账号状态、剩余额度、刷新时间
- 处理 401 失效账号与额度耗尽账号

适合场景：

- 你有一批认证文件，需要看哪些还能继续用
- 你希望把耗尽账号自动隔离，减少主目录噪音
- 你希望定期复查隔离账号，看哪些已经恢复

---

## 2. 目录结构

```text
batch-auth-quota/
├── batch_auth_quota.py
├── README.md
├── USAGE.md
├── config.example.json
├── .env.example
└── run.sh
```

默认运行时目录：

```text
~/.batch-auth-quota/
├── config.json
└── results/
    ├── latest/
    └── latest.json
```

---

## 3. 环境准备

需要具备：

- `python3`
- 可访问的 CLI Proxy API 管理接口
- 一批认证文件，默认目录为 `~/.cli-proxy-api`

如果你的服务不在默认地址，请准备好实际管理地址，例如：

```text
http://127.0.0.1:8317
```

---

## 4. 快速上手

### 4.1 准备 `.env`

```bash
cd ~/batch-auth-quota
cp .env.example .env
```

写入：

```bash
CPA_MANAGEMENT_KEY=你的管理密钥
```

### 4.2 准备配置文件

```bash
mkdir -p ~/.batch-auth-quota
cp config.example.json ~/.batch-auth-quota/config.json
```

### 4.3 启动检查

```bash
bash run.sh --type codex
```

如果不想依赖默认路径，也可以直接传参：

```bash
python3 batch_auth_quota.py   --type codex   --auth-dir ~/.cli-proxy-api   --api-base http://127.0.0.1:8317   --concurrency 8
```

---

## 5. 命令行参数

### 5.1 常用参数

- `--auth-dir`：认证文件目录
- `--api-base`：管理接口基地址
- `--management-key`：管理密钥
- `--type`：认证类型，如 `codex` / `kimi` / `iflow`
- `--concurrency`：并发数量
- `--timeout`：单次请求超时秒数
- `--out-dir`：本次输出目录
- `--debug-http`：输出管理接口错误明细

### 5.2 认证索引模式

- `--use-auth-index`：优先使用服务端 `/auth-files` 的 `auth_index`
- `--no-use-auth-index`：关闭该模式，强制本地 token 查询

### 5.3 管理密钥与预检查

- `--prompt-management-key`
- `--no-prompt-management-key`
- `--preflight-check`
- `--no-preflight-check`

### 5.4 重试与摘要

- `--retry-count`
- `--retry-backoff-base`
- `--show-run-summary`
- `--no-show-run-summary`

### 5.5 隔离与恢复

- `--isolation-dir`
- `--check-isolated`
- `--no-check-isolated`
- `--prompt-isolate-exhausted`
- `--no-prompt-isolate-exhausted`
- `--prompt-restore-recovered`
- `--no-prompt-restore-recovered`
- `--restore-threshold-bucket`

恢复阈值支持：

```text
danger / alert / fair / usable / high / very_high / full
```

---

## 6. 配置文件

默认配置文件：

```text
~/.batch-auth-quota/config.json
```

也可以通过环境变量指定：

```bash
export BATCH_AUTH_QUOTA_CONFIG=/path/to/config.json
```

配置示例：

```json
{
  "auth_dir": "~/.cli-proxy-api",
  "api_base": "http://127.0.0.1:8317",
  "concurrency": 8,
  "auth_type": "codex",
  "timeout": 25.0,
  "use_auth_index": false,
  "prompt_concurrency": true,
  "prompt_management_key": true,
  "preflight_check": true,
  "retry_count": 2,
  "retry_backoff_base": 0.6,
  "show_run_summary": true,
  "isolation_dir": "~/.cli-proxy-api/.quota_isolated",
  "prompt_isolate_exhausted": true,
  "check_isolated_on_start": true,
  "prompt_restore_recovered": true,
  "restore_threshold_bucket": "danger"
}
```

说明：

- 独立工具优先使用平铺结构配置
- 同时兼容 `check_auth` 节点读取方式，方便从旧配置迁移
- 不要把 `CPA_MANAGEMENT_KEY` 写进 `config.json`

---

## 7. `.env` 自动加载

脚本启动时会按顺序尝试加载以下 `.env`：

1. `BATCH_AUTH_QUOTA_ENV_FILE` 指定的文件
2. 工具目录下的 `.env`
3. 当前工作目录下的 `.env`

示例：

```bash
export BATCH_AUTH_QUOTA_ENV_FILE=/home/chengwd/batch-auth-quota/.env
```

推荐 `.env` 内容：

```bash
CPA_MANAGEMENT_KEY=你的管理密钥
# MANAGEMENT_PASSWORD=兼容旧变量名，二选一即可
```

如果 `.env` 中已有管理密钥，且仍开启交互输入，脚本会提示当前已从 `.env` 获取，可直接回车沿用。

---

## 8. 隔离目录机制

默认隔离目录：

```text
~/.cli-proxy-api/.quota_isolated
```

处理流程如下：

```text
主认证目录账号检查
        │
        ├─ 401 失效 → 可确认后一键删除
        │
        ├─ 额度耗尽 → 可确认后一键移动到隔离目录
        │
        └─ 正常账号 → 保留在主认证目录

隔离目录账号复查
        │
        ├─ 仍未恢复 → 继续留在隔离目录
        └─ 已恢复到阈值档位 → 可确认后一键移回主认证目录
```

说明：

- 启动时若检测到隔离目录有文件，脚本可询问是否纳入本轮检查
- 无论是否纳入实际请求，统计里都会展示隔离目录总数与本轮纳入数
- 恢复判断依据是“剩余额度档位是否达到恢复阈值”

---

## 9. 统计口径说明

### 9.1 额度来源

主要读取响应中的 `rate_limit` 字段。

核心口径：

- `used_percent` 表示已使用比例
- 剩余额度 = `100 - used_percent`
- 刷新时间优先看 `reset_after_seconds` / `reset_at`
- 若同时存在 `primary_window` 与 `secondary_window`，采用**更保守口径**：取剩余额度更低的窗口作为主统计依据

### 9.2 8 档剩余额度分类

```text
满血      98-100
极充足    90-97
很充足    75-89
可用      50-74
一般      30-49
预警      10-29
危险      1-9
已耗尽    0
```

### 9.3 剩余额度总量总览

汇总信息会包含：

- 保守剩余额度总和
- 平均剩余额度
- 中位数剩余额度
- 等效满血账号数
- 各档位账号数量与占比
- 整体/分档位最近刷新时间
- 近期恢复批次分布
- 主周期 / 次周期统计

这能帮助你快速判断：

- 当前总体还能撑多久
- 是集中耗尽，还是少量账号见底
- 5 小时周期账号和 7 天周期账号分别有多少

### 9.4 异常分类

脚本会区分：

- `401失效账号`
- `无额度账号`
- `接口错误账号`
- `请求失败账号`

并在输出里给出异常诊断与 Top 错误类型。

---

## 10. 输出产物说明

默认输出目录：

```text
~/.batch-auth-quota/results/latest
```

最新索引：

```text
~/.batch-auth-quota/results/latest.json
```

常见输出文件：

- `summary.json`：本次统计总览
- `invalidated_401.txt`：401 失效账号
- `no_quota.txt`：无额度账号
- `quota_exhausted.txt`：耗尽账号
- `deleted.txt`：已删除 401 账号
- `isolated.txt`：已隔离账号
- `restored.txt`：已恢复回迁账号
- `delete_failed.txt`
- `isolate_failed.txt`
- `restore_failed.txt`

---

## 11. 常用示例

### 11.1 直接检查默认目录

```bash
bash run.sh --type codex
```

### 11.2 指定 API 和并发

```bash
python3 batch_auth_quota.py   --type codex   --api-base http://127.0.0.1:8317   --concurrency 12
```

### 11.3 指定隔离目录并启用复查

```bash
python3 batch_auth_quota.py   --type codex   --isolation-dir ~/.cli-proxy-api/.quota_isolated   --check-isolated   --restore-threshold-bucket fair
```

### 11.4 关闭交互，走全自动

```bash
python3 batch_auth_quota.py   --type codex   --no-prompt-management-key   --no-show-run-summary   --no-prompt-isolate-exhausted   --no-prompt-restore-recovered
```

---

## 12. 常见问题

### 12.1 为什么还是提示输入管理密钥？

常见原因：

- `.env` 没放在工具目录，且没有设置 `BATCH_AUTH_QUOTA_ENV_FILE`
- `.env` 变量名写错
- 当前启用了交互输入，脚本允许你覆盖已有密钥

### 12.2 为什么隔离目录账号没有发起请求？

因为你在启动确认时选择了“不纳入本轮检查”。

此时：

- 隔离目录总数仍会统计展示
- 但这些文件不会进入本轮真实请求
- 也不会产生“恢复候选”结果

### 12.3 为什么低额度账号没有进预期档位？

当前口径不是只看 `primary_window`，而是：

- 同时存在 `primary_window` 与 `secondary_window` 时
- 取剩余额度更低的那个窗口

这样更保守，也更接近真实可用上限。

### 12.4 管理密钥建议放哪里？

建议放 `.env`：

```bash
CPA_MANAGEMENT_KEY=你的管理密钥
```

不要写入 `config.json`。

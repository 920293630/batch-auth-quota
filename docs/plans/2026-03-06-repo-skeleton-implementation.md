# batch-auth-quota Repo Skeleton Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 为 `batch-auth-quota` 补齐可单独维护和分发的轻量仓库骨架。

**Architecture:** 保持单文件脚本为主入口，在根目录增加 Python 项目元数据、版本文件、变更记录、发布说明，并初始化独立 git 仓库。避免调整现有核心业务逻辑，只为安装、发布和维护提供支撑。

**Tech Stack:** Python 3、setuptools、git、Markdown

---

### Task 1: 补齐项目元数据

**Files:**
- Create: `pyproject.toml`
- Create: `VERSION`
- Modify: `batch_auth_quota.py`

**Step 1:** 在 `batch_auth_quota.py` 中增加版本常量与 `--version` 命令行参数。  
**Step 2:** 新增 `VERSION` 文件，写入首个版本号。  
**Step 3:** 新增 `pyproject.toml`，声明项目名、版本、脚本入口和 `py_modules`。  
**Step 4:** 运行 `python3 batch_auth_quota.py --version` 验证输出。  
**Step 5:** 运行 `python3 -m py_compile batch_auth_quota.py` 验证语法。

### Task 2: 补齐发布文档

**Files:**
- Modify: `README.md`
- Create: `CHANGELOG.md`
- Create: `RELEASE.md`
- Modify: `.gitignore`

**Step 1:** 在 `README.md` 增加安装与仓库使用说明。  
**Step 2:** 新增 `CHANGELOG.md`，记录 `0.1.0` 初始版本说明。  
**Step 3:** 新增 `RELEASE.md`，写发布前检查与发布步骤。  
**Step 4:** 扩充 `.gitignore`，忽略构建和虚拟环境产物。  
**Step 5:** 人工检查文档路径和命令是否一致。

### Task 3: 初始化仓库并校验

**Files:**
- Create: `.git/`（由 git 初始化生成）

**Step 1:** 执行 `git init -b main` 初始化独立仓库。  
**Step 2:** 执行 `git status --short` 确认骨架文件已纳入管理。  
**Step 3:** 执行 `python3 batch_auth_quota.py -h` 验证原命令帮助页仍正常。  
**Step 4:** 执行 `python3 -m pip install -e . --no-deps` 或至少做 `python3 -m build` 级别静态检查（若环境允许）。  
**Step 5:** 输出未完成项，仅保留 `LICENSE` 选型待确认。

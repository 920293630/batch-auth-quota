# 发布说明

## 发布前检查

1. 确认 `VERSION`、`pyproject.toml`、`CHANGELOG.md` 中版本一致
2. 执行语法检查：`python3 -m py_compile batch_auth_quota.py`
3. 检查帮助页：`python3 batch_auth_quota.py -h`
4. 检查版本号：`python3 batch_auth_quota.py --version`
5. 如需本机安装验证：`python3 -m pip install -e . --no-deps`

## 建议发布步骤

```bash
git status
git add .
git commit -m "chore: initialize batch-auth-quota repository skeleton"
git tag v0.1.0
```

## 说明

- 当前仓库已采用 `MIT License`，发布时请确保保留 `LICENSE` 文件。
- 若后续要接入 CI，可在仓库稳定后再补 GitHub Actions / Gitea Actions

# 发布说明

这个文件只面向维护者，不属于普通用户的运行时文档。

## 生成最终 hook 包

```bash
bash scripts/package_hook.sh
```

产物路径固定为：

- `dist/memory-knowledge-auto-migrate-hook.zip`

脚本会自动清理旧的时间戳包，只保留一个稳定文件名。

## 可选：单独打 skill 包

```bash
bash scripts/package_skill.sh
```

仅在你明确需要单独分发 skill 时使用。

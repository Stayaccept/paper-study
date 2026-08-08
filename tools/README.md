# 论文知识库同步器

`knowledge_sync.py` 把根目录下三份 PDF 齐全的 arXiv 论文增量登记到 Obsidian 知识库。

```bash
# 只读扫描
python3 tools/knowledge_sync.py scan --root .

# 检查库是否需要同步；需同步时退出码为 1
python3 tools/knowledge_sync.py check --root .

# 预览（默认不写入）
python3 tools/knowledge_sync.py sync --root .

# 确认后落盘
python3 tools/knowledge_sync.py sync --root . --write

# 持续监视；只有 --write 才落盘
python3 tools/knowledge_sync.py watch --root . --write --interval 10
```

语义生成与来源同步分离。默认只把新论文或版本变更标为 `pending`，不会隐式调用模型。

```bash
# 只预览 pending 队列和固定提示
python3 tools/knowledge_sync.py enrich --root .

# 显式允许调用 Codex 生成待审核候选
python3 tools/knowledge_sync.py enrich --root . --run

# 新 PDF 同步后显式运行语义生成
python3 tools/knowledge_sync.py watch --root . --write --enrich
```

程序拥有范围：

- paper frontmatter 中的 `uid`、`type`、`arxiv_id`、`source_*_hash` 和 `sync_status`；
- 新来源或明确的 PDF 指纹变更时，工作流状态重置为 `pending`；
- paper 笔记的 `AUTO:METADATA` 区块；
- `知识库/.meta/state.json`、`知识库/00-MOC/待审核论文.md` 和
  `知识库/90-视图/自动论文总览.canvas`。

paper 笔记中其他属性、正文、`AUTO:RELATIONS` 和 `AUTO:EVIDENCE` 都不会被来源同步器覆盖。人工专题 Canvas 也不在程序拥有范围内。

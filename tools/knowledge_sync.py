#!/usr/bin/env python3
"""Obsidian 论文知识库的增量同步器。

主路径只做可确定、可重复的来源登记：

* 只扫描根目录下名称符合 ``YYYY.NNNN[N]`` 的直接子目录；
* 只有英文原文、中文译文两份固定命名 PDF 齐全才处理；
* 使用稳定 uid 和 SHA-256 识别新论文、重复运行与来源变更；
* 更新 Markdown 时，只改写明确的程序拥有字段和
  ``AUTO:METADATA`` 区块，不改写人工正文、关系或证据区块；
* 语义关系默认为 pending，不在没有模型分析时伪造。

``sync`` 和 ``watch`` 默认 dry-run，必须显式传入 ``--write`` 才落盘。
``enrich`` 默认只预览待办与命令，必须显式传入 ``--run`` 才调用 Codex。
"""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Iterator, Mapping, Sequence


ARXIV_ID_RE = re.compile(r"^\d{4}\.\d{4,5}$")

PDF_VARIANTS: tuple[tuple[str, str, str], ...] = (
    ("english", "英文原文", "英文原文"),
    ("chinese", "中文译文", "中文译文"),
)

KNOWLEDGE_DIR = Path("知识库")
PAPER_NOTES_DIR = KNOWLEDGE_DIR / "10-论文"
STATE_PATH = KNOWLEDGE_DIR / ".meta" / "state.json"
STATE_LOCK_PATH = KNOWLEDGE_DIR / ".meta" / "state.json.lock"
PENDING_MOC_PATH = KNOWLEDGE_DIR / "00-MOC" / "待审核论文.md"
AUTO_CANVAS_PATH = KNOWLEDGE_DIR / "90-视图" / "自动论文总览.canvas"

AUTO_METADATA_BEGIN = "<!-- AUTO:METADATA:BEGIN -->"
AUTO_METADATA_END = "<!-- AUTO:METADATA:END -->"
AUTO_RELATIONS_BEGIN = "<!-- AUTO:RELATIONS:BEGIN -->"
AUTO_RELATIONS_END = "<!-- AUTO:RELATIONS:END -->"
AUTO_EVIDENCE_BEGIN = "<!-- AUTO:EVIDENCE:BEGIN -->"
AUTO_EVIDENCE_END = "<!-- AUTO:EVIDENCE:END -->"

YAML_ALWAYS_OWNED = (
    "uid",
    "type",
    "arxiv_id",
    "source_hash",
    "source_english_hash",
    "source_chinese_hash",
    "sync_status",
)
YAML_DEPRECATED_OWNED = ("source_bilingual_hash",)
YAML_WORKFLOW_FIELDS = ("semantic_status", "review_status")


class SyncError(RuntimeError):
    """可向用户展示的同步错误。"""


@dataclasses.dataclass(frozen=True)
class PaperSource:
    arxiv_id: str
    directory: str
    files: Mapping[str, str]
    missing: tuple[str, ...]
    hashes: Mapping[str, str]
    source_hash: str | None

    @property
    def uid(self) -> str:
        return f"paper:arxiv:{self.arxiv_id}"

    @property
    def complete(self) -> bool:
        return not self.missing

    def as_dict(self) -> dict[str, Any]:
        return {
            "arxiv_id": self.arxiv_id,
            "uid": self.uid,
            "directory": self.directory,
            "complete": self.complete,
            "missing": list(self.missing),
            "files": dict(self.files),
            "hashes": dict(self.hashes),
            "source_hash": self.source_hash,
        }


@dataclasses.dataclass(frozen=True)
class PaperPlan:
    source: PaperSource
    note_path: str
    action: str
    reason: str
    reset_workflow: bool
    semantic_status: str
    review_status: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "arxiv_id": self.source.arxiv_id,
            "uid": self.source.uid,
            "action": self.action,
            "reason": self.reason,
            "note_path": self.note_path,
            "source_hash": self.source.source_hash,
            "semantic_status": self.semantic_status,
            "review_status": self.review_status,
        }


def _root_path(root: str | os.PathLike[str]) -> Path:
    path = Path(root).expanduser().resolve()
    if not path.is_dir():
        raise SyncError(f"项目根目录不存在或不是目录：{path}")
    return path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
    except OSError as exc:
        raise SyncError(f"无法读取 PDF：{path}（{exc}）") from exc
    return digest.hexdigest()


def _pdf_header_error(path: Path) -> str | None:
    """最小成本阻止空文件、同名非 PDF 或尚未写入文件头的来源被纳管。"""
    try:
        with path.open("rb") as handle:
            header = handle.read(5)
    except OSError as exc:
        return f"{path.name}（无法读取：{exc}）"
    if header != b"%PDF-":
        return f"{path.name}（文件头不是 %PDF-）"
    return None


def _combined_source_hash(hashes: Mapping[str, str]) -> str:
    """由角色名和各文件哈希生成与路径无关的稳定指纹。"""
    digest = hashlib.sha256()
    for key, _, _ in PDF_VARIANTS:
        digest.update(key.encode("utf-8"))
        digest.update(b"\0")
        digest.update(hashes[key].encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def scan_sources(root: str | os.PathLike[str]) -> list[PaperSource]:
    """扫描论文来源；不修改任何文件。"""
    vault = _root_path(root)
    sources: list[PaperSource] = []
    try:
        children = sorted(vault.iterdir(), key=lambda item: item.name)
    except OSError as exc:
        raise SyncError(f"无法扫描项目根目录：{vault}（{exc}）") from exc

    for directory in children:
        if not directory.is_dir() or not ARXIV_ID_RE.fullmatch(directory.name):
            continue
        files: dict[str, str] = {}
        missing: list[str] = []
        hashes: dict[str, str] = {}
        for key, suffix, _ in PDF_VARIANTS:
            filename = f"{directory.name}_{suffix}.pdf"
            pdf_path = directory / filename
            relative = pdf_path.relative_to(vault).as_posix()
            files[key] = relative
            if not pdf_path.is_file():
                missing.append(filename)
            else:
                header_error = _pdf_header_error(pdf_path)
                if header_error:
                    missing.append(header_error)

        if not missing:
            for key, _, _ in PDF_VARIANTS:
                hashes[key] = _sha256_file(vault / files[key])
            source_hash: str | None = _combined_source_hash(hashes)
        else:
            source_hash = None

        sources.append(
            PaperSource(
                arxiv_id=directory.name,
                directory=directory.relative_to(vault).as_posix(),
                files=files,
                missing=tuple(missing),
                hashes=hashes,
                source_hash=source_hash,
            )
        )
    return sources


def _empty_state() -> dict[str, Any]:
    return {"schema_version": 1, "papers": {}, "pending": {"semantic": []}}


def load_state(root: str | os.PathLike[str]) -> dict[str, Any]:
    """读取 state.json，并保留所有未知字段。"""
    vault = _root_path(root)
    state_path = vault / STATE_PATH
    if not state_path.exists():
        return _empty_state()
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SyncError(
            f"状态文件无法读取或不是有效 JSON：{state_path}（{exc}）"
        ) from exc
    if not isinstance(state, dict):
        raise SyncError(f"状态文件顶层必须是 JSON 对象：{state_path}")
    papers = state.get("papers", {})
    if not isinstance(papers, dict):
        raise SyncError(f"状态文件中 papers 必须是 JSON 对象：{state_path}")
    pending = state.get("pending", {})
    if pending is not None and not isinstance(pending, dict):
        raise SyncError(f"状态文件中 pending 必须是 JSON 对象：{state_path}")
    return state


def _line_ending(text: str) -> str:
    return "\r\n" if "\r\n" in text else "\n"


def _frontmatter_bounds(text: str) -> tuple[int, int] | None:
    """返回 frontmatter 起止行索引（含分隔线）。"""
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].lstrip("\ufeff").strip() != "---":
        return None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return 0, index
    raise SyncError(
        "Markdown 以 frontmatter 分隔线开始，但缺少结束分隔线；为保护人工内容已停止修改。"
    )


_TOP_LEVEL_FIELD_RE = re.compile(
    r"^([A-Za-z_][A-Za-z0-9_-]*):(?:[ \t]*(.*?))?(\r?\n)?$"
)


def _frontmatter_scalars(text: str) -> dict[str, str]:
    bounds = _frontmatter_bounds(text)
    if bounds is None:
        return {}
    lines = text.splitlines(keepends=True)
    values: dict[str, str] = {}
    for line in lines[bounds[0] + 1 : bounds[1]]:
        if line[:1].isspace():
            continue
        match = _TOP_LEVEL_FIELD_RE.match(line)
        if not match:
            continue
        raw = (match.group(2) or "").strip()
        if raw.startswith('"'):
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = raw
            values[match.group(1)] = str(parsed)
        elif len(raw) >= 2 and raw.startswith("'") and raw.endswith("'"):
            values[match.group(1)] = raw[1:-1].replace("''", "'")
        else:
            values[match.group(1)] = raw
    return values


def _yaml_scalar(value: str) -> str:
    """JSON 字符串是 YAML 的安全子集，可避免 ID 或状态被误解析。"""
    return json.dumps(value, ensure_ascii=False)


def _update_frontmatter(text: str, fields: Mapping[str, str]) -> str:
    newline = _line_ending(text)
    bounds = _frontmatter_bounds(text)
    ordered_keys = [
        key for key in (*YAML_ALWAYS_OWNED, *YAML_WORKFLOW_FIELDS) if key in fields
    ]
    if bounds is None:
        frontmatter = [
            "---",
            *(f"{key}: {_yaml_scalar(fields[key])}" for key in ordered_keys),
            "---",
        ]
        prefix = newline.join(frontmatter) + newline
        return prefix + text

    lines = text.splitlines(keepends=True)
    _, end = bounds
    seen: set[str] = set()
    rewritten: list[str] = [lines[0]]
    for line in lines[1:end]:
        match = None if line[:1].isspace() else _TOP_LEVEL_FIELD_RE.match(line)
        key = match.group(1) if match else None
        if key in YAML_DEPRECATED_OWNED:
            continue
        if key in fields:
            if key not in seen:
                rewritten.append(f"{key}: {_yaml_scalar(fields[key])}{newline}")
                seen.add(key)
            # 删除同一程序拥有字段的重复顶层定义。
            continue
        rewritten.append(line)
    for key in ordered_keys:
        if key not in seen:
            rewritten.append(f"{key}: {_yaml_scalar(fields[key])}{newline}")
    rewritten.extend(lines[end:])
    return "".join(rewritten)


def _replace_or_append_block(text: str, begin: str, end: str, block: str) -> str:
    """只替换完整的自动区块；标记损坏时宁可报错也不猜测。"""
    begin_matches = list(re.finditer(rf"(?m)^{re.escape(begin)}[ \t]*$", text))
    end_matches = list(re.finditer(rf"(?m)^{re.escape(end)}[ \t]*$", text))
    if not begin_matches and not end_matches:
        separator = (
            ""
            if not text
            else (
                _line_ending(text)
                if text.endswith(("\n", "\r"))
                else _line_ending(text) * 2
            )
        )
        return text + separator + block
    if len(begin_matches) != 1 or len(end_matches) != 1:
        raise SyncError(
            f"自动区块标记数量异常：{begin} / {end}；为保护人工内容已停止修改。"
        )
    start = begin_matches[0].start()
    finish = end_matches[0].end()
    if finish < start:
        raise SyncError(
            f"自动区块标记顺序错误：{begin} / {end}；为保护人工内容已停止修改。"
        )
    return text[:start] + block + text[finish:]


def _metadata_block(source: PaperSource, newline: str = "\n") -> str:
    links: list[str] = []
    for key, _, label in PDF_VARIANTS:
        links.append(f"- {label}：[[{source.files[key]}|{label}]]")
    lines = [
        AUTO_METADATA_BEGIN,
        "## 自动同步元数据",
        "",
        "> 本区块由 `tools/knowledge_sync.py` 管理；人工内容请写在区块外。",
        "",
        f"- 稳定标识：`{source.uid}`",
        f"- arXiv ID：`{source.arxiv_id}`",
        *links,
        f"- 来源指纹：`sha256:{source.source_hash}`",
        AUTO_METADATA_END,
    ]
    return newline.join(lines)


def _pending_block(begin: str, end: str, title: str, newline: str = "\n") -> str:
    return newline.join(
        [
            begin,
            f"## {title}",
            "",
            "> 状态：`pending`。来源同步器不会在未进行语义分析时伪造内容。",
            end,
        ]
    )


def _new_note(source: PaperSource) -> str:
    fields = _source_fields(source)
    fields.update({"semantic_status": "pending", "review_status": "pending"})
    newline = "\n"
    frontmatter = newline.join(
        [
            "---",
            *(
                f"{key}: {_yaml_scalar(fields[key])}"
                for key in (*YAML_ALWAYS_OWNED, *YAML_WORKFLOW_FIELDS)
            ),
            "---",
        ]
    )
    return newline.join(
        [
            frontmatter,
            "",
            f"# 论文 - {source.arxiv_id}",
            "",
            _metadata_block(source),
            "",
            _pending_block(AUTO_RELATIONS_BEGIN, AUTO_RELATIONS_END, "候选关系"),
            "",
            _pending_block(AUTO_EVIDENCE_BEGIN, AUTO_EVIDENCE_END, "候选证据"),
            "",
            "## 人工笔记",
            "",
            "",
        ]
    )


def _source_fields(source: PaperSource) -> dict[str, str]:
    if not source.complete or source.source_hash is None:
        raise SyncError(f"不完整的论文来源不能生成笔记：{source.arxiv_id}")
    return {
        "uid": source.uid,
        "type": "paper",
        "arxiv_id": source.arxiv_id,
        "source_hash": source.source_hash,
        "source_english_hash": source.hashes["english"],
        "source_chinese_hash": source.hashes["chinese"],
        "sync_status": "synced",
    }


def _updated_note(
    existing: str,
    source: PaperSource,
    *,
    reset_workflow: bool,
    default_semantic_status: str = "pending",
    default_review_status: str = "pending",
) -> str:
    current_fields = _frontmatter_scalars(existing)
    fields = _source_fields(source)
    if reset_workflow or "semantic_status" not in current_fields:
        fields["semantic_status"] = default_semantic_status
    if reset_workflow or "review_status" not in current_fields:
        fields["review_status"] = default_review_status
    updated = _update_frontmatter(existing, fields)
    return _replace_or_append_block(
        updated,
        AUTO_METADATA_BEGIN,
        AUTO_METADATA_END,
        _metadata_block(source, _line_ending(updated)),
    )


def _read_note(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise SyncError(f"无法读取 Markdown 笔记：{path}（{exc}）") from exc


def _discover_notes_by_uid(vault: Path) -> dict[str, str]:
    knowledge = vault / KNOWLEDGE_DIR
    if not knowledge.is_dir():
        return {}
    discovered: dict[str, str] = {}
    duplicates: dict[str, list[str]] = {}
    for path in sorted(knowledge.rglob("*.md")):
        if not path.is_file():
            continue
        uid = _frontmatter_scalars(_read_note(path)).get("uid")
        if not uid:
            continue
        relative = path.relative_to(vault).as_posix()
        if uid in discovered:
            duplicates.setdefault(uid, [discovered[uid]]).append(relative)
        else:
            discovered[uid] = relative
    if duplicates:
        details = "；".join(
            f"{uid}: {', '.join(paths)}" for uid, paths in sorted(duplicates.items())
        )
        raise SyncError(f"发现重复 uid，无法安全选择论文笔记：{details}")
    return discovered


def _safe_note_path(vault: Path, relative: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute():
        raise SyncError(f"状态中的笔记路径不能是绝对路径：{relative}")
    resolved = (vault / candidate).resolve()
    try:
        resolved.relative_to(vault)
    except ValueError as exc:
        raise SyncError(f"状态中的笔记路径越出项目根目录：{relative}") from exc
    return resolved


def _canonical_note_path(source: PaperSource) -> str:
    return (PAPER_NOTES_DIR / f"论文 - {source.arxiv_id}.md").as_posix()


def _select_note_path(
    vault: Path,
    source: PaperSource,
    state_entry: Mapping[str, Any],
    discovered: Mapping[str, str],
) -> str:
    state_relative = state_entry.get("note_path")
    if isinstance(state_relative, str):
        state_path = _safe_note_path(vault, state_relative)
        if state_path.is_file():
            state_uid = _frontmatter_scalars(_read_note(state_path)).get("uid")
            if state_uid and state_uid != source.uid:
                raise SyncError(
                    f"状态指向的笔记 uid 不匹配：{state_relative}"
                    f"（期望 {source.uid}，实际 {state_uid}）"
                )
            discovered_relative = discovered.get(source.uid)
            if discovered_relative and discovered_relative != state_relative:
                raise SyncError(
                    f"状态路径与 uid 检索结果冲突：{state_relative} / {discovered_relative}"
                )
            return state_relative

    if source.uid in discovered:
        return discovered[source.uid]

    canonical = _canonical_note_path(source)
    canonical_path = _safe_note_path(vault, canonical)
    if canonical_path.is_file():
        canonical_uid = _frontmatter_scalars(_read_note(canonical_path)).get("uid")
        if canonical_uid and canonical_uid != source.uid:
            raise SyncError(
                f"目标笔记路径已被其他 uid 占用：{canonical}（{canonical_uid}）"
            )
    return canonical


def _known_source_hashes(
    state_entry: Mapping[str, Any], note_text: str | None
) -> tuple[str, ...]:
    """同时考虑 state 与 note，避免单方索引漂移造成误降级。"""
    hashes: list[str] = []
    state_hash = state_entry.get("source_hash")
    if isinstance(state_hash, str) and state_hash:
        hashes.append(state_hash)
    if note_text is not None:
        note_hash = _frontmatter_scalars(note_text).get("source_hash")
        if note_hash and note_hash not in hashes:
            hashes.append(note_hash)
    return tuple(hashes)


def _current_variant_hashes_match(
    state_entry: Mapping[str, Any], source: PaperSource
) -> bool:
    """识别只移除旧来源角色、但现有 PDF 内容未变的模式迁移。"""
    stored = state_entry.get("source_hashes")
    if not isinstance(stored, Mapping):
        return False
    return all(stored.get(key) == source.hashes[key] for key, _, _ in PDF_VARIANTS)


def _workflow_statuses(
    state_entry: Mapping[str, Any], note_text: str | None, *, reset_workflow: bool
) -> tuple[str, str]:
    if reset_workflow:
        return "pending", "pending"
    note_fields = _frontmatter_scalars(note_text) if note_text is not None else {}
    review = note_fields.get("review_status")
    if not review:
        raw_review = state_entry.get("review_status")
        review = (
            str(raw_review) if isinstance(raw_review, str) and raw_review else "pending"
        )
    semantic = note_fields.get("semantic_status")
    if not semantic:
        raw_semantic = state_entry.get("semantic_status")
        if isinstance(raw_semantic, str) and raw_semantic:
            semantic = raw_semantic
        elif review in {"reviewed", "approved"}:
            # 兼容接管早于同步器已经人工审核的 paper 笔记。
            semantic = "reviewed"
        else:
            semantic = "pending"
    return semantic, review


def build_plan(
    root: str | os.PathLike[str],
    *,
    sources: Sequence[PaperSource] | None = None,
    state: Mapping[str, Any] | None = None,
) -> tuple[list[PaperPlan], list[PaperSource]]:
    """计算同步计划，不写入磁盘。"""
    vault = _root_path(root)
    scanned = list(sources) if sources is not None else scan_sources(vault)
    current_state = dict(state) if state is not None else load_state(vault)
    state_papers = current_state.get("papers", {})
    if not isinstance(state_papers, Mapping):
        raise SyncError("状态文件中 papers 必须是 JSON 对象。")
    discovered = _discover_notes_by_uid(vault)
    plans: list[PaperPlan] = []
    skipped: list[PaperSource] = []

    for source in scanned:
        if not source.complete:
            skipped.append(source)
            continue
        raw_entry = state_papers.get(source.uid, {})
        if raw_entry is None:
            raw_entry = {}
        if not isinstance(raw_entry, Mapping):
            raise SyncError(f"状态中 {source.uid} 的记录必须是 JSON 对象。")
        note_relative = _select_note_path(vault, source, raw_entry, discovered)
        note_path = _safe_note_path(vault, note_relative)
        existing = _read_note(note_path) if note_path.is_file() else None
        known_hashes = _known_source_hashes(raw_entry, existing)
        source_changed = (
            bool(known_hashes)
            and source.source_hash not in known_hashes
            and not _current_variant_hashes_match(raw_entry, source)
        )
        # 已有人工笔记第一次被接管时无旧哈希，这不等于 PDF 变更，
        # 因此不得把 reviewed 降级为 pending。
        # state 与 note 中只要有一方的已知哈希与当前 PDF 一致，就视为
        # “索引待修复”而不是“来源变更”。
        reset_workflow = existing is None or source_changed
        semantic_status, review_status = _workflow_statuses(
            raw_entry, existing, reset_workflow=reset_workflow
        )

        if existing is None:
            action = "create"
            reason = "新论文：创建最小 paper 笔记"
        else:
            desired = _updated_note(
                existing,
                source,
                reset_workflow=reset_workflow,
                default_semantic_status=semantic_status,
                default_review_status=review_status,
            )
            state_needs_update = (
                raw_entry.get("source_hash") != source.source_hash
                or raw_entry.get("note_path") != note_relative
                or raw_entry.get("sync_status") != "synced"
                or raw_entry.get("semantic_status") != semantic_status
                or raw_entry.get("review_status") != review_status
            )
            if desired != existing:
                action = "update"
                reason = (
                    "PDF 变更，更新自动元数据"
                    if source_changed
                    else "补齐或校正自动元数据"
                )
            elif state_needs_update:
                action = "state-only"
                reason = "笔记已同步，仅补齐状态索引"
            else:
                action = "unchanged"
                reason = "PDF 指纹与笔记均未变化"

        plans.append(
            PaperPlan(
                source=source,
                note_path=note_relative,
                action=action,
                reason=reason,
                reset_workflow=reset_workflow,
                semantic_status=semantic_status,
                review_status=review_status,
            )
        )
    return plans, skipped


def _merge_state(
    state: Mapping[str, Any], plans: Sequence[PaperPlan]
) -> dict[str, Any]:
    """只深合并本轮处理的论文，不删除任何未知或旧记录。"""
    merged = dict(state)
    merged.setdefault("schema_version", 1)
    raw_papers = merged.get("papers", {})
    if not isinstance(raw_papers, Mapping):
        raise SyncError("状态文件中 papers 必须是 JSON 对象。")
    papers: dict[str, Any] = dict(raw_papers)

    for plan in plans:
        source = plan.source
        old = papers.get(source.uid, {})
        if old is None:
            old = {}
        if not isinstance(old, Mapping):
            raise SyncError(f"状态中 {source.uid} 的记录必须是 JSON 对象。")
        entry = dict(old)
        entry.update(
            {
                "uid": source.uid,
                "arxiv_id": source.arxiv_id,
                "note_path": plan.note_path,
                "source_hash": source.source_hash,
                "source_hashes": dict(source.hashes),
                "source_files": dict(source.files),
                "sync_status": "synced",
            }
        )
        entry["semantic_status"] = plan.semantic_status
        entry["review_status"] = plan.review_status
        papers[source.uid] = entry

    merged["papers"] = papers
    raw_pending = merged.get("pending", {})
    pending = dict(raw_pending) if isinstance(raw_pending, Mapping) else {}
    pending["semantic"] = sorted(
        uid
        for uid, entry in papers.items()
        if isinstance(entry, Mapping) and entry.get("semantic_status") == "pending"
    )
    merged["pending"] = pending
    return merged


def _json_text(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _pending_moc_text(vault: Path, state: Mapping[str, Any]) -> str:
    """生成整个程序拥有的待审核 MOC，使 pending paper 不成为孤立节点。"""
    papers = state.get("papers", {})
    rows: list[tuple[str, str, str]] = []
    if isinstance(papers, Mapping):
        for uid, raw_entry in sorted(papers.items()):
            if not isinstance(raw_entry, Mapping):
                continue
            semantic_status = raw_entry.get("semantic_status")
            review_status = raw_entry.get("review_status")
            if semantic_status not in {
                "pending",
                "needs_review",
            } and review_status not in {
                "pending",
                "needs_review",
            }:
                continue
            note_relative = raw_entry.get("note_path")
            arxiv_id = raw_entry.get("arxiv_id")
            if not isinstance(note_relative, str) or not isinstance(arxiv_id, str):
                continue
            # state 历史记录不会被删除；MOC 只链接当前存在的笔记，避免制造幽灵节点。
            if not _safe_note_path(vault, note_relative).is_file():
                continue
            target = (
                note_relative[:-3] if note_relative.endswith(".md") else note_relative
            )
            rows.append((arxiv_id, target, str(semantic_status or review_status)))

    lines = [
        "---",
        'uid: "moc:pending-papers"',
        'type: "moc"',
        'managed_by: "knowledge_sync"',
        "---",
        "",
        "# 待审核论文",
        "",
        "> 本文件由 `tools/knowledge_sync.py` 整体生成，请勿在此写人工内容。",
        "",
    ]
    if rows:
        lines.extend(
            f"- [[{target}|{arxiv_id}]] — `{status}`"
            for arxiv_id, target, status in rows
        )
    else:
        lines.append("- 当前没有待审核论文。")
    return "\n".join(lines) + "\n"


def _canvas_status(raw_entry: Mapping[str, Any]) -> str:
    semantic = raw_entry.get("semantic_status")
    review = raw_entry.get("review_status")
    if semantic in {"pending", "needs_review"} or review in {"pending", "needs_review"}:
        return "pending"
    return "processed"


def _auto_canvas_text(vault: Path, state: Mapping[str, Any]) -> str:
    """生成稳定 JSON Canvas；仅管理“自动论文总览.canvas”。"""
    papers = state.get("papers", {})
    grouped: dict[str, list[Mapping[str, Any]]] = {"pending": [], "processed": []}
    if isinstance(papers, Mapping):
        for _, raw_entry in sorted(papers.items()):
            if not isinstance(raw_entry, Mapping):
                continue
            note_relative = raw_entry.get("note_path")
            arxiv_id = raw_entry.get("arxiv_id")
            if not isinstance(note_relative, str) or not isinstance(arxiv_id, str):
                continue
            if not _safe_note_path(vault, note_relative).is_file():
                continue
            grouped[_canvas_status(raw_entry)].append(raw_entry)

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    group_specs = (
        ("pending", "待语义审核", "1", 0),
        ("processed", "已生成候选 / 已审核", "4", 520),
    )
    for group_key, label, color, base_y in group_specs:
        entries = grouped[group_key]
        if not entries:
            continue
        status_id = f"status-{group_key}"
        nodes.append(
            {
                "id": status_id,
                "type": "text",
                "text": f"# {label}\n\n{len(entries)} 篇论文",
                "x": 0,
                "y": base_y,
                "width": 260,
                "height": 120,
                "color": color,
            }
        )
        for index, entry in enumerate(
            sorted(entries, key=lambda item: str(item.get("arxiv_id")))
        ):
            arxiv_id = str(entry["arxiv_id"])
            safe_id = arxiv_id.replace(".", "-")
            node_id = f"paper-{safe_id}"
            column = index % 4
            row = index // 4
            nodes.append(
                {
                    "id": node_id,
                    "type": "file",
                    "file": str(entry["note_path"]),
                    "x": 360 + column * 330,
                    "y": base_y + row * 210,
                    "width": 280,
                    "height": 160,
                }
            )
            edges.append(
                {
                    "id": f"edge-{group_key}-{safe_id}",
                    "fromNode": status_id,
                    "fromSide": "right",
                    "toNode": node_id,
                    "toSide": "left",
                    "toEnd": "arrow",
                    "label": label,
                }
            )
    canvas = {"nodes": nodes, "edges": edges}
    return json.dumps(canvas, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _managed_content_equal(relative: Path, current: str, desired: str) -> bool:
    """Canvas 由 Obsidian 重排 JSON 空白后仍视为相同内容。"""
    if relative.suffix == ".canvas":
        try:
            return json.loads(current) == json.loads(desired)
        except json.JSONDecodeError:
            return False
    return current == desired


def _managed_view_changes(vault: Path, state: Mapping[str, Any]) -> list[str]:
    desired = {
        PENDING_MOC_PATH: _pending_moc_text(vault, state),
        AUTO_CANVAS_PATH: _auto_canvas_text(vault, state),
    }
    changes: list[str] = []
    for relative, content in desired.items():
        path = vault / relative
        if not path.is_file() or not _managed_content_equal(
            relative, _read_note(path), content
        ):
            changes.append(relative.as_posix())
    return changes


def _write_managed_views(vault: Path, state: Mapping[str, Any]) -> list[str]:
    desired = {
        PENDING_MOC_PATH: _pending_moc_text(vault, state),
        AUTO_CANVAS_PATH: _auto_canvas_text(vault, state),
    }
    written: list[str] = []
    for relative, content in desired.items():
        path = vault / relative
        if path.is_file() and _managed_content_equal(
            relative, _read_note(path), content
        ):
            continue
        if _atomic_write_text(path, content):
            written.append(relative.as_posix())
    return written


def _atomic_write_text(path: Path, text: str) -> bool:
    """内容不变时不触碰文件；变更时使用同目录原子替换。"""
    if path.is_file():
        try:
            if path.read_text(encoding="utf-8") == text:
                return False
        except (OSError, UnicodeError) as exc:
            raise SyncError(f"无法读取待写入文件：{path}（{exc}）") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
            temp_name = handle.name
        os.replace(temp_name, path)
    except OSError as exc:
        raise SyncError(f"无法原子写入文件：{path}（{exc}）") from exc
    finally:
        if temp_name and os.path.exists(temp_name):
            with contextlib.suppress(OSError):
                os.unlink(temp_name)
    return True


@contextlib.contextmanager
def _state_lock(vault: Path) -> Iterator[None]:
    lock_path = vault / STATE_LOCK_PATH
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with lock_path.open("a+", encoding="utf-8") as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError as exc:
        raise SyncError(f"无法获取同步锁：{lock_path}（{exc}）") from exc


def _apply_plan(
    vault: Path, plans: Sequence[PaperPlan], state: Mapping[str, Any]
) -> dict[str, Any]:
    written_notes: list[str] = []
    # 先处理笔记，最后写 state。即使中途失败，重跑也会幂等修复。
    for plan in plans:
        note_path = _safe_note_path(vault, plan.note_path)
        if note_path.is_file():
            latest = _read_note(note_path)
            desired = _updated_note(
                latest,
                plan.source,
                reset_workflow=plan.reset_workflow,
                default_semantic_status=plan.semantic_status,
                default_review_status=plan.review_status,
            )
        else:
            desired = _new_note(plan.source)
        if _atomic_write_text(note_path, desired):
            written_notes.append(plan.note_path)

    merged_state = _merge_state(state, plans)
    state_written = _atomic_write_text(vault / STATE_PATH, _json_text(merged_state))
    written_views = _write_managed_views(vault, merged_state)
    return {
        "written_notes": written_notes,
        "written_views": written_views,
        "state_written": state_written,
        "state": merged_state,
    }


def sync_repository(
    root: str | os.PathLike[str], *, write: bool = False
) -> dict[str, Any]:
    """同步一次。``write=False`` 为无副作用预览。"""
    vault = _root_path(root)
    initial_sources = scan_sources(vault)
    complete_count = sum(source.complete for source in initial_sources)

    # 仅有不完整目录且尚无 state 时，dry-run 和 write 都应是稳定的
    # no-op；否则 check 会要求一个 sync --write 也无法修复的视图变更。
    if complete_count == 0 and not (vault / STATE_PATH).exists():
        return {
            "mode": "write" if write else "dry-run",
            "root": str(vault),
            "papers": [],
            "skipped": [source.as_dict() for source in initial_sources],
            "pending_semantic": [],
            "written_notes": [],
            "written_views": [],
            "view_changes": [],
            "state_written": False,
        }

    if not write:
        state = load_state(vault)
        plans, skipped = build_plan(vault, sources=initial_sources, state=state)
        merged = _merge_state(state, plans)
        view_changes = _managed_view_changes(vault, merged)
        return {
            "mode": "dry-run",
            "root": str(vault),
            "papers": [plan.as_dict() for plan in plans],
            "skipped": [source.as_dict() for source in skipped],
            "pending_semantic": merged.get("pending", {}).get("semantic", []),
            "written_notes": [],
            "written_views": [],
            "view_changes": view_changes,
            "state_written": False,
        }

    with _state_lock(vault):
        # 获锁后重新读取扫描和状态，避免并发 sync 覆盖对方更新。
        sources = scan_sources(vault)
        state = load_state(vault)
        plans, skipped = build_plan(vault, sources=sources, state=state)
        applied = _apply_plan(vault, plans, state)
        return {
            "mode": "write",
            "root": str(vault),
            "papers": [plan.as_dict() for plan in plans],
            "skipped": [source.as_dict() for source in skipped],
            "pending_semantic": applied["state"].get("pending", {}).get("semantic", []),
            "written_notes": applied["written_notes"],
            "written_views": applied["written_views"],
            "view_changes": [],
            "state_written": applied["state_written"],
        }


def scan_repository(root: str | os.PathLike[str]) -> dict[str, Any]:
    vault = _root_path(root)
    state = load_state(vault)
    papers_state = state.get("papers", {})
    sources = scan_sources(vault)
    rows: list[dict[str, Any]] = []
    source_uids: set[str] = set()
    for source in sources:
        source_uids.add(source.uid)
        row = source.as_dict()
        entry = (
            papers_state.get(source.uid, {})
            if isinstance(papers_state, Mapping)
            else {}
        )
        if not source.complete:
            row["status"] = "incomplete"
        elif not isinstance(entry, Mapping) or not entry:
            row["status"] = "new"
        elif entry.get("source_hash") == source.source_hash:
            row["status"] = "synced"
        else:
            row["status"] = "changed"
        if isinstance(entry, Mapping) and isinstance(entry.get("note_path"), str):
            row["note_path"] = entry["note_path"]
        rows.append(row)
    state_only = (
        sorted(
            uid
            for uid in papers_state
            if isinstance(uid, str)
            and uid.startswith("paper:arxiv:")
            and uid not in source_uids
        )
        if isinstance(papers_state, Mapping)
        else []
    )
    return {"root": str(vault), "papers": rows, "state_only": state_only}


def check_repository(root: str | os.PathLike[str]) -> dict[str, Any]:
    """检查来源与库是否同步；不完整目录是跳过警告，不是同步失败。"""
    report = sync_repository(root, write=False)
    needs_sync = [paper for paper in report["papers"] if paper["action"] != "unchanged"]
    report["ok"] = not needs_sync and not report.get("view_changes")
    report["needs_sync"] = needs_sync
    report["warnings"] = [
        {
            "arxiv_id": source["arxiv_id"],
            "message": f"缺少固定文件，已跳过：{', '.join(source['missing'])}",
        }
        for source in report["skipped"]
    ]
    return report


def _pending_entries(
    vault: Path, state: Mapping[str, Any]
) -> tuple[list[Mapping[str, Any]], list[dict[str, str]]]:
    """以 paper 笔记的当前状态为最终审核门，不盲信可能滞后的 state。"""
    papers = state.get("papers", {})
    if not isinstance(papers, Mapping):
        return [], []
    entries: list[Mapping[str, Any]] = []
    skipped: list[dict[str, str]] = []
    for uid, entry in sorted(papers.items()):
        if not isinstance(entry, Mapping) or entry.get("semantic_status") not in {
            "pending",
            "needs_review",
        }:
            continue
        note_relative = entry.get("note_path")
        if not isinstance(note_relative, str):
            skipped.append({"uid": str(uid), "reason": "状态记录缺少 note_path"})
            continue
        note_path = _safe_note_path(vault, note_relative)
        if not note_path.is_file():
            skipped.append({"uid": str(uid), "reason": "paper 笔记不存在"})
            continue
        note_fields = _frontmatter_scalars(_read_note(note_path))
        if note_fields.get("uid") != uid:
            skipped.append({"uid": str(uid), "reason": "paper 笔记 uid 不匹配"})
            continue
        note_semantic = note_fields.get("semantic_status")
        note_review = note_fields.get("review_status")
        if note_review in {"reviewed", "approved"}:
            skipped.append({"uid": str(uid), "reason": "paper 笔记已审核"})
            continue
        if note_semantic and note_semantic not in {"pending", "needs_review"}:
            skipped.append({"uid": str(uid), "reason": "paper 笔记已生成语义候选"})
            continue
        entries.append(entry)
    return entries, skipped


def _enrichment_prompt(entries: Sequence[Mapping[str, Any]]) -> str:
    targets = [
        {
            "uid": entry.get("uid"),
            "arxiv_id": entry.get("arxiv_id"),
            "note_path": entry.get("note_path"),
            "source_files": entry.get("source_files"),
            "source_hash": entry.get("source_hash"),
        }
        for entry in entries
    ]
    return (
        """
你正在为 Obsidian 论文知识库生成“待人工审核”的语义候选。只处理下方 JSON 列出的论文。

强制边界：
1. 不得修改、删除或重命名任何 PDF。
2. 不得修改 paper 笔记中任何人工区域，只能改写完整的
   AUTO:RELATIONS 和 AUTO:EVIDENCE 标记内容；AUTO:METADATA 由同步器拥有，不得修改。
3. 每条关系都必须明示为候选，新建的概念/命题笔记必须包含 `review_status: pending`。
4. 每条证据必须指向来源 PDF 和可核验页码；无法核验时标记不确定，不得猜测。
5. 不得删除、合并或覆盖现有人工知识笔记。按稳定 uid 复用现有节点。
6. 完成后，将对应 paper frontmatter 的 `semantic_status` 改为 `candidate_generated`，
   `review_status` 保持 `pending`；并仅同步更新 `知识库/.meta/state.json`
   对应记录的这两个工作流字段和 pending.semantic 列表，保留其他内容。
7. 不得把 `$...$` 数学公式写进 Wiki 链接的 target 或显示文本，也不得放进
   frontmatter 的 title/aliases、普通 Markdown 链接标签或 Canvas label；这些位置只用纯文本。
   应写成 `[[概念笔记|概念名称]] $N$`，使 Obsidian 能正确渲染公式。
8. Obsidian 数学语法只使用 `$...$`（行内）或 `$$...$$`（独立公式块）；
   不使用 `\\(...\\)`、`\\[...\\]`，也不留下裸 LaTeX。

待处理对象：
""".strip()
        + "\n"
        + json.dumps(targets, ensure_ascii=False, indent=2)
    )


def _codex_command(codex_path: str, vault: Path) -> list[str]:
    # 2026 版 CLI 已移除旧 ``-a never``；等价的当前配置写法如下。
    return [
        codex_path,
        "exec",
        "-C",
        str(vault),
        "-s",
        "workspace-write",
        "-c",
        'approval_policy="never"',
        "--skip-git-repo-check",
        "--ephemeral",
        "-",
    ]


def enrich_repository(
    root: str | os.PathLike[str],
    *,
    run: bool = False,
    runner: Any = subprocess.run,
) -> dict[str, Any]:
    """预览或显式调用 Codex 处理 pending 语义候选。"""
    vault = _root_path(root)
    state = load_state(vault)
    entries, skipped = _pending_entries(vault, state)
    pending_uids = [str(entry.get("uid")) for entry in entries]
    if not entries:
        return {
            "root": str(vault),
            "mode": "run" if run else "preview",
            "pending": [],
            "skipped": skipped,
            "invoked": False,
        }
    discovered_codex = shutil.which("codex")
    if run and not discovered_codex:
        raise SyncError("未找到 codex 命令；无法执行语义候选生成。")
    codex_path = discovered_codex or "codex"
    command = _codex_command(codex_path, vault)
    prompt = _enrichment_prompt(entries)
    if not run:
        return {
            "root": str(vault),
            "mode": "preview",
            "pending": pending_uids,
            "skipped": skipped,
            "invoked": False,
            "command": command,
            "prompt": prompt,
        }
    try:
        completed = runner(command, input=prompt, text=True, cwd=vault, check=False)
    except OSError as exc:
        raise SyncError(f"无法启动 Codex：{exc}") from exc
    return {
        "root": str(vault),
        "mode": "run",
        "pending": pending_uids,
        "skipped": skipped,
        "invoked": True,
        "returncode": int(completed.returncode),
    }


def _watch_signature(vault: Path) -> tuple[Any, ...]:
    values: list[Any] = []
    for directory in sorted(vault.iterdir(), key=lambda path: path.name):
        if not directory.is_dir() or not ARXIV_ID_RE.fullmatch(directory.name):
            continue
        values.append(directory.name)
        for _, suffix, _ in PDF_VARIANTS:
            path = directory / f"{directory.name}_{suffix}.pdf"
            try:
                stat = path.stat()
                values.append((path.name, stat.st_size, stat.st_mtime_ns))
            except FileNotFoundError:
                values.append((path.name, None, None))
    return tuple(values)


def watch_repository(
    root: str | os.PathLike[str],
    *,
    interval: float = 10.0,
    write: bool = False,
    enrich: bool = False,
    once: bool = False,
) -> Iterator[dict[str, Any]]:
    """轮询变更并产生报告；只在文件签名改变时重新计算 SHA-256。"""
    vault = _root_path(root)
    if interval <= 0:
        raise SyncError("watch 的 --interval 必须大于 0。")
    if enrich and not write:
        raise SyncError(
            "watch --enrich 必须与 --write 同时使用，避免在 dry-run 中隐式调用模型。"
        )
    previous: tuple[Any, ...] | None = None
    while True:
        signature = _watch_signature(vault)
        if signature != previous:
            # 大 PDF 通常不是瞬间复制完成。非 once 模式至少等待一个
            # interval，并仅在 size/mtime 签名连续两次一致时才计算哈希和同步。
            if not once:
                time.sleep(interval)
                confirmed = _watch_signature(vault)
                if confirmed != signature:
                    continue
            sync_report = sync_repository(vault, write=write)
            event: dict[str, Any] = {"event": "sync", "sync": sync_report}
            if enrich:
                event["enrich"] = enrich_repository(vault, run=True)
            yield event
            previous = signature
        if once:
            return
        time.sleep(interval)


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--root", default=".", help="Obsidian vault 根目录（默认：当前目录）"
    )
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="增量同步论文 PDF 与 Obsidian 知识笔记"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    scan_parser = subparsers.add_parser("scan", help="扫描论文目录和来源指纹（只读）")
    _add_common_arguments(scan_parser)

    check_parser = subparsers.add_parser("check", help="检查是否需要同步（只读）")
    _add_common_arguments(check_parser)

    sync_parser = subparsers.add_parser("sync", help="预览或执行一次增量同步")
    _add_common_arguments(sync_parser)
    sync_parser.add_argument(
        "--write", action="store_true", help="实际写入；未传入时默认 dry-run"
    )

    watch_parser = subparsers.add_parser("watch", help="持续监视 PDF 变更")
    _add_common_arguments(watch_parser)
    watch_parser.add_argument(
        "--write", action="store_true", help="实际写入；未传入时默认 dry-run"
    )
    watch_parser.add_argument(
        "--interval", type=float, default=10.0, help="轮询秒数（默认：10）"
    )
    watch_parser.add_argument(
        "--enrich", action="store_true", help="每次变更同步后显式调用 Codex 生成候选"
    )
    watch_parser.add_argument("--once", action="store_true", help=argparse.SUPPRESS)

    enrich_parser = subparsers.add_parser(
        "enrich", help="预览或显式调用 Codex 生成语义候选"
    )
    _add_common_arguments(enrich_parser)
    enrich_parser.add_argument(
        "--run", action="store_true", help="实际调用 Codex；未传入时只显示待办"
    )
    return parser


def _print_human(command: str, report: Mapping[str, Any]) -> None:
    if command == "scan":
        print(f"扫描根目录：{report['root']}")
        if not report["papers"]:
            print("未发现符合 arXiv ID 命名的论文目录。")
        for paper in report["papers"]:
            if paper["status"] == "incomplete":
                print(
                    f"- {paper['arxiv_id']}: 不完整，跳过（缺少 {', '.join(paper['missing'])}）"
                )
            else:
                print(
                    f"- {paper['arxiv_id']}: {paper['status']}  sha256:{paper['source_hash']}"
                )
        for uid in report.get("state_only", []):
            print(f"- 警告：状态中仍有记录，但来源目录不存在：{uid}")
        return

    if command in {"sync", "check"}:
        mode = report.get("mode", "dry-run")
        print(f"模式：{mode}；根目录：{report['root']}")
        for paper in report.get("papers", []):
            print(f"- {paper['arxiv_id']}: {paper['action']} — {paper['reason']}")
        for skipped in report.get("skipped", []):
            print(
                f"- {skipped['arxiv_id']}: 跳过（缺少 {', '.join(skipped['missing'])}）"
            )
        if command == "check":
            print(
                "检查结果：已同步"
                if report.get("ok")
                else "检查结果：需要执行 sync --write"
            )
        elif mode == "dry-run":
            print("未写入任何文件；确认后请加 --write。")
        else:
            print(
                f"已写入笔记：{len(report.get('written_notes', []))} 个；"
                f"自动视图/MOC：{len(report.get('written_views', []))} 个。"
            )
        return

    if command == "enrich":
        print(f"待语义处理：{len(report.get('pending', []))} 篇。")
        if report.get("pending"):
            print("- " + "\n- ".join(report["pending"]))
        if report.get("mode") == "preview" and report.get("pending"):
            print("未调用模型；确认后请加 --run。")
        elif report.get("invoked"):
            print(f"Codex 已结束，退出码：{report.get('returncode')}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "scan":
            report = scan_repository(args.root)
        elif args.command == "check":
            report = check_repository(args.root)
        elif args.command == "sync":
            report = sync_repository(args.root, write=args.write)
        elif args.command == "enrich":
            report = enrich_repository(args.root, run=args.run)
        elif args.command == "watch":
            try:
                for event in watch_repository(
                    args.root,
                    interval=args.interval,
                    write=args.write,
                    enrich=args.enrich,
                    once=args.once,
                ):
                    if args.json:
                        print(
                            json.dumps(event, ensure_ascii=False, sort_keys=True),
                            flush=True,
                        )
                    else:
                        _print_human("sync", event["sync"])
                        if "enrich" in event:
                            _print_human("enrich", event["enrich"])
                    enrich_report = event.get("enrich")
                    if (
                        isinstance(enrich_report, Mapping)
                        and enrich_report.get("invoked")
                        and enrich_report.get("returncode") != 0
                    ):
                        return int(enrich_report.get("returncode") or 1)
            except KeyboardInterrupt:
                if not args.json:
                    print("\n已停止监视。")
            return 0
        else:  # pragma: no cover - argparse 会拦截
            parser.error("未知命令")
            return 2
    except SyncError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        _print_human(args.command, report)
    if args.command == "check" and not report.get("ok", False):
        return 1
    if (
        args.command == "enrich"
        and report.get("invoked")
        and report.get("returncode") != 0
    ):
        return int(report["returncode"]) or 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


MODULE_PATH = Path(__file__).parents[1] / "tools" / "knowledge_sync.py"
SPEC = importlib.util.spec_from_file_location("knowledge_sync", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
knowledge_sync = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = knowledge_sync
SPEC.loader.exec_module(knowledge_sync)


class KnowledgeSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def make_complete_paper(self, arxiv_id: str = "2203.15556") -> Path:
        directory = self.root / arxiv_id
        directory.mkdir(parents=True)
        for index, (_, suffix, _) in enumerate(knowledge_sync.PDF_VARIANTS, start=1):
            (directory / f"{arxiv_id}_{suffix}.pdf").write_bytes(
                f"%PDF-fixture-{index}\n".encode()
            )
        return directory

    def note_path(self, arxiv_id: str = "2203.15556") -> Path:
        return self.root / "知识库" / "10-论文" / f"论文 - {arxiv_id}.md"

    def state(self) -> dict:
        return json.loads(
            (self.root / "知识库" / ".meta" / "state.json").read_text(encoding="utf-8")
        )

    def test_new_paper_creates_minimal_note_and_pending_state(self) -> None:
        self.make_complete_paper()

        report = knowledge_sync.sync_repository(self.root, write=True)

        note = self.note_path().read_text(encoding="utf-8")
        self.assertIn('uid: "paper:arxiv:2203.15556"', note)
        self.assertIn('semantic_status: "pending"', note)
        self.assertIn(knowledge_sync.AUTO_METADATA_BEGIN, note)
        self.assertIn(knowledge_sync.AUTO_RELATIONS_BEGIN, note)
        self.assertIn(knowledge_sync.AUTO_EVIDENCE_BEGIN, note)
        self.assertIn("[[2203.15556/2203.15556_英文原文.pdf|英文原文]]", note)
        self.assertIn("[[2203.15556/2203.15556_中文译文.pdf|中文译文]]", note)
        self.assertEqual(
            set(self.state()["papers"]["paper:arxiv:2203.15556"]["source_files"]),
            {"english", "chinese"},
        )
        state = self.state()
        self.assertEqual(state["pending"]["semantic"], ["paper:arxiv:2203.15556"])
        self.assertEqual(
            state["papers"]["paper:arxiv:2203.15556"]["note_path"],
            "知识库/10-论文/论文 - 2203.15556.md",
        )
        self.assertEqual(report["papers"][0]["action"], "create")
        moc = (self.root / "知识库" / "00-MOC" / "待审核论文.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("[[知识库/10-论文/论文 - 2203.15556|2203.15556]]", moc)
        canvas_path = self.root / "知识库" / "90-视图" / "自动论文总览.canvas"
        canvas = json.loads(canvas_path.read_text(encoding="utf-8"))
        self.assertEqual(canvas["nodes"][0]["id"], "status-pending")
        self.assertEqual(
            canvas["nodes"][1]["file"], "知识库/10-论文/论文 - 2203.15556.md"
        )

    def test_repeated_sync_is_byte_for_byte_idempotent(self) -> None:
        self.make_complete_paper()
        knowledge_sync.sync_repository(self.root, write=True)
        note_before = self.note_path().read_bytes()
        state_path = self.root / "知识库" / ".meta" / "state.json"
        state_before = state_path.read_bytes()
        moc_path = self.root / "知识库" / "00-MOC" / "待审核论文.md"
        canvas_path = self.root / "知识库" / "90-视图" / "自动论文总览.canvas"
        moc_before = moc_path.read_bytes()
        canvas_before = canvas_path.read_bytes()
        note_mtime = self.note_path().stat().st_mtime_ns
        state_mtime = state_path.stat().st_mtime_ns

        report = knowledge_sync.sync_repository(self.root, write=True)

        self.assertEqual(report["papers"][0]["action"], "unchanged")
        self.assertEqual(report["written_notes"], [])
        self.assertFalse(report["state_written"])
        self.assertEqual(report["written_views"], [])
        self.assertEqual(note_before, self.note_path().read_bytes())
        self.assertEqual(state_before, state_path.read_bytes())
        self.assertEqual(note_mtime, self.note_path().stat().st_mtime_ns)
        self.assertEqual(state_mtime, state_path.stat().st_mtime_ns)
        self.assertEqual(moc_before, moc_path.read_bytes())
        self.assertEqual(canvas_before, canvas_path.read_bytes())

    def test_obsidian_canvas_reformat_does_not_trigger_sync(self) -> None:
        self.make_complete_paper()
        knowledge_sync.sync_repository(self.root, write=True)
        canvas_path = self.root / "知识库" / "90-视图" / "自动论文总览.canvas"
        canvas = json.loads(canvas_path.read_text(encoding="utf-8"))
        obsidian_text = (
            json.dumps(canvas, ensure_ascii=False, separators=(",", ":")) + "\n"
        )
        canvas_path.write_text(obsidian_text, encoding="utf-8")

        preview = knowledge_sync.sync_repository(self.root, write=False)
        written = knowledge_sync.sync_repository(self.root, write=True)

        self.assertNotIn("知识库/90-视图/自动论文总览.canvas", preview["view_changes"])
        self.assertEqual(written["written_views"], [])
        self.assertEqual(canvas_path.read_text(encoding="utf-8"), obsidian_text)

    def test_pdf_change_updates_hash_and_resets_workflow(self) -> None:
        directory = self.make_complete_paper()
        knowledge_sync.sync_repository(self.root, write=True)
        old_hash = self.state()["papers"]["paper:arxiv:2203.15556"]["source_hash"]

        note = self.note_path().read_text(encoding="utf-8")
        note = note.replace(
            'semantic_status: "pending"', 'semantic_status: "candidate_generated"'
        )
        note = note.replace('review_status: "pending"', 'review_status: "approved"')
        self.note_path().write_text(note, encoding="utf-8")
        english = directory / "2203.15556_英文原文.pdf"
        english.write_bytes(english.read_bytes() + b"changed")

        report = knowledge_sync.sync_repository(self.root, write=True)

        new_note = self.note_path().read_text(encoding="utf-8")
        new_state = self.state()
        self.assertEqual(report["papers"][0]["action"], "update")
        self.assertNotEqual(
            old_hash, new_state["papers"]["paper:arxiv:2203.15556"]["source_hash"]
        )
        self.assertIn('semantic_status: "pending"', new_note)
        self.assertIn('review_status: "pending"', new_note)
        self.assertEqual(
            new_state["papers"]["paper:arxiv:2203.15556"]["semantic_status"], "pending"
        )

    def test_state_merge_preserves_unknown_top_level_and_entry_fields(self) -> None:
        directory = self.make_complete_paper()
        knowledge_sync.sync_repository(self.root, write=True)
        state_path = self.root / "知识库" / ".meta" / "state.json"
        state = self.state()
        state["external_pipeline"] = {"cursor": 7, "keep": ["x", "y"]}
        state["papers"]["paper:arxiv:2203.15556"]["human_flag"] = "keep-me"
        state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        chinese = directory / "2203.15556_中文译文.pdf"
        chinese.write_bytes(chinese.read_bytes() + b"revision")

        knowledge_sync.sync_repository(self.root, write=True)

        merged = self.state()
        self.assertEqual(merged["external_pipeline"], {"cursor": 7, "keep": ["x", "y"]})
        self.assertEqual(
            merged["papers"]["paper:arxiv:2203.15556"]["human_flag"], "keep-me"
        )

    def test_legacy_bilingual_metadata_is_removed(self) -> None:
        self.make_complete_paper()
        knowledge_sync.sync_repository(self.root, write=True)
        note = self.note_path()
        note.write_text(
            note.read_text(encoding="utf-8").replace(
                'source_chinese_hash:',
                'source_bilingual_hash: "legacy"\nsource_chinese_hash:',
                1,
            ),
            encoding="utf-8",
        )
        state_path = self.root / "知识库" / ".meta" / "state.json"
        state = self.state()
        entry = state["papers"]["paper:arxiv:2203.15556"]
        entry["semantic_status"] = "reviewed"
        entry["review_status"] = "reviewed"
        entry["source_files"]["bilingual"] = "legacy.pdf"
        entry["source_hashes"]["bilingual"] = "legacy"
        state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        note.write_text(
            note.read_text(encoding="utf-8")
            .replace('semantic_status: "pending"', 'semantic_status: "reviewed"')
            .replace('review_status: "pending"', 'review_status: "reviewed"'),
            encoding="utf-8",
        )

        knowledge_sync.sync_repository(self.root, write=True)

        self.assertNotIn("source_bilingual_hash", note.read_text(encoding="utf-8"))
        migrated = self.state()["papers"]["paper:arxiv:2203.15556"]
        self.assertNotIn("bilingual", migrated["source_files"])
        self.assertNotIn("bilingual", migrated["source_hashes"])
        self.assertEqual(migrated["semantic_status"], "reviewed")
        self.assertEqual(migrated["review_status"], "reviewed")

    def test_stale_state_hash_does_not_downgrade_current_reviewed_note(self) -> None:
        self.make_complete_paper()
        knowledge_sync.sync_repository(self.root, write=True)
        note = self.note_path()
        text = note.read_text(encoding="utf-8")
        text = text.replace('semantic_status: "pending"', 'semantic_status: "reviewed"')
        text = text.replace('review_status: "pending"', 'review_status: "reviewed"')
        note.write_text(text, encoding="utf-8")
        state_path = self.root / "知识库" / ".meta" / "state.json"
        state = self.state()
        state["papers"]["paper:arxiv:2203.15556"]["source_hash"] = "0" * 64
        state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

        knowledge_sync.sync_repository(self.root, write=True)

        updated = note.read_text(encoding="utf-8")
        self.assertIn('semantic_status: "reviewed"', updated)
        self.assertIn('review_status: "reviewed"', updated)
        self.assertNotEqual(
            self.state()["papers"]["paper:arxiv:2203.15556"]["source_hash"],
            "0" * 64,
        )

    def test_human_content_and_semantic_blocks_are_protected(self) -> None:
        directory = self.make_complete_paper()
        knowledge_sync.sync_repository(self.root, write=True)
        note = self.note_path().read_text(encoding="utf-8")
        note = note.replace("---\n\n#", 'custom_field: "人工保留"\n---\n\n#', 1)
        note = note.replace(
            "> 状态：`pending`。来源同步器不会在未进行语义分析时伪造内容。",
            "- [[概念 - 计算最优]] # 已有候选，不应被来源同步覆盖",
            1,
        )
        human = "这是我的人工理解，必须逐字保留。"
        note += human + "\n"
        self.note_path().write_text(note, encoding="utf-8")
        chinese = directory / "2203.15556_中文译文.pdf"
        chinese.write_bytes(chinese.read_bytes() + b"revision")

        knowledge_sync.sync_repository(self.root, write=True)

        updated = self.note_path().read_text(encoding="utf-8")
        self.assertIn('custom_field: "人工保留"', updated)
        self.assertIn(human, updated)
        self.assertIn("[[概念 - 计算最优]] # 已有候选，不应被来源同步覆盖", updated)

    def test_incomplete_directory_is_skipped_without_creating_state(self) -> None:
        directory = self.root / "2001.08361"
        directory.mkdir()
        (directory / "2001.08361_英文原文.pdf").write_bytes(b"%PDF-one")

        report = knowledge_sync.sync_repository(self.root, write=True)

        self.assertEqual(report["papers"], [])
        self.assertEqual(len(report["skipped"]), 1)
        self.assertIn("2001.08361_中文译文.pdf", report["skipped"][0]["missing"])
        self.assertFalse(self.note_path("2001.08361").exists())
        self.assertFalse((self.root / "知识库" / ".meta" / "state.json").exists())
        check = knowledge_sync.check_repository(self.root)
        self.assertTrue(check["ok"])
        self.assertEqual(len(check["warnings"]), 1)

    def test_same_name_non_pdf_is_treated_as_incomplete(self) -> None:
        directory = self.make_complete_paper()
        (directory / "2203.15556_中文译文.pdf").write_bytes(b"not-a-pdf")

        report = knowledge_sync.sync_repository(self.root, write=True)

        self.assertEqual(report["papers"], [])
        self.assertIn("文件头不是 %PDF-", report["skipped"][0]["missing"][0])
        self.assertFalse(self.note_path().exists())

    def test_existing_note_is_found_by_uid_after_manual_move(self) -> None:
        self.make_complete_paper()
        moved = self.root / "知识库" / "10-论文" / "我的自定义论文名.md"
        moved.parent.mkdir(parents=True)
        moved.write_text(
            "---\nuid: paper:arxiv:2203.15556\n---\n\n# 人工标题\n\n人工内容\n",
            encoding="utf-8",
        )

        knowledge_sync.sync_repository(self.root, write=True)

        self.assertTrue(moved.exists())
        self.assertIn("人工内容", moved.read_text(encoding="utf-8"))
        self.assertFalse(self.note_path().exists())
        self.assertEqual(
            self.state()["papers"]["paper:arxiv:2203.15556"]["note_path"],
            "知识库/10-论文/我的自定义论文名.md",
        )

    def test_adopting_reviewed_note_does_not_downgrade_it_to_pending(self) -> None:
        self.make_complete_paper()
        note = self.note_path()
        note.parent.mkdir(parents=True)
        note.write_text(
            "---\n"
            "uid: paper:arxiv:2203.15556\n"
            "review_status: reviewed\n"
            "---\n\n"
            "# 已审核笔记\n\n"
            "<!-- AUTO:RELATIONS:BEGIN -->\n- [[现有命题]]\n<!-- AUTO:RELATIONS:END -->\n",
            encoding="utf-8",
        )

        knowledge_sync.sync_repository(self.root, write=True)

        updated = note.read_text(encoding="utf-8")
        entry = self.state()["papers"]["paper:arxiv:2203.15556"]
        # 既有工作流字段本身也不应因首次纳管被无谓重写。
        self.assertIn("review_status: reviewed", updated)
        self.assertIn('semantic_status: "reviewed"', updated)
        self.assertIn("[[现有命题]]", updated)
        self.assertEqual(entry["review_status"], "reviewed")
        self.assertEqual(entry["semantic_status"], "reviewed")
        self.assertNotIn("paper:arxiv:2203.15556", self.state()["pending"]["semantic"])

    def test_enrich_requires_explicit_run_and_can_be_mocked(self) -> None:
        self.make_complete_paper()
        knowledge_sync.sync_repository(self.root, write=True)
        fake_completed = subprocess.CompletedProcess(args=[], returncode=0)
        runner = mock.Mock(return_value=fake_completed)

        with mock.patch.object(
            knowledge_sync.shutil, "which", return_value="/mock/codex"
        ):
            preview = knowledge_sync.enrich_repository(
                self.root, run=False, runner=runner
            )
            executed = knowledge_sync.enrich_repository(
                self.root, run=True, runner=runner
            )

        self.assertFalse(preview["invoked"])
        self.assertEqual(runner.call_count, 1)
        command = runner.call_args.args[0]
        self.assertEqual(command[:2], ["/mock/codex", "exec"])
        self.assertIn("workspace-write", command)
        self.assertIn('approval_policy="never"', command)
        prompt = runner.call_args.kwargs["input"]
        self.assertIn("AUTO:RELATIONS", prompt)
        self.assertIn("不得把 `$...$` 数学公式写进 Wiki 链接", prompt)
        self.assertEqual(executed["returncode"], 0)

    def test_enrich_skips_reviewed_note_when_state_is_stale(self) -> None:
        self.make_complete_paper()
        knowledge_sync.sync_repository(self.root, write=True)
        note = self.note_path()
        text = note.read_text(encoding="utf-8")
        text = text.replace('semantic_status: "pending"', 'semantic_status: "reviewed"')
        text = text.replace('review_status: "pending"', 'review_status: "reviewed"')
        note.write_text(text, encoding="utf-8")
        runner = mock.Mock()

        report = knowledge_sync.enrich_repository(self.root, run=True, runner=runner)

        self.assertFalse(report["invoked"])
        self.assertEqual(report["pending"], [])
        self.assertEqual(report["skipped"][0]["reason"], "paper 笔记已审核")
        runner.assert_not_called()

    def test_sync_cli_defaults_to_dry_run(self) -> None:
        self.make_complete_paper()

        with mock.patch("builtins.print"):
            exit_code = knowledge_sync.main(["sync", "--root", str(self.root)])

        self.assertEqual(exit_code, 0)
        self.assertFalse(self.note_path().exists())
        self.assertFalse((self.root / "知识库" / ".meta" / "state.json").exists())

    def test_watch_waits_until_file_signature_is_stable(self) -> None:
        fake_report = {"mode": "dry-run"}
        signatures = [("copying",), ("larger",), ("larger",), ("larger",)]
        with (
            mock.patch.object(
                knowledge_sync, "_watch_signature", side_effect=signatures
            ),
            mock.patch.object(
                knowledge_sync, "sync_repository", return_value=fake_report
            ) as sync_mock,
            mock.patch.object(knowledge_sync.time, "sleep") as sleep_mock,
        ):
            watcher = knowledge_sync.watch_repository(
                self.root, interval=2, write=False
            )
            event = next(watcher)

        self.assertEqual(event["sync"], fake_report)
        self.assertEqual(sync_mock.call_count, 1)
        self.assertEqual(sleep_mock.call_count, 2)

    def test_watch_cli_propagates_enrich_failure(self) -> None:
        event = {
            "event": "sync",
            "sync": {
                "mode": "write",
                "root": str(self.root),
                "papers": [],
                "skipped": [],
            },
            "enrich": {"invoked": True, "pending": ["paper:arxiv:1"], "returncode": 17},
        }
        with (
            mock.patch.object(
                knowledge_sync, "watch_repository", return_value=iter([event])
            ),
            mock.patch("builtins.print"),
        ):
            exit_code = knowledge_sync.main(
                ["watch", "--root", str(self.root), "--write", "--enrich", "--once"]
            )

        self.assertEqual(exit_code, 17)


if __name__ == "__main__":
    unittest.main()

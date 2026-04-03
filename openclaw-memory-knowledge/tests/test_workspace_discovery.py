import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import auto_migrate  # noqa: E402
import mk_arch_core  # noqa: E402


class WorkspaceDiscoveryTests(unittest.TestCase):
    def test_discover_openclaw_paths_skips_runtime_only_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir) / ".openclaw" / "workspace"
            memory = workspace / "memory"
            memory.mkdir(parents=True)
            (memory / "main.sqlite").write_text("", encoding="utf-8")

            result = mk_arch_core.discover_openclaw_paths(workspace)

            self.assertIsNone(result["workspace_base"])
            self.assertIsNone(result["memory_path"])
            self.assertIsNone(result["default_target_root"])

    def test_discover_openclaw_paths_accepts_user_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            workspace = Path(tmp_dir) / ".openclaw" / "workspace"
            memory = workspace / "memory"
            memory.mkdir(parents=True)
            (memory / "notes.md").write_text("用户记忆内容", encoding="utf-8")

            result = mk_arch_core.discover_openclaw_paths(workspace)

            self.assertEqual(result["workspace_base"], workspace.resolve())
            self.assertEqual(result["memory_path"], memory.resolve())
            self.assertEqual(result["default_target_root"], (memory / ".adaptr-v1").resolve())

    def test_resolve_workspace_root_uses_filtered_home_fallback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            home = Path(tmp_dir)
            workspace = home / ".openclaw" / "workspace"
            memory = workspace / "memory"
            memory.mkdir(parents=True)
            (memory / "session.md").write_text("用户工作区内容", encoding="utf-8")

            with mock.patch.object(auto_migrate.Path, "home", return_value=home):
                with mock.patch.object(auto_migrate, "guess_workspace_root", return_value=None):
                    resolved = auto_migrate.resolve_workspace_root("")

            self.assertEqual(resolved, workspace.resolve())


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""零配置自动迁移入口。

目标：
1. 自动识别原生 OpenClaw 工作区（无需用户传 memory/knowledge 路径）
2. 首次执行自动 bootstrap 重构
3. 后续执行自动 incremental 增量入库
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from mk_arch_core import discover_openclaw_paths


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="原生 OpenClaw memory/knowledge 零配置自动迁移")
    parser.add_argument(
        "--workspace-root",
        default="",
        help="可选：OpenClaw 根目录或 workspace 目录。不传则从当前目录向上自动发现",
    )
    parser.add_argument(
        "--mode",
        choices=["auto", "bootstrap", "ingest"],
        default="auto",
        help="auto=首次重构，后续增量；也可强制 bootstrap 或 ingest",
    )
    parser.add_argument(
        "--no-apply",
        action="store_true",
        help="不落库，仅预览",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="输出精简日志（适合 hook 调用）",
    )
    parser.add_argument(
        "--evolve",
        choices=["auto", "on", "off"],
        default="auto",
        help="迁移后是否执行 self-evolve（auto: apply 时自动执行）",
    )
    parser.add_argument(
        "--emit-summary-json",
        action="store_true",
        help="输出结构化执行摘要（适合 hook/UI 集成）",
    )
    return parser.parse_args()


def has_workspace_markers(root: Path) -> bool:
    markers = ["AGENTS.md", "MEMORY.md", ".adaptr-v1", ".openclaw"]
    return any((root / name).exists() for name in markers)


def looks_like_workspace_root(
    root: Path,
    *,
    allow_direct_pair: bool,
    require_direct_pair_markers: bool,
) -> bool:
    has_workspace_subdir = (
        (root / "workspace" / "memory").exists() or
        (root / "workspace" / "knowledge").exists()
    )
    if has_workspace_subdir:
        return True

    mem = root / "memory"
    kn = root / "knowledge"
    if root.name == "workspace" and (mem.exists() or kn.exists()):
        return True
    if not allow_direct_pair:
        return False
    if not (mem.exists() and kn.exists()):
        return False
    if require_direct_pair_markers and not has_workspace_markers(root):
        return False
    return True


def guess_workspace_root(start: Path) -> Optional[Path]:
    """从当前目录向上推断 OpenClaw 根或 workspace 目录。"""
    candidates = [start, *start.parents]
    for p in candidates:
        if looks_like_workspace_root(
            p,
            allow_direct_pair=True,
            require_direct_pair_markers=True,
        ):
            return p
        oc = p / ".openclaw"
        if looks_like_workspace_root(
            oc,
            allow_direct_pair=False,
            require_direct_pair_markers=False,
        ):
            return p / ".openclaw"
    return None


def resolve_workspace_root(arg_workspace_root: str) -> Path:
    if arg_workspace_root:
        return Path(arg_workspace_root).expanduser().resolve()
    guessed = guess_workspace_root(Path.cwd().resolve())
    if guessed is None:
        # 兜底：复用 discover_openclaw_paths 的过滤逻辑，避免误命中 runtime sqlite。
        for home_candidate in [
            Path.home() / ".openclaw" / "workspace",
            Path.home() / ".openclaw",
        ]:
            if not home_candidate.exists():
                continue
            result = discover_openclaw_paths(home_candidate)
            if result.get("workspace_base") is not None:
                return result["workspace_base"]
        raise SystemExit(
            "无法自动发现 OpenClaw 工作区。请在 OpenClaw 项目目录执行，或传 --workspace-root。"
        )
    return guessed


def has_bootstrap_state(target_root: Path) -> bool:
    hash_file = target_root / "state" / "record_hashes.txt"
    if not hash_file.exists():
        return False
    try:
        return bool(hash_file.read_text(encoding="utf-8").strip())
    except OSError:
        return False


def acquire_lock(target_root: Path) -> Optional[Path]:
    lock_dir = target_root / "state"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_file = lock_dir / ".auto_migrate.lock"
    try:
        fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode("utf-8"))
        os.close(fd)
        return lock_file
    except FileExistsError:
        return None


def release_lock(lock_file: Optional[Path]) -> None:
    if lock_file is None:
        return
    try:
        lock_file.unlink(missing_ok=True)
    except OSError:
        pass


def run_cmd(cmd: list[str], quiet: bool) -> Tuple[int, str, str]:
    if not quiet:
        print(f"[auto-migrate] run: {' '.join(cmd)}")

    proc = subprocess.run(cmd, text=True, capture_output=True)
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""

    if not quiet:
        if stdout:
            print(stdout, end="" if stdout.endswith("\n") else "\n")
        if stderr:
            print(stderr, file=sys.stderr, end="" if stderr.endswith("\n") else "\n")

    if proc.returncode != 0 and not quiet:
        tail = (stderr or stdout).strip().splitlines()[-8:]
        msg = "\n".join(tail)
        print(
            f"[auto-migrate] command failed ({proc.returncode}): {' '.join(cmd)}",
            file=sys.stderr,
        )
        if msg:
            print(msg, file=sys.stderr)
    return proc.returncode, stdout, stderr


def parse_report_from_stdout(stdout: str) -> Optional[Dict[str, Any]]:
    text = (stdout or "").strip()
    if not text:
        return None

    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass

    # 兜底：尝试从最后一个 JSON 对象起始位置反向解析。
    idx = text.rfind("{")
    while idx >= 0:
        chunk = text[idx:]
        try:
            obj = json.loads(chunk)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        idx = text.rfind("{", 0, idx)
    return None


def migration_has_meaningful_changes(report: Optional[Dict[str, Any]]) -> bool:
    if not report:
        # 无法解析报告时，保守处理：继续执行自修复。
        return True

    run_type = str(report.get("run_type", ""))
    update_stats = report.get("update_stats") or {}
    if isinstance(update_stats, dict):
        removed = int(update_stats.get("old_records_removed", 0) or 0)
        inserted = int(update_stats.get("new_records_inserted", 0) or 0)
        if removed > 0 or inserted > 0:
            return True

    if run_type == "bootstrap_restructure":
        write_stats = report.get("write_stats") or {}
        if isinstance(write_stats, dict):
            if int(write_stats.get("records_written", 0) or 0) > 0:
                return True
        return False

    scan = report.get("scan") or {}
    if isinstance(scan, dict):
        if int(scan.get("changed_files", 0) or 0) > 0:
            return True

    return False


def should_run_evolve(evolve_mode: str, apply_enabled: bool, changed: bool) -> bool:
    if not apply_enabled:
        return False
    if evolve_mode == "off":
        return False
    if evolve_mode == "on":
        return True
    # auto: 仅在迁移有实质变更时执行。
    return changed


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def build_error_hints(error_text: str) -> list[str]:
    text = (error_text or "").lower()
    hints: list[str] = []
    if "无法自动发现 openclaw 工作区" in error_text or "未发现原生 openclaw" in error_text:
        hints.append("确认当前目录含有 workspace/memory 或 workspace/knowledge，或显式传 --workspace-root。")
    if "缺少 --target-root" in error_text or "无法推断 target_root" in error_text:
        hints.append("确认 memory 或 knowledge 目录存在，并允许在其下创建 .adaptr-v1。")
    if "permission" in text or "权限" in error_text:
        hints.append("检查目标目录写权限（state/、reports/、layers/）。")
    if "no such file" in text or "not found" in text:
        hints.append("检查脚本文件是否完整同步到 hook/skills 目录。")
    if not hints:
        hints.append("查看 reports 目录中的最新报告，定位 warnings/missing_paths/failed probe。")
    return hints


def emit_summary(summary: Dict[str, Any], args: argparse.Namespace) -> None:
    if args.emit_summary_json or args.quiet:
        print(json.dumps(summary, ensure_ascii=False))


def build_discovery(workspace_root: Path) -> Dict[str, Optional[Path]]:
    discovered = discover_openclaw_paths(workspace_root)
    if discovered["workspace_base"] is None:
        raise SystemExit(
            f"未发现原生 OpenClaw memory/knowledge 目录：{workspace_root}"
        )
    return discovered


def main() -> int:
    args = parse_args()
    summary: Dict[str, Any] = {
        "ok": False,
        "generated_at": utc_now(),
        "mode_requested": args.mode,
        "apply": not args.no_apply,
        "quiet": args.quiet,
        "workspace_root": "",
        "target_root": "",
        "run_mode": "",
        "changed": False,
        "migrate_report_path": "",
        "evolve_report_path": "",
        "error_detail": "",
        "hints": [],
        "error": "",
    }

    try:
        workspace_root = resolve_workspace_root(args.workspace_root)
        discovered = build_discovery(workspace_root)
    except SystemExit as exc:
        err = str(exc)
        summary["error"] = err
        summary["hints"] = build_error_hints(err)
        emit_summary(summary, args)
        if not args.quiet:
            print(err, file=sys.stderr)
        return 2

    summary["workspace_root"] = str(workspace_root)
    target_root = discovered["default_target_root"]
    if target_root is None:
        err = "无法推断 target_root，请检查工作区结构。"
        summary["error"] = err
        summary["hints"] = build_error_hints(err)
        emit_summary(summary, args)
        if not args.quiet:
            print(err, file=sys.stderr)
        return 2
    summary["target_root"] = str(target_root)

    bootstrap_script = Path(__file__).resolve().parent / "bootstrap_restructure.py"
    ingest_script = Path(__file__).resolve().parent / "incremental_ingest.py"
    evolve_script = Path(__file__).resolve().parent / "self_evolve.py"
    py = sys.executable or "python3"

    mode = args.mode
    if mode == "auto":
        mode = "ingest" if has_bootstrap_state(target_root) else "bootstrap"
    summary["run_mode"] = mode

    lock_file = acquire_lock(target_root)
    if lock_file is None:
        summary["ok"] = True
        summary["hints"] = ["检测到已有迁移进程，已安全跳过本次执行。"]
        emit_summary(summary, args)
        if not args.quiet:
            print("[auto-migrate] skip: another migration process is running.")
        return 0

    try:
        cmd: list[str]
        if mode == "bootstrap":
            cmd = [
                py,
                str(bootstrap_script),
                "--workspace-root",
                str(workspace_root),
            ]
            if not args.no_apply:
                cmd.append("--apply")
        else:
            cmd = [
                py,
                str(ingest_script),
                "--workspace-root",
                str(workspace_root),
            ]
            if not args.no_apply:
                cmd.append("--apply")

        migrate_rc, migrate_stdout, migrate_stderr = run_cmd(cmd, quiet=args.quiet)
        if migrate_rc != 0:
            summary["error"] = "migration_subprocess_failed"
            detail = (migrate_stderr or migrate_stdout).strip()
            if detail:
                summary["error_detail"] = "\n".join(detail.splitlines()[-12:])
            summary["hints"] = build_error_hints("\n".join([migrate_stdout, migrate_stderr]))
            emit_summary(summary, args)
            return migrate_rc

        migrate_report = parse_report_from_stdout(migrate_stdout)
        if migrate_report:
            summary["migrate_report_path"] = str(migrate_report.get("report_path", "") or "")
        changed = migration_has_meaningful_changes(migrate_report)
        summary["changed"] = bool(changed)
        run_evolve = should_run_evolve(
            args.evolve,
            apply_enabled=not args.no_apply,
            changed=changed,
        )
        if run_evolve:
            evolve_report_out = target_root / "reports" / (
                f"self_evolve_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
            )
            evolve_cmd = [
                py,
                str(evolve_script),
                "--target-root",
                str(target_root),
                "--repair",
                "--report-out",
                str(evolve_report_out),
            ]
            evolve_rc, evolve_stdout, evolve_stderr = run_cmd(evolve_cmd, quiet=args.quiet)
            if evolve_rc != 0 and not args.quiet:
                print("[auto-migrate] warning: self-evolve failed, but migration has completed.")
            if evolve_report_out.exists():
                summary["evolve_report_path"] = str(evolve_report_out)
            if evolve_stdout:
                evolve_report = parse_report_from_stdout(evolve_stdout)
                if evolve_report:
                    summary["evolve_report_path"] = str(evolve_report.get("report_out", "") or "")
            if evolve_rc != 0:
                summary["error"] = "self_evolve_failed"
                detail = (evolve_stderr or evolve_stdout).strip()
                if detail:
                    summary["error_detail"] = "\n".join(detail.splitlines()[-12:])
                summary["hints"] = ["迁移已完成，但自修复失败。可手动执行 self_evolve.py --repair 并检查输出报告。"]
                emit_summary(summary, args)
                return 0
        elif not args.quiet:
            print("[auto-migrate] skip self-evolve (no meaningful changes detected).")

        summary["ok"] = True
        if not summary["hints"]:
            summary["hints"] = ["迁移执行完成。可查看 reports 中最新报告与 retrieval-hints.json。"]
        emit_summary(summary, args)
        return 0
    finally:
        release_lock(lock_file)


if __name__ == "__main__":
    raise SystemExit(main())

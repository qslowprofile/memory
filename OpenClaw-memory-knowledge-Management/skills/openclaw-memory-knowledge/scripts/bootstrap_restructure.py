#!/usr/bin/env python3
"""存量 memory/knowledge 重构脚本。"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 确保同目录下的模块可被 import
_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from mk_arch_core import (
    BUCKET_FILES,
    clear_arch_files,
    get_llm_backend_name,
    get_llm_status,
    collect_and_normalize,
    discover_openclaw_paths,
    ensure_arch_layout,
    load_file_state,
    load_record_hashes,
    read_jsonl,
    rebuild_materialized_views_from_l2,
    save_file_state,
    save_record_hashes,
    summarize_records,
    update_file_state,
    write_json,
    write_jsonl,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="重构已有 memory/knowledge 为 AdaMem-lite + OpenViking 分层架构"
    )
    parser.add_argument(
        "--workspace-root",
        default="",
        help="原生 OpenClaw 工作区根目录。传入后可自动识别 memory/knowledge 路径",
    )
    parser.add_argument(
        "--memory-path",
        action="append",
        default=[],
        help="memory 根目录或文件，可重复传入",
    )
    parser.add_argument(
        "--knowledge-path",
        action="append",
        default=[],
        help="knowledge 根目录或文件，可重复传入",
    )
    parser.add_argument(
        "--target-root",
        default="",
        help="重构后数据落盘根目录",
    )
    parser.add_argument(
        "--mode",
        choices=["rebuild", "merge"],
        default="rebuild",
        help="rebuild: 重建写入；merge: 合并写入",
    )
    parser.add_argument(
        "--backup-mode",
        choices=["auto", "on", "off"],
        default="auto",
        help="apply 且 mode=rebuild 时是否备份已有 .adaptr-v1（auto: 有旧数据才备份）",
    )
    parser.add_argument(
        "--backup-dir",
        default="",
        help="可选：备份输出目录。默认放在 target-root 同级目录",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="执行写入。默认仅 dry-run 预览",
    )
    parser.add_argument(
        "--recursive",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="是否递归扫描目录",
    )
    parser.add_argument(
        "--include-hidden",
        action="store_true",
        help="是否包含隐藏目录/文件",
    )
    parser.add_argument(
        "--max-file-size-mb",
        type=int,
        default=30,
        help="单文件最大读取大小（MB）",
    )
    parser.add_argument(
        "--max-records-per-file",
        type=int,
        default=3000,
        help="每个文件最多抽取记录数",
    )
    parser.add_argument(
        "--report-out",
        default="",
        help="可选：将报告写到指定路径（JSON）",
    )
    return parser.parse_args()


def resolve_sources(memory_paths: List[str], knowledge_paths: List[str]) -> Tuple[List[Tuple[str, Path]], List[str]]:
    source_items: List[Tuple[str, Path]] = []
    missing: List[str] = []

    for p in memory_paths:
        path = Path(p).expanduser()
        if path.exists():
            source_items.append(("memory", path.resolve()))
        else:
            missing.append(str(path))

    for p in knowledge_paths:
        path = Path(p).expanduser()
        if path.exists():
            source_items.append(("knowledge", path.resolve()))
        else:
            missing.append(str(path))

    return source_items, missing


def has_existing_arch_data(target_root: Path) -> bool:
    if not target_root.exists():
        return False
    try:
        for p in target_root.rglob("*"):
            if p.is_file():
                return True
    except OSError:
        return False
    return False


def create_target_backup(target_root: Path, backup_dir_arg: str) -> Optional[Path]:
    if not target_root.exists():
        return None
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_base = Path(backup_dir_arg).expanduser().resolve() if backup_dir_arg else target_root.parent
    backup_base.mkdir(parents=True, exist_ok=True)
    backup_path = backup_base / f"{target_root.name}.backup.{timestamp}"
    shutil.copytree(target_root, backup_path, dirs_exist_ok=False)
    return backup_path


def restore_target_from_backup(target_root: Path, backup_path: Path) -> None:
    if target_root.exists():
        shutil.rmtree(target_root, ignore_errors=True)
    shutil.copytree(backup_path, target_root, dirs_exist_ok=False)


def main() -> int:
    args = parse_args()
    auto_discovery = {}
    memory_paths = list(args.memory_path)
    knowledge_paths = list(args.knowledge_path)

    if args.workspace_root:
        discovered = discover_openclaw_paths(Path(args.workspace_root))
        auto_discovery = {
            "workspace_root": str(discovered["workspace_root"]) if discovered["workspace_root"] else None,
            "workspace_base": str(discovered["workspace_base"]) if discovered["workspace_base"] else None,
            "memory_path": str(discovered["memory_path"]) if discovered["memory_path"] else None,
            "knowledge_path": str(discovered["knowledge_path"]) if discovered["knowledge_path"] else None,
        }
        if discovered["memory_path"] and not memory_paths:
            memory_paths.append(str(discovered["memory_path"]))
        if discovered["knowledge_path"] and not knowledge_paths:
            knowledge_paths.append(str(discovered["knowledge_path"]))
        if not args.target_root and discovered["default_target_root"] is not None:
            args.target_root = str(discovered["default_target_root"])

    if not args.target_root:
        raise SystemExit("缺少 --target-root。可传 --workspace-root 以自动推断默认目标目录。")

    target_root = Path(args.target_root).expanduser().resolve()

    source_items, missing = resolve_sources(memory_paths, knowledge_paths)
    if not source_items:
        raise SystemExit("未找到可用输入路径，请至少提供一个存在的 --memory-path 或 --knowledge-path")

    paths = ensure_arch_layout(target_root)

    max_file_size_bytes = args.max_file_size_mb * 1024 * 1024
    result = collect_and_normalize(
        source_items=source_items,
        max_file_size_bytes=max_file_size_bytes,
        max_records_per_file=args.max_records_per_file,
        recursive=args.recursive,
        include_hidden=args.include_hidden,
        formation_mode="bootstrap",
    )

    normalized_records = result["normalized_records"]
    rejected_records = result.get("rejected_records", [])
    policy_skipped_records = result.get("policy_skipped_records", [])

    existing_hashes = load_record_hashes(paths["hash_state"]) if args.mode == "merge" else set()
    deduped_records = []
    seen_hashes = set(existing_hashes)
    duplicate_count = 0
    for rec in normalized_records:
        if rec.content_hash in seen_hashes:
            duplicate_count += 1
            continue
        deduped_records.append(rec)
        seen_hashes.add(rec.content_hash)

    summary_before = summarize_records(normalized_records)
    summary_after = summarize_records(deduped_records)

    report: Dict[str, Any] = {
        "run_type": "bootstrap_restructure",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "llm_backend": get_llm_backend_name(),
        "llm_status": get_llm_status(),
        "mode": args.mode,
        "backup_mode": args.backup_mode,
        "backup_dir": args.backup_dir,
        "apply": args.apply,
        "target_root": str(target_root),
        "source_paths": [
            {"source_kind": kind, "path": str(path)} for kind, path in source_items
        ],
        "auto_discovery": auto_discovery,
        "missing_paths": missing,
        "scan": {
            "total_candidate_files": len(result["scanned_files"]) + len(result["skipped_files"]),
            "scanned_files": len(result["scanned_files"]),
            "skipped_files": len(result["skipped_files"]),
            "warnings": result["warnings"],
            "policy_skipped_records": len(policy_skipped_records),
            "rejected_records": len(rejected_records),
        },
        "raw_record_count": len(result["raw_records"]),
        "normalized_before_dedup": summary_before,
        "normalized_after_dedup": summary_after,
        "duplicate_count": duplicate_count,
        "bucket_files": BUCKET_FILES,
    }

    if args.apply:
        backup_path: Optional[Path] = None
        backup_created = False
        rollback_restored = False
        try:
            if args.mode == "rebuild":
                old_data_exists = has_existing_arch_data(target_root)
                should_backup = args.backup_mode == "on" or (
                    args.backup_mode == "auto" and old_data_exists
                )
                if should_backup and old_data_exists:
                    backup_path = create_target_backup(target_root, args.backup_dir)
                    backup_created = backup_path is not None
                clear_arch_files(paths)
                base_l2_rows = []
            else:
                base_l2_rows = read_jsonl(paths["l2"])

            incoming_l2_rows = [r.to_json() for r in deduped_records]
            merged_l2_rows = []
            merged_seen_hashes = set()
            for row in base_l2_rows + incoming_l2_rows:
                content_hash = str(row.get("content_hash", ""))
                if content_hash and content_hash in merged_seen_hashes:
                    continue
                if content_hash:
                    merged_seen_hashes.add(content_hash)
                merged_l2_rows.append(row)

            write_stats = rebuild_materialized_views_from_l2(paths, merged_l2_rows)
            save_record_hashes(paths["hash_state"], seen_hashes | merged_seen_hashes)

            file_state = load_file_state(paths["file_state"])
            for _, file_path in result["scanned_files"]:
                update_file_state(file_path, file_state)
            save_file_state(paths["file_state"], file_state)

            report["write_stats"] = write_stats
            report["backup"] = {
                "created": backup_created,
                "backup_path": str(backup_path) if backup_path else "",
                "rollback_restored": False,
            }

            if rejected_records:
                rejected_path = paths["reports_dir"] / f"rejected_bootstrap_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.jsonl"
                write_jsonl(rejected_path, rejected_records, append=False)
                report["rejected_report_path"] = str(rejected_path)
            if policy_skipped_records:
                policy_path = paths["reports_dir"] / f"policy_skipped_bootstrap_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.jsonl"
                write_jsonl(policy_path, policy_skipped_records, append=False)
                report["policy_skipped_report_path"] = str(policy_path)

            report_path = paths["reports_dir"] / f"bootstrap_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
            write_json(report_path, report)
            report["report_path"] = str(report_path)
        except Exception as exc:  # noqa: BLE001
            report["apply_error"] = str(exc)
            if backup_path is not None and backup_path.exists() and args.mode == "rebuild":
                try:
                    restore_target_from_backup(target_root, backup_path)
                    rollback_restored = True
                except Exception as rollback_exc:  # noqa: BLE001
                    report["rollback_error"] = str(rollback_exc)
            report["backup"] = {
                "created": backup_created,
                "backup_path": str(backup_path) if backup_path else "",
                "rollback_restored": rollback_restored,
            }
            # 尽量写错误报告，失败也不再抛出二次异常。
            try:
                error_report = paths["reports_dir"] / f"bootstrap_failed_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
                write_json(error_report, report)
                report["report_path"] = str(error_report)
            except Exception:  # noqa: BLE001
                pass
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 2

    if args.report_out:
        report_out = Path(args.report_out).expanduser().resolve()
        report_out.parent.mkdir(parents=True, exist_ok=True)
        write_json(report_out, report)
        report["report_out"] = str(report_out)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""增量入库脚本：处理新增/变更的 memory/knowledge 文件。"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 确保同目录下的模块可被 import
_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from mk_arch_core import (
    BUCKET_FILES,
    collect_and_normalize,
    discover_openclaw_paths,
    ensure_arch_layout,
    infer_source_kind_from_path,
    iter_source_files,
    load_file_state,
    read_jsonl,
    rebuild_materialized_views_from_l2,
    save_file_state,
    should_reingest,
    update_file_state,
    get_llm_backend_name,
    get_llm_status,
    write_json,
    write_jsonl,
)
from native_memory_search import search_openclaw_memory


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="增量扫描并按架构入库 memory/knowledge")
    parser.add_argument(
        "--workspace-root",
        default="",
        help="原生 OpenClaw 工作区根目录。传入后可自动识别 memory/knowledge 路径",
    )
    parser.add_argument(
        "--input-path",
        action="append",
        default=[],
        help="新增数据目录或文件，可重复传入",
    )
    parser.add_argument(
        "--input-type",
        choices=["auto", "memory", "knowledge"],
        default="auto",
        help="输入类型。auto 会按路径推断",
    )
    parser.add_argument(
        "--target-root",
        default="",
        help="架构根目录（通常是 bootstrap 的 --target-root）",
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
        default=2000,
        help="每个文件最多抽取记录数",
    )
    parser.add_argument(
        "--semantic-dedup",
        "--retrieval-dedup",
        dest="semantic_dedup",
        choices=["auto", "on", "off"],
        default="auto",
        help="是否启用基于 OpenClaw 原生 memory search 的检索增强去重（默认 auto）",
    )
    parser.add_argument(
        "--semantic-threshold",
        "--retrieval-threshold",
        dest="semantic_threshold",
        type=float,
        default=0.94,
        help="全局重复判定阈值（0-1，默认 0.94；knowledge bucket 会按策略覆盖）",
    )
    parser.add_argument(
        "--semantic-limit",
        "--retrieval-limit",
        dest="semantic_limit",
        type=int,
        default=8,
        help="每次 native memory search 的检索条数（默认 8）",
    )
    parser.add_argument(
        "--semantic-max-calls",
        "--retrieval-max-calls",
        dest="semantic_max_calls",
        type=int,
        default=50,
        help="单次增量最多调用 native memory search 次数（默认 50）",
    )
    parser.add_argument(
        "--semantic-min-text-chars",
        "--retrieval-min-text-chars",
        dest="semantic_min_text_chars",
        type=int,
        default=80,
        help="仅对长度达到阈值的文本启用语义去重（默认 80）",
    )
    parser.add_argument(
        "--semantic-search-min-score",
        "--retrieval-search-min-score",
        dest="semantic_search_min_score",
        type=float,
        default=0.0,
        help="调用 openclaw memory search 时的最小检索分数（0-1，默认 0）",
    )
    parser.add_argument(
        "--dedup-audit-max-records",
        type=int,
        default=300,
        help="去重审计日志最多落盘条数（默认 300）",
    )
    parser.add_argument(
        "--report-out",
        default="",
        help="可选：将报告写到指定路径（JSON）",
    )
    return parser.parse_args()


def collect_candidate_files(
    input_paths: Sequence[str],
    input_type: str,
    max_file_size_bytes: int,
    recursive: bool,
    include_hidden: bool,
) -> Tuple[List[Tuple[str, Path]], List[str]]:
    source_files: List[Tuple[str, Path]] = []
    missing: List[str] = []

    for raw in input_paths:
        path = Path(raw).expanduser()
        if not path.exists():
            missing.append(str(path))
            continue

        resolved = path.resolve()
        kind = input_type
        if kind == "auto":
            kind = infer_source_kind_from_path(resolved, default_kind="memory")

        if resolved.is_file():
            source_files.append((kind, resolved))
            continue

        for file_path in iter_source_files(
            root=resolved,
            max_file_size_bytes=max_file_size_bytes,
            recursive=recursive,
            include_hidden=include_hidden,
        ):
            local_kind = kind
            if input_type == "auto":
                local_kind = infer_source_kind_from_path(file_path, default_kind=kind)
            source_files.append((local_kind, file_path))

    return source_files, missing


RESULT_TEXT_KEYS = (
    "snippet",
    "content",
    "text",
    "body",
    "summary",
    "memory",
    "passage",
)

SCORE_KEYS = (
    "score",
    "similarity",
    "relevance",
    "vectorScore",
    "textScore",
    "hybridScore",
    "confidence",
)

DISTANCE_KEYS = (
    "dist",
    "distance",
)

PATH_KEYS = (
    "path",
    "file",
    "source",
    "uri",
)

DEFAULT_BUCKET_THRESHOLDS = {
    "memory.profile": 0.94,
    "memory.preferences": 0.94,
    "memory.events": 0.94,
    "memory.agent_skill": 0.94,
    "memory.working": 0.94,
    "knowledge.facts": 0.84,
    "knowledge.procedures": 0.87,
    "knowledge.references": 0.90,
}


def normalize_query_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip().lower()
    return cleaned


def clamp_score(value: float) -> float:
    if value < 0:
        return 0.0
    if value > 1:
        return 1.0
    return float(value)


def parse_optional_score(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not (number == number):  # NaN
        return None
    return clamp_score(number)


def resolve_threshold_for_bucket(bucket: str, fallback_threshold: float) -> float:
    if bucket in DEFAULT_BUCKET_THRESHOLDS:
        return float(DEFAULT_BUCKET_THRESHOLDS[bucket])
    return float(fallback_threshold)


def extract_score_from_mapping(node: Dict[str, Any]) -> Optional[float]:
    # 按优先级读取，避免被无关 confidence 字段“抬高”分数。
    for key in SCORE_KEYS:
        if key in node:
            score = parse_optional_score(node.get(key))
            if score is not None:
                return score
    # 防御性策略：若仅返回 distance 而无 score，不做 1-dist 这类假设性换算。
    # 原因：不同后端 distance 定义不同（cosine/L2/IP），盲目换算会误判去重。
    # 当前 OpenClaw memory search 正常会返回 score，故这里返回 None 更安全。
    # 仅保留一个保守特例：distance==0 视为完全一致，返回 1.0。
    for key in DISTANCE_KEYS:
        if key in node:
            dist = parse_optional_score(node.get(key))
            if dist is None:
                continue
            if abs(dist) <= 1e-9:
                return 1.0
            return None
    return None


def extract_path_from_mapping(node: Dict[str, Any]) -> str:
    for key in PATH_KEYS:
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def extract_result_text(node: Dict[str, Any]) -> str:
    for key in RESULT_TEXT_KEYS:
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return re.sub(r"\s+", " ", value).strip()
    for value in node.values():
        if isinstance(value, str) and value.strip():
            return re.sub(r"\s+", " ", value).strip()
    return ""


def collect_search_candidates(
    payload: Any,
    max_items: int = 64,
) -> List[Dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    results = payload.get("results")
    if not isinstance(results, list):
        return []

    candidates: List[Dict[str, Any]] = []
    for entry in results:
        if len(candidates) >= max_items:
            break
        if not isinstance(entry, dict):
            continue
        score = extract_score_from_mapping(entry)
        if score is None:
            continue
        candidates.append(
            {
                "score": clamp_score(score),
                "text": extract_result_text(entry)[:1200],
                "source_path": extract_path_from_mapping(entry),
            }
        )
    candidates.sort(key=lambda item: float(item.get("score", 0.0)), reverse=True)
    return candidates


def semantic_duplicate_check(
    text: str,
    search_payload: Any,
    threshold: float,
    current_source_path: str = "",
) -> Dict[str, Any]:
    if not str(text or "").strip():
        return {
            "is_duplicate": False,
            "best_score": 0.0,
            "best_retrieval_score": 0.0,
            "candidate_count": 0,
            "best_match": "",
            "best_match_path": "",
        }

    candidates = collect_search_candidates(search_payload)
    if not candidates:
        return {
            "is_duplicate": False,
            "best_score": 0.0,
            "best_retrieval_score": 0.0,
            "candidate_count": 0,
            "best_match": "",
            "best_match_path": "",
        }

    normalized_current = str(current_source_path or "").strip()
    filtered = candidates
    if normalized_current:
        normalized_current = normalized_current.replace("\\", "/").lower()
        keep: List[Dict[str, Any]] = []
        for cand in candidates:
            cand_path = str(cand.get("source_path", "") or "").replace("\\", "/").lower()
            if cand_path and cand_path == normalized_current:
                continue
            keep.append(cand)
        filtered = keep

    if not filtered:
        return {
            "is_duplicate": False,
            "best_score": 0.0,
            "best_retrieval_score": 0.0,
            "candidate_count": 0,
            "best_match": "",
            "best_match_path": "",
        }

    top = filtered[0]
    top_score = float(top.get("score", 0.0) or 0.0)
    return {
        "is_duplicate": top_score >= threshold,
        "best_score": round(top_score, 4),
        "best_retrieval_score": round(top_score, 4),
        "candidate_count": len(filtered),
        "best_match": str(top.get("text", ""))[:240],
        "best_match_path": str(top.get("source_path", "")),
    }


def merge_records_with_update(
    existing_l2_rows: Sequence[Dict[str, object]],
    new_records,
    changed_files: Sequence[Path],
    semantic_options: Optional[Dict[str, Any]] = None,
) -> Tuple[List[Dict[str, object]], Dict[str, Any], List[Dict[str, Any]]]:
    changed_set = {str(p) for p in changed_files}
    retained_rows: List[Dict[str, object]] = []
    old_rows: List[Dict[str, object]] = []

    for row in existing_l2_rows:
        source_path = str(row.get("source_path", ""))
        if source_path in changed_set:
            old_rows.append(row)
        else:
            retained_rows.append(row)

    old_versions: Dict[Tuple[str, str], int] = {}
    for row in old_rows:
        source_path = str(row.get("source_path", ""))
        locator = str(row.get("locator", ""))
        key = (source_path, locator)
        version = int(row.get("version", 1) or 1)
        old_versions[key] = max(old_versions.get(key, 0), version)

    seen_hashes = {str(row.get("content_hash")) for row in retained_rows if row.get("content_hash")}
    inserted_rows: List[Dict[str, object]] = []
    duplicate_by_hash = 0
    duplicate_by_semantic = 0
    version_bumped = 0

    semantic_cfg = semantic_options or {}
    semantic_mode = str(semantic_cfg.get("mode", "off"))
    semantic_enabled = semantic_mode != "off"
    semantic_threshold = float(semantic_cfg.get("threshold", 0.94))
    semantic_limit = int(semantic_cfg.get("limit", 8))
    semantic_max_calls = int(semantic_cfg.get("max_calls", 50))
    semantic_min_text_chars = int(semantic_cfg.get("min_text_chars", 80))
    semantic_search_min_score = float(semantic_cfg.get("search_min_score", 0.0))
    semantic_workspace_root = str(semantic_cfg.get("workspace_root", "") or "")
    semantic_calls = 0
    semantic_checks = 0
    semantic_errors = 0
    semantic_consecutive_errors = 0
    _SEMANTIC_MAX_CONSECUTIVE_ERRORS = 3
    semantic_disabled_reason = ""
    semantic_best_score = 0.0
    semantic_best_text_score = 0.0
    semantic_best_retrieval_score = 0.0
    semantic_best_hybrid_score = 0.0
    semantic_best_candidates = 0
    dedup_audit_rows: List[Dict[str, Any]] = []
    dedup_audit_max = int(semantic_cfg.get("audit_max_records", 300))

    for rec in new_records:
        key = (rec.source_path, rec.locator)
        next_version = old_versions.get(key, 0) + 1
        rec.version = next_version
        if next_version > 1:
            version_bumped += 1

        if rec.content_hash in seen_hashes:
            duplicate_by_hash += 1
            if len(dedup_audit_rows) < dedup_audit_max:
                dedup_audit_rows.append(
                    {
                        "record_id": rec.record_id,
                        "source_path": rec.source_path,
                        "locator": rec.locator,
                        "bucket": rec.bucket,
                        "action": "skipped_as_hash_duplicate",
                        "reason": "content_hash_hit",
                        "score": 1.0,
                        "score_text": 1.0,
                        "score_retrieval": 1.0,
                        "score_hybrid": 1.0,
                        "threshold_used": 1.0,
                        "text_snippet": rec.text[:180],
                        "matched_snippet": "",
                        "matched_path": "",
                        "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                    }
                )
            continue

        if (
            semantic_enabled
            and len(rec.text or "") >= semantic_min_text_chars
        ):
            threshold_used = resolve_threshold_for_bucket(rec.bucket, semantic_threshold)
            if semantic_calls >= semantic_max_calls:
                semantic_enabled = False
                if not semantic_disabled_reason:
                    semantic_disabled_reason = f"max_calls_reached:{semantic_max_calls}"
            else:
                semantic_calls += 1
                query = normalize_query_text(rec.text)[:220]
                sem_result = search_openclaw_memory(
                    query=query,
                    workspace_root=semantic_workspace_root,
                    limit=semantic_limit,
                    min_score=semantic_search_min_score,
                )
                if not sem_result.get("ok"):
                    semantic_errors += 1
                    semantic_consecutive_errors += 1
                    if semantic_consecutive_errors >= _SEMANTIC_MAX_CONSECUTIVE_ERRORS:
                        semantic_enabled = False
                        semantic_disabled_reason = f"search_failed_consecutively:{semantic_consecutive_errors}:{sem_result.get('error', 'unknown')}"
                else:
                    semantic_consecutive_errors = 0  # 成功则重置连续失败计数
                    semantic_checks += 1
                    check = semantic_duplicate_check(
                        rec.text,
                        sem_result.get("result"),
                        threshold=threshold_used,
                        current_source_path=rec.source_path,
                    )
                    is_dup = bool(check.get("is_duplicate", False))
                    best_score = float(check.get("best_score", 0.0) or 0.0)
                    best_retrieval_score = float(check.get("best_retrieval_score", 0.0) or 0.0)
                    # 纯 score 语义判重：保留旧字段以兼容历史报表读取。
                    best_text_score = best_retrieval_score
                    best_hybrid_score = best_retrieval_score
                    candidate_count = int(check.get("candidate_count", 0) or 0)
                    best_match = str(check.get("best_match", "") or "")
                    best_match_path = str(check.get("best_match_path", "") or "")
                    # LLM 灰区判断：score 在 0.55–0.85 时不够确定，交 LLM 裁决
                    if not is_dup and 0.55 <= best_score <= 0.85:
                        _llm = _get_ingest_llm()
                        if _llm.is_available():
                            best_match_preview = str(check.get("best_match", "") or "")
                            if best_match_preview:
                                _llm_prompt = (
                                    "以下两条记忆是否描述同一件事？\n"
                                    f"A: {best_match_preview[:300]}\n"
                                    f"B: {rec.text[:300]}\n"
                                    "只返回 duplicate 或 distinct，不要解释。"
                                )
                                _llm_result = _llm.complete(_llm_prompt, max_tokens=30, temperature=0.0)
                                if _llm_result:
                                    _answer = _llm_result.strip().lower().split()[0] if _llm_result.strip() else ""
                                    _answer = _answer.strip(".,;:!?()[]{}'\"\u3002\uff1f\uff01")
                                    if _answer == "duplicate":
                                        is_dup = True

                    if best_score > semantic_best_score:
                        semantic_best_score = best_score
                        semantic_best_text_score = best_text_score
                        semantic_best_retrieval_score = best_retrieval_score
                        semantic_best_hybrid_score = best_hybrid_score
                        semantic_best_candidates = candidate_count
                    if is_dup:
                        duplicate_by_semantic += 1
                        if len(dedup_audit_rows) < dedup_audit_max:
                            dedup_audit_rows.append(
                                {
                                    "record_id": rec.record_id,
                                    "source_path": rec.source_path,
                                    "locator": rec.locator,
                                    "bucket": rec.bucket,
                                    "action": "skipped_as_duplicate",
                                    "reason": "retrieval_assisted_match",
                                    "score": round(best_score, 4),
                                    "score_text": round(best_text_score, 4),
                                    "score_retrieval": round(best_retrieval_score, 4),
                                    "score_hybrid": round(best_hybrid_score, 4),
                                    "threshold_used": round(threshold_used, 4),
                                    "candidate_count": candidate_count,
                                    "text_snippet": rec.text[:180],
                                    "matched_snippet": best_match[:180],
                                    "matched_path": best_match_path,
                                    "created_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                                }
                            )
                        continue

        seen_hashes.add(rec.content_hash)
        inserted_rows.append(rec.to_json())

    merged = retained_rows + inserted_rows
    stats = {
        "old_records_removed": len(old_rows),
        "new_records_inserted": len(inserted_rows),
        "duplicate_by_hash": duplicate_by_hash,
        "duplicate_by_semantic": duplicate_by_semantic,
        "version_bumped_records": version_bumped,
        "retrieval_dedup_mode": semantic_mode,
        "retrieval_dedup_enabled": semantic_mode != "off",
        "retrieval_search_calls": semantic_calls,
        "retrieval_checks": semantic_checks,
        "retrieval_errors": semantic_errors,
        "retrieval_disabled_reason": semantic_disabled_reason,
        "retrieval_threshold_default": semantic_threshold,
        "retrieval_bucket_thresholds": DEFAULT_BUCKET_THRESHOLDS,
        "retrieval_limit": semantic_limit,
        "retrieval_search_min_score": semantic_search_min_score,
        "retrieval_max_calls": semantic_max_calls,
        "retrieval_min_text_chars": semantic_min_text_chars,
        "retrieval_best_score": round(semantic_best_score, 4),
        "retrieval_best_text_score": round(semantic_best_text_score, 4),
        "retrieval_best_retrieval_score": round(semantic_best_retrieval_score, 4),
        "retrieval_best_hybrid_score": round(semantic_best_hybrid_score, 4),
        "retrieval_best_candidates": semantic_best_candidates,
        "semantic_dedup_mode": semantic_mode,
        "semantic_dedup_enabled": semantic_mode != "off",
        "semantic_search_calls": semantic_calls,
        "semantic_checks": semantic_checks,
        "semantic_errors": semantic_errors,
        "semantic_disabled_reason": semantic_disabled_reason,
        "semantic_threshold": semantic_threshold,
        "semantic_limit": semantic_limit,
        "semantic_search_min_score": semantic_search_min_score,
        "semantic_max_calls": semantic_max_calls,
        "semantic_min_text_chars": semantic_min_text_chars,
        "semantic_best_score": round(semantic_best_score, 4),
        "semantic_best_text_score": round(semantic_best_text_score, 4),
        "semantic_best_retrieval_score": round(semantic_best_retrieval_score, 4),
        "semantic_best_hybrid_score": round(semantic_best_hybrid_score, 4),
        "semantic_best_candidates": semantic_best_candidates,
        "dedup_audit_count": len(dedup_audit_rows),
        "dedup_audit_max_records": dedup_audit_max,
    }
    return merged, stats, dedup_audit_rows


def summarize_l2_rows(rows: Sequence[Dict[str, object]]) -> Dict[str, object]:
    by_bucket = Counter()
    by_source_kind = Counter()
    by_memory_function = Counter()
    by_trust_tier = Counter()
    by_memory_policy = Counter()
    top_entity_counter = Counter()
    top_tag_counter = Counter()
    for row in rows:
        by_bucket[str(row.get("bucket", "unknown"))] += 1
        by_source_kind[str(row.get("source_kind", "unknown"))] += 1
        by_memory_function[str(row.get("memory_function", "unknown"))] += 1
        by_trust_tier[str(row.get("trust_tier", "unknown"))] += 1
        by_memory_policy[str(row.get("memory_policy", "persist"))] += 1
        for ent in row.get("entities", []) or []:
            top_entity_counter[str(ent)] += 1
        for tag in row.get("tags", []) or []:
            top_tag_counter[str(tag)] += 1
    return {
        "record_count": len(rows),
        "by_bucket": dict(by_bucket),
        "by_source_kind": dict(by_source_kind),
        "by_memory_function": dict(by_memory_function),
        "by_trust_tier": dict(by_trust_tier),
        "by_memory_policy": dict(by_memory_policy),
        "top_entities": [k for k, _ in top_entity_counter.most_common(20)],
        "top_tags": [k for k, _ in top_tag_counter.most_common(20)],
    }


def main() -> int:
    args = parse_args()
    args.semantic_threshold = max(0.0, min(1.0, args.semantic_threshold))
    args.semantic_search_min_score = max(0.0, min(1.0, args.semantic_search_min_score))
    args.semantic_limit = max(1, args.semantic_limit)
    args.semantic_max_calls = max(0, args.semantic_max_calls)
    args.semantic_min_text_chars = max(0, args.semantic_min_text_chars)
    args.dedup_audit_max_records = max(0, args.dedup_audit_max_records)
    auto_discovery = {}
    input_paths = list(args.input_path)

    if args.workspace_root:
        discovered = discover_openclaw_paths(Path(args.workspace_root))
        auto_discovery = {
            "workspace_root": str(discovered["workspace_root"]) if discovered["workspace_root"] else None,
            "workspace_base": str(discovered["workspace_base"]) if discovered["workspace_base"] else None,
            "memory_path": str(discovered["memory_path"]) if discovered["memory_path"] else None,
            "knowledge_path": str(discovered["knowledge_path"]) if discovered["knowledge_path"] else None,
        }
        if not input_paths:
            if discovered["memory_path"] is not None:
                input_paths.append(str(discovered["memory_path"]))
            if discovered["knowledge_path"] is not None:
                input_paths.append(str(discovered["knowledge_path"]))
        if not args.target_root and discovered["default_target_root"] is not None:
            args.target_root = str(discovered["default_target_root"])

    if not input_paths:
        raise SystemExit("未找到可扫描路径。请传 --input-path，或传 --workspace-root 自动识别。")
    if not args.target_root:
        raise SystemExit("缺少 --target-root。可传 --workspace-root 以自动推断默认目标目录。")

    target_root = Path(args.target_root).expanduser().resolve()

    paths = ensure_arch_layout(target_root)
    file_state = load_file_state(paths["file_state"])

    max_file_size_bytes = args.max_file_size_mb * 1024 * 1024
    all_files, missing = collect_candidate_files(
        input_paths=input_paths,
        input_type=args.input_type,
        max_file_size_bytes=max_file_size_bytes,
        recursive=args.recursive,
        include_hidden=args.include_hidden,
    )

    changed_items: List[Tuple[str, Path]] = []
    unchanged_count = 0
    for source_kind, file_path in all_files:
        if should_reingest(file_path, file_state):
            changed_items.append((source_kind, file_path))
        else:
            unchanged_count += 1

    result = collect_and_normalize(
        source_items=changed_items,
        max_file_size_bytes=max_file_size_bytes,
        max_records_per_file=args.max_records_per_file,
        recursive=False,
        include_hidden=True,
        formation_mode="ingest",
    )

    normalized_records = result["normalized_records"]
    rejected_records = result.get("rejected_records", [])
    policy_skipped_records = result.get("policy_skipped_records", [])
    existing_l2_rows = read_jsonl(paths["l2"])
    changed_files = [p for _, p in changed_items]
    semantic_workspace_root = str(auto_discovery.get("workspace_root") or args.workspace_root or "")
    merged_l2_rows, update_stats, dedup_audit_rows = merge_records_with_update(
        existing_l2_rows=existing_l2_rows,
        new_records=normalized_records,
        changed_files=changed_files,
        semantic_options={
            "mode": args.semantic_dedup,
            "threshold": args.semantic_threshold,
            "limit": args.semantic_limit,
            "search_min_score": args.semantic_search_min_score,
            "max_calls": args.semantic_max_calls,
            "min_text_chars": args.semantic_min_text_chars,
            "audit_max_records": args.dedup_audit_max_records,
            "workspace_root": semantic_workspace_root,
        },
    )

    report: Dict[str, object] = {
        "run_type": "incremental_ingest",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "llm_backend": get_llm_backend_name(),
        "llm_status": get_llm_status(),
        "apply": args.apply,
        "target_root": str(target_root),
        "input_type": args.input_type,
        "auto_discovery": auto_discovery,
        "missing_paths": missing,
        "scan": {
            "total_candidate_files": len(all_files),
            "changed_files": len(changed_items),
            "unchanged_files": unchanged_count,
            "warnings": result["warnings"],
            "skipped_files": len(result["skipped_files"]),
            "policy_skipped_records": len(policy_skipped_records),
            "rejected_records": len(rejected_records),
        },
        "changed_file_paths": [str(p) for p in changed_files],
        "raw_record_count": len(result["raw_records"]),
        "normalized_from_changed_files": summarize_l2_rows([r.to_json() for r in normalized_records]),
        "existing_l2_before_update": summarize_l2_rows(existing_l2_rows),
        "merged_l2_after_update": summarize_l2_rows(merged_l2_rows),
        "update_stats": update_stats,
        "retrieval_dedup": {
            "mode": args.semantic_dedup,
            "threshold_default": args.semantic_threshold,
            "bucket_thresholds": DEFAULT_BUCKET_THRESHOLDS,
            "limit": args.semantic_limit,
            "search_min_score": args.semantic_search_min_score,
            "max_calls": args.semantic_max_calls,
            "min_text_chars": args.semantic_min_text_chars,
            "workspace_root": semantic_workspace_root,
        },
        "semantic_dedup": {
            "mode": args.semantic_dedup,
            "threshold": args.semantic_threshold,
            "limit": args.semantic_limit,
            "search_min_score": args.semantic_search_min_score,
            "max_calls": args.semantic_max_calls,
            "min_text_chars": args.semantic_min_text_chars,
            "workspace_root": semantic_workspace_root,
        },
        "bucket_files": BUCKET_FILES,
    }

    if args.apply:
        write_stats = rebuild_materialized_views_from_l2(paths, merged_l2_rows)

        for _, file_path in changed_items:
            update_file_state(file_path, file_state)
        save_file_state(paths["file_state"], file_state)

        report["write_stats"] = {
            **write_stats,
            "changed_files_state_updated": len(changed_items),
        }

        if rejected_records:
            rejected_path = paths["reports_dir"] / f"rejected_ingest_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.jsonl"
            write_jsonl(rejected_path, rejected_records, append=False)
            report["rejected_report_path"] = str(rejected_path)
        if policy_skipped_records:
            policy_path = paths["reports_dir"] / f"policy_skipped_ingest_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.jsonl"
            write_jsonl(policy_path, policy_skipped_records, append=False)
            report["policy_skipped_report_path"] = str(policy_path)

        if dedup_audit_rows:
            audit_path = paths["reports_dir"] / f"dedup_audit_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.jsonl"
            write_jsonl(audit_path, dedup_audit_rows, append=False)
            report["dedup_audit_report_path"] = str(audit_path)
            report["dedup_audit_count"] = len(dedup_audit_rows)
        else:
            report["dedup_audit_count"] = 0

        report_path = paths["reports_dir"] / f"ingest_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
        write_json(report_path, report)
        report["report_path"] = str(report_path)
    else:
        if dedup_audit_rows:
            report["dedup_audit_sample"] = dedup_audit_rows[:8]
            report["dedup_audit_count"] = len(dedup_audit_rows)
        else:
            report["dedup_audit_count"] = 0

    if args.report_out:
        out = Path(args.report_out).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        write_json(out, report)
        report["report_out"] = str(out)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Memory/Knowledge 自进化脚本：probe -> verify -> repair。

该脚本面向 adaptr-v1 架构，聚焦四件事：
1. 结构完整性检查（L0/L1/L2、bucket 一致性）
2. 质量检查（重复、图谱缺边、state 覆盖）
3. TTL/热度检查（过期降级与归档候选）
4. 安全修复（以 L2 为准重建物化视图）
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from mk_arch_core import (
    BUCKET_FILES,
    discover_openclaw_paths,
    ensure_arch_layout,
    extract_entities,
    extract_relations,
    load_record_hashes,
    normalize_text,
    normalize_l2_row_defaults,
    parse_iso_datetime,
    read_jsonl,
    rebuild_materialized_views_from_l2,
    sha256_text,
    utc_now,
    write_json,
    write_jsonl,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="对 adaptr-v1 memory/knowledge 存储执行 probe-verify-repair"
    )
    parser.add_argument(
        "--workspace-root",
        default="",
        help="可选：OpenClaw workspace 根目录（用于自动推断 target-root）",
    )
    parser.add_argument(
        "--target-root",
        default="",
        help="adaptr-v1 根目录（默认自动推断）",
    )
    parser.add_argument(
        "--repair",
        action="store_true",
        help="执行安全修复（默认仅检查）",
    )
    parser.add_argument(
        "--report-out",
        default="",
        help="可选：输出 JSON 报告文件路径",
    )
    return parser.parse_args()


def resolve_target_root(workspace_root: str, target_root: str) -> Tuple[Path, Dict[str, Optional[str]]]:
    if target_root:
        resolved = Path(target_root).expanduser().resolve()
        auto = {
            "workspace_root": None,
            "workspace_base": None,
            "memory_path": None,
            "knowledge_path": None,
            "target_root": str(resolved),
        }
        return resolved, auto

    if not workspace_root:
        raise SystemExit("缺少 --target-root。可传 --workspace-root 自动推断。")

    discovered = discover_openclaw_paths(Path(workspace_root))
    default_target = discovered["default_target_root"]
    if default_target is None:
        raise SystemExit(f"未发现可用 memory/knowledge 目录：{workspace_root}")

    auto = {
        "workspace_root": str(discovered["workspace_root"]) if discovered["workspace_root"] else None,
        "workspace_base": str(discovered["workspace_base"]) if discovered["workspace_base"] else None,
        "memory_path": str(discovered["memory_path"]) if discovered["memory_path"] else None,
        "knowledge_path": str(discovered["knowledge_path"]) if discovered["knowledge_path"] else None,
        "target_root": str(default_target),
    }
    return default_target, auto


def load_l0(path: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not path.exists():
        return None, "missing"
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, str(exc)


def load_json_obj(path: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    if not path.exists():
        return None, "missing"
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(obj, dict):
            return None, "not_json_object"
        return obj, None
    except (OSError, json.JSONDecodeError) as exc:
        return None, str(exc)


def dedupe_rows(rows: Sequence[Dict[str, Any]], key_fields: Sequence[str]) -> Tuple[List[Dict[str, Any]], int]:
    seen: Set[str] = set()
    output: List[Dict[str, Any]] = []
    dup_count = 0

    for row in rows:
        key = ""
        for field in key_fields:
            value = row.get(field)
            if value:
                key = f"{field}:{value}"
                break
        if not key:
            key = "raw:" + sha256_text(json.dumps(row, ensure_ascii=False, sort_keys=True))
        if key in seen:
            dup_count += 1
            continue
        seen.add(key)
        output.append(row)
    return output, dup_count


def parse_ttl_days(ttl: Any) -> Optional[int]:
    if ttl is None:
        return None
    text = str(ttl).strip().lower()
    if not text or text == "permanent":
        return None

    match = re.fullmatch(r"(\d+)\s*([dwh])", text)
    if match:
        value = int(match.group(1))
        unit = match.group(2)
        if unit == "d":
            return value
        if unit == "w":
            return value * 7
        if unit == "h":
            return max(1, value // 24)
    if text.isdigit():
        return int(text)
    return None


def evaluate_ttl_rows(rows: Sequence[Dict[str, Any]], now_dt: datetime) -> Dict[str, Any]:
    checked = 0
    expired = 0
    archive_candidates = 0
    needs_downgrade = 0
    samples: List[Dict[str, Any]] = []

    for row in rows:
        ttl_days = parse_ttl_days(row.get("ttl"))
        if ttl_days is None:
            continue
        base_time = parse_iso_datetime(row.get("last_accessed") or row.get("created_at"))
        if base_time is None:
            continue
        checked += 1
        age_days = max(0, int((now_dt - base_time).total_seconds() // 86400))
        is_expired = age_days > ttl_days
        is_archive_candidate = age_days > ttl_days * 2
        heat = str(row.get("heat", "warm")).lower()
        if is_expired:
            expired += 1
            if heat != "cold":
                needs_downgrade += 1
        if is_archive_candidate:
            archive_candidates += 1
        if (is_expired or is_archive_candidate) and len(samples) < 10:
            samples.append(
                {
                    "id": row.get("id"),
                    "bucket": row.get("bucket"),
                    "ttl": row.get("ttl"),
                    "heat": heat,
                    "age_days": age_days,
                    "last_accessed": row.get("last_accessed"),
                }
            )

    return {
        "checked": checked,
        "expired": expired,
        "archive_candidates": archive_candidates,
        "needs_downgrade": needs_downgrade,
        "samples": samples,
    }


def relation_semantic_stats(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    total = 0
    weak = 0
    samples: List[Dict[str, Any]] = []

    for row in rows:
        for rel in row.get("relations") or []:
            if not isinstance(rel, dict):
                continue
            pred = str(rel.get("predicate", "")).strip().lower()
            if not pred:
                continue
            total += 1
            if pred in {"cooccurs_with", "related_to"}:
                weak += 1
                if len(samples) < 10:
                    samples.append(
                        {
                            "record_id": row.get("id"),
                            "bucket": row.get("bucket"),
                            "subject": rel.get("subject"),
                            "predicate": pred,
                            "object": rel.get("object"),
                        }
                    )

    ratio = (weak / total) if total else 0.0
    return {
        "total_relations": total,
        "weak_relations": weak,
        "weak_ratio": round(ratio, 4),
        "samples": samples,
    }


def relation_decision_stats(
    relation_rows: Sequence[Dict[str, Any]],
    decision_rows: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    group_all: Dict[str, int] = {}
    primary_count: Dict[str, int] = {}
    weakly_resolved = 0
    missing_group_id = 0

    for row in relation_rows:
        gid = str(row.get("relation_group_id", "")).strip()
        if not gid:
            missing_group_id += 1
            continue
        group_all[gid] = group_all.get(gid, 0) + 1
        if bool(row.get("is_primary")):
            primary_count[gid] = primary_count.get(gid, 0) + 1

    decision_ids = {
        str(d.get("id", "")).strip()
        for d in decision_rows
        if str(d.get("id", "")).strip()
    }
    weakly_resolved = sum(1 for d in decision_rows if str(d.get("status", "")) == "weakly_resolved")

    groups_from_rel = set(group_all.keys())
    uncovered = sorted(groups_from_rel - decision_ids)
    invalid_primary = sorted(g for g in groups_from_rel if primary_count.get(g, 0) != 1)

    return {
        "groups_from_relations": len(groups_from_rel),
        "groups_in_decisions": len(decision_ids),
        "missing_group_id_rows": missing_group_id,
        "uncovered_groups": uncovered[:20],
        "invalid_primary_groups": invalid_primary[:20],
        "weakly_resolved_groups": weakly_resolved,
    }


def enforce_ttl_policy(rows: Sequence[Dict[str, Any]], now_dt: datetime) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, int]]:
    kept_rows: List[Dict[str, Any]] = []
    archived_rows: List[Dict[str, Any]] = []
    heat_degraded = 0
    expired_records = 0

    for row in rows:
        ttl_days = parse_ttl_days(row.get("ttl"))
        if ttl_days is None:
            kept_rows.append(row)
            continue

        base_time = parse_iso_datetime(row.get("last_accessed") or row.get("created_at"))
        if base_time is None:
            kept_rows.append(row)
            continue

        age_days = max(0, int((now_dt - base_time).total_seconds() // 86400))
        if age_days <= ttl_days:
            kept_rows.append(row)
            continue

        expired_records += 1
        if age_days > ttl_days * 2:
            archived = dict(row)
            archived["archived_at"] = utc_now()
            archived["archive_reason"] = f"ttl_expired_{ttl_days}d"
            archived["age_days"] = age_days
            archived_rows.append(archived)
            continue

        updated = dict(row)
        before = str(updated.get("heat", "warm")).lower()
        after = "cold"
        updated["heat"] = after
        if after != before:
            heat_degraded += 1
        kept_rows.append(updated)

    return kept_rows, archived_rows, {
        "expired_records": expired_records,
        "archived_records": len(archived_rows),
        "heat_degraded": heat_degraded,
    }


def normalize_relation_dicts(rows: Sequence[Dict[str, Any]]) -> List[Tuple[str, str, str]]:
    tuples: Set[Tuple[str, str, str]] = set()
    for rel in rows:
        if not isinstance(rel, dict):
            continue
        sub = str(rel.get("subject", "")).strip()
        pred = str(rel.get("predicate", "")).strip().lower()
        obj = str(rel.get("object", "")).strip()
        if not sub or not pred or not obj:
            continue
        tuples.add((sub, pred, obj))
    return sorted(tuples)


def recompute_graph_fields(rows: Sequence[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    updated_rows: List[Dict[str, Any]] = []
    changed = 0
    relations_before = 0
    relations_after = 0
    entities_before = 0
    entities_after = 0

    for row in rows:
        updated = dict(row)
        text = normalize_text(str(updated.get("text", "")))
        bucket = str(updated.get("bucket", ""))
        old_entities = [str(e) for e in (updated.get("entities") or []) if str(e).strip()]
        old_relations = normalize_relation_dicts(updated.get("relations") or [])
        entities_before += len(old_entities)
        relations_before += len(old_relations)

        if text:
            new_entities = extract_entities(text)
            new_relations_tuples = extract_relations(text, new_entities, bucket=bucket)
            new_relations_dicts = [
                {"subject": s, "predicate": p, "object": o} for s, p, o in new_relations_tuples
            ]
            if old_entities != new_entities or old_relations != normalize_relation_dicts(new_relations_dicts):
                changed += 1
            updated["entities"] = new_entities
            updated["relations"] = new_relations_dicts

        entities_after += len(updated.get("entities") or [])
        relations_after += len(updated.get("relations") or [])
        updated_rows.append(updated)

    return updated_rows, {
        "rows_updated": changed,
        "entities_before": entities_before,
        "entities_after": entities_after,
        "relations_before": relations_before,
        "relations_after": relations_after,
    }


def evaluate(
    paths: Dict[str, Path],
    auto_info: Dict[str, Optional[str]],
) -> Dict[str, Any]:
    l2_rows = [normalize_l2_row_defaults(row) for row in read_jsonl(paths["l2"])]
    l1_rows = read_jsonl(paths["l1"])
    l0_obj, l0_err = load_l0(paths["l0"])
    profile_snapshot_obj, profile_snapshot_err = load_json_obj(paths["profile_snapshot"])
    preferences_snapshot_obj, preferences_snapshot_err = load_json_obj(paths["preferences_snapshot"])
    retrieval_protocol_obj, retrieval_protocol_err = load_json_obj(paths["retrieval_protocol"])
    entity_rows = read_jsonl(paths["entity"])
    relation_rows = read_jsonl(paths["relation"])
    relation_decisions = read_jsonl(paths["relation_decisions"])
    hash_state = load_record_hashes(paths["hash_state"])

    bucket_expected = Counter()
    bucket_actual: Dict[str, int] = {}
    for row in l2_rows:
        bucket = row.get("bucket")
        if bucket:
            bucket_expected[str(bucket)] += 1
    for bucket in BUCKET_FILES:
        bucket_actual[bucket] = len(read_jsonl(paths[bucket]))

    probes: List[Dict[str, Any]] = []

    # Probe 1: L0 可用性
    if not l2_rows:
        probes.append(
            {
                "id": "l0_with_no_data",
                "type": "completeness",
                "severity": "low",
                "question": "当 L2 为空时，是否允许 L0 为空？",
                "status": "pass",
                "evidence": "当前无 L2 数据，跳过严格检查",
                "repair": "NONE",
            }
        )
    else:
        l0_ok = l0_obj is not None and l0_obj.get("total_records") == len(l2_rows)
        probes.append(
            {
                "id": "l0_consistency",
                "type": "completeness",
                "severity": "high",
                "question": "L0 是否存在且 total_records 与 L2 一致？",
                "status": "pass" if l0_ok else "fail",
                "evidence": (
                    f"l0_total={l0_obj.get('total_records') if l0_obj else None}, l2_total={len(l2_rows)}"
                    if l0_obj is not None
                    else f"l0_error={l0_err}"
                ),
                "repair": "REBUILD_LAYERS",
            }
        )

    # Probe 1.1: snapshots / retrieval protocol 是否存在
    derived_views_ok = (
        profile_snapshot_obj is not None
        and preferences_snapshot_obj is not None
        and retrieval_protocol_obj is not None
    )
    probes.append(
        {
            "id": "derived_views_present",
            "type": "completeness",
            "severity": "medium",
            "question": "profile/preference 快照与 retrieval protocol 是否已生成？",
            "status": "pass" if derived_views_ok else "fail",
            "evidence": (
                f"profile_snapshot={'ok' if profile_snapshot_obj is not None else profile_snapshot_err}; "
                f"preferences_snapshot={'ok' if preferences_snapshot_obj is not None else preferences_snapshot_err}; "
                f"retrieval_protocol={'ok' if retrieval_protocol_obj is not None else retrieval_protocol_err}"
            ),
            "repair": "REBUILD_LAYERS",
        }
    )

    # Probe 2: L1 是否覆盖 bucket
    expected_bucket_count = len([k for k, v in bucket_expected.items() if v > 0])
    l1_ok = expected_bucket_count == 0 or len(l1_rows) >= expected_bucket_count
    probes.append(
        {
            "id": "l1_coverage",
            "type": "completeness",
            "severity": "medium",
            "question": "L1 是否覆盖所有非空 bucket？",
            "status": "pass" if l1_ok else "fail",
            "evidence": f"expected_nonempty_buckets={expected_bucket_count}, l1_rows={len(l1_rows)}",
            "repair": "REBUILD_LAYERS",
        }
    )

    # Probe 3: bucket 与 L2 一致性
    mismatch: List[str] = []
    for bucket in BUCKET_FILES:
        expected = bucket_expected.get(bucket, 0)
        actual = bucket_actual.get(bucket, 0)
        if expected != actual:
            mismatch.append(f"{bucket}: expected={expected}, actual={actual}")
    probes.append(
        {
            "id": "bucket_consistency",
            "type": "cross_memory",
            "severity": "medium",
            "question": "bucket 文件计数是否与 L2 一致？",
            "status": "pass" if not mismatch else "fail",
            "evidence": "; ".join(mismatch) if mismatch else "all bucket counts matched",
            "repair": "REWRITE_BUCKETS_FROM_L2",
        }
    )

    # Probe 4: L2 去重状态
    _, l2_dup_count = dedupe_rows(l2_rows, key_fields=("content_hash", "id"))
    probes.append(
        {
            "id": "l2_dedup",
            "type": "redundancy",
            "severity": "medium",
            "question": "L2 是否存在重复记录？",
            "status": "pass" if l2_dup_count == 0 else "fail",
            "evidence": f"duplicate_rows={l2_dup_count}",
            "repair": "DEDUP_L2_AND_BUCKETS",
        }
    )

    # Probe 4.1: memory_policy 是否被执行
    private_rows = [row for row in l2_rows if str(row.get("memory_policy", "persist")).lower() == "private"]
    policy_ok = not private_rows
    probes.append(
        {
            "id": "memory_policy_enforcement",
            "type": "safety",
            "severity": "medium",
            "question": "private 记录是否已被排除出长期层？",
            "status": "pass" if policy_ok else "fail",
            "evidence": f"private_rows={len(private_rows)}",
            "repair": "REAPPLY_POLICY_FILTERS",
            "sample": [
                {
                    "id": row.get("id"),
                    "bucket": row.get("bucket"),
                    "source_path": row.get("source_path"),
                }
                for row in private_rows[:10]
            ],
        }
    )

    # Probe 5: 图谱连边
    graph_ok = not entity_rows or bool(relation_rows)
    probes.append(
        {
            "id": "graph_edges",
            "type": "cross_memory",
            "severity": "low",
            "question": "存在实体时，是否存在关系边？",
            "status": "pass" if graph_ok else "fail",
            "evidence": f"entities={len(entity_rows)}, relations={len(relation_rows)}",
            "repair": "SYNTH_RELATIONS",
        }
    )

    # Probe 5.1: 图谱语义质量（低价值关系占比）
    graph_semantics = relation_semantic_stats(l2_rows)
    weak_ratio = float(graph_semantics["weak_ratio"])
    enough_data = int(graph_semantics["total_relations"]) >= 6
    semantic_ok = (not enough_data) or weak_ratio <= 0.72
    probes.append(
        {
            "id": "graph_semantic_quality",
            "type": "cross_memory",
            "severity": "medium",
            "question": "关系图里低价值关系（cooccurs/related）占比是否可控？",
            "status": "pass" if semantic_ok else "fail",
            "evidence": (
                f"total_relations={graph_semantics['total_relations']}, "
                f"weak_relations={graph_semantics['weak_relations']}, "
                f"weak_ratio={graph_semantics['weak_ratio']}"
            ),
            "repair": "RECOMPUTE_GRAPH_FIELDS",
            "sample": graph_semantics["samples"],
        }
    )

    # Probe 5.2: 关系冲突裁决一致性
    decision_stats = relation_decision_stats(relation_rows, relation_decisions)
    decision_ok = (
        (not relation_rows)
        or (
            decision_stats["missing_group_id_rows"] == 0
            and not decision_stats["uncovered_groups"]
            and not decision_stats["invalid_primary_groups"]
        )
    )
    probes.append(
        {
            "id": "relation_decision_consistency",
            "type": "cross_memory",
            "severity": "medium",
            "question": "关系冲突裁决是否完整（每组唯一主值、决策可追溯）？",
            "status": "pass" if decision_ok else "fail",
            "evidence": (
                f"groups_from_relations={decision_stats['groups_from_relations']}, "
                f"groups_in_decisions={decision_stats['groups_in_decisions']}, "
                f"missing_group_id_rows={decision_stats['missing_group_id_rows']}, "
                f"weakly_resolved_groups={decision_stats['weakly_resolved_groups']}"
            ),
            "repair": "REBUILD_RELATION_DECISIONS",
            "sample": {
                "uncovered_groups": decision_stats["uncovered_groups"],
                "invalid_primary_groups": decision_stats["invalid_primary_groups"],
            },
        }
    )

    # Probe 6: hash state 覆盖
    l2_hashes = {str(row.get("content_hash")) for row in l2_rows if row.get("content_hash")}
    hash_ok = not l2_hashes or l2_hashes.issubset(hash_state)
    probes.append(
        {
            "id": "hash_state_coverage",
            "type": "freshness",
            "severity": "medium",
            "question": "state/record_hashes.txt 是否覆盖当前 L2 content_hash？",
            "status": "pass" if hash_ok else "fail",
            "evidence": f"l2_hashes={len(l2_hashes)}, state_hashes={len(hash_state)}",
            "repair": "REBUILD_HASH_STATE",
        }
    )

    # Probe 7: memory + knowledge 覆盖（若 workspace 中两类源都存在）
    src_counter = Counter(str(row.get("source_kind")) for row in l2_rows)
    memory_path = auto_info.get("memory_path")
    knowledge_path = auto_info.get("knowledge_path")
    both_expected = bool(memory_path and knowledge_path)
    both_present = src_counter.get("memory", 0) > 0 and src_counter.get("knowledge", 0) > 0
    coverage_ok = (not both_expected) or both_present
    probes.append(
        {
            "id": "memory_knowledge_coverage",
            "type": "completeness",
            "severity": "low",
            "question": "当 memory+knowledge 同时存在时，L2 是否都已覆盖？",
            "status": "pass" if coverage_ok else "fail",
            "evidence": f"source_kind_counts={dict(src_counter)}",
            "repair": "MANUAL_RECHECK_SOURCE_SCAN",
        }
    )

    # Probe 8: TTL + 热度卫生
    ttl_eval = evaluate_ttl_rows(l2_rows, now_dt=datetime.now(timezone.utc))
    ttl_ok = ttl_eval["archive_candidates"] == 0 and ttl_eval["needs_downgrade"] == 0
    probes.append(
        {
            "id": "ttl_heat_hygiene",
            "type": "freshness",
            "severity": "medium",
            "question": "TTL 是否健康（过期记录是否得到降级或归档）？",
            "status": "pass" if ttl_ok else "fail",
            "evidence": (
                f"checked={ttl_eval['checked']}, expired={ttl_eval['expired']}, "
                f"needs_downgrade={ttl_eval['needs_downgrade']}, archive_candidates={ttl_eval['archive_candidates']}"
            ),
            "repair": "TTL_HEAT_HYGIENE",
            "sample": ttl_eval["samples"],
        }
    )

    return {
        "l2_rows": l2_rows,
        "probes": probes,
    }


def apply_repairs(
    paths: Dict[str, Path],
    eval_result: Dict[str, Any],
) -> List[Dict[str, Any]]:
    actions: List[Dict[str, Any]] = []
    probes = eval_result["probes"]
    failed_repairs = {p["repair"] for p in probes if p["status"] == "fail" and p["repair"] != "NONE"}

    l2_rows = [normalize_l2_row_defaults(row) for row in eval_result["l2_rows"]]
    l2_changed = False

    if "DEDUP_L2_AND_BUCKETS" in failed_repairs:
        deduped_l2, dropped = dedupe_rows(l2_rows, key_fields=("content_hash", "id"))
        if dropped > 0:
            l2_rows = deduped_l2
            l2_changed = True
            actions.append(
                {
                    "action": "dedup_l2",
                    "dropped_rows": dropped,
                }
            )

    if "TTL_HEAT_HYGIENE" in failed_repairs:
        l2_after_ttl, archived_rows, ttl_stats = enforce_ttl_policy(
            l2_rows,
            now_dt=datetime.now(timezone.utc),
        )
        l2_rows = l2_after_ttl
        if ttl_stats["heat_degraded"] > 0 or ttl_stats["archived_records"] > 0:
            l2_changed = True

        actions.append(
            {
                "action": "ttl_heat_repair",
                **ttl_stats,
            }
        )

        if archived_rows:
            archive_existing = read_jsonl(paths["archive"])
            archive_merged, _ = dedupe_rows(
                archive_existing + archived_rows,
                key_fields=("id", "content_hash"),
            )
            write_jsonl(paths["archive"], archive_merged, append=False)
            actions.append(
                {
                    "action": "archive_expired_records",
                    "archived_now": len(archived_rows),
                    "archive_total": len(archive_merged),
                }
            )

    if "RECOMPUTE_GRAPH_FIELDS" in failed_repairs:
        l2_after_graph, graph_stats = recompute_graph_fields(l2_rows)
        l2_rows = l2_after_graph
        if graph_stats["rows_updated"] > 0:
            l2_changed = True
        actions.append(
            {
                "action": "recompute_graph_fields",
                **graph_stats,
            }
        )

    if "REAPPLY_POLICY_FILTERS" in failed_repairs:
        actions.append(
            {
                "action": "reapply_policy_filters",
                "status": "scheduled_via_rebuild_materialized_views",
            }
        )

    if "REBUILD_RELATION_DECISIONS" in failed_repairs:
        actions.append(
            {
                "action": "rebuild_relation_decisions",
                "status": "scheduled_via_rebuild_materialized_views",
            }
        )

    if "MANUAL_RECHECK_SOURCE_SCAN" in failed_repairs:
        actions.append(
            {
                "action": "manual_action_required",
                "reason": "memory_knowledge_coverage_failed",
                "message": "检测到 memory/knowledge 覆盖不足。请手动重跑 bootstrap_restructure.py --apply（或 auto_migrate.py --mode bootstrap）。",
            }
        )

    rebuild_triggers = {
        "REBUILD_LAYERS",
        "REWRITE_BUCKETS_FROM_L2",
        "DEDUP_L2_AND_BUCKETS",
        "SYNTH_RELATIONS",
        "RECOMPUTE_GRAPH_FIELDS",
        "REBUILD_RELATION_DECISIONS",
        "REAPPLY_POLICY_FILTERS",
        "REBUILD_HASH_STATE",
        "TTL_HEAT_HYGIENE",
    }
    if l2_changed or (failed_repairs & rebuild_triggers):
        stats = rebuild_materialized_views_from_l2(paths, l2_rows)
        actions.append(
            {
                "action": "rebuild_materialized_views_from_l2",
                **stats,
            }
        )

    return actions


def main() -> int:
    args = parse_args()
    target_root, auto_info = resolve_target_root(args.workspace_root, args.target_root)
    paths = ensure_arch_layout(target_root)

    eval_result = evaluate(paths, auto_info=auto_info)
    probes = eval_result["probes"]
    pass_count = sum(1 for p in probes if p["status"] == "pass")
    fail_count = len(probes) - pass_count

    report: Dict[str, Any] = {
        "run_type": "self_evolve",
        "generated_at": utc_now(),
        "target_root": str(target_root),
        "auto_discovery": auto_info,
        "repair": args.repair,
        "probe_count": len(probes),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "health_score": round(100.0 * pass_count / max(1, len(probes)), 2),
        "probes": probes,
        "actions": [],
    }

    if args.repair and fail_count > 0:
        actions = apply_repairs(paths, eval_result=eval_result)
        report["actions"] = actions

        eval_after = evaluate(paths, auto_info=auto_info)
        probes_after = eval_after["probes"]
        pass_after = sum(1 for p in probes_after if p["status"] == "pass")
        fail_after = len(probes_after) - pass_after
        report["after_repair"] = {
            "probe_count": len(probes_after),
            "pass_count": pass_after,
            "fail_count": fail_after,
            "health_score": round(100.0 * pass_after / max(1, len(probes_after)), 2),
            "probes": probes_after,
        }
        if fail_after > 0:
            report["after_repair_note"] = (
                "修复后仍存在失败项。可能是修复过程引入的新探针失败，"
                "或需要跨轮修复。建议再次执行 self_evolve.py --repair 并查看 actions/probes 详情。"
            )

    if args.report_out:
        out = Path(args.report_out).expanduser().resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        write_json(out, report)
        report["report_out"] = str(out)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""OpenClaw 原生 memory search 包装器（零外部 embedding 服务依赖）。

用途：
1. 统一调用 `openclaw memory search`（底层 builtin + hybrid）
2. 兼容不同参数形态（优先 `--max-results`，兼容旧版 `--limit`）
3. 输出稳定 JSON，便于在 skill / agent 里复用
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 确保同目录下的模块可被 import
_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="调用 OpenClaw 原生 memory search（builtin/hybrid）")
    parser.add_argument("--query", required=True, help="检索问题")
    parser.add_argument("--workspace-root", default="", help="可选：OpenClaw workspace 根目录（作为执行 cwd）")
    parser.add_argument("--limit", type=int, default=8, help="检索条数（默认 8）")
    parser.add_argument("--min-score", type=float, default=0.0, help="可选：最小相关性分数（0-1）")
    return parser.parse_args()


def parse_json_payload(text: str) -> Optional[Any]:
    payload = (text or "").strip()
    if not payload:
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        pass

    idx = payload.rfind("{")
    while idx >= 0:
        chunk = payload[idx:]
        try:
            return json.loads(chunk)
        except json.JSONDecodeError:
            pass
        idx = payload.rfind("{", 0, idx)

    idx = payload.rfind("[")
    while idx >= 0:
        chunk = payload[idx:]
        try:
            return json.loads(chunk)
        except json.JSONDecodeError:
            pass
        idx = payload.rfind("[", 0, idx)
    return None


def run_attempt(
    cmd: Sequence[str],
    cwd: Optional[Path],
) -> Tuple[int, str, str]:
    proc = subprocess.run(
        list(cmd),
        text=True,
        capture_output=True,
        cwd=str(cwd) if cwd else None,
    )
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def search_openclaw_memory(
    query: str,
    workspace_root: str = "",
    limit: int = 8,
    min_score: Optional[float] = None,
) -> Dict[str, Any]:
    """调用 OpenClaw 原生 memory search 并返回稳定结构。

    返回字段：
    - ok: bool
    - error: str（仅失败时）
    - backend/mode/workspace_root/command/query/limit/result（成功时）
    """
    openclaw_bin = shutil.which("openclaw")
    if not openclaw_bin:
        return {
            "ok": False,
            "error": "openclaw_not_found",
            "message": "未找到 openclaw 命令，请先确认 OpenClaw 已安装且在 PATH 中。",
        }

    cwd = Path(workspace_root).expanduser().resolve() if workspace_root else None

    min_score_value: Optional[float] = None
    if min_score is not None:
        try:
            min_score_value = max(0.0, min(1.0, float(min_score)))
        except (TypeError, ValueError):
            min_score_value = None

    def build_cmd(use_json: bool, use_max_results: bool, use_limit: bool) -> List[str]:
        cmd = [openclaw_bin, "memory", "search", "--query", query]
        if use_json:
            cmd.append("--json")
        if use_max_results and limit > 0:
            cmd.extend(["--max-results", str(limit)])
        if use_limit and limit > 0:
            cmd.extend(["--limit", str(limit)])
        if min_score_value is not None:
            cmd.extend(["--min-score", f"{min_score_value:.4f}"])
        return cmd

    attempts: List[List[str]] = [
        build_cmd(use_json=True, use_max_results=True, use_limit=False),
        build_cmd(use_json=True, use_max_results=False, use_limit=True),
        build_cmd(use_json=True, use_max_results=False, use_limit=False),
        build_cmd(use_json=False, use_max_results=False, use_limit=False),
    ]

    last_err = ""
    for cmd in attempts:
        rc, out, err = run_attempt(cmd, cwd=cwd)
        if rc != 0:
            last_err = err.strip() or out.strip() or f"exit={rc}"
            continue

        parsed = parse_json_payload(out)
        return {
            "ok": True,
            "backend": "openclaw-native",
            "mode": "hybrid_builtin",
            "workspace_root": str(cwd) if cwd else "",
            "command": cmd,
            "query": query,
            "limit": limit,
            "min_score": min_score_value,
            "result": parsed if parsed is not None else out.strip(),
        }

    return {
        "ok": False,
        "error": "openclaw_memory_search_failed",
        "message": last_err or "unknown error",
        "workspace_root": str(cwd) if cwd else "",
        "query": query,
        "limit": limit,
        "min_score": min_score_value,
    }


def _get_llm() -> Any:
    """懒加载 LLM 后端，用于 rerank_and_summarize。"""
    try:
        from llm_backend import CatclawLLMBackend  # type: ignore
        return CatclawLLMBackend()
    except Exception:
        class _Stub:
            def is_available(self) -> bool:
                return False
            def complete(self, *a: Any, **kw: Any) -> str:
                return ""
        return _Stub()


def rerank_and_summarize(
    query: str,
    results: List[Dict[str, Any]],
    llm: Any,
) -> Dict[str, Any]:
    """对召回结果做后处理：过滤不相关、合并重复、生成摘要。

    返回：
    {
        "summary": "...",         # 上下文摘要（200 字以内）
        "used_indices": [...],    # 被使用的结果索引
        "filtered_indices": [...],# 被过滤的结果索引
        "ok": bool,               # 是否调用了 LLM
    }
    """
    fallback: Dict[str, Any] = {
        "summary": "",
        "used_indices": list(range(len(results))),
        "filtered_indices": [],
        "ok": False,
    }

    if not results:
        return fallback

    try:
        llm_available = llm.is_available()
    except Exception:
        llm_available = False

    if not llm_available:
        return fallback

    formatted_parts: List[str] = []
    for idx, item in enumerate(results[:20]):
        text = ""
        for key in ("snippet", "content", "text", "body", "summary", "memory", "passage"):
            val = item.get(key)
            if isinstance(val, str) and val.strip():
                text = val.strip()[:300]
                break
        if not text:
            for val in item.values():
                if isinstance(val, str) and val.strip():
                    text = val.strip()[:300]
                    break
        score = item.get("score", item.get("similarity", item.get("relevance", "")))
        score_str = f" [score={score:.3f}]" if isinstance(score, float) else ""
        formatted_parts.append(f"[{idx}]{score_str} {text}")

    formatted_results = "\n".join(formatted_parts)

    prompt = (
        f"问题：{query}\n"
        f"以下是从记忆库检索到的片段（按相关性排序）：\n"
        f"{formatted_results}\n\n"
        f"请：\n"
        f"1. 过滤掉与问题无关的片段\n"
        f"2. 合并语义重复的内容\n"
        f"3. 生成一段 200 字以内的上下文摘要\n\n"
        f"返回 JSON：\n"
        f'{{ "summary": "...", "used_indices": [0, 2, 3], "filtered_indices": [1, 4] }}'
    )

    try:
        llm_result = llm.complete(prompt, max_tokens=400, temperature=0.0)
    except Exception:
        return fallback

    if not llm_result:
        return fallback

    cleaned = llm_result.strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return {
            "summary": cleaned[:400] if cleaned else "",
            "used_indices": list(range(len(results))),
            "filtered_indices": [],
            "ok": True,
        }

    try:
        parsed = json.loads(cleaned[start:end + 1])
        summary_text = str(parsed.get("summary", "") or "")
        used = parsed.get("used_indices")
        filtered = parsed.get("filtered_indices")
        if not isinstance(used, list):
            used = list(range(len(results)))
        if not isinstance(filtered, list):
            filtered = []
        used = [int(i) for i in used if isinstance(i, (int, float))]
        filtered = [int(i) for i in filtered if isinstance(i, (int, float))]
        return {
            "summary": summary_text,
            "used_indices": used,
            "filtered_indices": filtered,
            "ok": True,
        }
    except (json.JSONDecodeError, ValueError, TypeError):
        return {
            "summary": cleaned[:400],
            "used_indices": list(range(len(results))),
            "filtered_indices": [],
            "ok": True,
        }


def main() -> int:
    args = parse_args()
    result = search_openclaw_memory(
        query=args.query,
        workspace_root=args.workspace_root,
        limit=args.limit,
        min_score=args.min_score,
    )
    if not result.get("ok"):
        raise SystemExit(f"openclaw memory search 调用失败：{result.get('message', result.get('error', 'unknown'))}")
    output = {k: v for k, v in result.items() if k != "ok"}

    # LLM 检索后处理：过滤 + 去重 + 摘要
    raw_result = output.get("result")
    hits: List[Dict[str, Any]] = []
    if isinstance(raw_result, list):
        hits = raw_result
    elif isinstance(raw_result, dict):
        for key in ("results", "hits", "records", "data", "items"):
            v = raw_result.get(key)
            if isinstance(v, list):
                hits = v
                break
    if hits:
        llm = _get_llm()
        rerank = rerank_and_summarize(query=args.query, results=hits, llm=llm)
        if rerank.get("ok"):
            output["rerank"] = rerank

    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

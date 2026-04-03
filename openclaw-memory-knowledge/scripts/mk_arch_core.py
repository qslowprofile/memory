#!/usr/bin/env python3
"""OpenClaw memory/knowledge 重构与增量入库核心模块。

该模块实现：
1. 深度扫描 memory/knowledge 目录（文本、JSON、JSONL、YAML、CSV、SQLite）
2. 规则化抽取文本记录并分类到 AdaMem-lite + OpenViking 分层目录
3. 去重、分层索引（L0/L1/L2）与状态维护（file fingerprint + content hash）
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".markdown",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".csv",
    ".log",
}
SQLITE_EXTENSIONS = {".db", ".sqlite", ".sqlite3"}
INDEX_FILE_NAMES = {".abstract.md", ".overview.md"}
ALLOWED_MEMORY_POLICIES = {"persist", "private", "ephemeral"}
TRUST_TIER_ORDER = {"generated": 0, "extracted": 1, "curated": 2}

DEFAULT_IGNORE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "venv",
    ".venv",
    "__pycache__",
    "dist",
    "build",
    "target",
    ".idea",
    ".vscode",
}

TIMESTAMP_KEYS = {
    "timestamp",
    "time",
    "datetime",
    "created_at",
    "updated_at",
    "date",
    "when",
}

TEXT_PRIORITY_KEYS = {
    "content",
    "text",
    "message",
    "summary",
    "note",
    "memory",
    "knowledge",
    "description",
    "title",
    "detail",
}

BUCKET_FILES = {
    "memory.profile": "viking/user/memories/profile.jsonl",
    "memory.preferences": "viking/user/memories/preferences.jsonl",
    "memory.events": "viking/user/memories/events.jsonl",
    "memory.agent_skill": "viking/agent/skills/buffer.jsonl",
    "memory.working": "viking/session/working/buffer.jsonl",
    "knowledge.facts": "viking/user/knowledge/facts.jsonl",
    "knowledge.procedures": "viking/user/knowledge/procedures.jsonl",
    "knowledge.references": "viking/user/knowledge/references.jsonl",
}

ENTITY_FILE = "viking/user/memories/entities.jsonl"
RELATION_FILE = "viking/user/memories/relations.jsonl"
RELATION_DECISIONS_FILE = "viking/user/memories/relation_decisions.jsonl"
L2_FILE = "layers/l2_records.jsonl"
L1_FILE = "layers/l1_overview.jsonl"
L0_FILE = "layers/l0_abstract.json"
RETRIEVAL_HINTS_FILE = "layers/retrieval-hints.json"
RETRIEVAL_PROTOCOL_FILE = "layers/retrieval_protocol.json"
PROFILE_SNAPSHOT_FILE = "layers/profile_snapshot.json"
PREFERENCES_SNAPSHOT_FILE = "layers/preferences_snapshot.json"
HASH_STATE_FILE = "state/record_hashes.txt"
FILE_STATE_FILE = "state/processed_files.json"
ARCHIVE_FILE = "viking/archive/records.jsonl"
REPORTS_DIR = "reports"

PROFILE_FIELD_ALIASES = {
    "name": {"name", "姓名", "名字", "我叫", "my name"},
    "nickname": {"nickname", "昵称", "别名"},
    "role": {"role", "角色", "职位", "职业", "occupation"},
    "identity": {"identity", "身份"},
    "background": {"background", "背景"},
    "timezone": {"timezone", "时区", "tz"},
    "locale": {"locale", "语言环境"},
    "language": {"language", "语言"},
    "company": {"company", "公司"},
    "team": {"team", "团队"},
    "location": {"location", "所在地", "地点", "城市"},
}

PREFERENCE_FIELD_ALIASES = {
    "likes": {"likes", "喜欢", "偏好"},
    "dislikes": {"dislikes", "不喜欢", "避开", "avoid"},
    "favorite_tools": {"favorite tools", "常用工具", "工具偏好", "tools"},
    "workflow_preferences": {"workflow preferences", "工作流偏好", "流程偏好"},
    "communication_style": {"communication style", "沟通风格"},
    "coding_style": {"coding style", "编码风格"},
}

CHINESE_STOPWORDS = {
    "这个",
    "那个",
    "我们",
    "你们",
    "他们",
    "以及",
    "如果",
    "因为",
    "所以",
    "然后",
    "但是",
    "可以",
    "需要",
    "进行",
    "已经",
    "没有",
    "一个",
    "一些",
    "自己",
    "使用",
    "系统",
    "用户",
    "问题",
    "内容",
    "时候",
    "今天",
    "昨天",
    "明天",
}

ENGLISH_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "into",
    "have",
    "has",
    "had",
    "your",
    "you",
    "our",
    "their",
    "about",
    "there",
    "here",
    "what",
    "when",
    "where",
    "which",
    "will",
    "would",
    "could",
    "should",
    "been",
    "were",
    "is",
    "are",
    "was",
    "to",
    "of",
    "in",
    "on",
    "at",
    "it",
    "as",
    "an",
    "or",
    "by",
    "be",
    "not",
}

PROFILE_KWS = [
    "我是",
    "我叫",
    "名字",
    "生日",
    "职业",
    "背景",
    "身份",
    "i am",
    "my name",
    "my background",
    "occupation",
]
PREFERENCE_KWS = [
    "喜欢",
    "不喜欢",
    "偏好",
    "习惯",
    "常用",
    "通常",
    "爱好",
    "prefer",
    "favorite",
    "i like",
    "i dislike",
]
EVENT_KWS = [
    "会议",
    "项目",
    "完成",
    "发生",
    "今天",
    "昨天",
    "上周",
    "去年",
    "任务",
    "met",
    "did",
    "happened",
    "completed",
]
WORKING_KWS = ["todo", "待办", "稍后", "follow up", "临时", "暂存", "next step"]
AGENT_SKILL_KWS = [
    "skill",
    "技能",
    "工具",
    "tool",
    "workflow",
    "playbook",
    "提示词",
    "prompt",
    "policy",
    "规则",
    "routing",
]

PROCEDURE_KWS = [
    "步骤",
    "流程",
    "如何",
    "首先",
    "然后",
    "最后",
    "step",
    "how to",
    "install",
    "run",
    "configure",
]
REFERENCE_KWS = [
    "http://",
    "https://",
    "文档",
    "参考",
    "api",
    "readme",
    "wiki",
    "手册",
]

CATEGORY_TO_BUCKET = {
    "profile": "memory.profile",
    "persona": "memory.profile",
    "identity": "memory.profile",
    "preferences": "memory.preferences",
    "preference": "memory.preferences",
    "events": "memory.events",
    "event": "memory.events",
    "working": "memory.working",
    "short_term": "memory.working",
    "agent_skill": "memory.agent_skill",
    "agent-skills": "memory.agent_skill",
    "agent_skill_memory": "memory.agent_skill",
    "skills": "memory.agent_skill",
    "facts": "knowledge.facts",
    "procedures": "knowledge.procedures",
    "procedure": "knowledge.procedures",
    "references": "knowledge.references",
    "reference": "knowledge.references",
}

TTL_RULES = [
    (r"/memory/short-term/", "7d", "warm"),
    (r"/memory/agent/memories/cases/", "permanent", "hot"),
    (r"/memory/user/memories/events/", "permanent", "hot"),
    (r"/memory/agent/skills/", "90d", "warm"),
    (r"/knowledge/", "90d", "warm"),
]

GENERIC_ENTITY_WORDS = {
    "memory",
    "knowledge",
    "profile",
    "preferences",
    "events",
    "working",
    "section",
    "chunk",
    "user",
    "agent",
    "skill",
    "步骤",
    "流程",
    "内容",
    "信息",
    "记录",
    "文档",
    "数据",
    "知识",
    "记忆",
}

PREDICATE_ALIASES = {
    "是": "is_a",
    "is": "is_a",
    "are": "is_a",
    "属于": "belongs_to",
    "belongs to": "belongs_to",
    "喜欢": "likes",
    "likes": "likes",
    "偏好": "likes",
    "不喜欢": "dislikes",
    "dislikes": "dislikes",
    "依赖": "depends_on",
    "depends on": "depends_on",
    "调用": "calls",
    "calls": "calls",
    "使用": "uses",
    "uses": "uses",
    "集成": "integrates",
    "integrates": "integrates",
    "包含": "contains",
    "contains": "contains",
    "引用": "references",
    "references": "references",
    "链接": "references",
    "link": "references",
    "负责": "owns",
    "负责人": "owns",
    "owner": "owns",
    "assignee": "owns",
    "管理": "owns",
    "works on": "works_on",
    "参与": "works_on",
    "参与了": "works_on",
    "participants": "works_with",
    "参与者": "works_with",
    "works_with": "works_with",
    "主导": "leads",
    "主导了": "leads",
    "负责开发": "owns",
    "维护": "maintains",
    "supports": "supports",
    "支持": "supports",
    "实现": "implements",
    "implements": "implements",
    "影响": "affects",
    "影响了": "affects",
    "affects": "affects",
    "leads to": "leads_to",
    "导致": "leads_to",
    "驱动": "drives",
    "drives": "drives",
    "优于": "better_than",
    "better than": "better_than",
    "超过": "better_than",
    "用于": "used_for",
    "used for": "used_for",
    "基于": "based_on",
    "based on": "based_on",
    "来源于": "derived_from",
    "derived from": "derived_from",
    "responsible for": "owns",
    "is responsible for": "owns",
    "in charge of": "owns",
    "关联": "related_to",
    "完成": "completed",
    "cooccurs_with": "cooccurs_with",
    "related_to": "related_to",
}

KV_FIELD_PREDICATES = {
    "姓名": "name_is",
    "名字": "name_is",
    "name": "name_is",
    "昵称": "alias",
    "alias": "alias",
    "角色": "role_is",
    "role": "role_is",
    "职业": "role_is",
    "岗位": "role_is",
    "负责人": "owns",
    "owner": "owns",
    "assignee": "owns",
    "部门": "affiliated_with",
    "团队": "affiliated_with",
    "所属团队": "affiliated_with",
    "company": "affiliated_with",
    "公司": "affiliated_with",
    "组织": "affiliated_with",
    "喜欢": "likes",
    "偏好": "likes",
    "preference": "likes",
    "不喜欢": "dislikes",
    "dislike": "dislikes",
    "技能": "skilled_in",
    "skills": "skilled_in",
    "工具": "uses",
    "tools": "uses",
    "依赖": "depends_on",
    "dependency": "depends_on",
    "参与者": "works_with",
    "participants": "works_with",
    "仓库": "references",
    "repo": "references",
    "文档": "references",
    "docs": "references",
}

GENERIC_FIELD_NAMES = {
    "id",
    "uuid",
    "note",
    "notes",
    "desc",
    "description",
    "备注",
    "说明",
    "内容",
    "时间",
    "日期",
    "timestamp",
    "created_at",
    "updated_at",
}

SOURCE_KIND_KNOWLEDGE_HINTS = [
    "runbook",
    "playbook",
    "howto",
    "guide",
    "tutorial",
    "manual",
    "api",
    "spec",
    "reference",
    "doc",
    "paper",
    "benchmark",
    "研究",
    "结论",
    "知识",
    "流程",
    "步骤",
    "部署",
]

SOURCE_KIND_MEMORY_HINTS = [
    "我",
    "我的",
    "今天",
    "昨天",
    "明天",
    "偏好",
    "喜欢",
    "事件",
    "任务",
    "待办",
    "profile",
    "preference",
    "event",
    "memory",
    "todo",
]

RELATION_BRIDGE_STOPWORDS = {
    "和",
    "与",
    "及",
    "的",
    "是",
    "不是",
    "可以",
    "需要",
    "并",
    "并且",
    "且",
    "或",
    "或者",
    "以及",
    "to",
    "for",
    "of",
    "in",
    "on",
    "at",
    "as",
    "by",
    "from",
    "and",
    "or",
    "with",
    "is",
    "are",
    "was",
    "were",
    "be",
    "been",
    "being",
    "can",
    "need",
    "needs",
    "required",
    "the",
    "a",
    "an",
}

RELATION_FIELD_KEYWORDS = {
    "负责人",
    "归属",
    "所属",
    "关系",
    "关联",
    "来源",
    "目标",
    "依赖",
    "依赖项",
    "工具",
    "调用",
    "集成",
    "支持",
    "影响",
    "主导",
    "参与者",
    "owner",
    "assignee",
    "belongs",
    "depends",
    "dependency",
    "uses",
    "tool",
    "calls",
    "integrates",
    "supports",
    "related",
    "relation",
    "source",
    "target",
    "reference",
}

MENTION_PATTERN = re.compile(
    r"(?<![\w\[])\@([A-Za-z\u4e00-\u9fff][A-Za-z0-9_\-\u4e00-\u9fff]{1,30})(?![\w|])",
    flags=re.UNICODE,
)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def utc_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def normalize_text(text: str) -> str:
    text = text.replace("\u3000", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def normalize_text_keep_lines(text: str) -> str:
    text = text.replace("\u3000", " ")
    out_lines: List[str] = []
    for raw in text.splitlines():
        line = re.sub(r"[ \t]+", " ", raw).strip()
        if line:
            out_lines.append(line)
    return "\n".join(out_lines).strip()


def parse_iso_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)

    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        try:
            return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    iso = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def detect_timestamp(text: str) -> Optional[str]:
    patterns = [
        r"\b\d{4}[-/]\d{1,2}[-/]\d{1,2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?\b",
        r"\b\d{1,2}[-/]\d{1,2}[-/]\d{2,4}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?\b",
        r"\b(?:today|yesterday|tomorrow)\b",
        r"(?:今天|昨天|明天|上周|下周|去年|今年)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(0)
    return None


@dataclass
class SourceRecord:
    source_kind: str
    source_path: str
    locator: str
    text: str
    timestamp: Optional[str]
    metadata: Dict[str, Any]
    section_title: str = ""


@dataclass
class NormalizedRecord:
    record_id: str
    content_hash: str
    source_kind: str
    source_path: str
    locator: str
    bucket: str
    text: str
    timestamp: Optional[str]
    tags: List[str]
    entities: List[str]
    relations: List[Tuple[str, str, str]]
    confidence: float
    metadata: Dict[str, Any]
    created_at: str
    version: int
    ttl: str
    heat: str
    last_accessed: str
    memory_function: str
    formation_mode: str
    trust_tier: str
    memory_policy: str

    def to_json(self) -> Dict[str, Any]:
        return {
            "id": self.record_id,
            "content_hash": self.content_hash,
            "source_kind": self.source_kind,
            "source_path": self.source_path,
            "locator": self.locator,
            "bucket": self.bucket,
            "text": self.text,
            "timestamp": self.timestamp,
            "tags": self.tags,
            "entities": self.entities,
            "relations": [
                {"subject": s, "predicate": p, "object": o} for s, p, o in self.relations
            ],
            "confidence": self.confidence,
            "version": self.version,
            "ttl": self.ttl,
            "heat": self.heat,
            "last_accessed": self.last_accessed,
            "memory_function": self.memory_function,
            "formation_mode": self.formation_mode,
            "trust_tier": self.trust_tier,
            "memory_policy": self.memory_policy,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }


def is_supported_file(path: Path) -> bool:
    suffix = path.suffix.lower()
    return suffix in TEXT_EXTENSIONS or suffix in SQLITE_EXTENSIONS


def is_binary_file(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            head = f.read(2048)
        if b"\x00" in head:
            return True
        return False
    except OSError:
        return True


def read_text_file(path: Path) -> str:
    encodings = ["utf-8", "utf-8-sig", "gb18030", "gbk", "latin-1"]
    for enc in encodings:
        try:
            return path.read_text(encoding=enc)
        except UnicodeDecodeError:
            continue
    return path.read_text(encoding="utf-8", errors="ignore")


def extract_category_from_frontmatter(text: str) -> Optional[str]:
    """从 markdown/frontmatter/注释中提取 category。"""
    # 1) frontmatter 内的 category: xxx
    fm_match = re.match(r"^\s*---\s*\n(?P<fm>.*?)(?:\n---\s*\n|\n---\s*$)", text, flags=re.DOTALL)
    if fm_match:
        fm = fm_match.group("fm")
        m = re.search(r"(?:^|\n)\s*category\s*:\s*([A-Za-z0-9_.\-]+)\s*(?:\n|$)", fm, flags=re.IGNORECASE)
        if m:
            return m.group(1).strip().lower()

    # 2) 普通文本中的 Category: xxx / > Category: xxx
    m = re.search(r"(?:^|\n)\s*>?\s*[Cc]ategory\s*:\s*([A-Za-z0-9_.\-]+)\s*(?:\n|$)", text)
    if m:
        return m.group(1).strip().lower()
    return None


def strip_frontmatter_block(text: str) -> str:
    match = re.match(r"^\s*---\s*\n.*?\n---\s*(?:\n|$)", text, flags=re.DOTALL)
    if not match:
        return text
    return text[match.end() :].lstrip("\n")


def parse_frontmatter_fields(text: str) -> Dict[str, str]:
    match = re.match(r"^\s*---\s*\n(?P<fm>.*?)(?:\n---\s*\n|\n---\s*$)", text, flags=re.DOTALL)
    if not match:
        return {}
    fields: Dict[str, str] = {}
    for raw_line in match.group("fm").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip().strip("'\"")
        if not key or not value:
            continue
        fields[key] = value
    return fields


def normalize_frontmatter_value(value: str) -> str:
    lowered = value.strip().lower()
    return re.sub(r"\s+", " ", lowered)


def infer_memory_policy(metadata: Dict[str, Any], text: str) -> str:
    policy_candidates: List[str] = []
    meta_policy = metadata.get("memory_policy")
    if isinstance(meta_policy, str):
        policy_candidates.append(meta_policy)
    frontmatter_fields = metadata.get("frontmatter_fields")
    if isinstance(frontmatter_fields, dict):
        fm_policy = frontmatter_fields.get("memory_policy") or frontmatter_fields.get("privacy")
        if isinstance(fm_policy, str):
            policy_candidates.append(fm_policy)

    for candidate in policy_candidates:
        normalized = normalize_frontmatter_value(candidate)
        if normalized in ALLOWED_MEMORY_POLICIES:
            return normalized

    if re.search(r"<private>.*?</private>", text, flags=re.IGNORECASE | re.DOTALL):
        return "persist"
    return "persist"


def redact_private_spans(text: str) -> Tuple[str, bool]:
    had_private = False

    def _replace(match: re.Match[str]) -> str:
        nonlocal had_private
        had_private = True
        inner = normalize_text_keep_lines(match.group("body"))
        if not inner:
            return "[redacted-private]"
        preview = normalize_text(inner)[:48]
        if preview:
            return f"[redacted-private:{preview}]"
        return "[redacted-private]"

    redacted = re.sub(
        r"<private>(?P<body>.*?)</private>",
        _replace,
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return redacted, had_private


def split_text_chunks(text: str, max_chars: int = 1200) -> List[str]:
    text = text.strip()
    if not text:
        return []

    parts = re.split(r"\n\s*\n", text)
    chunks: List[str] = []
    cur = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if len(part) > max_chars:
            for i in range(0, len(part), max_chars):
                segment = part[i : i + max_chars].strip()
                if segment:
                    chunks.append(segment)
            continue

        if not cur:
            cur = part
        elif len(cur) + len(part) + 2 <= max_chars:
            cur = f"{cur}\n\n{part}"
        else:
            chunks.append(cur)
            cur = part

    if cur:
        chunks.append(cur)

    return chunks


def split_markdown_sections(text: str, max_chars: int = 1200) -> List[Tuple[str, str]]:
    """按 ##/### 标题切块，保留 section 标题上下文。"""
    lines = text.splitlines()
    sections: List[Tuple[str, str]] = []

    current_title = ""
    current_lines: List[str] = []
    section_idx = 0

    def flush_section() -> None:
        nonlocal section_idx
        if not current_lines:
            return
        raw = "\n".join(current_lines).strip()
        if not raw:
            return
        section_idx += 1
        title = current_title or f"section-{section_idx}"
        for chunk in split_text_chunks(raw, max_chars=max_chars):
            sections.append((title, chunk))

    for line in lines:
        if line.startswith("## ") or line.startswith("### "):
            flush_section()
            current_title = line.lstrip("#").strip()
            current_lines = []
            continue
        current_lines.append(line)

    flush_section()
    return sections


def _json_pointer_append(pointer: str, key: str) -> str:
    if pointer == "$":
        return f"$.{key}"
    return f"{pointer}.{key}"


def iter_json_text_nodes(obj: Any, pointer: str = "$") -> Iterable[Tuple[str, str, Optional[str], Dict[str, Any]]]:
    if isinstance(obj, dict):
        text_fragments: List[str] = []
        timestamp: Optional[str] = None
        for key, value in obj.items():
            key_lower = str(key).lower()
            if key_lower in TIMESTAMP_KEYS and isinstance(value, str):
                timestamp = value
            if key_lower in TEXT_PRIORITY_KEYS and isinstance(value, str):
                if value.strip():
                    text_fragments.append(f"{key}: {value.strip()}")

        if text_fragments:
            meta = {"json_keys": list(obj.keys())[:40]}
            yield pointer, "\n".join(text_fragments), timestamp, meta

        for key, value in obj.items():
            sub_pointer = _json_pointer_append(pointer, str(key))
            yield from iter_json_text_nodes(value, sub_pointer)

    elif isinstance(obj, list):
        for idx, value in enumerate(obj):
            sub_pointer = f"{pointer}[{idx}]"
            yield from iter_json_text_nodes(value, sub_pointer)

    elif isinstance(obj, str):
        text = obj.strip()
        if len(text) >= 24:
            yield pointer, text, detect_timestamp(text), {}


def extract_from_json_file(path: Path, source_kind: str, max_records_per_file: int) -> List[SourceRecord]:
    records: List[SourceRecord] = []
    if path.suffix.lower() == ".jsonl":
        with path.open("r", encoding="utf-8", errors="ignore") as f:
            for line_idx, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    for pointer, text, ts, meta in iter_json_text_nodes(obj, f"$[{line_idx}]"):
                        records.append(
                            SourceRecord(
                                source_kind=source_kind,
                                source_path=str(path),
                                locator=f"line:{line_idx}:{pointer}",
                                text=text,
                                timestamp=ts or detect_timestamp(text),
                                metadata={"source_format": "jsonl", **meta},
                            )
                        )
                except json.JSONDecodeError:
                    records.append(
                        SourceRecord(
                            source_kind=source_kind,
                            source_path=str(path),
                            locator=f"line:{line_idx}",
                            text=line,
                            timestamp=detect_timestamp(line),
                            metadata={"source_format": "jsonl", "raw_line": True},
                        )
                    )
                if len(records) >= max_records_per_file:
                    break
        return records[:max_records_per_file]

    data = json.loads(read_text_file(path))
    for pointer, text, ts, meta in iter_json_text_nodes(data):
        records.append(
            SourceRecord(
                source_kind=source_kind,
                source_path=str(path),
                locator=pointer,
                text=text,
                timestamp=ts or detect_timestamp(text),
                metadata={"source_format": "json", **meta},
            )
        )
        if len(records) >= max_records_per_file:
            break
    return records[:max_records_per_file]


def extract_from_csv_file(path: Path, source_kind: str, max_records_per_file: int) -> List[SourceRecord]:
    records: List[SourceRecord] = []
    with path.open("r", encoding="utf-8", errors="ignore", newline="") as f:
        reader = csv.DictReader(f)
        for row_idx, row in enumerate(reader, start=1):
            text_fields = []
            timestamp = None
            for key, value in row.items():
                if value is None:
                    continue
                v = str(value).strip()
                if not v:
                    continue
                if key and key.lower() in TIMESTAMP_KEYS:
                    timestamp = v
                text_fields.append(f"{key}: {v}")
            if text_fields:
                text = "\n".join(text_fields)
                records.append(
                    SourceRecord(
                        source_kind=source_kind,
                        source_path=str(path),
                        locator=f"row:{row_idx}",
                        text=text,
                        timestamp=timestamp or detect_timestamp(text),
                        metadata={"source_format": "csv"},
                    )
                )
            if len(records) >= max_records_per_file:
                break
    return records[:max_records_per_file]


def extract_from_plaintext_file(path: Path, source_kind: str, max_records_per_file: int) -> List[SourceRecord]:
    text = read_text_file(path)
    records: List[SourceRecord] = []
    suffix = path.suffix.lower()
    category_hint = extract_category_from_frontmatter(text)
    frontmatter_fields = parse_frontmatter_fields(text)
    memory_policy = ""
    if frontmatter_fields:
        raw_policy = frontmatter_fields.get("memory_policy") or frontmatter_fields.get("privacy")
        if isinstance(raw_policy, str):
            memory_policy = normalize_frontmatter_value(raw_policy)

    if suffix in {".md", ".markdown"}:
        body_text = strip_frontmatter_block(text)
        sections = split_markdown_sections(body_text, max_chars=1200)
        # 没有 markdown 标题时回退。
        if not sections:
            sections = [(f"chunk-{idx}", chunk) for idx, chunk in enumerate(split_text_chunks(body_text, max_chars=1200), 1)]

        for idx, (section_title, chunk) in enumerate(sections, start=1):
            contextual_text = f"## {section_title}\n{chunk}".strip() if section_title else chunk
            if len(normalize_text(contextual_text)) < 12:
                continue
            records.append(
                SourceRecord(
                    source_kind=source_kind,
                    source_path=str(path),
                    locator=f"section:{idx}:{section_title}",
                    text=contextual_text,
                    timestamp=detect_timestamp(contextual_text),
                    metadata={
                        "source_format": "markdown",
                        "section_title": section_title,
                        "category_hint": category_hint,
                        "frontmatter_fields": frontmatter_fields,
                        "memory_policy": memory_policy,
                    },
                    section_title=section_title,
                )
            )
            if len(records) >= max_records_per_file:
                break
        return records[:max_records_per_file]

    chunks = split_text_chunks(text, max_chars=1200)
    for idx, chunk in enumerate(chunks, start=1):
        records.append(
            SourceRecord(
                source_kind=source_kind,
                source_path=str(path),
                locator=f"chunk:{idx}",
                text=chunk,
                timestamp=detect_timestamp(chunk),
                metadata={
                    "source_format": "text",
                    "category_hint": category_hint,
                    "frontmatter_fields": frontmatter_fields,
                    "memory_policy": memory_policy,
                },
                section_title="",
            )
        )
        if len(records) >= max_records_per_file:
            break
    return records[:max_records_per_file]


def is_sqlite_file(path: Path) -> bool:
    if path.suffix.lower() in SQLITE_EXTENSIONS:
        return True
    try:
        with path.open("rb") as f:
            return f.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def extract_from_sqlite_file(path: Path, source_kind: str, max_records_per_file: int) -> List[SourceRecord]:
    records: List[SourceRecord] = []
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        ]
        if not tables:
            return records

        per_table_limit = max(30, max_records_per_file // max(1, len(tables)))
        for table in tables:
            safe_table = table.replace("'", "''")
            cols_info = conn.execute(f"PRAGMA table_info('{safe_table}')").fetchall()
            text_cols = []
            for col in cols_info:
                col_name = col[1]
                col_type = (col[2] or "").upper()
                if "CHAR" in col_type or "TEXT" in col_type or "CLOB" in col_type or not col_type:
                    text_cols.append(col_name)

            sql = f"SELECT rowid AS __rowid__, * FROM '{safe_table}' LIMIT {per_table_limit}"
            for row in conn.execute(sql).fetchall():
                line_bits = []
                timestamp = None
                keys = row.keys()
                rowid_val = row[0] if len(row) > 0 else None
                for key in keys:
                    val = row[key]
                    if val is None:
                        continue
                    if str(key).lower() in {"rowid", "__rowid__"}:
                        continue
                    if text_cols and key not in text_cols:
                        continue
                    sval = str(val).strip()
                    if not sval:
                        continue
                    if key.lower() in TIMESTAMP_KEYS:
                        timestamp = sval
                    line_bits.append(f"{key}: {sval}")

                if not line_bits:
                    continue

                text = "\n".join(line_bits)
                records.append(
                    SourceRecord(
                        source_kind=source_kind,
                        source_path=str(path),
                        locator=f"table:{table}:rowid:{rowid_val}",
                        text=text,
                        timestamp=timestamp or detect_timestamp(text),
                        metadata={
                            "source_format": "sqlite",
                            "table": table,
                            "text_columns": text_cols,
                        },
                    )
                )

                if len(records) >= max_records_per_file:
                    return records[:max_records_per_file]
    finally:
        conn.close()

    return records[:max_records_per_file]


def extract_from_file(path: Path, source_kind: str, max_records_per_file: int) -> Tuple[List[SourceRecord], Optional[str]]:
    try:
        if path.suffix.lower() in {".json", ".jsonl"}:
            return extract_from_json_file(path, source_kind, max_records_per_file), None
        if path.suffix.lower() in {".yaml", ".yml"}:
            # YAML 按纯文本处理，避免引入外部依赖。
            return extract_from_plaintext_file(path, source_kind, max_records_per_file), None
        if path.suffix.lower() == ".csv":
            return extract_from_csv_file(path, source_kind, max_records_per_file), None
        if is_sqlite_file(path):
            return extract_from_sqlite_file(path, source_kind, max_records_per_file), None
        return extract_from_plaintext_file(path, source_kind, max_records_per_file), None
    except Exception as exc:  # noqa: BLE001
        return [], f"{path}: {exc}"


def iter_source_files(
    root: Path,
    max_file_size_bytes: int,
    recursive: bool,
    include_hidden: bool,
    ignore_dirs: Optional[Set[str]] = None,
) -> List[Path]:
    ignore_dirs = ignore_dirs or DEFAULT_IGNORE_DIRS

    if root.is_file():
        return [root] if is_supported_file(root) else []

    if not root.exists():
        return []

    files: List[Path] = []
    if recursive:
        iterator = root.rglob("*")
    else:
        iterator = root.glob("*")

    for path in iterator:
        if not path.is_file():
            continue

        rel_parts = path.relative_to(root).parts
        if any(part in ignore_dirs for part in rel_parts):
            continue
        if not include_hidden and any(part.startswith(".") for part in rel_parts):
            continue
        if path.name.lower() in INDEX_FILE_NAMES:
            continue
        if not is_supported_file(path):
            continue
        try:
            if path.stat().st_size > max_file_size_bytes:
                continue
        except OSError:
            continue
        if path.suffix.lower() in TEXT_EXTENSIONS and is_binary_file(path):
            continue
        files.append(path)

    return sorted(files)


def keyword_score(text_lower: str, keywords: Sequence[str]) -> int:
    score = 0
    for kw in keywords:
        if kw in text_lower:
            score += 1
    return score


def map_category_to_bucket(source_kind: str, category: str) -> Optional[str]:
    cat = category.strip().lower()
    if not cat:
        return None
    if cat in BUCKET_FILES:
        return cat
    mapped = CATEGORY_TO_BUCKET.get(cat)
    if mapped:
        return mapped

    # category 未显式映射时，按 source kind 兜底。
    if source_kind == "knowledge":
        if cat.startswith("fact"):
            return "knowledge.facts"
        if cat.startswith("proc"):
            return "knowledge.procedures"
        if cat.startswith("ref"):
            return "knowledge.references"
    else:
        if cat.startswith("profile") or cat.startswith("persona"):
            return "memory.profile"
        if cat.startswith("pref"):
            return "memory.preferences"
        if cat.startswith("event"):
            return "memory.events"
        if cat.startswith("work"):
            return "memory.working"
        if cat.startswith("agent") or cat.startswith("skill"):
            return "memory.agent_skill"
    return None


def infer_bucket_from_path(path_lower: str, text_lower: str) -> Optional[Tuple[str, float]]:
    # P1: 路径优先（最高优先级）
    rules = [
        (r"/memory/user/memories/profile/", "memory.profile", 0.98),
        (r"/memory/user/memories/preferences/", "memory.preferences", 0.97),
        (r"/memory/user/memories/events/", "memory.events", 0.97),
        (r"/memory/user/memories/entities/", "memory.profile", 0.92),
        (r"/memory/short-term/", "memory.working", 0.95),
        (r"/memory/agent/memories/cases/", "memory.events", 0.92),
        (r"/memory/agent/memories/patterns/", "knowledge.procedures", 0.91),
        (r"/memory/agent/skills/", "memory.agent_skill", 0.95),
        (r"/knowledge/.*/papers/", "knowledge.facts", 0.90),
        (r"/knowledge/.*/github/", "knowledge.references", 0.90),
        (r"/knowledge/.*/benchmarks/", "knowledge.facts", 0.88),
    ]

    for pattern, bucket, confidence in rules:
        if re.search(pattern, path_lower):
            if bucket == "knowledge.facts" and ("http://" in text_lower or "https://" in text_lower):
                return "knowledge.references", 0.92
            return bucket, confidence
    return None


def infer_knowledge_bucket_from_filename(path_lower: str, text_lower: str) -> Optional[Tuple[str, float]]:
    """knowledge 专用路径/文件名策略，优先于关键词兜底。"""
    if "/knowledge/" not in path_lower and not path_lower.endswith(".knowledge"):
        return None

    filename = path_lower.rsplit("/", 1)[-1]
    merged = f"{filename} {path_lower} {text_lower[:260]}"

    procedure_patterns = [
        r"runbook",
        r"playbook",
        r"how[-_ ]?to",
        r"guide",
        r"tutorial",
        r"步骤",
        r"流程",
        r"安装",
        r"部署",
        r"运维",
        r"procedure",
        r"workflow",
    ]
    reference_patterns = [
        r"readme",
        r"wiki",
        r"api",
        r"spec",
        r"reference",
        r"github",
        r"repo",
        r"链接",
        r"网址",
        r"http://",
        r"https://",
    ]
    fact_patterns = [
        r"paper",
        r"arxiv",
        r"benchmark",
        r"report",
        r"insight",
        r"notes?",
        r"研究",
        r"结论",
        r"洞察",
        r"实验",
    ]

    if any(re.search(p, merged, flags=re.IGNORECASE) for p in procedure_patterns):
        return "knowledge.procedures", 0.9
    if any(re.search(p, merged, flags=re.IGNORECASE) for p in reference_patterns):
        return "knowledge.references", 0.88
    if any(re.search(p, merged, flags=re.IGNORECASE) for p in fact_patterns):
        return "knowledge.facts", 0.85
    return None


def infer_ttl_and_heat(source_path: str, bucket: str) -> Tuple[str, str]:
    path_lower = source_path.replace("\\", "/").lower()
    for pattern, ttl, heat in TTL_RULES:
        if re.search(pattern, path_lower):
            return ttl, heat

    # 对 bucket 的兜底策略。
    if bucket in {"memory.events", "memory.profile"}:
        return "permanent", "hot"
    if bucket == "memory.agent_skill":
        return "90d", "warm"
    if bucket == "memory.working":
        return "7d", "warm"
    if bucket.startswith("knowledge."):
        return "90d", "warm"
    return "30d", "warm"


def infer_memory_function(bucket: str) -> str:
    if bucket == "memory.working":
        return "working"
    if bucket in {"memory.events", "memory.agent_skill"}:
        return "experiential"
    return "factual"


def infer_trust_tier(metadata: Dict[str, Any], source_path: str) -> str:
    explicit = metadata.get("trust_tier")
    if isinstance(explicit, str):
        normalized = normalize_frontmatter_value(explicit)
        if normalized in TRUST_TIER_ORDER:
            return normalized

    frontmatter_fields = metadata.get("frontmatter_fields")
    if isinstance(frontmatter_fields, dict):
        fm_trust = frontmatter_fields.get("trust_tier")
        if isinstance(fm_trust, str):
            normalized = normalize_frontmatter_value(fm_trust)
            if normalized in TRUST_TIER_ORDER:
                return normalized

    source_format = str(metadata.get("source_format", "")).lower()
    path_lower = source_path.replace("\\", "/").lower()
    if ".adaptr-v1/" in path_lower:
        return "generated"
    if metadata.get("frontmatter_fields") or metadata.get("category_hint"):
        return "curated"
    if source_format in {"markdown", "text"}:
        return "curated"
    return "extracted"


def classify_bucket(
    source_kind: str,
    text: str,
    source_path: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[str, float]:
    text_lower = text.lower()
    path_lower = source_path.replace("\\", "/").lower()
    metadata = metadata or {}

    source_kind = infer_effective_source_kind(
        source_kind=source_kind,
        text=text,
        source_path=source_path,
        metadata=metadata,
    )

    # P1: 路径优先
    by_path = infer_bucket_from_path(path_lower, text_lower)
    if by_path is not None:
        return by_path

    # P2: category/frontmatter 优先
    category_hint = ""
    meta_hint = metadata.get("category_hint")
    if isinstance(meta_hint, str):
        category_hint = meta_hint.strip().lower()
    if not category_hint:
        category_hint = extract_category_from_frontmatter(text) or ""
    if category_hint:
        bucket = map_category_to_bucket(source_kind, category_hint)
        if bucket:
            return bucket, 0.95

    # P2.5: knowledge 路径/文件名专用策略
    if source_kind == "knowledge":
        by_knowledge_filename = infer_knowledge_bucket_from_filename(path_lower, text_lower)
        if by_knowledge_filename is not None:
            return by_knowledge_filename

    # P3: 关键词计分兜底
    if source_kind == "knowledge":
        score_procedure = keyword_score(text_lower, PROCEDURE_KWS)
        score_reference = keyword_score(text_lower, REFERENCE_KWS)
        score_facts = keyword_score(text_lower, ["paper", "arxiv", "benchmark", "研究", "结论", "insight", "facts"])
        score_map = {
            "knowledge.procedures": score_procedure,
            "knowledge.references": score_reference,
            "knowledge.facts": score_facts,
        }
        ranked = sorted(score_map.items(), key=lambda item: item[1], reverse=True)
        top_bucket, top_score = ranked[0]
        second_score = ranked[1][1] if len(ranked) > 1 else 0
        total_score = sum(score_map.values())
        conf = confidence_from_keyword_scores(
            top_score=top_score,
            second_score=second_score,
            total_score=total_score,
            floor=0.5,
            ceiling=0.9,
        )
        if top_score <= 0:
            return "knowledge.facts", 0.56
        return top_bucket, conf

    score_profile = keyword_score(text_lower, PROFILE_KWS)
    score_preferences = keyword_score(text_lower, PREFERENCE_KWS)
    score_event = keyword_score(text_lower, EVENT_KWS)
    score_working = keyword_score(text_lower, WORKING_KWS)
    score_agent_skill = keyword_score(text_lower, AGENT_SKILL_KWS)
    score_map = {
        "memory.profile": score_profile,
        "memory.preferences": score_preferences,
        "memory.events": score_event + (1 if detect_timestamp(text) else 0),
        "memory.agent_skill": score_agent_skill,
        "memory.working": score_working,
    }
    ranked = sorted(score_map.items(), key=lambda item: item[1], reverse=True)
    top_bucket, top_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0
    total_score = sum(score_map.values())
    conf = confidence_from_keyword_scores(
        top_score=top_score,
        second_score=second_score,
        total_score=total_score,
        floor=0.52,
        ceiling=0.88,
    )
    if top_score <= 0:
        return "memory.working", 0.59
    return top_bucket, conf


def normalize_predicate(predicate: str) -> str:
    pred = normalize_text(predicate).lower()
    if not pred:
        return "related_to"
    return PREDICATE_ALIASES.get(pred, pred.replace(" ", "_"))


def clean_entity_surface(token: str) -> str:
    if not token:
        return ""
    text = token.strip().strip("`\"'[](){}<>，。！？；：,.!?;:")
    text = normalize_text(text)
    return text


def canonical_entity_key(token: str) -> str:
    text = clean_entity_surface(token).lower()
    text = re.sub(r"\s+", " ", text).strip()
    return text


def looks_like_valid_entity(token: str) -> bool:
    t = clean_entity_surface(token)
    if len(t) < 2:
        return False
    if len(t) > 64:
        return False
    if re.fullmatch(r"[\W_]+", t, flags=re.UNICODE):
        return False
    if t.lower() in {"user", "agent"}:
        return True
    if t.lower() in GENERIC_ENTITY_WORDS:
        return False
    return True


def split_field_values(value: str) -> List[str]:
    bits = re.split(r"[、,，;/；\|]\s*|\s+and\s+|\s+或\s+|\s+以及\s+", value, flags=re.IGNORECASE)
    out: List[str] = []
    for b in bits:
        cleaned = clean_entity_surface(b)
        if not cleaned:
            continue
        if cleaned not in out:
            out.append(cleaned)
    return out


def infer_predicate_from_field(field: str) -> Optional[str]:
    normalized = normalize_text(field).lower()
    if not normalized:
        return None
    if normalized in KV_FIELD_PREDICATES:
        return KV_FIELD_PREDICATES[normalized]
    if normalized in GENERIC_FIELD_NAMES:
        return None
    if len(normalized) > 32:
        return None
    if normalized in RELATION_FIELD_KEYWORDS:
        return normalize_predicate(normalized)
    # 仅当字段名本身看起来像“关系谓词”时才放行，避免把“状态/优先级”等名词列当谓词。
    if re.search(r"(负责|归属|所属|依赖|使用|调用|集成|支持|影响|主导|参与|来源|目标|关系|关联)", normalized):
        return normalize_predicate(normalized)
    if re.search(r"(owner|assignee|belong|depend|use|call|integrat|support|impact|lead|relation|related|source|target|reference)", normalized):
        return normalize_predicate(normalized)
    return None


def is_markdown_table_separator(line: str) -> bool:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return False
    body = stripped.strip("|").strip()
    if not body:
        return False
    return bool(re.fullmatch(r"[:\-\s|]+", body))


def parse_markdown_table_line(line: str) -> List[str]:
    return [normalize_text(cell) for cell in line.strip().strip("|").split("|")]


def extract_markdown_table_relations(text: str, bucket: str) -> List[Tuple[str, str, str]]:
    rows: List[Tuple[str, str, str]] = []
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    i = 0

    while i + 2 <= len(lines) - 1:
        header_line = lines[i].strip()
        sep_line = lines[i + 1].strip() if i + 1 < len(lines) else ""
        if not (header_line.startswith("|") and is_markdown_table_separator(sep_line)):
            i += 1
            continue

        headers = parse_markdown_table_line(header_line)
        j = i + 2
        while j < len(lines):
            row_line = lines[j].strip()
            if not row_line.startswith("|"):
                break
            cells = parse_markdown_table_line(row_line)
            if len(cells) >= 2 and len(headers) >= 2:
                subject = clean_entity_surface(cells[0])
                if looks_like_valid_entity(subject):
                    for col_idx in range(1, min(len(headers), len(cells))):
                        field = headers[col_idx]
                        value = cells[col_idx]
                        if not value:
                            continue
                        predicate = infer_predicate_from_field(field)
                        if not predicate:
                            continue
                        for obj in split_field_values(value):
                            if looks_like_valid_entity(obj):
                                rows.append((subject, predicate, obj))
            j += 1
        i = j

    deduped: List[Tuple[str, str, str]] = []
    seen: Set[Tuple[str, str, str]] = set()
    for rel in rows:
        if rel in seen:
            continue
        seen.add(rel)
        deduped.append(rel)
    return deduped


def infer_effective_source_kind(
    source_kind: str,
    text: str,
    source_path: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    path_lower = source_path.replace("\\", "/").lower()
    text_lower = text.lower()
    metadata = metadata or {}

    explicit_hint = str(metadata.get("source_kind_hint", "")).strip().lower()
    if explicit_hint in {"memory", "knowledge"}:
        return explicit_hint

    if "/knowledge/" in path_lower:
        return "knowledge"
    if "/memory/" in path_lower:
        return "memory"

    knowledge_score = keyword_score(text_lower, SOURCE_KIND_KNOWLEDGE_HINTS)
    memory_score = keyword_score(text_lower, SOURCE_KIND_MEMORY_HINTS)

    # 仅在信号明显时覆盖原始 source_kind，避免抖动。
    if source_kind == "memory" and knowledge_score >= 2 and knowledge_score >= memory_score + 1:
        return "knowledge"
    if source_kind == "knowledge" and memory_score >= 2 and memory_score >= knowledge_score + 1:
        return "memory"
    return source_kind


def confidence_from_keyword_scores(
    top_score: int,
    second_score: int,
    total_score: int,
    floor: float = 0.52,
    ceiling: float = 0.9,
) -> float:
    if top_score <= 0:
        return round(max(0.0, floor - 0.04), 2)
    margin = max(0, top_score - second_score)
    concentration = top_score / max(1.0, float(total_score))
    signal = 0.0
    signal += 0.026 * min(top_score, 8)
    signal += 0.016 * min(margin, 6)
    signal += 0.08 * max(0.0, concentration - 0.5)
    signal -= 0.12 * max(0.0, 0.6 - concentration)
    conf = floor + signal
    return round(min(ceiling, max(0.0, conf)), 2)


def extract_mentions(text: str) -> List[str]:
    mentions: List[str] = []
    for m in MENTION_PATTERN.finditer(text):
        name = m.group(1).strip()
        if not name or "|" in name:
            continue
        mentions.append("@" + name)
    return mentions


def extract_key_value_relations(text: str, bucket: str) -> List[Tuple[str, str, str]]:
    rows: List[Tuple[str, str, str]] = []
    user_like_bucket = bucket in {"memory.profile", "memory.preferences", "memory.events"}
    default_subject = "user" if user_like_bucket else "agent"

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        m = re.match(r"^(?:[-*]\s*)?([A-Za-z\u4e00-\u9fff0-9_ ./\-]{1,36})\s*[:：]\s*(.+)$", line)
        if not m:
            continue
        field = normalize_text(m.group(1)).lower()
        value = normalize_text(m.group(2))
        if not value:
            continue

        predicate = infer_predicate_from_field(field)
        if not predicate:
            continue

        # URL 型字段保持整体；其余按分隔符拆分。
        values = [value]
        if "http://" not in value and "https://" not in value:
            values = split_field_values(value)

        for obj in values:
            if not looks_like_valid_entity(obj) and not re.match(r"^https?://\S+$", obj):
                continue
            rows.append((default_subject, normalize_predicate(predicate), obj))

    # 去重，保持顺序
    deduped: List[Tuple[str, str, str]] = []
    seen: Set[Tuple[str, str, str]] = set()
    for rel in rows:
        if rel in seen:
            continue
        seen.add(rel)
        deduped.append(rel)
    return deduped


def extract_tags(text: str) -> List[str]:
    tags: Set[str] = set()

    if "⚡" in text:
        tags.add("flash")

    for m in re.finditer(r"#[\w\-\u4e00-\u9fff]+", text):
        tags.add(m.group(0).lower())

    for mention in extract_mentions(text):
        tags.add(mention.lower())

    for m in re.finditer(r"https?://[^\s)]+", text):
        url = m.group(0)
        domain_match = re.match(r"https?://([^/]+)", url)
        if domain_match:
            tags.add(f"domain:{domain_match.group(1).lower()}")

    return sorted(tags)


def extract_entities(text: str, max_entities: int = 12) -> List[str]:
    counter: Counter[str] = Counter()

    for tok in re.findall(r"[A-Za-z][A-Za-z0-9_\-]{2,}", text):
        low = tok.lower()
        if low in ENGLISH_STOPWORDS:
            continue
        if low in GENERIC_ENTITY_WORDS:
            continue
        if re.fullmatch(r"\d+[a-z]?", low):
            continue
        counter[low] += 1

    for tok in re.findall(r"[\u4e00-\u9fff]{2,8}", text):
        if tok in CHINESE_STOPWORDS:
            continue
        if tok in GENERIC_ENTITY_WORDS:
            continue
        counter[tok] += 1

    for tok in extract_mentions(text):
        counter[tok.lower()] += 2

    entities = [tok for tok, _ in counter.most_common(max_entities)]
    return entities


def bridge_predicate_too_long(bridge: str) -> bool:
    if re.search(r"[\u4e00-\u9fff]", bridge):
        return len(bridge) > 18
    alpha_words = re.findall(r"[A-Za-z]+", bridge)
    if alpha_words:
        return len(alpha_words) > 6 or len(bridge) > 42
    return len(bridge) > 24


def extract_relations(
    text: str,
    entities: Sequence[str],
    max_relations: int = 8,
    bucket: str = "",
) -> List[Tuple[str, str, str]]:
    relations: List[Tuple[str, str, str]] = []
    seen: Set[Tuple[str, str, str]] = set()

    def add_relation(subject: str, predicate: str, obj: str) -> None:
        nonlocal relations
        sub = clean_entity_surface(subject)
        pred = normalize_predicate(predicate)
        ob = clean_entity_surface(obj)
        if not looks_like_valid_entity(sub) or not looks_like_valid_entity(ob):
            return
        if sub.lower() == ob.lower():
            return
        rel = (sub, pred, ob)
        if rel in seen:
            return
        seen.add(rel)
        relations.append(rel)

    pattern_specs = [
        re.compile(
            r"(?P<sub>[A-Za-z\u4e00-\u9fff0-9_@.\-]{2,40})\s*"
            r"(?P<pred>is responsible for|responsible for|in charge of|belongs to|depends on|works on|是|is|are|属于|喜欢|likes|不喜欢|dislikes|使用|uses|依赖|调用|calls|集成|integrates|包含|contains|负责|参与|参与了|主导|主导了|影响|影响了|优于|导致|支持|supports|实现|implements|维护|用于|基于|关联|完成)"
            r"\s*(?P<obj>[A-Za-z\u4e00-\u9fff0-9_@.\-]{2,64})",
            flags=re.IGNORECASE,
        ),
        re.compile(
            r"(?P<sub>[A-Za-z\u4e00-\u9fff0-9_@.\-]{2,40})\s*(?:->|=>)\s*(?P<obj>[A-Za-z\u4e00-\u9fff0-9_@.\-]{2,64})",
            flags=re.IGNORECASE,
        ),
    ]

    text_lines = [normalize_text(line) for line in re.split(r"[\n\r;；。!?！？]", text) if normalize_text(line)]
    for line in text_lines:
        for reg in pattern_specs:
            for m in reg.finditer(line):
                gd = m.groupdict()
                sub = gd.get("sub", "")
                obj = gd.get("obj", "")
                pred = gd.get("pred", "related_to")
                add_relation(sub, pred, obj)
                if len(relations) >= max_relations:
                    return relations

    for sub, pred, obj in extract_markdown_table_relations(text, bucket=bucket):
        add_relation(sub, pred, obj)
        if len(relations) >= max_relations:
            return relations

    for sub, pred, obj in extract_key_value_relations(text, bucket=bucket):
        add_relation(sub, pred, obj)
        if len(relations) >= max_relations:
            return relations

    # 开放式桥接谓词：在同一行内识别“实体A [谓词短语] 实体B”。
    strong_entities = [e for e in entities if looks_like_valid_entity(e)]
    if len(strong_entities) >= 2 and len(relations) < max_relations:
        entity_pairs = [(e, canonical_entity_key(e)) for e in strong_entities[:8]]
        for line in text_lines:
            line_norm = normalize_text(line)
            if line_norm.count("|") >= 2:
                continue
            line_lower = line_norm.lower()
            for sub, sub_key in entity_pairs:
                start = line_lower.find(sub_key)
                if start < 0:
                    continue
                for obj, obj_key in entity_pairs:
                    if sub_key == obj_key:
                        continue
                    end = line_lower.find(obj_key, start + len(sub_key))
                    if end < 0:
                        continue
                    bridge = line_norm[start + len(sub) : end].strip(" :-=>，,。;；")
                    bridge = normalize_text(bridge)
                    if not bridge or bridge_predicate_too_long(bridge):
                        continue
                    if "|" in bridge:
                        continue
                    bridge_lower = bridge.lower()
                    if bridge_lower in RELATION_BRIDGE_STOPWORDS:
                        continue
                    if not re.search(r"[A-Za-z\u4e00-\u9fff]", bridge):
                        continue
                    if re.search(r"https?://", bridge, flags=re.IGNORECASE):
                        continue
                    pred_norm = normalize_predicate(bridge_lower)
                    if pred_norm in {"related_to", "cooccurs_with"}:
                        continue
                    add_relation(sub, pred_norm, obj)
                    if len(relations) >= max_relations:
                        return relations

    # 低价值共现边只做保底，且仅在实体质量较高时触发，减少噪音。
    if not relations and len(strong_entities) >= 3 and bucket not in {"memory.profile", "memory.preferences"}:
        head = strong_entities[0]
        for tail in strong_entities[1:3]:
            add_relation(head, "cooccurs_with", tail)
            if len(relations) >= max_relations:
                break

    return relations


def normalize_record(raw: SourceRecord, formation_mode: str = "manual") -> NormalizedRecord:
    structured_text = normalize_text_keep_lines(raw.text)
    memory_policy = infer_memory_policy(raw.metadata, structured_text)
    structured_text, had_private_redaction = redact_private_spans(structured_text)
    cleaned = normalize_text(structured_text)
    effective_source_kind = infer_effective_source_kind(
        source_kind=raw.source_kind,
        text=cleaned,
        source_path=raw.source_path,
        metadata=raw.metadata,
    )
    bucket, confidence = classify_bucket(effective_source_kind, cleaned, raw.source_path, metadata=raw.metadata)
    tags = extract_tags(structured_text)
    entities = extract_entities(structured_text)
    relations = extract_relations(structured_text, entities, bucket=bucket)
    timestamp = raw.timestamp or detect_timestamp(structured_text)
    ttl, heat = infer_ttl_and_heat(raw.source_path, bucket)
    memory_function = infer_memory_function(bucket)
    trust_tier = infer_trust_tier(raw.metadata, raw.source_path)

    content_hash = sha256_text(f"{raw.source_kind}|{cleaned}")
    record_id = sha256_text(f"{raw.source_path}|{raw.locator}|{content_hash}")[:16]
    normalized_meta = dict(raw.metadata)
    if effective_source_kind != raw.source_kind:
        normalized_meta.setdefault("source_kind_original", raw.source_kind)
        normalized_meta.setdefault("source_kind_inferred", effective_source_kind)
    if raw.section_title:
        normalized_meta.setdefault("section_title", raw.section_title)
    if had_private_redaction:
        normalized_meta["contains_private_redaction"] = True
    normalized_meta["memory_policy"] = memory_policy

    return NormalizedRecord(
        record_id=record_id,
        content_hash=content_hash,
        source_kind=effective_source_kind,
        source_path=raw.source_path,
        locator=raw.locator,
        bucket=bucket,
        text=cleaned,
        timestamp=timestamp,
        tags=tags,
        entities=entities,
        relations=relations,
        confidence=confidence,
        metadata=normalized_meta,
        created_at=utc_now(),
        version=1,
        ttl=ttl,
        heat=heat,
        last_accessed=utc_today(),
        memory_function=memory_function,
        formation_mode=formation_mode,
        trust_tier=trust_tier,
        memory_policy=memory_policy,
    )


def confidence_gate(record: NormalizedRecord) -> Tuple[bool, str]:
    min_confidence = 0.60
    min_text_length = 30
    if record.bucket == "memory.working" or record.memory_function == "working":
        min_confidence = 0.35
        min_text_length = 8

    if record.confidence < min_confidence:
        return False, "low_confidence"
    if len(record.text.strip()) < min_text_length:
        return False, "text_too_short"
    if re.fullmatch(r"[\W\d\s]+", record.text.strip(), flags=re.UNICODE):
        return False, "non_informative_text"
    return True, ""


def apply_memory_policy(
    records: Sequence[NormalizedRecord],
) -> Tuple[List[NormalizedRecord], List[Dict[str, Any]]]:
    accepted: List[NormalizedRecord] = []
    skipped: List[Dict[str, Any]] = []

    for rec in records:
        policy = (rec.memory_policy or "persist").strip().lower()
        if policy == "private":
            skipped.append(
                {
                    "record_id": rec.record_id,
                    "source_kind": rec.source_kind,
                    "source_path": rec.source_path,
                    "locator": rec.locator,
                    "reason": "policy_private",
                    "memory_policy": policy,
                    "text_preview": rec.text[:180],
                    "created_at": rec.created_at,
                }
            )
            continue

        if policy == "ephemeral":
            rec.bucket = "memory.working"
            rec.memory_function = "working"
            rec.ttl = "7d"
            rec.heat = "warm"
            rec.metadata["policy_applied"] = "ephemeral_to_working"

        accepted.append(rec)

    return accepted, skipped


def apply_confidence_gate(
    records: Sequence[NormalizedRecord],
) -> Tuple[List[NormalizedRecord], List[Dict[str, Any]]]:
    accepted: List[NormalizedRecord] = []
    rejected: List[Dict[str, Any]] = []

    for rec in records:
        ok, reason = confidence_gate(rec)
        if ok:
            accepted.append(rec)
            continue
        rejected.append(
            {
                "record_id": rec.record_id,
                "source_kind": rec.source_kind,
                "source_path": rec.source_path,
                "locator": rec.locator,
                "reason": reason,
                "confidence": rec.confidence,
                "text_preview": rec.text[:180],
                "created_at": rec.created_at,
            }
        )

    return accepted, rejected


def confidence_gate_l2_row(row: Dict[str, Any]) -> Tuple[bool, str]:
    text = normalize_text(str(row.get("text", "")))
    bucket = str(row.get("bucket", ""))
    memory_function = str(row.get("memory_function", ""))
    min_confidence = 0.60
    min_text_length = 30
    if bucket == "memory.working" or memory_function == "working":
        min_confidence = 0.35
        min_text_length = 8

    if len(text) < min_text_length:
        return False, "text_too_short"
    if re.fullmatch(r"[\W\d\s]+", text, flags=re.UNICODE):
        return False, "non_informative_text"

    conf_val = row.get("confidence")
    if conf_val is None:
        return True, ""
    try:
        conf = float(conf_val)
    except (TypeError, ValueError):
        return True, ""
    if conf < min_confidence:
        return False, "low_confidence"
    return True, ""


def ensure_arch_layout(target_root: Path) -> Dict[str, Path]:
    target_root.mkdir(parents=True, exist_ok=True)

    required_dirs = {
        "viking/user/memories",
        "viking/user/knowledge",
        "viking/agent/skills",
        "viking/archive",
        "viking/session/working",
        "layers",
        "state",
        REPORTS_DIR,
    }
    for d in required_dirs:
        (target_root / d).mkdir(parents=True, exist_ok=True)

    paths = {bucket: target_root / rel for bucket, rel in BUCKET_FILES.items()}
    paths["entity"] = target_root / ENTITY_FILE
    paths["relation"] = target_root / RELATION_FILE
    paths["relation_decisions"] = target_root / RELATION_DECISIONS_FILE
    paths["l2"] = target_root / L2_FILE
    paths["l1"] = target_root / L1_FILE
    paths["l0"] = target_root / L0_FILE
    paths["retrieval_hints"] = target_root / RETRIEVAL_HINTS_FILE
    paths["retrieval_protocol"] = target_root / RETRIEVAL_PROTOCOL_FILE
    paths["profile_snapshot"] = target_root / PROFILE_SNAPSHOT_FILE
    paths["preferences_snapshot"] = target_root / PREFERENCES_SNAPSHOT_FILE
    paths["hash_state"] = target_root / HASH_STATE_FILE
    paths["file_state"] = target_root / FILE_STATE_FILE
    paths["archive"] = target_root / ARCHIVE_FILE
    paths["reports_dir"] = target_root / REPORTS_DIR
    return paths


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def write_jsonl(path: Path, rows: Sequence[Dict[str, Any]], append: bool = True) -> None:
    if not rows:
        return
    mode = "a" if append else "w"
    with path.open(mode, encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_json(path: Path, obj: Any) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def load_record_hashes(path: Path) -> Set[str]:
    if not path.exists():
        return set()
    hashes = set()
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            h = line.strip()
            if h:
                hashes.add(h)
    return hashes


def save_record_hashes(path: Path, hashes: Set[str]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for h in sorted(hashes):
            f.write(h + "\n")


def load_file_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"version": 1, "files": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"version": 1, "files": {}}


def save_file_state(path: Path, state: Dict[str, Any]) -> None:
    write_json(path, state)


def fingerprint_file(path: Path) -> Dict[str, Any]:
    sha = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            block = f.read(1024 * 1024)
            if not block:
                break
            sha.update(block)

    stat = path.stat()
    return {
        "sha256": sha.hexdigest(),
        "size": stat.st_size,
        "mtime": int(stat.st_mtime),
    }


def should_reingest(path: Path, state: Dict[str, Any]) -> bool:
    fp = fingerprint_file(path)
    old = state.get("files", {}).get(str(path))
    if not old:
        return True
    return (
        old.get("sha256") != fp["sha256"]
        or old.get("size") != fp["size"]
        or old.get("mtime") != fp["mtime"]
    )


def update_file_state(path: Path, state: Dict[str, Any]) -> None:
    fp = fingerprint_file(path)
    files = state.setdefault("files", {})
    files[str(path)] = {
        **fp,
        "last_ingested_at": utc_now(),
    }


def bucket_rows(records: Sequence[NormalizedRecord]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for rec in records:
        grouped[rec.bucket].append(rec.to_json())
    return grouped


def build_entity_rows(records: Sequence[NormalizedRecord]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for rec in records:
        for entity in rec.entities:
            rows.append(
                {
                    "id": sha256_text(f"entity|{entity}|{rec.record_id}")[:16],
                    "entity": entity,
                    "record_id": rec.record_id,
                    "source_kind": rec.source_kind,
                    "source_path": rec.source_path,
                    "bucket": rec.bucket,
                    "created_at": rec.created_at,
                }
            )
    return rows


def build_relation_rows(records: Sequence[NormalizedRecord]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for rec in records:
        for sub, pred, obj in rec.relations:
            rows.append(
                {
                    "id": sha256_text(f"rel|{sub}|{pred}|{obj}|{rec.record_id}")[:16],
                    "subject": sub,
                    "predicate": pred,
                    "object": obj,
                    "record_id": rec.record_id,
                    "source_kind": rec.source_kind,
                    "source_path": rec.source_path,
                    "bucket": rec.bucket,
                    "created_at": rec.created_at,
                }
            )
    return rows


def normalize_l2_row_defaults(row: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(row)
    normalized.setdefault("version", 1)
    normalized.setdefault("ttl", "30d")
    normalized.setdefault("heat", "warm")
    normalized.setdefault("last_accessed", utc_today())
    normalized.setdefault("created_at", utc_now())
    normalized.setdefault("tags", [])
    normalized.setdefault("entities", [])
    normalized.setdefault("relations", [])
    bucket = str(normalized.get("bucket", ""))
    normalized.setdefault("memory_function", infer_memory_function(bucket))
    normalized.setdefault("formation_mode", "bootstrap")
    metadata = normalized.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        normalized["metadata"] = metadata
    normalized.setdefault("trust_tier", infer_trust_tier(metadata, str(normalized.get("source_path", ""))))
    normalized.setdefault("memory_policy", str(metadata.get("memory_policy") or "persist").lower())
    return normalized


def dedupe_rows_by_keys(rows: Sequence[Dict[str, Any]], keys: Sequence[str]) -> List[Dict[str, Any]]:
    seen: Set[str] = set()
    output: List[Dict[str, Any]] = []
    for row in rows:
        key = ""
        for k in keys:
            val = row.get(k)
            if val:
                key = f"{k}:{val}"
                break
        if not key:
            key = "raw:" + sha256_text(json.dumps(row, ensure_ascii=False, sort_keys=True))
        if key in seen:
            continue
        seen.add(key)
        output.append(row)
    return output


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def compute_relation_conflict_score(row: Dict[str, Any], now_dt: Optional[datetime] = None) -> float:
    now_dt = now_dt or datetime.now(timezone.utc)
    support = safe_int(row.get("support_count"), 0)
    avg_conf = safe_float(row.get("avg_confidence"), 0.75)
    latest_version = safe_int(row.get("latest_version"), 1)

    last_seen_dt = parse_iso_datetime(row.get("last_seen_at"))
    age_days = 999
    if last_seen_dt is not None:
        age_days = max(0, int((now_dt - last_seen_dt).total_seconds() // 86400))

    support_component = min(20, support) * 1.6
    confidence_component = max(0.0, min(1.0, avg_conf)) * 2.8
    recency_component = max(0.0, 120 - min(365, age_days)) / 120 * 1.6
    version_component = min(12, max(1, latest_version)) * 0.09

    return round(support_component + confidence_component + recency_component + version_component, 4)


def resolve_relation_conflicts(
    relation_rows: Sequence[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    grouped: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for row in relation_rows:
        sub = str(row.get("subject", ""))
        pred = str(row.get("predicate", ""))
        group_key = (canonical_entity_key(sub), normalize_predicate(pred))
        if not group_key[0] or not group_key[1]:
            continue
        grouped[group_key].append(dict(row))

    now_dt = datetime.now(timezone.utc)
    relation_out: List[Dict[str, Any]] = []
    decisions: List[Dict[str, Any]] = []

    for (sub_key, pred_key), members in grouped.items():
        group_id = sha256_text(f"relgrp|{sub_key}|{pred_key}")[:16]

        for m in members:
            m["conflict_score"] = compute_relation_conflict_score(m, now_dt=now_dt)

        members_sorted = sorted(
            members,
            key=lambda r: (
                safe_float(r.get("conflict_score"), 0.0),
                safe_int(r.get("support_count"), 0),
                safe_float(r.get("avg_confidence"), 0.0),
                safe_int(r.get("latest_version"), 0),
                str(r.get("last_seen_at", "")),
                canonical_entity_key(str(r.get("object", ""))),
            ),
            reverse=True,
        )

        top = members_sorted[0]
        top_score = safe_float(top.get("conflict_score"), 0.0)
        second_score = safe_float(members_sorted[1].get("conflict_score"), 0.0) if len(members_sorted) > 1 else 0.0
        score_margin = round(top_score - second_score, 4)
        score_sum = sum(safe_float(m.get("conflict_score"), 0.0) for m in members_sorted)
        resolution_conf = round(top_score / max(score_sum, 1e-6), 4)
        status = "resolved"
        if len(members_sorted) > 1 and score_margin < 0.45 and resolution_conf < 0.62:
            status = "weakly_resolved"

        primary_object = str(top.get("object", ""))
        subject = str(top.get("subject", ""))
        predicate = normalize_predicate(str(top.get("predicate", "")))

        for rank, m in enumerate(members_sorted, start=1):
            row_out = dict(m)
            row_out["subject"] = clean_entity_surface(str(row_out.get("subject", "")))
            row_out["predicate"] = normalize_predicate(str(row_out.get("predicate", "")))
            row_out["object"] = clean_entity_surface(str(row_out.get("object", "")))
            row_out["relation_group_id"] = group_id
            row_out["object_rank"] = rank
            row_out["is_primary"] = rank == 1
            row_out["primary_object"] = primary_object
            row_out["conflict_size"] = len(members_sorted)
            row_out["resolution_confidence"] = resolution_conf
            row_out["conflict_margin"] = score_margin
            row_out["conflict_status"] = status if rank == 1 else "alternate"
            row_out["updated_at"] = utc_now()
            relation_out.append(row_out)

        alternatives = [
            {
                "object": str(m.get("object", "")),
                "conflict_score": safe_float(m.get("conflict_score"), 0.0),
                "support_count": safe_int(m.get("support_count"), 0),
                "avg_confidence": safe_float(m.get("avg_confidence"), 0.0),
                "last_seen_at": str(m.get("last_seen_at", "")),
                "latest_version": safe_int(m.get("latest_version"), 1),
                "is_primary": idx == 0,
            }
            for idx, m in enumerate(members_sorted[:8])
        ]

        decisions.append(
            {
                "id": group_id,
                "subject": subject,
                "predicate": predicate,
                "primary_object": primary_object,
                "status": status,
                "conflict_size": len(members_sorted),
                "resolution_confidence": resolution_conf,
                "score_margin": score_margin,
                "alternatives": alternatives,
                "updated_at": utc_now(),
            }
        )

    relation_out.sort(
        key=lambda r: (
            safe_int(r.get("is_primary"), 0),
            safe_float(r.get("conflict_score"), 0.0),
            safe_int(r.get("support_count"), 0),
            str(r.get("last_seen_at", "")),
        ),
        reverse=True,
    )
    decisions.sort(
        key=lambda d: (
            safe_float(d.get("resolution_confidence"), 0.0),
            safe_float(d.get("score_margin"), 0.0),
            -safe_int(d.get("conflict_size"), 0),
        ),
        reverse=True,
    )
    return relation_out, decisions


def build_entity_rows_from_l2_rows(l2_rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for row in l2_rows:
        record_id = str(row.get("id", ""))
        source_kind = row.get("source_kind")
        source_path = row.get("source_path")
        bucket = row.get("bucket")
        created_at = row.get("created_at") or utc_now()
        for entity in row.get("entities", []) or []:
            ent = str(entity).strip()
            if not ent:
                continue
            rows.append(
                {
                    "id": sha256_text(f"entity|{ent}|{record_id}")[:16],
                    "entity": ent,
                    "record_id": record_id,
                    "source_kind": source_kind,
                    "source_path": source_path,
                    "bucket": bucket,
                    "created_at": created_at,
                }
            )
    return dedupe_rows_by_keys(rows, keys=("id",))


def build_relation_rows_from_l2_rows(l2_rows: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    aggregated: Dict[Tuple[str, str, str], Dict[str, Any]] = {}

    def dict_inc(counter: Dict[str, int], key: str) -> None:
        counter[key] = counter.get(key, 0) + 1

    for row in l2_rows:
        record_id = str(row.get("id", ""))
        source_kind = str(row.get("source_kind", "unknown"))
        source_path = str(row.get("source_path", ""))
        bucket = str(row.get("bucket", "unknown"))
        created_at = str(row.get("created_at") or utc_now())
        version = int(row.get("version", 1) or 1)
        confidence = safe_float(row.get("confidence"), 0.75)

        relations = row.get("relations") or []
        explicit_rel_added = False
        relation_candidates: List[Tuple[str, str, str]] = []
        for rel in relations:
            if not isinstance(rel, dict):
                continue
            sub = str(rel.get("subject", "")).strip()
            pred = str(rel.get("predicate", "")).strip()
            obj = str(rel.get("object", "")).strip()
            if not sub or not pred or not obj:
                continue
            explicit_rel_added = True
            relation_candidates.append((sub, pred, obj))

        # 没有显式关系时，保底生成共现边，避免图谱层完全为空。
        if not explicit_rel_added:
            entities = [str(e).strip() for e in (row.get("entities") or []) if str(e).strip()]
            if len(entities) >= 2:
                head = entities[0]
                for tail in entities[1:4]:
                    relation_candidates.append((head, "cooccurs_with", tail))

        for sub_raw, pred_raw, obj_raw in relation_candidates:
            sub = clean_entity_surface(sub_raw)
            pred = normalize_predicate(pred_raw)
            obj = clean_entity_surface(obj_raw)
            if not looks_like_valid_entity(sub) or not looks_like_valid_entity(obj):
                continue
            if sub.lower() == obj.lower():
                continue

            key = (canonical_entity_key(sub), pred, canonical_entity_key(obj))
            row_obj = aggregated.get(key)
            if row_obj is None:
                row_obj = {
                    "id": sha256_text(f"rel|{canonical_entity_key(sub)}|{pred}|{canonical_entity_key(obj)}")[:16],
                    "subject": sub,
                    "predicate": pred,
                    "object": obj,
                    "support_count": 0,
                    "record_ids": [],
                    "source_paths": [],
                    "source_kind_counts": {},
                    "bucket_counts": {},
                    "first_seen_at": created_at,
                    "last_seen_at": created_at,
                    "latest_version": version,
                    "confidence_sum": 0.0,
                    "confidence_count": 0,
                    "max_confidence": 0.0,
                }
                aggregated[key] = row_obj

            row_obj["support_count"] += 1
            if record_id and record_id not in row_obj["record_ids"] and len(row_obj["record_ids"]) < 40:
                row_obj["record_ids"].append(record_id)
            if source_path and source_path not in row_obj["source_paths"] and len(row_obj["source_paths"]) < 40:
                row_obj["source_paths"].append(source_path)

            dict_inc(row_obj["source_kind_counts"], source_kind)
            dict_inc(row_obj["bucket_counts"], bucket)

            if created_at < row_obj["first_seen_at"]:
                row_obj["first_seen_at"] = created_at
            if created_at > row_obj["last_seen_at"]:
                row_obj["last_seen_at"] = created_at
            if version > int(row_obj.get("latest_version", 1)):
                row_obj["latest_version"] = version
            row_obj["confidence_sum"] = safe_float(row_obj.get("confidence_sum"), 0.0) + confidence
            row_obj["confidence_count"] = safe_int(row_obj.get("confidence_count"), 0) + 1
            if confidence > safe_float(row_obj.get("max_confidence"), 0.0):
                row_obj["max_confidence"] = confidence

    output: List[Dict[str, Any]] = []
    for _, item in aggregated.items():
        source_kind_counts = item.get("source_kind_counts", {})
        bucket_counts = item.get("bucket_counts", {})
        primary_source_kind = (
            sorted(source_kind_counts.items(), key=lambda kv: kv[1], reverse=True)[0][0]
            if source_kind_counts
            else "unknown"
        )
        primary_bucket = (
            sorted(bucket_counts.items(), key=lambda kv: kv[1], reverse=True)[0][0]
            if bucket_counts
            else "unknown"
        )
        confidence_sum = safe_float(item.get("confidence_sum"), 0.0)
        confidence_count = max(1, safe_int(item.get("confidence_count"), 1))
        avg_confidence = round(confidence_sum / confidence_count, 4)
        output.append(
            {
                "id": item["id"],
                "subject": item["subject"],
                "predicate": item["predicate"],
                "object": item["object"],
                "support_count": item["support_count"],
                "avg_confidence": avg_confidence,
                "max_confidence": round(safe_float(item.get("max_confidence"), 0.0), 4),
                "record_ids": item["record_ids"],
                "source_paths": item["source_paths"],
                "source_kind": primary_source_kind,
                "bucket": primary_bucket,
                "source_kind_counts": source_kind_counts,
                "bucket_counts": bucket_counts,
                "first_seen_at": item["first_seen_at"],
                "last_seen_at": item["last_seen_at"],
                "latest_version": item["latest_version"],
                "updated_at": utc_now(),
            }
        )

    output.sort(key=lambda r: (int(r.get("support_count", 0)), str(r.get("last_seen_at", ""))), reverse=True)
    return output


def rebuild_materialized_views_from_l2(paths: Dict[str, Path], l2_rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    normalized_rows_raw, policy_skipped_rows = apply_memory_policy_to_l2_rows(l2_rows)
    normalized_rows: List[Dict[str, Any]] = []
    rejected_rows: List[Dict[str, Any]] = []

    for row in normalized_rows_raw:
        ok, reason = confidence_gate_l2_row(row)
        if ok:
            normalized_rows.append(row)
            continue
        rejected_rows.append(
            {
                "record_id": row.get("id"),
                "source_kind": row.get("source_kind"),
                "source_path": row.get("source_path"),
                "locator": row.get("locator"),
                "reason": reason,
                "confidence": row.get("confidence"),
                "text_preview": str(row.get("text", ""))[:180],
                "created_at": utc_now(),
            }
        )

    write_jsonl(paths["l2"], normalized_rows, append=False)

    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in normalized_rows:
        bucket = row.get("bucket")
        if bucket in BUCKET_FILES:
            grouped[bucket].append(row)

    for bucket in BUCKET_FILES:
        rows = dedupe_rows_by_keys(grouped.get(bucket, []), keys=("id", "content_hash"))
        if rows:
            write_jsonl(paths[bucket], rows, append=False)
        else:
            paths[bucket].parent.mkdir(parents=True, exist_ok=True)
            paths[bucket].write_text("", encoding="utf-8")

    entity_rows = build_entity_rows_from_l2_rows(normalized_rows)
    relation_rows_raw = build_relation_rows_from_l2_rows(normalized_rows)
    relation_rows, relation_decisions = resolve_relation_conflicts(relation_rows_raw)
    write_jsonl(paths["entity"], entity_rows, append=False)
    write_jsonl(paths["relation"], relation_rows, append=False)
    write_jsonl(paths["relation_decisions"], relation_decisions, append=False)

    hashes = {str(row.get("content_hash")) for row in normalized_rows if row.get("content_hash")}
    save_record_hashes(paths["hash_state"], hashes)
    l0 = rebuild_layers(paths)

    rejected_written = 0
    rejected_report_path = ""
    if rejected_rows:
        rejected_written = len(rejected_rows)
        rejected_path = paths["reports_dir"] / f"rejected_rebuild_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.jsonl"
        write_jsonl(rejected_path, rejected_rows, append=False)
        rejected_report_path = str(rejected_path)

    policy_skipped_written = 0
    policy_skipped_report_path = ""
    if policy_skipped_rows:
        policy_skipped_written = len(policy_skipped_rows)
        policy_path = paths["reports_dir"] / f"policy_skipped_rebuild_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.jsonl"
        write_jsonl(policy_path, policy_skipped_rows, append=False)
        policy_skipped_report_path = str(policy_path)

    return {
        "records_written": len(normalized_rows),
        "entities_written": len(entity_rows),
        "relations_written": len(relation_rows),
        "relation_decisions_written": len(relation_decisions),
        "rejected_rows": rejected_written,
        "rejected_report_path": rejected_report_path,
        "policy_skipped_rows": policy_skipped_written,
        "policy_skipped_report_path": policy_skipped_report_path,
        "l0_after": l0,
    }


def clear_arch_files(paths: Dict[str, Path]) -> None:
    for key, path in paths.items():
        if key in {"reports_dir"}:
            continue
        if key.endswith("state"):
            continue
        if path.exists() and path.is_file():
            path.unlink()


def normalize_snapshot_key(raw_key: str) -> str:
    normalized = normalize_text(str(raw_key)).lower()
    normalized = normalized.replace("_", " ").replace("-", " ").strip()
    return re.sub(r"\s+", " ", normalized)


def canonical_snapshot_field(raw_key: str, alias_map: Dict[str, Set[str]]) -> Optional[str]:
    key = normalize_snapshot_key(raw_key)
    if not key:
        return None
    for canonical, aliases in alias_map.items():
        if key == canonical or key in aliases:
            return canonical
    return None


def snapshot_rank(row: Dict[str, Any]) -> Tuple[int, int, str, float]:
    trust = TRUST_TIER_ORDER.get(str(row.get("trust_tier", "extracted")).lower(), 1)
    version = safe_int(row.get("version"), 1)
    dt = parse_iso_datetime(row.get("last_accessed")) or parse_iso_datetime(row.get("created_at"))
    dt_key = dt.isoformat() if dt else ""
    confidence = safe_float(row.get("confidence"), 0.0)
    return trust, version, dt_key, confidence


def build_snapshot_entry(field: str, value: Any, row: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "field": field,
        "value": value,
        "source_record_id": row.get("id"),
        "source_path": row.get("source_path"),
        "locator": row.get("locator"),
        "version": row.get("version"),
        "trust_tier": row.get("trust_tier"),
        "confidence": row.get("confidence"),
        "updated_at": row.get("last_accessed") or row.get("created_at") or "",
    }


def extract_snapshot_kv_candidates(
    row: Dict[str, Any],
    alias_map: Dict[str, Set[str]],
) -> List[Tuple[str, str]]:
    candidates: List[Tuple[str, str]] = []
    seen: Set[Tuple[str, str]] = set()

    metadata = row.get("metadata")
    if isinstance(metadata, dict):
        frontmatter_fields = metadata.get("frontmatter_fields")
        if isinstance(frontmatter_fields, dict):
            for key, value in frontmatter_fields.items():
                canonical = canonical_snapshot_field(str(key), alias_map)
                normalized_value = normalize_text_keep_lines(str(value)).strip()
                pair = (canonical or "", normalized_value)
                if canonical and normalized_value and pair not in seen:
                    candidates.append((canonical, normalized_value))
                    seen.add(pair)

    text = str(row.get("text", ""))
    for line in text.splitlines():
        if line.startswith("#") or line.startswith("|"):
            continue
        match = re.match(r"^\s*([^:：]{1,60})\s*[:：]\s*(.+?)\s*$", line)
        if not match:
            continue
        key = match.group(1)
        value = match.group(2)
        canonical = canonical_snapshot_field(key, alias_map)
        value = normalize_text_keep_lines(value).strip()
        pair = (canonical or "", value)
        if canonical and value and pair not in seen:
            candidates.append((canonical, value))
            seen.add(pair)
    return candidates


def apply_memory_policy_to_l2_rows(
    rows: Sequence[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    accepted: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    for raw_row in rows:
        row = normalize_l2_row_defaults(raw_row)
        metadata = row.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
            row["metadata"] = metadata

        policy = str(row.get("memory_policy") or metadata.get("memory_policy") or "persist").strip().lower()
        if policy not in ALLOWED_MEMORY_POLICIES:
            policy = "persist"
        row["memory_policy"] = policy
        metadata["memory_policy"] = policy

        if policy == "private":
            skipped.append(
                {
                    "record_id": row.get("id"),
                    "source_kind": row.get("source_kind"),
                    "source_path": row.get("source_path"),
                    "locator": row.get("locator"),
                    "reason": "policy_private_rebuild",
                    "memory_policy": policy,
                    "text_preview": str(row.get("text", ""))[:180],
                    "created_at": utc_now(),
                }
            )
            continue

        if policy == "ephemeral":
            row["bucket"] = "memory.working"
            row["memory_function"] = "working"
            row["ttl"] = "7d"
            row["heat"] = "warm"
            metadata["policy_applied"] = "ephemeral_to_working"

        accepted.append(row)

    return accepted, skipped


def build_profile_snapshot(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    profile_rows = [r for r in rows if r.get("bucket") == "memory.profile"]
    best_fields: Dict[str, Dict[str, Any]] = {}
    best_rank: Dict[str, Tuple[int, int, str, float]] = {}

    relation_field_map = {
        "name_is": "name",
        "role_is": "role",
        "lives_in": "location",
    }

    for row in profile_rows:
        for field, value in extract_snapshot_kv_candidates(row, PROFILE_FIELD_ALIASES):
            rank = snapshot_rank(row)
            if field not in best_fields or rank > best_rank[field]:
                best_fields[field] = build_snapshot_entry(field, value, row)
                best_rank[field] = rank

        for rel in row.get("relations", []):
            if not isinstance(rel, dict):
                continue
            field = relation_field_map.get(str(rel.get("predicate", "")).lower())
            value = normalize_text(str(rel.get("object", "")))
            if field and value:
                rank = snapshot_rank(row)
                if field not in best_fields or rank > best_rank[field]:
                    best_fields[field] = build_snapshot_entry(field, value, row)
                    best_rank[field] = rank

    return {
        "schema": "adaptr-v1",
        "generated_at": utc_now(),
        "record_count": len(profile_rows),
        "fields": best_fields,
    }


def build_preferences_snapshot(rows: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    pref_rows = [r for r in rows if r.get("bucket") == "memory.preferences"]
    scalar_fields: Dict[str, Dict[str, Any]] = {}
    scalar_rank: Dict[str, Tuple[int, int, str, float]] = {}
    list_fields: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    seen_list_values: Dict[str, Set[str]] = defaultdict(set)

    relation_field_map = {
        "likes": "likes",
        "dislikes": "dislikes",
        "uses": "favorite_tools",
    }

    list_like_fields = {"likes", "dislikes", "favorite_tools"}

    for row in pref_rows:
        for field, value in extract_snapshot_kv_candidates(row, PREFERENCE_FIELD_ALIASES):
            rank = snapshot_rank(row)
            if field in list_like_fields:
                norm_value = normalize_text(value)
                if norm_value and norm_value not in seen_list_values[field]:
                    list_fields[field].append(build_snapshot_entry(field, norm_value, row))
                    seen_list_values[field].add(norm_value)
            else:
                if field not in scalar_fields or rank > scalar_rank[field]:
                    scalar_fields[field] = build_snapshot_entry(field, value, row)
                    scalar_rank[field] = rank

        for rel in row.get("relations", []):
            if not isinstance(rel, dict):
                continue
            field = relation_field_map.get(str(rel.get("predicate", "")).lower())
            value = normalize_text(str(rel.get("object", "")))
            if not field or not value:
                continue
            if field in list_like_fields:
                if value not in seen_list_values[field]:
                    list_fields[field].append(build_snapshot_entry(field, value, row))
                    seen_list_values[field].add(value)
            else:
                rank = snapshot_rank(row)
                if field not in scalar_fields or rank > scalar_rank[field]:
                    scalar_fields[field] = build_snapshot_entry(field, value, row)
                    scalar_rank[field] = rank

    merged_fields: Dict[str, Any] = dict(scalar_fields)
    for field, entries in list_fields.items():
        merged_fields[field] = {
            "field": field,
            "values": [e["value"] for e in entries[:20]],
            "sources": entries[:20],
        }

    return {
        "schema": "adaptr-v1",
        "generated_at": utc_now(),
        "record_count": len(pref_rows),
        "fields": merged_fields,
    }


def build_retrieval_protocol(
    rows: Sequence[Dict[str, Any]],
    bucket_hints: Dict[str, Dict[str, Any]],
    profile_snapshot: Dict[str, Any],
    preferences_snapshot: Dict[str, Any],
) -> Dict[str, Any]:
    bucket_to_flow = {
        "memory.profile": ["profile_snapshot", "l2_records"],
        "memory.preferences": ["preferences_snapshot", "l2_records"],
        "memory.events": ["retrieval_hints", "l1_overview", "l2_records"],
        "memory.agent_skill": ["retrieval_hints", "l1_overview", "l2_records"],
        "memory.working": ["l2_records"],
        "knowledge.facts": ["retrieval_hints", "l1_overview", "l2_records"],
        "knowledge.procedures": ["retrieval_hints", "l1_overview", "l2_records"],
        "knowledge.references": ["retrieval_hints", "l1_overview", "l2_records"],
    }
    bucket_query_patterns = {
        "memory.profile": ["who am i", "身份", "背景", "用户画像"],
        "memory.preferences": ["喜欢什么", "偏好", "习惯"],
        "memory.events": ["发生了什么", "最近事件", "timeline"],
        "memory.agent_skill": ["怎么做", "成功做法", "skill"],
        "memory.working": ["当前待办", "next step", "session state"],
        "knowledge.facts": ["事实", "结论", "研究发现"],
        "knowledge.procedures": ["怎么做", "步骤", "流程"],
        "knowledge.references": ["文档", "链接", "api"],
    }

    memory_function_flows = {
        "factual": ["profile_snapshot", "preferences_snapshot", "retrieval_hints", "l2_records"],
        "experiential": ["retrieval_hints", "l1_overview", "l2_records"],
        "working": ["l2_records"],
    }

    return {
        "schema": "adaptr-v1",
        "generated_at": utc_now(),
        "default_flow": ["profile_snapshot", "preferences_snapshot", "retrieval_hints", "l1_overview", "l2_records"],
        "memory_function_flows": memory_function_flows,
        "snapshots": {
            "profile_fields": len(profile_snapshot.get("fields", {})),
            "preferences_fields": len(preferences_snapshot.get("fields", {})),
        },
        "stop_rules": {
            "profile_snapshot": "若已命中需要的当前态字段，则停止，不继续展开 L2。",
            "preferences_snapshot": "若偏好字段已充分覆盖问题所需信息，则停止，不继续展开 L2。",
            "l1_overview": "若 bucket 摘要已经足够回答路由问题，则优先停在 L1，仅在需要证据时打开 L2。",
        },
        "bucket_entrypoints": {
            bucket: {
                "preferred_entrypoints": bucket_to_flow.get(bucket, ["retrieval_hints", "l2_records"]),
                "query_patterns": bucket_query_patterns.get(bucket, []),
                "recommended_keywords": bucket_hints.get(bucket, {}).get("recommended_keywords", []),
            }
            for bucket in BUCKET_FILES
        },
        "record_count": len(rows),
    }


def rebuild_layers(paths: Dict[str, Path]) -> Dict[str, Any]:
    rows = read_jsonl(paths["l2"])
    relation_rows = read_jsonl(paths["relation"])
    relation_decisions = read_jsonl(paths["relation_decisions"])
    by_bucket = Counter()
    by_source_kind = Counter()
    by_memory_function = Counter()
    by_trust_tier = Counter()
    by_memory_policy = Counter()
    entity_counter = Counter()
    tag_counter = Counter()
    heat_score_by_bucket = Counter()
    last_updated_by_bucket: Dict[str, str] = {}
    flash_records: List[Dict[str, Any]] = []

    for row in rows:
        bucket = row.get("bucket", "unknown")
        source_kind = row.get("source_kind", "unknown")
        by_bucket[bucket] += 1
        by_source_kind[source_kind] += 1
        by_memory_function[str(row.get("memory_function", "unknown"))] += 1
        by_trust_tier[str(row.get("trust_tier", "unknown"))] += 1
        by_memory_policy[str(row.get("memory_policy", "persist"))] += 1
        heat = str(row.get("heat", "warm")).lower()
        heat_score_by_bucket[bucket] += {"hot": 3, "warm": 2, "cold": 1}.get(heat, 1)
        updated_at = str(row.get("last_accessed") or row.get("created_at") or "")
        if updated_at and (bucket not in last_updated_by_bucket or updated_at > last_updated_by_bucket[bucket]):
            last_updated_by_bucket[bucket] = updated_at

        for tag in row.get("tags", []):
            tag_counter[tag] += 1
        for entity in row.get("entities", []):
            entity_counter[entity] += 1

        row_tags = row.get("tags", [])
        row_text = str(row.get("text", ""))
        if "flash" in row_tags or "⚡" in row_text:
            flash_records.append(
                {
                    "id": row.get("id"),
                    "bucket": bucket,
                    "text_preview": row_text[:180],
                }
            )

    l0 = {
        "schema": "adaptr-v1",
        "updated_at": utc_now(),
        "total_records": len(rows),
        "by_bucket": dict(by_bucket),
        "by_source_kind": dict(by_source_kind),
        "by_memory_function": dict(by_memory_function),
        "by_trust_tier": dict(by_trust_tier),
        "by_memory_policy": dict(by_memory_policy),
        "top_entities": [k for k, _ in entity_counter.most_common(20)],
        "top_tags": [k for k, _ in tag_counter.most_common(20)],
        "relation_edges": len(relation_rows),
        "relation_decisions": len(relation_decisions),
    }

    primary_relations = [r for r in relation_rows if r.get("is_primary")]
    top_relations = []
    for rel in sorted(
        primary_relations,
        key=lambda r: (
            safe_int(r.get("support_count"), 0),
            safe_float(r.get("resolution_confidence"), 0.0),
            safe_float(r.get("conflict_score"), 0.0),
        ),
        reverse=True,
    )[:20]:
        top_relations.append(
            {
                "subject": rel.get("subject"),
                "predicate": rel.get("predicate"),
                "object": rel.get("object"),
                "support_count": rel.get("support_count"),
                "resolution_confidence": rel.get("resolution_confidence"),
            }
        )
    l0["top_relations"] = top_relations

    write_json(paths["l0"], l0)

    grouped = defaultdict(list)
    for row in rows:
        grouped[row.get("bucket", "unknown")].append(row)

    profile_snapshot = build_profile_snapshot(rows)
    preferences_snapshot = build_preferences_snapshot(rows)
    write_json(paths["profile_snapshot"], profile_snapshot)
    write_json(paths["preferences_snapshot"], preferences_snapshot)

    l1_rows: List[Dict[str, Any]] = []
    for bucket, bucket_rows in sorted(grouped.items()):
        b_tag_counter = Counter()
        b_entity_counter = Counter()
        samples: List[str] = []
        for row in bucket_rows[:50]:
            for tag in row.get("tags", []):
                b_tag_counter[tag] += 1
            for entity in row.get("entities", []):
                b_entity_counter[entity] += 1
            if len(samples) < 3:
                samples.append(row.get("text", "")[:180])

        l1_rows.append(
            {
                "id": f"overview::{bucket}",
                "bucket": bucket,
                "count": len(bucket_rows),
                "top_tags": [k for k, _ in b_tag_counter.most_common(8)],
                "top_entities": [k for k, _ in b_entity_counter.most_common(8)],
                "sample_texts": samples,
                "updated_at": utc_now(),
            }
        )

    write_jsonl(paths["l1"], l1_rows, append=False)

    static_keywords = {
        "memory.profile": ["姓名", "身份", "背景", "profile", "persona"],
        "memory.preferences": ["偏好", "喜欢", "习惯", "prefer", "favorite"],
        "memory.events": ["事件", "决策", "完成", "项目", "timeline"],
        "memory.agent_skill": ["skill", "规则", "prompt", "workflow", "tool"],
        "memory.working": ["todo", "待办", "临时", "next step", "session"],
        "knowledge.facts": ["事实", "结论", "benchmark", "paper", "finding"],
        "knowledge.procedures": ["步骤", "流程", "how to", "run", "configure"],
        "knowledge.references": ["链接", "文档", "api", "readme", "url"],
    }

    bucket_hints: Dict[str, Dict[str, Any]] = {}
    for bucket in BUCKET_FILES:
        bucket_rows = grouped.get(bucket, [])
        b_tag_counter = Counter()
        b_entity_counter = Counter()
        for row in bucket_rows[:200]:
            b_tag_counter.update(row.get("tags", []))
            b_entity_counter.update(row.get("entities", []))
        dynamic_keywords = [k for k, _ in b_tag_counter.most_common(4)]
        dynamic_keywords.extend([k for k, _ in b_entity_counter.most_common(4)])
        merged_keywords: List[str] = []
        for kw in static_keywords.get(bucket, []) + dynamic_keywords:
            if kw and kw not in merged_keywords:
                merged_keywords.append(kw)
        bucket_hints[bucket] = {
            "recommended_keywords": merged_keywords[:10],
            "record_count": len(bucket_rows),
            "last_updated": last_updated_by_bucket.get(bucket, ""),
            "memory_function": infer_memory_function(bucket),
            "preferred_entrypoints": (
                ["profile_snapshot", "l2_records"]
                if bucket == "memory.profile"
                else ["preferences_snapshot", "l2_records"]
                if bucket == "memory.preferences"
                else ["l2_records"]
                if bucket == "memory.working"
                else ["retrieval_hints", "l1_overview", "l2_records"]
            ),
        }

    hot_buckets = [
        bucket for bucket, _ in sorted(
            heat_score_by_bucket.items(),
            key=lambda kv: (kv[1], by_bucket.get(kv[0], 0)),
            reverse=True,
        )
    ][:4]

    retrieval_hints = {
        "schema": "adaptr-v1",
        "generated_at": utc_now(),
        "hot_buckets": hot_buckets,
        "snapshots": {
            "profile_snapshot": {
                "available": bool(profile_snapshot.get("fields")),
                "field_count": len(profile_snapshot.get("fields", {})),
            },
            "preferences_snapshot": {
                "available": bool(preferences_snapshot.get("fields")),
                "field_count": len(preferences_snapshot.get("fields", {})),
            },
        },
        "bucket_hints": bucket_hints,
        "flash_records": flash_records[:50],
        "relation_hints": top_relations[:15],
        "low_conflict_groups": [
            {
                "subject": d.get("subject"),
                "predicate": d.get("predicate"),
                "primary_object": d.get("primary_object"),
                "resolution_confidence": d.get("resolution_confidence"),
                "status": d.get("status"),
            }
            for d in relation_decisions
            if str(d.get("status")) == "weakly_resolved"
        ][:30],
    }
    write_json(paths["retrieval_hints"], retrieval_hints)

    retrieval_protocol = build_retrieval_protocol(
        rows=rows,
        bucket_hints=bucket_hints,
        profile_snapshot=profile_snapshot,
        preferences_snapshot=preferences_snapshot,
    )
    write_json(paths["retrieval_protocol"], retrieval_protocol)
    return l0


def path_token_match(part: str, token: str) -> bool:
    if part == token:
        return True
    pattern = rf"(?:^|[^a-z0-9]){re.escape(token)}(?:[^a-z0-9]|$)"
    return re.search(pattern, part) is not None


def infer_source_kind_from_path(path: Path, default_kind: str = "memory") -> str:
    parts_lower = [p.lower() for p in path.parts]
    file_index = len(parts_lower) - 1 if path.suffix else len(parts_lower)
    dir_parts = parts_lower[:file_index]
    file_part = parts_lower[file_index] if file_index < len(parts_lower) else ""

    strong_knowledge_tokens = {"knowledge", "kb"}
    strong_memory_tokens = {"memory", "mem"}
    weak_knowledge_tokens = {
        "wiki",
        "reference",
        "references",
        "runbook",
        "playbook",
        "guide",
        "tutorial",
        "manual",
        "paper",
        "benchmark",
        "docs",
    }
    weak_memory_tokens = {
        "profile",
        "preferences",
        "events",
        "journal",
        "diary",
        "todo",
        "task",
    }

    if any(path_token_match(p, token) for p in dir_parts for token in strong_knowledge_tokens):
        return "knowledge"
    if any(path_token_match(p, token) for p in dir_parts for token in strong_memory_tokens):
        return "memory"
    if any(path_token_match(p, token) for p in dir_parts for token in weak_knowledge_tokens):
        return "knowledge"
    if any(path_token_match(p, token) for p in dir_parts for token in weak_memory_tokens):
        return "memory"

    # 文件名仅使用强词，避免 api/spec 这类普通文件名造成误判。
    file_stem = Path(file_part).stem.lower() if file_part else ""
    if file_stem and path_token_match(file_stem, "knowledge"):
        return "knowledge"
    if file_stem and path_token_match(file_stem, "memory"):
        return "memory"
    return default_kind


def discover_openclaw_paths(workspace_root: Path) -> Dict[str, Optional[Path]]:
    """从原生 OpenClaw 工作区自动发现 memory/knowledge 路径。

    候选顺序（优先级从高到低）：
    1. root 本身（若名为 workspace，或含 memory/knowledge 且有 workspace 标记）
    2. root/workspace（标准 OpenClaw 安装布局）
    3. root 本身（含 memory/knowledge，无结构要求）—— 兜底，但排除 OpenClaw runtime 内置 memory

    当 root 目录下同时有 memory/ 和 .openclaw/workspace/ 结构时，
    优先取 workspace/ 子目录，避免误命中 OpenClaw runtime 自身的 memory/main.sqlite。
    """
    root = workspace_root.expanduser().resolve()

    def _is_user_memory(mem: Path) -> bool:
        """排除只含 main.sqlite 的 OpenClaw runtime memory 目录。"""
        if not mem.exists():
            return False
        children = list(mem.iterdir())
        child_names = {c.name for c in children}
        if child_names <= {"main.sqlite", ".adaptr-v1"}:
            if ".adaptr-v1" in child_names:
                return True
            return False
        return True

    # 所有候选都走 user-memory 过滤，避免 workspace 目录名绕过 runtime 判断。
    candidates: List[Tuple[Path, bool]] = [
        (root / "workspace", True),
        (root, True),
    ]
    if root.name == "workspace":
        candidates = [(root, True), (root.parent / "workspace", True)]

    found_base: Optional[Path] = None
    memory_path: Optional[Path] = None
    knowledge_path: Optional[Path] = None

    for base, check_user_memory in candidates:
        mem = base / "memory"
        kn = base / "knowledge"
        mem_ok = mem.exists() and (not check_user_memory or _is_user_memory(mem))
        kn_ok = kn.exists()
        if mem_ok or kn_ok:
            found_base = base
            memory_path = mem if mem_ok else None
            knowledge_path = kn if kn_ok else None
            break

    default_target: Optional[Path] = None
    if found_base is not None:
        if memory_path is not None:
            default_target = memory_path / ".adaptr-v1"
        else:
            default_target = found_base / ".adaptr-v1"

    return {
        "workspace_root": root,
        "workspace_base": found_base,
        "memory_path": memory_path,
        "knowledge_path": knowledge_path,
        "default_target_root": default_target,
    }


def collect_and_normalize(
    source_items: Sequence[Tuple[str, Path]],
    max_file_size_bytes: int,
    max_records_per_file: int,
    recursive: bool,
    include_hidden: bool,
    formation_mode: str = "manual",
    ignore_dirs: Optional[Set[str]] = None,
) -> Dict[str, Any]:
    warnings: List[str] = []
    skipped: List[str] = []
    scanned_files: List[Tuple[str, Path]] = []

    for source_kind, root in source_items:
        files = iter_source_files(
            root=root,
            max_file_size_bytes=max_file_size_bytes,
            recursive=recursive,
            include_hidden=include_hidden,
            ignore_dirs=ignore_dirs,
        )
        for f in files:
            scanned_files.append((source_kind, f))

    raw_records: List[SourceRecord] = []
    for source_kind, file_path in scanned_files:
        rows, warning = extract_from_file(
            path=file_path,
            source_kind=source_kind,
            max_records_per_file=max_records_per_file,
        )
        if warning:
            warnings.append(warning)
            skipped.append(str(file_path))
            continue
        if not rows:
            skipped.append(str(file_path))
            continue
        raw_records.extend(rows)

    normalized_records_raw = [normalize_record(r, formation_mode=formation_mode) for r in raw_records if normalize_text(r.text)]
    policy_filtered_records, policy_skipped_records = apply_memory_policy(normalized_records_raw)
    normalized_records, rejected_records = apply_confidence_gate(policy_filtered_records)

    return {
        "warnings": warnings,
        "skipped_files": skipped,
        "scanned_files": scanned_files,
        "raw_records": raw_records,
        "normalized_records": normalized_records,
        "policy_skipped_records": policy_skipped_records,
        "rejected_records": rejected_records,
    }


def summarize_records(records: Sequence[NormalizedRecord]) -> Dict[str, Any]:
    by_bucket = Counter(r.bucket for r in records)
    by_source_kind = Counter(r.source_kind for r in records)
    by_memory_function = Counter(r.memory_function for r in records)
    by_trust_tier = Counter(r.trust_tier for r in records)
    by_memory_policy = Counter(r.memory_policy for r in records)
    entity_counter = Counter()
    tag_counter = Counter()
    for r in records:
        entity_counter.update(r.entities)
        tag_counter.update(r.tags)

    return {
        "record_count": len(records),
        "by_bucket": dict(by_bucket),
        "by_source_kind": dict(by_source_kind),
        "by_memory_function": dict(by_memory_function),
        "by_trust_tier": dict(by_trust_tier),
        "by_memory_policy": dict(by_memory_policy),
        "top_entities": [k for k, _ in entity_counter.most_common(20)],
        "top_tags": [k for k, _ in tag_counter.most_common(20)],
    }

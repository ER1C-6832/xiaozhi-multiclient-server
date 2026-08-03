"""Safe normalization for spoken tool arguments.

The helpers in this module only rewrite user-visible selectors.  They never
interpret a spoken number as an internal database identifier.
"""

from __future__ import annotations

import re
from typing import Any, Dict

from core.providers.tools.tool_workflow import (
    extract_exact_title,
    normalize_selector_query,
)


def normalize_exact_title(value: Any) -> str:
    title = str(value or "").strip()
    title = re.sub(r"^标题(?:为|是|叫|名为)?\s*", "", title)
    title = re.sub(r"\s*的(?:便签|笔记)?$", "", title)
    return title.strip("“”\"' ")


def normalize_tag_name(value: Any) -> str:
    tag = str(value or "").strip()
    tag = re.sub(
        r"^(?:打开|显示|切到|进入|查看|看看|有没有|搜索|查找|查询|查|找|"
        r"创建|新建|新增|添加|删除|删掉|删|移除)?\s*",
        "",
        tag,
    )
    tag = re.sub(r"^(?:标签|分类)(?:为|是|叫|名为)?\s*", "", tag)
    tag = re.sub(
        r"\s*(?:标签|分类)(?:(?:页|页面|视图)|(?:下|里|中的)(?:便签|笔记)?)?$",
        "",
        tag,
    )
    return tag.strip("“”\"' ，,。；;!?！？")


def normalize_resolve_arguments(arguments: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a note selector without converting it to ``note_id``."""

    normalized = dict(arguments or {})
    explicit = normalize_exact_title(normalized.get("exact_title"))
    query = str(normalized.get("query") or "").strip()
    inferred = explicit or extract_exact_title(query)
    if inferred:
        normalized.pop("query", None)
        normalized["exact_title"] = inferred
        return normalized
    if query:
        selector = normalize_selector_query(query)
        if selector:
            normalized["query"] = selector
    return normalized


def normalize_tool_arguments(
    tool_name: str, arguments: Dict[str, Any]
) -> Dict[str, Any]:
    normalized = dict(arguments or {})
    if tool_name == "notes_resolve":
        return normalize_resolve_arguments(normalized)
    if tool_name == "notes_search" and "query" in normalized:
        query = normalize_selector_query(normalized.get("query"))
        if query:
            normalized["query"] = query
    if tool_name in {"tags_create", "tags_delete"} and "name" in normalized:
        tag = normalize_tag_name(normalized.get("name"))
        if tag:
            normalized["name"] = tag
    if tool_name in {"notes_list_by_tag", "ui_show_tag"} and "tag" in normalized:
        tag = normalize_tag_name(normalized.get("tag"))
        if tag:
            normalized["tag"] = tag
    if tool_name == "tags_search" and "query" in normalized:
        tag = normalize_tag_name(normalized.get("query"))
        if tag:
            normalized["query"] = tag
    if tool_name == "tags_bind" and isinstance(normalized.get("tags"), list):
        tags = []
        for value in normalized["tags"]:
            tag = normalize_tag_name(value)
            if tag and tag not in tags:
                tags.append(tag)
        normalized["tags"] = tags
    return normalized


__all__ = [
    "normalize_exact_title",
    "normalize_resolve_arguments",
    "normalize_tag_name",
    "normalize_tool_arguments",
]

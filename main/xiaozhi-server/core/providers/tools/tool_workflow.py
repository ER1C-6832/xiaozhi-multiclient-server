"""Durable, provider-independent state for multi-turn tool workflows."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, replace
from typing import Any, Dict, Iterable, Optional, Tuple

from core.providers.tools.tool_routing import ToolRouteDecision


_OPERATION_PATTERNS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    ("delete", re.compile(r"删除|删掉|移除")),
    ("restore", re.compile(r"恢复|还原|找回")),
    ("update_title", re.compile(r"改.{0,3}标题|修改.{0,3}标题|改名|标题.{0,8}(?:改成|换成)")),
    ("replace_content", re.compile(
        r"替换|覆盖|改.{0,3}内容|修改.{0,3}内容|改.{0,3}正文|修改.{0,3}正文|"
        r"内容.{0,8}(?:改成|换成)|(?:这条|那条|的那条).{0,8}(?:改成|换成)"
    )),
    ("append", re.compile(r"追加|补充|补一句|后面加")),
    ("pin", re.compile(r"取消置顶|置顶")),
    ("create", re.compile(r"创建|新建|新增|添加|加.{0,3}便签|加一条|加个|记个|记一下|记下来|记录")),
    ("search_exact", re.compile(r"(?:查找|查询|搜索|找).{0,12}标题(?:为|是|叫|名为)")),
    ("search", re.compile(r"搜索|查找|找一下|找找|查询|查一条|看看")),
    ("list_deleted", re.compile(r"回收站|已删除")),
    ("list", re.compile(r"列出|有哪些|最近|全部|列表")),
    ("get", re.compile(r"读取|读一下|详情|打开.{0,8}便签")),
)

_OPERATION_TOOLS = {
    "create": ("notes_create",),
    "search": ("notes_search",),
    "search_exact": ("notes_resolve",),
    "list": ("notes_list",),
    "list_deleted": ("notes_list_deleted",),
    "get": ("notes_resolve", "notes_get"),
    "delete": ("notes_resolve", "notes_delete"),
    "restore": ("notes_resolve", "notes_restore"),
    "replace_content": ("notes_resolve", "notes_replace_content"),
    "update_title": ("notes_resolve", "notes_update_title"),
    "append": ("notes_resolve", "notes_append"),
    "pin": ("notes_resolve", "notes_pin"),
}

_COMPLETION_TOOLS = {
    "create": ("notes_create",),
    "search": ("notes_search",),
    "search_exact": ("notes_resolve",),
    "list": ("notes_list",),
    "list_deleted": ("notes_list_deleted",),
    "get": ("notes_get",),
    "delete": ("notes_delete",),
    "restore": ("notes_restore",),
    "replace_content": ("notes_replace_content",),
    "update_title": ("notes_update_title",),
    "append": ("notes_append",),
    "pin": ("notes_pin",),
}

_MUTATIONS = {
    "create",
    "delete",
    "restore",
    "replace_content",
    "update_title",
    "append",
    "pin",
}

_CANCEL_PATTERN = re.compile(r"取消|算了|不用了|停止|别弄了")


def _tool_name(tool: Dict[str, Any]) -> str:
    function = tool.get("function", {}) if isinstance(tool, dict) else {}
    return str(function.get("name") or "")


def infer_operation(query: str) -> Optional[str]:
    value = str(query or "").strip()
    for operation, pattern in _OPERATION_PATTERNS:
        if pattern.search(value):
            return operation
    return None


def is_cancellation(query: str) -> bool:
    return bool(_CANCEL_PATTERN.search(str(query or "")))


def is_mutation(operation: Optional[str]) -> bool:
    return operation in _MUTATIONS


def completion_tools(operation: Optional[str]) -> Tuple[str, ...]:
    return _COMPLETION_TOOLS.get(operation or "", ())


def workflow_completed(
    operation: Optional[str],
    outcomes: Iterable[Dict[str, Any]],
) -> bool:
    required = set(completion_tools(operation))
    return bool(required) and any(
        outcome.get("success") and outcome.get("name") in required
        for outcome in outcomes
    )


def guard_unverified_text(
    operation: Optional[str],
    outcomes: Iterable[Dict[str, Any]],
    content: str,
) -> Tuple[str, bool]:
    text = str(content or "")
    if not operation or workflow_completed(operation, outcomes):
        return text, False
    completion_claim = re.search(
        r"已.{0,16}(?:创建|添加|新增|修改|改成|替换|覆盖|删除|恢复|"
        r"追加|置顶|绑定|找到|查到)|(?:创建|添加|修改|删除|恢复|"
        r"替换|查找|查询).{0,10}(?:完成|成功)",
        text,
    )
    progress_only = (
        operation in {"search", "search_exact", "list", "list_deleted", "get"}
        and re.search(r"正在.{0,8}(?:查找|搜索|查询|读取)", text)
    )
    if not completion_claim and not progress_only:
        return text, False
    if is_mutation(operation):
        return "操作尚未实际完成，请补充刚才所问的信息后重试。", True
    return "查找尚未实际执行，请补充标题或关键词后重试。", True


@dataclass(frozen=True)
class PendingToolWorkflow:
    operation: str
    root_query: str
    candidate_names: Tuple[str, ...]
    created_at: float
    updated_at: float
    clarification_turns: int = 0

    def is_expired(self, now: Optional[float] = None, ttl_seconds: int = 300) -> bool:
        return (now if now is not None else time.monotonic()) - self.updated_at > ttl_seconds

    def touch(self) -> "PendingToolWorkflow":
        return replace(
            self,
            updated_at=time.monotonic(),
            clarification_turns=self.clarification_turns + 1,
        )


def start_workflow(
    query: str, decision: ToolRouteDecision
) -> Optional[PendingToolWorkflow]:
    operation = infer_operation(query)
    if decision.route != "tool" or operation is None:
        return None
    available_names = tuple(
        name
        for name in _OPERATION_TOOLS.get(operation, ())
        if any(_tool_name(tool) == name for tool in decision.candidates)
    )
    # The local router can prune the mutation tool when its score is low.
    # Keep the operation's exact allow-list; availability is checked again
    # against the full runtime registry when a continuation is built.
    candidate_names = _OPERATION_TOOLS.get(operation, available_names)
    now = time.monotonic()
    return PendingToolWorkflow(
        operation=operation,
        root_query=str(query or "").strip(),
        candidate_names=tuple(candidate_names),
        created_at=now,
        updated_at=now,
    )


def continuation_route(
    workflow: PendingToolWorkflow,
    tools: Iterable[Dict[str, Any]],
) -> ToolRouteDecision:
    available = list(tools or [])
    allowed = [
        tool for tool in available if _tool_name(tool) in workflow.candidate_names
    ]
    schema_chars = len(
        json.dumps(
            allowed,
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
    )
    return ToolRouteDecision(
        route="tool" if allowed else "chat",
        reason="pending_tool_workflow",
        candidates=allowed,
        available_tool_count=len(available),
        candidate_schema_chars=schema_chars,
    )


def should_replace_pending(
    workflow: PendingToolWorkflow, query: str
) -> bool:
    operation = infer_operation(query)
    return operation is not None and operation != workflow.operation

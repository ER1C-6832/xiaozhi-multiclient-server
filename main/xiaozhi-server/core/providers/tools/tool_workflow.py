"""Durable, provider-independent state for multi-turn tool workflows.

This module owns intent recognition and the small amount of state required to
keep a tool task alive across natural multi-turn conversations.  It deliberately
does not depend on a particular LLM provider or on client-side database IDs.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, replace
from typing import Any, Dict, Iterable, Optional, Tuple

from core.providers.tools.tool_routing import ToolRouteDecision


# More specific operations must appear before their generic parents.
_OPERATION_PATTERNS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    (
        "reject",
        re.compile(r"^(?:取消|拒绝|不要了|算了|别执行|别确认)[吧。！!，,\s]*$"),
    ),
    (
        "confirm",
        re.compile(r"^(?:确认|同意|执行吧|就这么做|确定执行|确认执行)[吧。！!，,\s]*$"),
    ),
    (
        "tag_delete",
        re.compile(r"(?:删除|删掉|删|移除).{0,8}(?:标签|分类)"),
    ),
    (
        "tag_bind",
        re.compile(
            r"(?:给|为).{0,16}(?:便签|笔记).{0,12}(?:添加|加上|加|移除|删除|换|改|绑定).{0,6}(?:标签|分类)|"
            r"(?:标签|分类).{0,8}(?:添加到|绑定到|移除自|从).{0,16}(?:便签|笔记)"
        ),
    ),
    (
        "tag_create",
        re.compile(r"(?:创建|新建|新增|添加|加个|建个).{0,8}(?:标签|分类)"),
    ),
    (
        "show_tag",
        re.compile(
            r"(?:打开|显示|切到|进入).{0,10}(?:标签|分类)(?:页|页面|视图)?|"
            r"看看.{0,10}(?:标签|分类)(?:页|页面|视图)|"
            r"(?:标签|分类)(?:页|页面|视图).{0,8}(?:打开|显示|切到|进入)"
        ),
    ),
    (
        "list_by_tag",
        re.compile(
            r"(?:列出|查看|看看|查|找).{0,12}(?:标签|分类).{0,12}(?:便签|笔记)|"
            r"(?:标签|分类).{0,10}(?:下|里|中的).{0,8}(?:便签|笔记)"
        ),
    ),
    (
        "tag_search",
        re.compile(r"(?:搜索|查找|查询|查|找|有没有).{0,12}(?:标签|分类)"),
    ),
    (
        "tag_list",
        re.compile(r"(?:有哪些|列出|全部|所有).{0,8}(?:标签|分类)"),
    ),
    ("delete", re.compile(r"(?:删除|删掉|删|移除)(?![^，。！？!?]{0,8}(?:标签|分类))")),
    ("restore", re.compile(r"恢复|还原|找回")),
    (
        "update_title",
        re.compile(
            r"改.{0,3}标题|修改.{0,3}标题|编辑.{0,3}标题|改名|重命名|"
            r"标题.{0,10}(?:改成|换成|修改为|设为)"
        ),
    ),
    (
        "replace_content",
        re.compile(
            r"替换|覆盖|重写|改.{0,3}内容|修改.{0,3}内容|编辑.{0,3}内容|"
            r"改.{0,3}正文|修改.{0,3}正文|编辑.{0,3}正文|"
            r"(?:内容|正文).{0,10}(?:改成|换成|修改为|替换成|设为)|"
            r"(?:这条|那条|的那条).{0,10}(?:改成|换成|修改为)"
        ),
    ),
    ("append", re.compile(r"追加|补充|补一句|补上|后面加|末尾加|续写")),
    (
        "convert_type",
        re.compile(r"(?:改成|变成|转换为|设为).{0,6}(?:待办|普通便签)|不再是待办"),
    ),
    ("pin", re.compile(r"取消置顶|(?:把|将|给).{0,12}置顶|置顶(?:这|那|一|某).{0,8}(?:条|便签|笔记)")),
    (
        "edit",
        re.compile(
            r"(?:修改|改|编辑|调整)(?:一条|一个|一下|下)?(?:便签|笔记)(?:吧|一下)?$|"
            r"(?:修改|改|编辑|调整).{0,8}(?:这条|那条|某条)(?:便签|笔记)?$"
        ),
    ),
    (
        "create",
        re.compile(
            r"创建|新建|新增|添加|加.{0,3}便签|加一条|加个|写一条|"
            r"记个|记一下|记下来|记录一下|记录下来"
        ),
    ),
    (
        "search_exact",
        re.compile(r"(?:查找|查询|搜索|找|查).{0,12}标题(?:为|是|叫|名为)"),
    ),
    (
        "show_trash",
        re.compile(r"(?:打开|显示|切到|进入).{0,8}(?:回收站|已删除页)"),
    ),
    (
        "show_pinned",
        re.compile(r"(?:打开|显示|切到|进入).{0,8}(?:置顶页|置顶列表)"),
    ),
    (
        "show_todos",
        re.compile(r"(?:打开|显示|切到|进入).{0,8}(?:待办页|待办列表)"),
    ),
    (
        "show_note_list",
        re.compile(r"(?:回到|打开|显示|切到).{0,8}(?:全部便签|便签主页|便签列表)"),
    ),
    (
        "list_deleted",
        re.compile(r"(?:列出|查看|看看)?.{0,6}(?:回收站|已删除)(?:便签|笔记)?"),
    ),
    (
        "list_pinned",
        re.compile(r"(?:列出|查看|看看)?.{0,6}(?:置顶|重要)(?:便签|笔记)"),
    ),
    (
        "list_todos",
        re.compile(r"(?:列出|查看|看看)?.{0,6}(?:待办)(?:便签|笔记|列表)?"),
    ),
    (
        "list_recent",
        re.compile(r"(?:最近|最新|刚才|刚刚).{0,8}(?:便签|笔记)|(?:便签|笔记).{0,8}(?:最近|最新)"),
    ),
    (
        "open_note",
        re.compile(r"(?:打开|展示|显示).{0,12}(?:便签|笔记|这条|那条)"),
    ),
    (
        "search",
        re.compile(
            r"搜索|查找|找一下|找找|查询|查一条|查个|看看|"
            r"(?:查|找|搜|看看).{0,20}(?:便签|笔记|记录)|"
            r"(?:便签|笔记|记录).{0,20}(?:查|找|搜|看看)"
        ),
    ),
    ("list", re.compile(r"列出|有哪些|全部便签|便签列表|笔记列表")),
    ("get", re.compile(r"读取|读一下|念一下|详情|看看内容")),
)


_OPERATION_TOOLS = {
    "confirm": ("assistant_list_pending_confirmations", "assistant_confirm"),
    "reject": ("assistant_list_pending_confirmations", "assistant_reject"),
    "create": ("notes_create",),
    "search": ("notes_search", "notes_resolve"),
    "search_exact": ("notes_resolve",),
    "list": ("notes_list_recent", "ui_show_note_list"),
    "list_recent": ("notes_list_recent",),
    "list_deleted": ("notes_list_deleted",),
    "list_pinned": ("notes_list_pinned",),
    "list_todos": ("notes_list_todos",),
    "list_by_tag": ("tags_search", "notes_list_by_tag"),
    "get": ("notes_resolve", "notes_get"),
    "open_note": ("notes_resolve", "ui_open_note"),
    "delete": ("notes_resolve", "notes_delete"),
    "restore": ("notes_resolve", "notes_restore"),
    "edit": (
        "notes_resolve",
        "notes_update_title",
        "notes_replace_content",
        "notes_append",
        "tags_bind",
    ),
    "replace_content": ("notes_resolve", "notes_replace_content"),
    "update_title": ("notes_resolve", "notes_update_title"),
    "append": ("notes_resolve", "notes_append"),
    "convert_type": ("notes_resolve", "notes_convert_type"),
    "pin": ("notes_resolve", "notes_pin"),
    "tag_create": ("tags_create",),
    "tag_search": ("tags_search",),
    "tag_list": ("tags_list",),
    "tag_delete": ("tags_search", "tags_delete"),
    "tag_bind": ("notes_resolve", "tags_search", "tags_bind"),
    "show_tag": ("tags_search", "ui_show_tag"),
    "show_trash": ("ui_show_trash",),
    "show_pinned": ("ui_show_pinned",),
    "show_todos": ("ui_show_todos",),
    "show_note_list": ("ui_show_note_list",),
}


_COMPLETION_TOOLS = {
    "confirm": ("assistant_confirm",),
    "reject": ("assistant_reject",),
    "create": ("notes_create",),
    "search": ("notes_resolve", "notes_search"),
    "search_exact": ("notes_resolve",),
    "list": ("notes_list_recent", "ui_show_note_list"),
    "list_recent": ("notes_list_recent",),
    "list_deleted": ("notes_list_deleted",),
    "list_pinned": ("notes_list_pinned",),
    "list_todos": ("notes_list_todos",),
    "list_by_tag": ("notes_list_by_tag",),
    "get": ("notes_get",),
    "open_note": ("ui_open_note",),
    "delete": ("notes_delete",),
    "restore": ("notes_restore",),
    "edit": ("notes_update_title", "notes_replace_content", "notes_append", "tags_bind"),
    "replace_content": ("notes_replace_content",),
    "update_title": ("notes_update_title",),
    "append": ("notes_append",),
    "convert_type": ("notes_convert_type",),
    "pin": ("notes_pin",),
    "tag_create": ("tags_create",),
    "tag_search": ("tags_search",),
    "tag_list": ("tags_list",),
    "tag_delete": ("tags_delete",),
    "tag_bind": ("tags_bind",),
    "show_tag": ("ui_show_tag",),
    "show_trash": ("ui_show_trash",),
    "show_pinned": ("ui_show_pinned",),
    "show_todos": ("ui_show_todos",),
    "show_note_list": ("ui_show_note_list",),
}


_MUTATIONS = {
    "confirm",
    "create",
    "delete",
    "restore",
    "edit",
    "replace_content",
    "update_title",
    "append",
    "convert_type",
    "pin",
    "tag_create",
    "tag_delete",
    "tag_bind",
}

_MULTI_STEP_OPERATIONS = {
    operation
    for operation, names in _OPERATION_TOOLS.items()
    if len(names) > 1
} | _MUTATIONS

_EDIT_REFINEMENTS = {
    "update_title",
    "replace_content",
    "append",
    "tag_bind",
    "convert_type",
    "pin",
}
_LIST_REFINEMENTS = {
    "list_recent",
    "list_deleted",
    "list_pinned",
    "list_todos",
    "list_by_tag",
}

_CANCEL_PATTERN = re.compile(r"取消|算了|不用了|停止|别弄了|不改了|不删了")
_TARGET_REJECTION_PATTERN = re.compile(
    r"^(?:不对|不是(?:这|那|它)?(?:一?条|一个)?|不是这个|找错了|选错了|换一条)[。！!，,\s]*$"
)

_GENERIC_SELECTOR_REQUESTS = {
    "create": re.compile(
        r"^(?:请|帮我|麻烦)?(?:创建|新建|新增|添加|加|写|记)(?:一条|一个|一下|个)?(?:便签|笔记)?[吧。！!，,\s]*$"
    ),
    "search": re.compile(
        r"^(?:请|帮我|麻烦)?(?:查|查找|查询|搜索|找|看看)(?:一条|一个|一下)?(?:便签|笔记)?[吧。！!，,\s]*$"
    ),
    "search_exact": re.compile(
        r"^(?:请|帮我|麻烦)?(?:查|查找|查询|搜索|找)(?:一条|一个|一下)?(?:便签|笔记)?[吧。！!，,\s]*$"
    ),
    "get": re.compile(
        r"^(?:请|帮我|麻烦)?(?:读取|读一下|念一下|查看)(?:一条|一个|一下)?(?:便签|笔记)?[吧。！!，,\s]*$"
    ),
    "open_note": re.compile(
        r"^(?:请|帮我|麻烦)?(?:打开|展示|显示)(?:一条|一个|一下)?(?:便签|笔记)?[吧。！!，,\s]*$"
    ),
    "delete": re.compile(
        r"^(?:请|帮我|麻烦)?(?:删除|删掉|删|移除)(?:一条|一个|一下)?(?:便签|笔记)?[吧。！!，,\s]*$"
    ),
    "restore": re.compile(
        r"^(?:请|帮我|麻烦)?(?:恢复|还原|找回)(?:一条|一个|一下)?(?:便签|笔记)?[吧。！!，,\s]*$"
    ),
    "edit": re.compile(
        r"^(?:请|帮我|麻烦)?(?:修改|改|编辑|调整)(?:一条|一个|一下)?(?:便签|笔记)?[吧。！!，,\s]*$"
    ),
    "replace_content": re.compile(
        r"^(?:请|帮我|麻烦)?(?:修改|改|替换|覆盖|编辑)(?:一条|一个|一下)?(?:便签|笔记)?(?:的)?(?:内容|正文)?[吧。！!，,\s]*$"
    ),
    "update_title": re.compile(
        r"^(?:请|帮我|麻烦)?(?:修改|改|编辑)(?:一条|一个|一下)?(?:便签|笔记)?(?:的)?标题[吧。！!，,\s]*$"
    ),
    "append": re.compile(
        r"^(?:请|帮我|麻烦)?(?:追加|补充|补一句|续写)(?:一条|一个|一下)?(?:便签|笔记)?[吧。！!，,\s]*$"
    ),
    "pin": re.compile(
        r"^(?:请|帮我|麻烦)?(?:置顶|取消置顶)(?:一条|一个|一下)?(?:便签|笔记)?[吧。！!，,\s]*$"
    ),
    "tag_bind": re.compile(
        r"^(?:请|帮我|麻烦)?(?:给|为)?(?:一条|一个)?(?:便签|笔记)?(?:加|添加|移除|换|改|绑定)(?:一个|一下)?(?:标签|分类)[吧。！!，,\s]*$"
    ),
    "show_tag": re.compile(
        r"^(?:请|帮我|麻烦)?(?:打开|显示|切到|进入|看看)?(?:标签|分类)(?:页|页面|视图)?[吧。！!，,\s]*$"
    ),
    "list_by_tag": re.compile(
        r"^(?:请|帮我|麻烦)?(?:查看|列出|看看)?(?:标签|分类)(?:下|里的)?(?:便签|笔记)?[吧。！!，,\s]*$"
    ),
    "tag_delete": re.compile(
        r"^(?:请|帮我|麻烦)?(?:删除|删掉|删|移除)(?:一个|一下)?(?:标签|分类)[吧。！!，,\s]*$"
    ),
}

_TITLE_SELECTOR_PATTERN = re.compile(
    r"(?:^|[，,。；;\s])(?:标题|名字|名称)(?:为|是|叫|名为|写着|写的是)?\s*"
    r"[“\"']?(.+?)[”\"']?(?:的(?:便签|笔记)?|[。！!？?，,；;]|$)"
)


def _tool_name(tool: Dict[str, Any]) -> str:
    function = tool.get("function", {}) if isinstance(tool, dict) else {}
    return str(function.get("name") or "")


def infer_operation(query: str) -> Optional[str]:
    value = str(query or "").strip()
    for operation, pattern in _OPERATION_PATTERNS:
        if pattern.search(value):
            return operation
    return None


def operation_tools(operation: Optional[str]) -> Tuple[str, ...]:
    return _OPERATION_TOOLS.get(operation or "", ())


def requires_tool_followup(operation: Optional[str]) -> bool:
    return operation in _MULTI_STEP_OPERATIONS


def extract_exact_title(value: str) -> Optional[str]:
    """Extract an explicit spoken title without treating it as a database ID."""

    text = str(value or "").strip()
    if not text:
        return None
    match = _TITLE_SELECTOR_PATTERN.search(text)
    if match is not None:
        title = match.group(1).strip("“”\"' ，,。；;!?！？")
        return title or None
    # A bare number is a valid user-visible title in this application.  It is
    # never converted to an internal note_id; it is only used as exact_title.
    if re.fullmatch(r"\d{1,32}", text):
        return text
    return None


def normalize_selector_query(value: str) -> str:
    """Remove spoken wrappers while preserving the semantic selector."""

    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(
        r"^(?:请|帮我|麻烦|给我|找一下|查一下|查找|查询|搜索|找|查|看看)\s*",
        "",
        text,
    )
    text = re.sub(
        r"^(?:内容|正文)(?:为|是|叫|名为|包含|写着|写的是)?\s*",
        "",
        text,
    )
    text = re.sub(r"^(?:标题|名字|名称)(?:为|是|叫|名为)?\s*", "", text)
    text = re.sub(r"\s*的(?:那条|这条)?(?:便签|笔记)?$", "", text)
    text = re.sub(r"\s*(?:相关|有关)(?:的)?(?:便签|笔记)?$", "", text)
    return text.strip("“”\"' ，,。；;!?！？")


def is_cancellation(query: str) -> bool:
    return bool(_CANCEL_PATTERN.search(str(query or "")))


def is_target_rejection(query: str) -> bool:
    return bool(_TARGET_REJECTION_PATTERN.match(str(query or "").strip()))


def selector_clarification(operation: Optional[str], query: str) -> Optional[str]:
    if is_target_rejection(query):
        return "好的，上一条作废。请说正确便签的完整标题或关键词。"
    pattern = _GENERIC_SELECTOR_REQUESTS.get(operation or "")
    if pattern is None or not pattern.match(str(query or "").strip()):
        return None
    if operation == "create":
        return "请说便签标题和内容，也可以一句话一起说。"
    if operation in {"show_tag", "list_by_tag", "tag_delete"}:
        return "请说标签或分类的准确名称。"
    if operation == "tag_bind":
        return "请说要操作的便签标题，以及要添加、移除或替换的标签。"
    if operation in {"edit", "replace_content", "update_title", "append"}:
        return "请先说要修改的便签完整标题或关键词，也可以描述正文内容。"
    return "请说要操作的便签完整标题或关键词，也可以描述正文内容。"


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
        r"已.{0,20}(?:创建|添加|新增|修改|改成|替换|覆盖|删除|删掉|恢复|"
        r"追加|置顶|绑定|打开|找到|查到|确认|执行)|(?:创建|添加|修改|删除|恢复|"
        r"替换|查询|查找|打开|确认|执行).{0,12}(?:完成|成功|好了)",
        text,
    )
    progress_only = (
        operation
        in {
            "search",
            "search_exact",
            "list",
            "list_recent",
            "list_deleted",
            "list_pinned",
            "list_todos",
            "list_by_tag",
            "get",
            "open_note",
            "show_tag",
        }
        and re.search(r"正在.{0,10}(?:查找|搜索|查询|读取|打开)", text)
    )
    asks_for_information = re.search(
        r"[？?]|(?:请|需要|麻烦).{0,16}(?:提供|告诉|补充|说明|选择|说)|"
        r"(?:哪条|哪个|什么|如何|怎么|想要|标题|内容|正文|标签).{0,16}(?:呢|吗|[？?])",
        text,
    )
    awaiting_confirmation = re.search(r"需要.{0,8}确认|请.{0,12}确认|确认卡片", text)
    if asks_for_information or awaiting_confirmation:
        return text, False
    if not completion_claim and not progress_only:
        return text, False
    if is_mutation(operation):
        return "操作尚未实际完成，请继续补充刚才所问的信息。", True
    return "查找尚未实际执行，请补充标题、内容关键词或标签后重试。", True


@dataclass(frozen=True)
class PendingToolWorkflow:
    operation: str
    root_query: str
    candidate_names: Tuple[str, ...]
    created_at: float
    updated_at: float
    clarification_turns: int = 0
    fragments: Tuple[str, ...] = ()

    def is_expired(self, now: Optional[float] = None, ttl_seconds: int = 300) -> bool:
        return (now if now is not None else time.monotonic()) - self.updated_at > ttl_seconds

    def touch(self, query: str = "") -> "PendingToolWorkflow":
        operation = self.operation
        inferred = infer_operation(query)
        if operation == "edit" and inferred in _EDIT_REFINEMENTS:
            operation = inferred
        elif operation == "list" and inferred in _LIST_REFINEMENTS:
            operation = inferred
        elif operation == "search" and inferred == "search_exact":
            operation = inferred

        fragments = self.fragments
        cleaned = str(query or "").strip()
        if cleaned:
            fragments = (*fragments, cleaned)[-6:]
        return replace(
            self,
            operation=operation,
            candidate_names=operation_tools(operation) or self.candidate_names,
            updated_at=time.monotonic(),
            clarification_turns=self.clarification_turns + 1,
            fragments=fragments,
        )

    def context_summary(self) -> str:
        parts = self.fragments[-4:] or (self.root_query,)
        return "；".join(part for part in parts if part)


_ONE_TURN_OPERATIONS = {
    "search",
    "search_exact",
    "list",
    "list_recent",
    "list_deleted",
    "list_pinned",
    "list_todos",
    "tag_search",
    "tag_list",
}


def start_workflow(
    query: str, decision: ToolRouteDecision
) -> Optional[PendingToolWorkflow]:
    operation = infer_operation(query)
    if operation is None:
        return None
    generic_clarification = selector_clarification(operation, query) is not None
    # A fully specified deterministic read should keep the router's single-tool
    # decision.  Only an incomplete read needs durable parameter collection.
    if operation in _ONE_TURN_OPERATIONS and not generic_clarification:
        return None
    if (
        decision.route != "tool"
        and decision.reason != "generic_note_search_requires_clarification"
        and not generic_clarification
    ):
        return None
    candidate_names = operation_tools(operation)
    if not candidate_names:
        return None
    now = time.monotonic()
    root = str(query or "").strip()
    return PendingToolWorkflow(
        operation=operation,
        root_query=root,
        candidate_names=tuple(candidate_names),
        created_at=now,
        updated_at=now,
        fragments=(root,) if root else (),
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


def _is_parameter_followup(query: str) -> bool:
    text = str(query or "").strip()
    if not text:
        return False
    if extract_exact_title(text) is not None:
        return True
    return bool(
        re.match(
            r"^(?:标题|名字|名称|内容|正文|关键词|描述|标签|分类|新标题|新内容|新正文)"
            r"(?:为|是|叫|名为|写|写成|写着|包含|改成|换成|设为)?",
            text,
        )
    )


def should_replace_pending(
    workflow: PendingToolWorkflow, query: str
) -> bool:
    if _is_parameter_followup(query):
        return False
    operation = infer_operation(query)
    if operation is None or operation == workflow.operation:
        return False
    if workflow.operation == "edit" and operation in _EDIT_REFINEMENTS:
        return False
    if workflow.operation == "list" and operation in _LIST_REFINEMENTS:
        return False
    if workflow.operation == "search" and operation == "search_exact":
        return False
    return True


__all__ = [
    "PendingToolWorkflow",
    "completion_tools",
    "continuation_route",
    "extract_exact_title",
    "guard_unverified_text",
    "infer_operation",
    "is_cancellation",
    "is_mutation",
    "is_target_rejection",
    "normalize_selector_query",
    "operation_tools",
    "requires_tool_followup",
    "selector_clarification",
    "should_replace_pending",
    "start_workflow",
    "workflow_completed",
]

"""Cost-aware local routing for function-call requests.

The router intentionally uses no model call.  It selects a small candidate set
for explicit tool intents and keeps normal conversation tool-free.  Durable
multi-turn state lives in :mod:`tool_workflow`; this module only decides the
initial candidate domain and a few deterministic read routes.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List


@dataclass(frozen=True)
class ToolRouteDecision:
    route: str
    reason: str
    candidates: List[Dict[str, Any]]
    available_tool_count: int
    candidate_schema_chars: int


_DEVICE_TOOL_READINESS_REASONS = frozenset(
    {
        "domain_without_candidate",
        "exact_title_lookup",
        "implicit_note_keyword_search",
        "note_keyword_search",
        "recent_note_list",
        "deleted_note_list",
        "pinned_note_list",
        "todo_note_list",
    }
)
_REQUIRED_SINGLE_TOOL_REASONS = frozenset(
    {
        "exact_title_lookup",
        "implicit_note_keyword_search",
        "note_keyword_search",
        "recent_note_list",
        "deleted_note_list",
        "pinned_note_list",
        "todo_note_list",
    }
)


def should_wait_for_device_tools(decision: ToolRouteDecision) -> bool:
    return (
        decision.route == "chat"
        and not decision.candidates
        and decision.reason in _DEVICE_TOOL_READINESS_REASONS
    )


def required_tool_choice(decision: ToolRouteDecision) -> Dict[str, Any] | None:
    if (
        decision.route != "tool"
        or decision.reason not in _REQUIRED_SINGLE_TOOL_REASONS
        or len(decision.candidates) != 1
    ):
        return None
    name = _tool_name(decision.candidates[0])
    if not name:
        return None
    return {"type": "function", "function": {"name": name}}


_DOMAIN_PATTERNS = {
    "notes": re.compile(
        r"便签|笔记|待办|备忘|记录|记一下|记下来|标签|分类|回收站|置顶|"
        r"改内容|修改内容|编辑内容|改正文|修改正文|编辑正文|"
        r"改标题|修改标题|编辑标题|改名|重命名|追加|补充|"
        r"这条|那条|某条"
    ),
    "ui": re.compile(
        r"界面|页面|页|视图|打开|显示|展示|切到|进入|回到|搜索框|主页"
    ),
    "confirmation": re.compile(r"确认|取消|拒绝|待确认|就这么做|执行吧"),
    "weather": re.compile(r"天气|气温|温度|下雨|降雨|刮风|空气质量"),
    "news": re.compile(r"新闻|热搜|资讯|头条"),
    "music": re.compile(r"音乐|歌曲|唱歌|播放|放一首|来一首"),
    "lunar": re.compile(r"农历|阴历|黄历"),
    "exit": re.compile(r"再见|拜拜|退出|退下|待机|晚安"),
}

_NAME_DOMAINS = {
    "notes_": "notes",
    "tags_": "notes",
    "ui_": "ui",
    "assistant_": "confirmation",
    "get_weather": "weather",
    "get_news": "news",
    "play_music": "music",
    "get_lunar": "lunar",
    "handle_exit": "exit",
}

_ACTION_HINTS = {
    "create": re.compile(r"创建|新建|新增|添加|加个|加一条|写一条|记个|记一下|记下来|记录一下|记录下来"),
    "search": re.compile(r"搜索|搜|查找|找一下|找找|找|查询|查|看看"),
    "resolve": re.compile(r"刚才那|那一条|那条|这条|哪个|定位|标题(?:为|是|叫|名为)"),
    "list_recent": re.compile(r"最近|最新|刚才记|刚刚记"),
    "list_deleted": re.compile(r"回收站|已删除"),
    "list_todos": re.compile(r"待办"),
    "list_pinned": re.compile(r"置顶|重要"),
    "list_by_tag": re.compile(r"标签|分类"),
    "get": re.compile(r"读取|读一下|念一下|详情|看看内容"),
    "append": re.compile(r"追加|补充|补一句|补上|后面加|续写"),
    "update_title": re.compile(r"改名|重命名|改标题|修改标题|标题改"),
    "replace_content": re.compile(
        r"替换|覆盖|重写|全部改成|正文改|内容改|改内容|修改内容|改正文|修改正文"
    ),
    "convert_type": re.compile(r"变成待办|改成待办|不再是待办|普通便签"),
    "delete": re.compile(r"删除|删掉|删|移除"),
    "restore": re.compile(r"恢复|还原|找回"),
    "pin": re.compile(r"置顶|取消置顶"),
    "bind": re.compile(r"绑定标签|加标签|移除标签|换标签|改标签"),
    "show_tag": re.compile(r"标签页|分类页|打开标签|显示标签|切到标签"),
    "show_trash": re.compile(r"打开回收站|显示回收站|切到回收站"),
    "show_pinned": re.compile(r"打开置顶|显示置顶|切到置顶"),
    "show_todos": re.compile(r"打开待办|显示待办|切到待办"),
    "show_note_list": re.compile(r"全部便签|便签主页|便签列表"),
    "show": re.compile(r"打开|显示|展示|切到|进入|回到"),
    "confirm": re.compile(r"确认|同意|执行|就这么做"),
    "reject": re.compile(r"取消|拒绝|不要了|算了"),
}

_CLARIFICATION_PATTERN = re.compile(
    r"请.{0,16}(告诉|提供|补充|说明|选择|说)|"
    r"(?:给我|说一下|告诉我).{0,16}(?:标题|内容|正文|关键词|标签|分类|描述)|"
    r"(需要|还缺|缺少).{0,16}(什么|哪些|哪一|信息|内容|标题|地点|城市|标签)|"
    r"(什么|哪些|哪一|哪个|哪里|几号|几点|是否).{0,10}[？?]?$|"
    r"[？?]$"
)

_EXACT_TITLE_LOOKUP_PATTERN = re.compile(
    r"(?:搜索|查找|查询|找一下|找找|找|查|看看).{0,16}"
    r"标题(?:为|是|叫|名为)"
)
_NOTE_KEYWORD_SEARCH_PATTERN = re.compile(
    r"(?:搜索|搜一下|搜|查找|查询|找一下|找找|找|看看|查).{0,24}(?:便签|笔记|记录)|"
    r"(?:便签|笔记|记录).{0,24}(?:搜索|搜一下|搜|查找|查询|找一下|找找|找|看看|查)"
)
_RECENT_NOTE_LIST_PATTERN = re.compile(
    r"(?:最近|最新|刚才|刚刚|新近).{0,12}(?:便签|笔记)|"
    r"(?:便签|笔记).{0,12}(?:最近|最新|刚才|刚刚|新近)"
)
_DELETED_NOTE_LIST_PATTERN = re.compile(r"(?:回收站|已删除).{0,8}(?:便签|笔记)?")
_PINNED_NOTE_LIST_PATTERN = re.compile(
    r"(?:有哪些|列出|查看|看看|显示).{0,10}(?:置顶|重要).{0,8}(?:便签|笔记)|"
    r"(?:置顶|重要).{0,8}(?:便签|笔记).{0,6}(?:有哪些|列表)"
)
_TODO_NOTE_LIST_PATTERN = re.compile(
    r"(?:有哪些|列出|查看|看看|显示|打开).{0,10}待办(?:便签|笔记|列表)?|"
    r"待办(?:便签|笔记).{0,6}(?:有哪些|列表)"
)
_IMPLICIT_RELATED_NOTE_PATTERN = re.compile(
    r"^[\w\u4e00-\u9fff]{1,24}(?:相关|有关)(?:的)?(?:便签|笔记)?[。！!？?\s]*$"
)
_MUTATION_PATTERN = re.compile(
    r"删除|删掉|删|移除|恢复|还原|找回|追加|补充|改成|换成|修改|"
    r"编辑|调整|替换|覆盖|重写|置顶|绑定|加标签|移除标签|换标签|改标签"
)
_ROOT_TOOL_INTENT_PATTERN = re.compile(
    r"(?:创建|新建|新增|添加|加|写|记|搜索|搜|查找|查询|找|查|删除|删掉|删|"
    r"移除|恢复|还原|找回|修改|改|编辑|调整|追加|补充|置顶|打开|读取|显示|切到)"
    r".{0,18}(?:便签|笔记|标签|分类)|"
    r"(?:便签|笔记|标签|分类).{0,18}(?:创建|新增|搜索|查找|查询|删除|删|恢复|修改|改|追加|置顶|打开)"
)

_GENERIC_NOTE_SEARCH_FILLERS = tuple(
    sorted(
        {
            "麻烦你帮我", "麻烦帮我", "能不能帮我", "可以帮我", "请你帮我",
            "帮我查一下", "帮我找一下", "帮我看看", "帮我", "给我", "替我",
            "麻烦", "请问", "请", "能不能", "可以不可以", "可以", "帮忙",
            "搜索一下", "查询一下", "查找一下", "找一下", "查一下", "搜一下",
            "看一下", "搜索", "查询", "查找", "找找", "查查", "搜搜", "看看",
            "打开", "读取", "读一下", "读", "找", "查", "搜", "看",
            "任意一个", "随便一个", "随便一条", "某一个", "一个", "一条",
            "一则", "某个", "某条", "几个", "几条", "随便", "一下",
            "我的", "我这边", "这里", "里面", "小智便签应用", "小智便签",
            "便签应用", "便签app", "小智", "便签", "笔记", "记录", "内容",
            "详情", "标题", "的",
        },
        key=len,
        reverse=True,
    )
)


def _tool_name(tool: Dict[str, Any]) -> str:
    function = tool.get("function", {}) if isinstance(tool, dict) else {}
    return str(function.get("name") or "")


def _tool_description(tool: Dict[str, Any]) -> str:
    function = tool.get("function", {}) if isinstance(tool, dict) else {}
    return str(function.get("description") or "")


def _domain_for_name(name: str) -> str | None:
    for prefix, domain in _NAME_DOMAINS.items():
        if name.startswith(prefix):
            return domain
    return None


def _query_domains(query: str) -> set[str]:
    return {
        domain
        for domain, pattern in _DOMAIN_PATTERNS.items()
        if pattern.search(query)
    }


def _compact_text(value: str) -> str:
    return re.sub(r"[\W_]+", "", (value or "").lower(), flags=re.UNICODE)


def _is_generic_note_search_query(query: str) -> bool:
    compact = _compact_text(query)
    if not compact or not any(noun in compact for noun in ("便签", "笔记", "记录")):
        return False
    stripped = compact
    for phrase in _GENERIC_NOTE_SEARCH_FILLERS:
        stripped = stripped.replace(_compact_text(phrase), "")
    return not stripped


def _bigrams(value: str) -> set[str]:
    compact = _compact_text(value)
    return {
        compact[index : index + 2]
        for index in range(max(0, len(compact) - 1))
    }


def _score(query: str, tool: Dict[str, Any], domains: set[str]) -> int:
    name = _tool_name(tool)
    description = _tool_description(tool)
    domain = _domain_for_name(name)
    score = 20 if domain in domains else 0
    normalized_name = name.replace(".", "_").lower()
    for action, pattern in _ACTION_HINTS.items():
        if pattern.search(query) and action in normalized_name:
            score += 14
    query_bigrams = _bigrams(query)
    if query_bigrams:
        overlap = query_bigrams.intersection(_bigrams(description))
        score += min(18, len(overlap) * 3)
    return score


def _single_tool_decision(
    available: List[Dict[str, Any]],
    tool_name: str,
    reason: str,
) -> ToolRouteDecision:
    selected = next(
        (tool for tool in available if _tool_name(tool) == tool_name),
        None,
    )
    candidates = [selected] if selected is not None else []
    schema_chars = (
        len(json.dumps(candidates, ensure_ascii=False, separators=(",", ":"), default=str))
        if candidates
        else 0
    )
    return ToolRouteDecision(
        route="tool" if candidates else "chat",
        reason=reason if candidates else "domain_without_candidate",
        candidates=candidates,
        available_tool_count=len(available),
        candidate_schema_chars=schema_chars,
    )


def select_candidate_tools(
    query: str,
    tools: Iterable[Dict[str, Any]],
    *,
    previous_query: str = "",
    previous_assistant: str = "",
    max_candidates: int = 5,
    max_schema_chars: int = 5_000,
) -> ToolRouteDecision:
    available = list(tools or [])
    normalized_query = (query or "").strip().lower()
    domains = _query_domains(normalized_query)
    continuation = False
    implicit_note_search = bool(
        normalized_query
        and "标签" not in normalized_query
        and "分类" not in normalized_query
        and not domains
        and _IMPLICIT_RELATED_NOTE_PATTERN.match(normalized_query)
    )
    if implicit_note_search:
        domains = {"notes"}

    # Compatibility fallback only.  Durable workflow state takes precedence in
    # ConnectionHandler and no longer depends on these exact assistant words.
    if (
        normalized_query
        and _CLARIFICATION_PATTERN.search((previous_assistant or "").strip())
    ):
        normalized_previous = (previous_query or "").strip().lower()
        previous_domains = _query_domains(normalized_previous)
        # This compatibility fallback is intentionally one hop only: the
        # previous user message must itself contain an explicit tool request.
        # Durable workflows handle longer conversations.
        if (
            previous_domains
            and _ROOT_TOOL_INTENT_PATTERN.search(normalized_previous)
            and not _ROOT_TOOL_INTENT_PATTERN.search(normalized_query)
        ):
            domains = domains or previous_domains
            normalized_query = f"{previous_query} {query}".strip().lower()
            continuation = True

    if not normalized_query:
        candidates: List[Dict[str, Any]] = []
        reason = "empty_query"
    elif not domains:
        candidates = []
        reason = "no_explicit_tool_domain"
    else:
        exact_title_lookup = bool(
            _EXACT_TITLE_LOOKUP_PATTERN.search(normalized_query)
            and not _MUTATION_PATTERN.search(normalized_query)
        )
        if exact_title_lookup:
            return _single_tool_decision(available, "notes_resolve", "exact_title_lookup")

        if (
            "标签" not in normalized_query
            and "分类" not in normalized_query
            and not _MUTATION_PATTERN.search(normalized_query)
            and _RECENT_NOTE_LIST_PATTERN.search(normalized_query)
        ):
            return _single_tool_decision(available, "notes_list_recent", "recent_note_list")

        if not _MUTATION_PATTERN.search(normalized_query) and _DELETED_NOTE_LIST_PATTERN.search(normalized_query):
            return _single_tool_decision(available, "notes_list_deleted", "deleted_note_list")

        if _PINNED_NOTE_LIST_PATTERN.search(normalized_query):
            return _single_tool_decision(available, "notes_list_pinned", "pinned_note_list")

        if _TODO_NOTE_LIST_PATTERN.search(normalized_query):
            return _single_tool_decision(available, "notes_list_todos", "todo_note_list")

        note_search_match = bool(
            "标签" not in normalized_query
            and "分类" not in normalized_query
            and not _MUTATION_PATTERN.search(normalized_query)
            and (
                implicit_note_search
                or _NOTE_KEYWORD_SEARCH_PATTERN.search(normalized_query)
            )
        )
        if (
            note_search_match
            and not implicit_note_search
            and _is_generic_note_search_query(normalized_query)
        ):
            return ToolRouteDecision(
                route="chat",
                reason="generic_note_search_requires_clarification",
                candidates=[],
                available_tool_count=len(available),
                candidate_schema_chars=0,
            )
        if note_search_match:
            return _single_tool_decision(
                available,
                "notes_search",
                "implicit_note_keyword_search" if implicit_note_search else "note_keyword_search",
            )

        ranked = sorted(
            (
                (_score(normalized_query, tool, domains), index, tool)
                for index, tool in enumerate(available)
                if _domain_for_name(_tool_name(tool)) in domains
            ),
            key=lambda item: (-item[0], item[1]),
        )
        candidates = []
        for score, _, tool in ranked:
            if score < 20 or len(candidates) >= max(1, int(max_candidates)):
                continue
            proposed = [*candidates, tool]
            proposed_chars = len(
                json.dumps(
                    proposed,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    default=str,
                )
            )
            if proposed_chars <= max(0, int(max_schema_chars)):
                candidates.append(tool)
        if candidates:
            reason = "tool_parameter_continuation" if continuation else "explicit_tool_candidates"
        else:
            reason = "domain_without_candidate"

    schema_chars = (
        len(json.dumps(candidates, ensure_ascii=False, separators=(",", ":"), default=str))
        if candidates
        else 0
    )
    return ToolRouteDecision(
        route="tool" if candidates else "chat",
        reason=reason,
        candidates=candidates,
        available_tool_count=len(available),
        candidate_schema_chars=schema_chars,
    )


def parse_plain_tool_call(
    content: str, tools: Iterable[Dict[str, Any]]
) -> Dict[str, Any] | None:
    """Parse provider fallbacks such as ``notes_search{\"query\":\"x\"}``.

    Only names from the already-authorized candidate set are accepted.
    """

    value = (content or "").strip()
    if value.startswith("```") and value.endswith("```"):
        value = re.sub(r"^```(?:json)?\s*|\s*```$", "", value, flags=re.I)
    match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_.]*)\s*(\{.*\})", value, re.S)
    if match is None:
        return None
    allowed = {_tool_name(tool) for tool in tools or []}
    name = match.group(1)
    if name not in allowed:
        return None
    try:
        arguments = json.loads(match.group(2))
    except json.JSONDecodeError:
        return None
    if not isinstance(arguments, dict):
        return None
    return {"name": name, "arguments": arguments}

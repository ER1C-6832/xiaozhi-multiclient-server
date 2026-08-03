import unittest

from core.providers.tools.tool_arguments import (
    normalize_resolve_arguments,
    normalize_tag_name,
    normalize_tool_arguments,
)


class ToolArgumentNormalizationTest(unittest.TestCase):
    def test_explicit_numeric_title_becomes_exact_title(self):
        self.assertEqual(
            normalize_resolve_arguments({"query": "标题是3的", "limit": 5}),
            {"exact_title": "3", "limit": 5},
        )
        self.assertEqual(
            normalize_resolve_arguments({"query": "3"}),
            {"exact_title": "3"},
        )
        self.assertNotIn("note_id", normalize_resolve_arguments({"query": "3"}))

    def test_content_selector_is_reduced_to_semantic_text(self):
        self.assertEqual(
            normalize_resolve_arguments({"query": "内容叫嘻嘻哈哈的"}),
            {"query": "嘻嘻哈哈"},
        )
        self.assertEqual(
            normalize_resolve_arguments({"query": "正文是屏幕报价的便签"}),
            {"query": "屏幕报价"},
        )

    def test_tag_page_wrappers_are_removed(self):
        self.assertEqual(normalize_tag_name("打开客户标签页"), "客户")
        self.assertEqual(
            normalize_tool_arguments("ui_show_tag", {"tag": "客户标签页"}),
            {"tag": "客户"},
        )
        self.assertEqual(
            normalize_tool_arguments(
                "tags_bind", {"note_ids": [1], "operation": "add", "tags": ["客户标签", "客户"]}
            ),
            {"note_ids": [1], "operation": "add", "tags": ["客户"]},
        )
        self.assertEqual(
            normalize_tool_arguments("tags_search", {"query": "有没有客户标签"}),
            {"query": "客户"},
        )
        self.assertEqual(
            normalize_tool_arguments("tags_create", {"name": "新建项目标签"}),
            {"name": "项目"},
        )
        self.assertEqual(
            normalize_tool_arguments("tags_delete", {"name": "删掉测试标签"}),
            {"name": "测试"},
        )


if __name__ == "__main__":
    unittest.main()

from novel_selector.legado import evaluate_list, evaluate_value, render_url, source_support_status


def test_render_url_replaces_key_and_page():
    assert render_url("/search?q={{key}}&page={{page}}", "大道", 3) == "/search?q=%E5%A4%A7%E9%81%93&page=3"


def test_evaluate_jsonpath_search_rules():
    document = '{"data":{"books":[{"name":"书","author":"作者","kind":"完结"}]}}'
    items = evaluate_list(document, "application/json", "$.data.books[*]")
    assert len(items) == 1
    assert evaluate_value(items[0], "$.name") == "书"
    assert evaluate_value(items[0], "$.author") == "作者"


def test_evaluate_json_shorthand_and_interpolation():
    item = {"bookId": "123", "title": "书", "tagList": ["仙侠", "完结"], "status": "完结"}
    assert evaluate_value(item, "title") == "书"
    assert evaluate_value(item, "{{$.tagList}},{{$.status}}") == "仙侠,完结,完结"
    assert evaluate_value(item, "https://example.test/book/{$.bookId}") == "https://example.test/book/123"


def test_evaluate_basic_html_rule():
    html = '<div class="book"><a href="/b/1">书名</a><span class="author">作者</span></div>'
    items = evaluate_list(html, "text/html", ".book")
    assert len(items) == 1
    assert evaluate_value(items[0], "a@text") == "书名"
    assert evaluate_value(items[0], "a@href") == "/b/1"
    assert evaluate_value(items[0], ".author@text") == "作者"


def test_js_source_is_unsupported():
    supported, reason = source_support_status({"searchUrl": "@js:result='x'", "ruleSearch": {"bookList": "$"}})
    assert supported is False
    assert "js" in reason

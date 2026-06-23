from kalshi.data.sources.news import summarize_news_items


def test_summarize_formats_title_and_description():
    items = [
        {"title": "Argentina lose Messi to injury", "description": "He is doubtful for the World Cup opener."},
        {"headline": "Austria name unchanged lineup"},
    ]
    out = summarize_news_items(items)
    assert "- Argentina lose Messi to injury" in out
    assert "doubtful" in out
    assert "- Austria name unchanged lineup" in out


def test_summarize_skips_empty_titles_and_truncates_long_desc():
    long_desc = "x" * 500
    items = [
        {"title": "", "description": "ignored"},
        {"title": "Real headline", "description": long_desc},
    ]
    out = summarize_news_items(items)
    assert "ignored" not in out
    assert "Real headline" in out
    # description is truncated to <=180 chars
    body = out.split("—", 1)[1]
    assert len(body.strip()) <= 180


def test_summarize_respects_limit_and_handles_empty():
    items = [{"title": f"News {i}"} for i in range(20)]
    out = summarize_news_items(items, limit=3)
    assert out.count("\n") == 2          # 3 lines -> 2 newlines
    assert summarize_news_items([]) == ""
    assert summarize_news_items(None) == ""

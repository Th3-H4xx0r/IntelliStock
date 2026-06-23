from kalshi.decisions import decision_doc, summarize_decisions


def test_decision_doc_captures_model_sharp_llm_and_edge():
    d = decision_doc(
        instance_id="i1", brokerage_id="b1", ts="t", fixture_id="f1",
        market_ticker="KX-HOME", side="home", model_prob=0.55, sharp_prob=0.52,
        llm_adjustment=0.01, llm_rationale="Home unbeaten in 6; key striker fit.",
        fused_fair=0.55, edge=0.04, fee=0.01, size=12, opportunity_score=0.8, decision="placed",
    )
    assert d["id"] == "i1|KX-HOME|t"
    assert d["model_prob"] == 0.55 and d["sharp_prob"] == 0.52
    assert d["llm_rationale"].startswith("Home unbeaten")
    assert d["decision"] == "placed" and d["edge"] == 0.04
    assert d["outcome"] is None and d["clv"] is None  # filled on settlement


def test_summarize_groups_by_decision():
    rows = [{"decision": "placed"}, {"decision": "placed"}, {"decision": "skipped"}, {"decision": "queued"}]
    s = summarize_decisions(rows)
    assert s["placed"] == 2 and s["skipped"] == 1 and s["queued"] == 1 and s["total"] == 4

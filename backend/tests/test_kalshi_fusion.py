from kalshi.intelligence.fusion import fuse, clamp_adjustment, renormalize_group


def test_clamp_adjustment():
    assert clamp_adjustment(0.20, cap=0.05) == 0.05
    assert clamp_adjustment(-0.20, cap=0.05) == -0.05
    assert clamp_adjustment(0.02, cap=0.05) == 0.02


def test_fuse_blends_and_applies_clamped_llm():
    f = fuse(sharp=0.50, model=0.60, llm_adjustment=0.20, w_sharp=0.7, llm_cap=0.05)
    base = 0.7 * 0.50 + 0.3 * 0.60
    assert abs(f - (base + 0.05)) < 1e-9


def test_fuse_model_only_when_no_sharp():
    f = fuse(sharp=None, model=0.62, llm_adjustment=0.0, w_sharp=0.7, llm_cap=0.05)
    assert abs(f - 0.62) < 1e-9


def test_fuse_clamps_to_unit_interval():
    assert fuse(sharp=0.99, model=0.99, llm_adjustment=0.5, w_sharp=0.5, llm_cap=0.05) <= 1.0
    assert fuse(sharp=0.01, model=0.01, llm_adjustment=-0.5, w_sharp=0.5, llm_cap=0.05) >= 0.0


def test_renormalize_group_sums_to_one():
    g = renormalize_group({"home": 0.6, "draw": 0.3, "away": 0.3})
    assert abs(sum(g.values()) - 1.0) < 1e-9
    assert g["home"] == 0.5

from kalshi.quant.dixon_coles import scoreline_matrix, rho_correction


def _home_win(m):
    n = len(m)
    return sum(m[i][j] for i in range(n) for j in range(n) if i > j)


def test_matrix_sums_to_one():
    m = scoreline_matrix(home_xg=1.6, away_xg=1.1, max_goals=10)
    total = sum(sum(row) for row in m)
    assert abs(total - 1.0) < 1e-6


def test_higher_home_xg_raises_home_win_mass():
    m1 = scoreline_matrix(1.2, 1.2, 10)
    m2 = scoreline_matrix(2.0, 1.0, 10)
    assert _home_win(m2) > _home_win(m1)


def test_rho_correction_adjusts_low_scores():
    assert rho_correction(0, 0, 1.5, 1.2, rho=-0.1) != 1.0
    assert rho_correction(1, 1, 1.5, 1.2, rho=-0.1) != 1.0
    assert rho_correction(3, 2, 1.5, 1.2, rho=-0.1) == 1.0  # only low cells adjusted

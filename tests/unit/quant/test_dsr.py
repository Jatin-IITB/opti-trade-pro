"""Formula and behaviour tests for the deflated Sharpe ratio.

Reference: Bailey & López de Prado (2014), JPM 40(5). The hand-check below
recomputes the closed form inline with scipy, independently of the
implementation, for one fixed input set.
"""

import math

import numpy as np
import pytest
from scipy.stats import norm

from optitrade.backtest import deflated_sharpe_ratio

pytestmark = pytest.mark.unit

# One fixed, representative input set: daily SR 0.1 over a year of daily
# observations, mildly left-skewed and fat-tailed P&L, 10 trials.
SR = 0.1
N_TRIALS = 10
N_OBS = 252
SKEW = -0.5
KURT = 4.0  # raw kurtosis (Gaussian = 3)


def reference_dsr(sr, n_trials, n_obs, skew, kurt):
    gamma = np.euler_gamma
    sr0 = math.sqrt(1.0 / (n_obs - 1)) * (
        (1.0 - gamma) * norm.ppf(1.0 - 1.0 / n_trials)
        + gamma * norm.ppf(1.0 - 1.0 / (n_trials * math.e))
    )
    denom = math.sqrt(1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr)
    return float(norm.cdf((sr - sr0) * math.sqrt(n_obs - 1) / denom))


class TestFormula:
    def test_matches_inline_reference_computation(self):
        expected = reference_dsr(SR, N_TRIALS, N_OBS, SKEW, KURT)
        assert deflated_sharpe_ratio(SR, N_TRIALS, N_OBS, SKEW, KURT) == pytest.approx(
            expected, rel=1e-12
        )

    def test_gaussian_case_matches_reference(self):
        expected = reference_dsr(SR, N_TRIALS, N_OBS, 0.0, 3.0)
        assert deflated_sharpe_ratio(SR, N_TRIALS, N_OBS, 0.0, 3.0) == pytest.approx(
            expected, rel=1e-12
        )

    def test_result_is_a_probability(self):
        value = deflated_sharpe_ratio(SR, N_TRIALS, N_OBS, SKEW, KURT)
        assert 0.0 <= value <= 1.0


class TestMonotonicityInTrials:
    def test_more_trials_means_lower_probability(self):
        d2 = deflated_sharpe_ratio(SR, 2, N_OBS, SKEW, KURT)
        d10 = deflated_sharpe_ratio(SR, 10, N_OBS, SKEW, KURT)
        d100 = deflated_sharpe_ratio(SR, 100, N_OBS, SKEW, KURT)
        assert d2 > d10 > d100

    def test_single_trial_still_discounts_and_stays_below_one(self):
        d1 = deflated_sharpe_ratio(SR, 1, N_OBS, SKEW, KURT)
        assert d1 < 1.0
        # SR0(1) = 0 exactly (expected max of one standard-normal draw), so a
        # single trial is the probabilistic Sharpe ratio against zero — still
        # below the naive Phi(SR * sqrt(n_obs)) because of the n_obs - 1 and
        # the (here variance-inflating) skew/kurtosis adjustment.
        assert d1 < float(norm.cdf(SR * math.sqrt(N_OBS)))
        # And more trials can only deflate further.
        assert d1 > deflated_sharpe_ratio(SR, 2, N_OBS, SKEW, KURT)


class TestValidation:
    def test_zero_trials_rejected(self):
        with pytest.raises(ValueError, match="n_trials"):
            deflated_sharpe_ratio(SR, 0, N_OBS, SKEW, KURT)

    def test_too_few_observations_rejected(self):
        with pytest.raises(ValueError, match="n_obs"):
            deflated_sharpe_ratio(SR, N_TRIALS, 1, SKEW, KURT)

    def test_pathological_variance_adjustment_fails_loud(self):
        # skew * SR = 20 makes the variance adjustment negative.
        with pytest.raises(ValueError, match="variance adjustment"):
            deflated_sharpe_ratio(0.1, N_TRIALS, N_OBS, 200.0, 3.0)

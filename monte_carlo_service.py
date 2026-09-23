"""Daily copper price scenarios with historical calibration and rolling validation.

These are price distributions, not futures account P&L: rolls, margin, fees and
leverage are excluded. HG=F is Yahoo's rolling front-contract series.
"""
import logging
import threading
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)
MODELS = ('ensemble', 'gaussian', 'bootstrap')
HORIZONS = (1, 5, 21, 63, 126, 252)


class DataUnavailable(RuntimeError):
    pass


def integer(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)) or not low <= value <= high:
        raise ValueError(f'{name} must be an integer from {low} to {high}')
    return int(value)


class MonteCarloSimulator:
    def __init__(self):
        self._history = None
        self._history_at = float('-inf')
        self._lock = threading.Lock()
        self._backtests = {}

    def fetch_historical_data(self, symbol='HG=F', period='5y'):
        if symbol != 'HG=F' or period != '5y':
            raise ValueError('This model is calibrated for HG=F over five years')
        with self._lock:
            if self._history is not None and time.monotonic() - self._history_at < 3600:
                return self._history.copy()
            try:
                data = yf.Ticker(symbol).history(period=period, interval='1d', auto_adjust=False, timeout=15)
                close = pd.to_numeric(data['Close'], errors='coerce').sort_index()
                close = close[~close.index.duplicated(keep='last')]
                # Reject bad observations instead of bridging missing trading sessions.
                if len(close) < 505 or not np.isfinite(close).all() or (close <= 0).any():
                    raise ValueError('Need at least 505 valid positive daily closes')
                age = pd.Timestamp.now(tz='UTC') - pd.Timestamp(close.index[-1]).tz_convert('UTC')
                if age > pd.Timedelta(days=7):
                    raise ValueError('Historical prices are more than seven days old')
                self._history = close
                self._history_at = time.monotonic()
                self._backtests.clear()
                return close.copy()
            except Exception as exc:
                raise DataUnavailable(f'Copper history unavailable: {exc}') from exc

    @staticmethod
    def parameters(returns):
        returns = np.asarray(returns, dtype=float)
        if len(returns) < 252 or not np.isfinite(returns).all():
            raise DataUnavailable('At least 252 finite daily log returns are required')
        long_var = float(np.var(returns, ddof=1))
        if long_var <= 0:
            raise DataUnavailable('Historical returns have zero variance')
        centered = returns - returns.mean()
        variance = long_var
        for value in centered:
            variance = .94 * variance + .06 * value * value
        return {'daily_volatility': float(np.sqrt(long_var)),
                'recent_daily_volatility': float(np.sqrt(variance)),
                'historical_daily_log_return': float(returns.mean())}

    def paths(self, returns, price, days, count, model, rng):
        params = self.parameters(returns)
        sigma = params['daily_volatility']
        # Neutral log drift avoids extrapolating a noisy historical trend for years.
        # Each step is ONE trading day; daily volatility is not divided by sqrt(252).
        if model == 'ensemble':
            split = count // 2
            return np.concatenate((self.paths(returns, price, days, split, 'gaussian', rng),
                                   self.paths(returns, price, days, count-split, 'bootstrap', rng)))
        if model == 'gaussian':
            shocks = rng.normal(0, sigma, (count, days))
        else:
            # Resample contiguous five-day blocks, preserving local clustering/tails.
            centered = np.asarray(returns) - np.mean(returns)
            starts = rng.integers(0, len(centered)-4, (count, (days+4)//5))
            indices = (starts[..., None] + np.arange(5)).reshape(count, -1)[:, :days]
            shocks = centered[indices]
            # Recent volatility decays toward the long-run estimate (21-day half-life).
            weight = np.exp(-np.log(2)*np.arange(days)/21)
            variance = sigma**2 + (params['recent_daily_volatility']**2-sigma**2)*weight
            shocks = shocks * np.sqrt(variance)[None, :] / sigma
        logs = np.concatenate((np.zeros((count, 1)), np.cumsum(shocks, axis=1)), axis=1)
        with np.errstate(over='raise', invalid='raise'):
            return price * np.exp(logs)

    @staticmethod
    def statistics(values, price):
        returns = values / price - 1
        losses = -returns
        var95, var99 = np.quantile(losses, [.95, .99])
        return {
            'mean_final_price': float(values.mean()), 'median_final_price': float(np.median(values)),
            'std_final_price': float(values.std()), 'min_price': float(values.min()), 'max_price': float(values.max()),
            'var_95': float(max(0, var95)), 'var_99': float(max(0, var99)),
            'expected_shortfall_95': float(max(0, losses[losses >= var95].mean())),
            'probability_profit': float(np.mean(returns > 0)),
            'probability_loss_10pct': float(np.mean(returns < -.1)),
            'expected_return': float(returns.mean()),
            'percentiles': {f'{q}th': float(np.percentile(values, q)) for q in (5, 10, 25, 50, 75, 90, 95)},
        }

    def backtest(self, closes):
        key = (str(closes.index[-1]), len(closes), float(closes.iloc[-1]))
        with self._lock:
            if key in self._backtests:
                return self._backtests[key]
        returns = np.diff(np.log(closes.to_numpy()))
        report = []
        for horizon in HORIZONS:
            # Non-overlapping outcomes, up to 20 recent origins, with 252-day training minimum.
            origins = list(range(len(closes)-1-horizon, 251, -horizon))[:20][::-1]
            for model in MODELS:
                covered, widths, errors, baseline, scores = [], [], [], [], []
                for origin in origins:
                    train = returns[max(0, origin-756):origin]
                    samples = self.paths(train, 1., horizon, 600, model,
                                         np.random.default_rng(1000+origin+horizon))[:, -1]
                    lo, median, hi = np.quantile(samples, [.05, .5, .95])
                    actual = float(closes.iloc[origin+horizon] / closes.iloc[origin])
                    covered.append(lo <= actual <= hi)
                    widths.append(hi-lo)
                    errors.append(abs(median-actual))
                    baseline.append(abs(1-actual))
                    scores.append(hi-lo + 20*max(lo-actual, 0) + 20*max(actual-hi, 0))
                report.append({'days': horizon, 'model': model, 'origins': len(origins),
                               'coverage_90': float(np.mean(covered)),
                               'mean_interval_width': float(np.mean(widths)),
                               'interval_score': float(np.mean(scores)),
                               'median_absolute_error': float(np.mean(errors)),
                               'unchanged_price_error': float(np.mean(baseline)),
                               'limited_sample': len(origins) < 10})
        result = {'method': 'Rolling origins; training data precede each outcome; non-overlapping outcomes per horizon. 600 paths per origin.',
                  'target_coverage': .9, 'results': report,
                  'note': 'Diagnostic only; no model is selected using these test results. Long horizons have few independent observations.'}
        with self._lock:
            self._backtests[key] = result
        return result

    def run_monte_carlo_simulation(self, current_price=None, days=63, n_simulations=2000,
                                   symbol='HG=F', model='ensemble', seed=42):
        days = integer(days, 'days', 1, 252)
        count = integer(n_simulations, 'n_simulations', 500, 5000)
        seed = integer(seed, 'seed', 0, 2**32-1)
        if model not in MODELS:
            raise ValueError('model must be ensemble, gaussian, or bootstrap')
        if isinstance(current_price, bool) or not isinstance(current_price, (float, int)) or not np.isfinite(current_price) or current_price <= 0:
            raise ValueError('current_price must be a finite positive number')
        closes = self.fetch_historical_data(symbol)
        returns = np.diff(np.log(closes.to_numpy()))[-756:]
        price_paths = self.paths(returns, current_price, max(days, 252), count, model, np.random.default_rng(seed))
        stats = self.statistics(price_paths[:, days], current_price)
        selected = price_paths[:, :days+1]
        drawdowns = 1 - selected / np.maximum.accumulate(selected, axis=1)
        levels = (5, 25, 50, 75, 95)
        bands = np.percentile(selected, levels, axis=0)
        return {'simulation_params': {'current_price': current_price, 'days': days, 'n_simulations': count,
                                      'model': model, 'seed': seed, 'drift': 0., **self.parameters(returns)},
                'statistics': stats,
                'risk_metrics': {'mean_maximum_drawdown': float(drawdowns.max(axis=1).mean()),
                                 'drawdown_95': float(np.quantile(drawdowns.max(axis=1), .95))},
                'fan_chart': {'days': list(range(days+1)), **{f'p{q}': bands[i].tolist() for i, q in enumerate(levels)}},
                'horizons': [{'days': h, **self.statistics(price_paths[:, h], current_price)} for h in HORIZONS],
                'backtest': self.backtest(closes),
                'data_quality': {'symbol': symbol, 'source': 'Yahoo Finance daily closes',
                                 'history_start': str(closes.index[0]), 'history_end': str(closes.index[-1]),
                                 'calibration_returns': len(returns), 'history_observations': len(closes)},
                'limitations': ['Zero mean log-return assumption; mean prices can rise due to dispersion.',
                               'HG=F can contain contract-roll discontinuities. These are retained, not treated as verified spot returns.',
                               'Bands describe model uncertainty; they do not guarantee coverage or include fees, margin, leverage or roll costs.',
                               'The ensemble mixes equal numbers of Gaussian and historical-block paths. This is not an accuracy claim.'],
                'timestamp': datetime.now(timezone.utc).isoformat()}

    @staticmethod
    def get_simulation_summary(results):
        stats = results['statistics']
        return {'risk_assessment': 'High' if stats['var_95'] > .2 else 'Medium' if stats['var_95'] > .1 else 'Low',
                'recommendation': 'Scenario analysis, not a buy/sell signal. Compare coverage and error against the unchanged-price baseline.',
                'price_range': {'pessimistic': stats['percentiles']['5th'], 'expected': stats['median_final_price'],
                                'optimistic': stats['percentiles']['95th']}}


monte_carlo_simulator = MonteCarloSimulator()


def run_simulation_api(current_price=None, days=63, n_simulations=2000, model='ensemble', seed=42):
    try:
        results = monte_carlo_simulator.run_monte_carlo_simulation(current_price, days, n_simulations, model=model, seed=seed)
        return {'success': True, 'results': results, 'summary': monte_carlo_simulator.get_simulation_summary(results)}
    except ValueError as exc:
        return {'success': False, 'error': str(exc), 'error_type': 'validation'}
    except Exception as exc:
        logger.exception('Monte Carlo simulation failed')
        return {'success': False, 'error': str(exc), 'error_type': 'unavailable'}

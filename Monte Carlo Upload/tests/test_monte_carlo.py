import json
import unittest
from unittest.mock import patch
import numpy as np
import pandas as pd
from monte_carlo_service import MonteCarloSimulator, run_simulation_api, DataUnavailable

class SimulationTests(unittest.TestCase):
    def setUp(self):
        self.model = MonteCarloSimulator()
        self.returns = np.random.default_rng(1).normal(0, .02, 760)
        self.closes = pd.Series(5*np.exp(np.r_[0, np.cumsum(self.returns)]), index=pd.bdate_range('2023-01-01', periods=761, tz='UTC'))

    def test_daily_variance(self):
        p = self.model.paths(self.returns, 5, 21, 20000, 'gaussian', np.random.default_rng(3))
        self.assertAlmostEqual(np.log(p[:, -1]/5).std()/(self.returns.std(ddof=1)*np.sqrt(21)), 1, delta=.03)

    def test_rng_isolation_and_reproducibility(self):
        state = np.random.get_state()
        a = self.model.paths(self.returns, 5, 10, 500, 'ensemble', np.random.default_rng(2))
        b = self.model.paths(self.returns, 5, 10, 500, 'ensemble', np.random.default_rng(2))
        np.testing.assert_array_equal(a, b)
        np.testing.assert_array_equal(state[1], np.random.get_state()[1])
        self.assertTrue((a > 0).all())

    def test_tail_loss(self):
        s = self.model.statistics(np.array([50., 80., 100., 120., 150.]), 100.)
        self.assertAlmostEqual(s['var_95'], .44)
        self.assertAlmostEqual(s['expected_shortfall_95'], .5)

    def test_validation(self):
        for args in ({'days':True}, {'days':253}, {'n_simulations':1000000}, {'model':'bad'}, {'current_price':float('nan')}, {'seed':-1}):
            params = dict(current_price=5., days=21, n_simulations=500)
            params.update(args)
            self.assertFalse(run_simulation_api(**params)['success'])

    def test_finite_compact_output(self):
        with patch.object(self.model, 'fetch_historical_data', return_value=self.closes):
            r = self.model.run_monte_carlo_simulation(5., 21, 500)
        json.dumps(r, allow_nan=False)
        self.assertEqual(len(r['horizons']), 6)
        self.assertEqual(len(r['backtest']['results']), 18)
        self.assertLess(len(json.dumps(r)), 100000)
        for lo, mid, hi in zip(r['fan_chart']['p5'], r['fan_chart']['p50'], r['fan_chart']['p95']):
            self.assertLessEqual(lo, mid)
            self.assertLessEqual(mid, hi)

    def test_backtest_excludes_future(self):
        seen = []
        def paths(train, price, days, count, model, rng):
            seen.append((np.array(train), days))
            return np.ones((count, days+1))
        with patch.object(self.model, 'paths', side_effect=paths):
            self.model.backtest(self.closes)
        returns = np.diff(np.log(self.closes.to_numpy()))
        for train, days in seen:
            self.assertTrue(any(np.array_equal(train, returns[max(0,end-756):end]) for end in range(252, len(self.closes)-days)))

    def test_api(self):
        import integrated_dashboard as app
        client = app.app.test_client()
        with patch.object(app, 'get_latest_copper_price', return_value={'status':'unavailable'}):
            self.assertEqual(client.post('/api/monte_carlo', json={}).status_code, 503)
            self.assertEqual(client.get('/api/quick_simulation').status_code, 503)
        self.assertEqual(client.post('/api/monte_carlo', json=[]).status_code, 400)
        quote = {'status':'available','source':'Yahoo Finance','price':6.5,'timestamp':'time'}
        payload = {'success':True,'results':{'data_quality':{}},'summary':{}}
        with patch.object(app, 'get_latest_copper_price', return_value=quote), patch.object(app, 'run_simulation_api', return_value=payload) as run:
            self.assertEqual(client.post('/api/monte_carlo', json={}).status_code, 200)
            self.assertEqual(run.call_args.args[0], 6.5)
        with patch('monte_carlo_service.monte_carlo_simulator.fetch_historical_data', side_effect=DataUnavailable('missing')):
            response = client.post('/api/monte_carlo', json={'current_price':5.})
            self.assertEqual(response.status_code, 503)
            self.assertFalse(response.json['success'])

import asyncio
import os
import unittest
from unittest.mock import patch, AsyncMock
from src import youtube_proxy_trial as t
from src.youtube_proxy import ByteBudget, DownloadBudgetExceeded

class PrepaidTests(unittest.TestCase):
    def test_dataimpulse_first_then_geonode_with_same_budget(self):
        calls = []
        budget = ByteBudget(500)
        def attempt(operation, **kwargs):
            calls.append(t._provider.get())
            self.assertIs(kwargs['budget'], budget)
            budget.add(50)
            if t._provider.get() == 'dataimpulse':
                raise RuntimeError('balance exhausted')
            return 'video'
        with patch.dict(os.environ, YOUTUBE_PROXY_DEFAULT_PROVIDER='dataimpulse', YOUTUBE_PROXY_TRIAL_PREFIX='test:prepaid'), patch.object(t, '_template', return_value='http://u:p@proxy.geonode.io:10000'), patch.object(t, '_record'):
            self.assertEqual(t.trial_retry(attempt)(None, budget=budget), 'video')
        self.assertEqual(calls, ['dataimpulse', 'geonode'])
        self.assertEqual(budget.snapshot(), 100)
        self.assertIsNone(t._route.get())

    def test_no_loop_or_extra_spending_after_both_providers_fail(self):
        calls = []
        def fail(operation, **kwargs):
            calls.append(t._provider.get())
            raise RuntimeError('no balance')
        with patch.dict(os.environ, YOUTUBE_PROXY_DEFAULT_PROVIDER='dataimpulse', YOUTUBE_PROXY_TRIAL_PREFIX='test:prepaid'), patch.object(t, '_template', return_value='http://u:p@proxy.geonode.io:10000'), patch.object(t, '_record'):
            with self.assertRaises(RuntimeError):
                t.trial_retry(fail)(None, budget=ByteBudget(500))
        self.assertEqual(calls, ['dataimpulse', 'geonode'])

    def test_new_generation_uses_dataimpulse_without_trial_admission(self):
        async def exercise():
            redis = AsyncMock(); redis.hget.return_value = None
            async def process(**kwargs):
                self.assertEqual(t._route.get().active, 'dataimpulse')
            with patch.dict(os.environ, YOUTUBE_PROXY_DEFAULT_PROVIDER='dataimpulse', YOUTUBE_PROXY_TRIAL_PREFIX='test:prepaid'), patch.object(t, '_template', return_value='http://u:p@proxy.geonode.io:10000'):
                await t.run_trial_job(process, redis, 'new-job', 'youtube')
                redis.eval.assert_not_called()
        asyncio.run(exercise())

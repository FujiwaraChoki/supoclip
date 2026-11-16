import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch
from src import youtube_proxy_trial as t
from src.youtube_proxy import ByteBudget


class DefaultRoutingTests(unittest.TestCase):
    def test_more_than_thirty_jobs_and_redis_outage_do_not_restore_trial_cap(self):
        async def exercise():
            redis = AsyncMock()
            redis.hget.return_value = None
            seen = []
            async def process(**kwargs):
                seen.append(t._route.get().active)
            with patch.dict(os.environ, YOUTUBE_PROXY_DEFAULT_PROVIDER='geonode',
                            YOUTUBE_PROXY_TRIAL_PREFIX='test:default'), patch.object(t, '_template', return_value='http://u:p@proxy.geonode.io:10000'):
                for i in range(35):
                    await t.run_trial_job(process, redis, str(i), 'youtube')
                redis.eval.assert_not_called()
                self.assertEqual(seen, ['geonode'] * 35)
                redis.hget.side_effect = ConnectionError('unavailable')
                await t.run_trial_job(process, redis, 'outage', 'youtube')
                self.assertEqual(seen[-1], 'dataimpulse')
            self.assertIsNone(t._route.get())
        asyncio.run(exercise())

    def test_standalone_download_uses_primary_then_shared_budget_fallback(self):
        seen = []
        budget = ByteBudget(500)
        def attempt(operation, **kwargs):
            seen.append(t._provider.get())
            kwargs['budget'].add(20)
            if t._provider.get() == 'geonode':
                raise RuntimeError('primary unavailable')
            return 'valid'
        with patch.dict(os.environ, YOUTUBE_PROXY_DEFAULT_PROVIDER='geonode',
                        YOUTUBE_PROXY_TRIAL_PREFIX='test:default'), patch.object(t, '_template', return_value='http://u:p@proxy.geonode.io:10000'), patch.object(t, '_record'):
            self.assertEqual(t.trial_retry(attempt)(None, budget=budget), 'valid')
        self.assertEqual(seen, ['geonode', 'dataimpulse'])
        self.assertEqual(budget.snapshot(), 40)
        self.assertIsNone(t._route.get())
        self.assertIsNone(t._provider.get())

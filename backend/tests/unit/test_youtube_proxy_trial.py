import asyncio
import copy
import json
import os
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import redis
import redis.asyncio
from src import youtube_proxy_trial as t
from src.config import get_config
from src.youtube_proxy import ByteBudget, DownloadBudgetExceeded


class TrialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        c = get_config()
        cls.redis_args = dict(host=c.redis_host, port=c.redis_port,
                              password=c.redis_password, socket_timeout=3)
        cls.r = redis.Redis(**cls.redis_args)

    def setUp(self):
        self.prefix = 'supoclip:proxy-trial:test:' + uuid.uuid4().hex
        self.owned = [self.prefix, self.prefix + ':jobs']
        self.r.hset(self.prefix, mapping={'status': 'active', 'admitted': 0})

    def tearDown(self):
        self.r.delete(*self.owned)

    def admit(self, task):
        key = self.prefix + ':job:' + task
        return self.r.eval(t.ADMIT, 3, self.prefix, key, self.prefix + ':jobs', task, '1')

    def route(self):
        return t.Route(self.prefix, 'job', 1, 'http://user:pass@proxy.geonode.io:10000')

    def test_atomic_thirty_job_limit_and_idempotent_retries(self):
        ids = [str(i) for i in range(64)]
        self.owned += [self.prefix + ':job:' + i for i in ids]
        with ThreadPoolExecutor(max_workers=16) as pool:
            results = list(pool.map(self.admit, ids))
        self.assertEqual(sorted(x for x in results if x), list(range(1, 31)))
        self.assertEqual(self.r.hget(self.prefix, 'admitted'), b'30')
        self.assertEqual(self.r.hget(self.prefix, 'status'), b'draining')
        winner = next(i for i, seq in zip(ids, results) if seq)
        self.assertEqual(self.admit(winner), results[int(winner)])
        self.assertEqual(self.admit('overflow'), 0)
        self.assertEqual(self.r.zcard(self.prefix + ':jobs'), 30)

    def test_disabled_trial_does_not_admit(self):
        self.r.hset(self.prefix, 'status', 'prepared')
        self.assertEqual(self.admit('not-admitted'), 0)

    def test_configuration_copy_does_not_change_normal_or_other_jobs(self):
        config = SimpleNamespace(youtube_proxy_url='dataimpulse', other='preserved')
        self.assertIs(t.trial_config(config), config)
        route = self.route()
        token = t._route.set(route)
        try:
            candidate = t.trial_config(config)
            self.assertEqual(candidate.youtube_proxy_url, route.template)
            self.assertEqual(config.youtube_proxy_url, 'dataimpulse')
            self.assertEqual(candidate.other, 'preserved')
            route.active = 'dataimpulse'
            self.assertIs(t.trial_config(config), config)
        finally:
            t._route.reset(token)

    def test_fallback_keeps_budget_and_pins_remaining_phases(self):
        route, calls, events = self.route(), [], []
        budget = ByteBudget(500)
        def operation(_operation, **kw):
            self.assertIs(kw['budget'], budget)
            provider = t._provider.get()
            calls.append(provider)
            budget.add(50 if provider == 'geonode' else 70)
            if provider == 'geonode':
                raise RuntimeError('unavailable')
            return 'valid-media'
        wrapped = t.trial_retry(operation)
        token = t._route.set(route)
        try:
            with patch.object(t, '_record', side_effect=lambda r,e,**v: events.append((e,v))):
                self.assertEqual(wrapped(None, budget=budget), 'valid-media')
                self.assertEqual(wrapped(None, budget=budget), 'valid-media')
        finally:
            t._route.reset(token)
        self.assertEqual(calls, ['geonode', 'dataimpulse', 'dataimpulse'])
        self.assertEqual(budget.snapshot(), 190)
        self.assertEqual([v['bytes'] for e,v in events if e == 'provider_call'], [50,70,70])
        self.assertIsNone(t._provider.get())
        self.assertIsNone(t._route.get())

    def test_budget_exhaustion_does_not_start_fallback(self):
        route, calls = self.route(), []
        budget = ByteBudget(10)
        def operation(_operation, **kw):
            calls.append(t._provider.get())
            kw['budget'].add(11)
        token = t._route.set(route)
        try:
            with patch.object(t, '_record'):
                with self.assertRaises(DownloadBudgetExceeded):
                    t.trial_retry(operation)(None, budget=budget)
        finally:
            t._route.reset(token)
        self.assertEqual(calls, ['geonode'])

    def test_normal_download_and_dataimpulse_failure_are_not_retried(self):
        def fail(*a, **kw): raise ValueError('normal error')
        with self.assertRaises(ValueError):
            t.trial_retry(fail)(None, budget=ByteBudget(10))
        route = self.route(); route.active = 'dataimpulse'
        token = t._route.set(route)
        try:
            with patch.object(t, '_record') as record:
                with self.assertRaises(ValueError):
                    t.trial_retry(fail)(None, budget=ByteBudget(10))
                self.assertEqual(record.call_count, 1)
        finally:
            t._route.reset(token)

    def test_async_job_filter_context_propagation_and_named_arguments(self):
        async def exercise():
            r = redis.asyncio.Redis(**self.redis_args)
            with tempfile.TemporaryDirectory() as directory:
                secret = Path(directory)/'secret.json'
                secret.write_text(json.dumps({'GEONODE_PROXY_URL': self.route().template}))
                with patch.dict(os.environ, {'YOUTUBE_PROXY_TRIAL_PREFIX':self.prefix,
                                               'YOUTUBE_PROXY_TRIAL_SECRET_FILE':str(secret)}):
                    async def process(**kw):
                        self.assertEqual(kw['task_id'], 'actual-job')
                        route = await asyncio.to_thread(t._route.get)
                        return route.sequence if route else 0
                    self.owned.append(self.prefix + ':job:actual-job')
                    self.assertEqual(await t.run_trial_job(process,r,'actual-job','twitch',task_id='actual-job'),0)
                    self.assertEqual(await t.run_trial_job(process,r,'actual-job','youtube',task_id='actual-job'),1)
                    self.assertEqual(await t.run_trial_job(process,r,'actual-job','youtube',task_id='actual-job'),1)
                    self.assertIsNone(t._route.get())
            await r.aclose()
        asyncio.run(exercise())
        self.assertEqual(self.r.hget(self.prefix,'admitted'),b'1')


if __name__ == '__main__':
    unittest.main(verbosity=2)

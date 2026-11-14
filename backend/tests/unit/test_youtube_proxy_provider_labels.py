import unittest
from unittest.mock import patch
from src import youtube_proxy_trial as t
class ProviderLabelTests(unittest.TestCase):
 def test_cache_and_paid_recovery_keep_actual_provider(self):
  route=t.Route('test:labels','task',0,'http://u:p@proxy.geonode.io:10000')
  token=t._route.set(route)
  try:
   with patch.object(t,'_record'):
    log=t.trial_log(lambda **kwargs:kwargs['provider'])
    for provider in ['cache','rapidapi','apify']:
     self.assertEqual(log(provider=provider),provider)
    self.assertEqual(log(provider='yt_dlp_proxy'),'yt_dlp_geonode')
  finally:t._route.reset(token)

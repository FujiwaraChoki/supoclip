import hashlib
import json
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
import requests
from src import budget_apify_downloader as mod
from src.config import Config, set_config_override

FILE = 'example/file-worker'
URLS = 'example/url-worker'
SIGNED = 'example/signed-worker'
MEDIA = 'example/media-worker'
PROVIDERS = [dict(actor=actor, adapter=adapter, build='1.0.0', max_charge_usd=cap)
             for actor, adapter, cap in [(FILE, 'kv_file', .02), (URLS, 'url_objects', 6),
                                          (SIGNED, 'signed_url', 6), (MEDIA, 'kv_media', 6)]]

VIDEO = 'aqz-KE-bpKQ'

@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("APIFY_YOUTUBE_PROVIDERS", json.dumps(PROVIDERS))
    c = Config(); c.apify_youtube_downloader_actor = FILE
    c.apify_youtube_default_quality = '1080'; c.apify_run_timeout_seconds = 900
    c.temp_dir = str(tmp_path); set_config_override(c)
    monkeypatch.delenv('APIFY_YOUTUBE_FALLBACK_ACTOR', raising=False)
    monkeypatch.delenv('APIFY_YOUTUBE_FALLBACK_ACTORS', raising=False)
    monkeypatch.delenv('APIFY_DOWNLOAD_MAX_CHARGE_USD', raising=False)
    monkeypatch.delenv('APIFY_EXPENSIVE_FALLBACKS', raising=False)
    yield c
    set_config_override(None)

def record():
    return {'videoId': VIDEO, 'resultType': 'file', 'storage': 'apify',
            'fileKey': VIDEO + '_1080p.mp4', 'fileSizeBytes': 5,
            'sha256': hashlib.sha256(b'video').hexdigest(), 'durationSeconds': 635}

def test_bounded_kv_file_submission_and_run_owned_file(config, tmp_path, monkeypatch):
    calls = []
    run = {'id': 'run1', 'status': 'SUCCEEDED', 'defaultDatasetId': 'ds1', 'defaultKeyValueStoreId': 'store1'}
    def api(session, method, path, deadline, **kw):
        calls.append((method, kw)); return {'data': run} if method == 'POST' else [record()]
    monkeypatch.setattr(mod, '_api', api)
    def download(url, target, *args, **kwargs):
        assert url == 'https://api.apify.com/v2/key-value-stores/store1/records/aqz-KE-bpKQ_1080p.mp4'
        target.write_bytes(b'video')
    monkeypatch.setattr(mod, '_download', download)
    monkeypatch.setattr(mod, '_verify_media', lambda *a: None)
    path = mod.download_budget_apify(VIDEO, tmp_path, '1080', 'secret')
    assert path.read_bytes() == b'video'
    assert path.parent.name.startswith('apify-' + VIDEO + '-')
    assert calls[0][1]['params']['maxTotalChargeUsd'] == .02
    assert calls[0][1]['params']['restartOnError'] == 'false'
    assert calls[0][1]['json']['quality'] == '1080p'

def test_wrong_video_rejected_and_staging_removed(config, tmp_path, monkeypatch):
    wrong = record(); wrong['videoId'] = 'S7CrlFLAmEA'
    monkeypatch.setattr(mod, '_api', lambda s, method, *a, **k: {'data': {'id': 'r1', 'status': 'SUCCEEDED', 'defaultDatasetId': 'ds'}} if method == 'POST' else [wrong])
    with pytest.raises(mod.BudgetApifyError, match='matching video'):
        mod.download_budget_apify(VIDEO, tmp_path, '1080', 'secret')
    assert not list(tmp_path.iterdir())

def test_ambiguous_submission_not_repeated(config):
    session = Mock(); session.request.side_effect = requests.Timeout('secret provider payload')
    with pytest.raises(mod.BudgetApifyError, match='could not be reached') as error:
        mod._api(session, 'POST', 'acts/test/runs', mod.time.monotonic() + 30)
    assert 'secret' not in str(error.value)
    assert session.request.call_count == 1

def test_only_explicit_apify_fallback(config, tmp_path, monkeypatch):
    monkeypatch.setenv('APIFY_EXPENSIVE_FALLBACKS', 'true')
    monkeypatch.setenv('APIFY_YOUTUBE_FALLBACK_ACTOR', URLS)
    monkeypatch.setenv('APIFY_FALLBACK_MAX_CHARGE_USD', '1')
    calls = []
    def run(actor, *args):
        calls.append((actor, args[-1]))
        if actor == FILE: raise mod.BudgetApifyError('timed out')
        return tmp_path / 'result.mp4'
    monkeypatch.setattr(mod, '_run', run)
    assert mod.download_budget_apify(VIDEO, tmp_path, '1080', 'secret').name == 'result.mp4'
    assert calls == [(FILE, .02), (URLS, 1)]

def test_permanent_failure_skips_fallback(config, tmp_path, monkeypatch):
    monkeypatch.setenv('APIFY_EXPENSIVE_FALLBACKS', 'true')
    monkeypatch.setenv('APIFY_YOUTUBE_FALLBACK_ACTOR', URLS)
    run = Mock(side_effect=mod.BudgetApifyError('private video', permanent=True))
    monkeypatch.setattr(mod, '_run', run)
    with pytest.raises(mod.BudgetApifyError, match='private video'):
        mod.download_budget_apify(VIDEO, tmp_path, '1080', 'secret')
    assert run.call_count == 1

def test_short_audio_track_rejected(tmp_path, monkeypatch):
    probe = {'format': {'duration': '634.6'}, 'streams': [{'codec_type': 'video', 'width': 1920, 'height': 1080, 'duration': '634.5'}, {'codec_type': 'audio', 'duration': '68'}]}
    monkeypatch.setattr(mod.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout=json.dumps(probe)))
    with pytest.raises(mod.BudgetApifyError, match='incomplete or invalid'):
        mod._verify_media(tmp_path / 'video.mp4', record(), mod.time.monotonic() + 30)

def test_checksum_failure_and_no_token_to_cdn(tmp_path, monkeypatch):
    response = Mock(status_code=200, is_redirect=False); response.iter_content.return_value = [b'wrong']
    session = Mock(); session.__enter__ = Mock(return_value=session); session.__exit__ = Mock(return_value=False); session.get.return_value = response
    monkeypatch.setattr(mod.requests, 'Session', lambda: session)
    monkeypatch.setattr(mod, '_public_https', lambda url: mod.urlsplit(url))
    with pytest.raises(mod.BudgetApifyError, match='checksum'):
        mod._download('https://cdn.example.com/file', tmp_path / 'file.part', record(), {'defaultKeyValueStoreId': 'ours'}, 'secret', mod.time.monotonic() + 30)
    assert session.get.call_args.kwargs['headers'] == {}

def test_insecure_or_private_file_host_rejected(monkeypatch):
    with pytest.raises(mod.BudgetApifyError, match='insecure'): mod._public_https('http://example.com/file')
    monkeypatch.setattr(mod.socket, 'getaddrinfo', lambda *a, **k: [(None, None, None, None, ('127.0.0.1', 443))])
    with pytest.raises(mod.BudgetApifyError, match='invalid'): mod._public_https('https://internal.example/file')


def test_url_objects_requests_1080_without_paid_proxy(config, tmp_path, monkeypatch):
    config.apify_youtube_downloader_actor = URLS
    monkeypatch.setenv('APIFY_FALLBACK_MAX_CHARGE_USD', '6')
    calls = []
    run = {'id': 'run1', 'status': 'SUCCEEDED', 'defaultDatasetId': 'ds1'}
    item = dict(record(), status='success', downloadUrl='https://cdn.example.com/video.mp4')
    def api(session, method, path, deadline, **kw):
        calls.append((method, kw))
        return {'data': run} if method == 'POST' else [item]
    monkeypatch.setattr(mod, '_api', api)
    monkeypatch.setattr(mod, '_download', lambda url, target, *args, **kwargs: target.write_bytes(b'video'))
    monkeypatch.setattr(mod, '_verify_media', lambda *a: None)
    assert mod.download_budget_apify(VIDEO, tmp_path, '1080', 'secret').is_file()
    assert calls[0][1]['json'] == {'urls': [{'url': 'https://www.youtube.com/watch?v=' + VIDEO}],
                                  'quality': '1080', 'format': 'default', 'residentialProxyMode': 'disabled'}
    assert calls[0][1]['params']['maxTotalChargeUsd'] == 6


def test_transient_poll_failure_reuses_run_without_resubmission(config, tmp_path, monkeypatch):
    calls = []
    run = {'id': 'run1', 'status': 'RUNNING', 'defaultDatasetId': 'ds1', 'defaultKeyValueStoreId': 'store1'}
    polls = 0
    def api(session, method, path, deadline, **kw):
        nonlocal polls
        calls.append((method, path))
        if method == 'POST': return {'data': run}
        if path.startswith('actor-runs/'):
            polls += 1
            if polls == 1: raise mod.BudgetApifyError('temporary network outage')
            return {'data': dict(run, status='SUCCEEDED')}
        return [record()]
    monkeypatch.setattr(mod, '_api', api)
    monkeypatch.setattr(mod.time, 'sleep', lambda _: None)
    monkeypatch.setattr(mod, '_download', lambda url, target, *args, **kwargs: target.write_bytes(b'video'))
    monkeypatch.setattr(mod, '_verify_media', lambda *a: None)
    assert mod.download_budget_apify(VIDEO, tmp_path, '1080', 'secret').is_file()
    assert len([call for call in calls if call[0] == 'POST']) == 1
    assert polls == 2


def test_ordered_fallbacks_share_one_spending_allowance(config, tmp_path, monkeypatch):
    config.apify_youtube_downloader_actor = URLS
    monkeypatch.setenv('APIFY_EXPENSIVE_FALLBACKS', 'true')
    monkeypatch.setenv('APIFY_YOUTUBE_FALLBACK_ACTORS', SIGNED + ',' + MEDIA)
    calls = []
    def run(actor, *args):
        calls.append((actor, args[-1]))
        if actor == URLS: raise mod.BudgetApifyError('transport failed', spent=2)
        if actor == SIGNED: raise mod.BudgetApifyError('file unavailable', spent=1)
        return tmp_path / 'result.mp4'
    monkeypatch.setattr(mod, '_run', run)
    assert mod.download_budget_apify(VIDEO, tmp_path, '1080', 'secret').name == 'result.mp4'
    assert calls == [(URLS, 6), (SIGNED, 4), (MEDIA, 3)]


def test_unknown_charge_cannot_double_spend_with_fallback(config, tmp_path, monkeypatch):
    config.apify_youtube_downloader_actor = URLS
    monkeypatch.setenv('APIFY_EXPENSIVE_FALLBACKS', 'true')
    monkeypatch.setenv('APIFY_YOUTUBE_FALLBACK_ACTORS', SIGNED + ',' + MEDIA)
    run = Mock(side_effect=mod.BudgetApifyError('submission response lost'))
    monkeypatch.setattr(mod, '_run', run)
    with pytest.raises(mod.BudgetApifyError, match='spending allowance'):
        mod.download_budget_apify(VIDEO, tmp_path, '1080', 'secret')
    assert run.call_count == 1


def test_duplicate_fallbacks_are_attempted_once(config, tmp_path, monkeypatch):
    monkeypatch.setenv('APIFY_EXPENSIVE_FALLBACKS', 'true')
    monkeypatch.setenv('APIFY_YOUTUBE_FALLBACK_ACTORS', ','.join([FILE, FILE, URLS]))
    calls = []
    def run(actor, *args):
        calls.append(actor)
        if actor == FILE: raise mod.BudgetApifyError('blocked', spent=0)
        return tmp_path / 'file.mp4'
    monkeypatch.setattr(mod, '_run', run)
    mod.download_budget_apify(VIDEO, tmp_path, '1080', 'secret')
    assert calls == [FILE, URLS]


def test_expensive_fallbacks_are_off_by_default(config, tmp_path, monkeypatch):
    monkeypatch.setenv('APIFY_YOUTUBE_FALLBACK_ACTORS', URLS)
    calls = []

    def run(actor, *args):
        calls.append(actor)
        if actor == FILE:
            raise mod.BudgetApifyError('timed out')
        return tmp_path / 'result.mp4'

    monkeypatch.setattr(mod, '_run', run)
    with pytest.raises(mod.BudgetApifyError, match='timed out'):
        mod.download_budget_apify(VIDEO, tmp_path, '1080', 'secret')
    assert calls == [FILE]


def test_signed_url_uses_independent_source_duration(config, tmp_path, monkeypatch):
    config.apify_youtube_downloader_actor = SIGNED
    monkeypatch.setattr('src.youtube_utils.get_youtube_video_info', lambda url: {'duration': 635})
    run = {'id': 'run1', 'status': 'SUCCEEDED', 'defaultDatasetId': 'ds1'}
    item = {'sourceUrl': 'https://www.youtube.com/watch?v=' + VIDEO, 'downloadUrl': 'https://cdn.example.com/file.mp4'}
    monkeypatch.setattr(mod, '_api', lambda session, method, *a, **kw: {'data': run} if method == 'POST' else [item])
    def download(url, path, row, *args, **kwargs):
        assert row['durationSeconds'] == 635
        assert kwargs['allow_size_header'] is True
        path.write_bytes(b'video')
    monkeypatch.setattr(mod, '_download', download)
    monkeypatch.setattr(mod, '_verify_media', lambda *a: None)
    assert mod.download_budget_apify(VIDEO, tmp_path, '1080', 'secret').is_file()


def test_kv_media_uses_owned_store_instead_of_supplied_media_url(config, tmp_path, monkeypatch):
    config.apify_youtube_downloader_actor = MEDIA
    run = {'id': 'run1', 'status': 'SUCCEEDED', 'defaultDatasetId': 'ds1', 'defaultKeyValueStoreId': 'owned'}
    item = dict(record(), ok=True, mediaKey='video.mp4', mediaUrl='https://untrusted.example/file')
    monkeypatch.setattr(mod, '_api', lambda session, method, *a, **kw: {'data': run} if method == 'POST' else [item])
    def download(url, path, *args, **kwargs):
        assert url == 'https://api.apify.com/v2/key-value-stores/owned/records/video.mp4'
        path.write_bytes(b'video')
    monkeypatch.setattr(mod, '_download', download)
    monkeypatch.setattr(mod, '_verify_media', lambda *a: None)
    assert mod.download_budget_apify(VIDEO, tmp_path, '1080', 'secret').is_file()


def test_missing_size_requires_bounded_identity_http_length(tmp_path, monkeypatch):
    response = Mock(status_code=200, is_redirect=False, headers={'Content-Length': '5'})
    response.iter_content.return_value = [b'video']
    session = Mock(); session.__enter__ = Mock(return_value=session); session.__exit__ = Mock(return_value=False); session.get.return_value = response
    monkeypatch.setattr(mod.requests, 'Session', lambda: session)
    monkeypatch.setattr(mod, '_public_https', lambda url: mod.urlsplit(url))
    target = tmp_path / 'file.part'
    mod._download('https://cdn.example.com/file', target, {}, {}, 'secret', mod.time.monotonic() + 30, allow_size_header=True)
    assert target.read_bytes() == b'video'
    response.headers = {}
    with pytest.raises(mod.BudgetApifyError, match='no usable size'):
        mod._download('https://cdn.example.com/file', target, {}, {}, 'secret', mod.time.monotonic() + 30, allow_size_header=True)


@pytest.mark.parametrize('size', [2_007_450_000, 10_000_000_000])
def test_signed_delivery_admits_large_files_through_10gb(tmp_path, monkeypatch, size):
    # Admit the size without allocating gigabytes; a deliberately empty stream
    # must still fail the independent transfer-completeness check.
    response = Mock(status_code=200, is_redirect=False,
                    headers={'Content-Length': str(size)})
    response.iter_content.return_value = []
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    session.get.return_value = response
    monkeypatch.setattr(mod.requests, 'Session', lambda: session)
    monkeypatch.setattr(mod, '_public_https', lambda url: mod.urlsplit(url))

    with pytest.raises(mod.BudgetApifyError, match='transfer was incomplete'):
        mod._download('https://cdn.example.com/file', tmp_path / 'file', {}, {},
                      'secret', mod.time.monotonic() + 30, allow_size_header=True)

    response.iter_content.assert_called_once()
    response.close.assert_called_once()


def test_signed_delivery_rejects_above_10gb_before_reading_body(tmp_path, monkeypatch):
    response = Mock(status_code=200, is_redirect=False,
                    headers={'Content-Length': '10000000001'})
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    session.get.return_value = response
    monkeypatch.setattr(mod.requests, 'Session', lambda: session)
    monkeypatch.setattr(mod, '_public_https', lambda url: mod.urlsplit(url))

    with pytest.raises(mod.BudgetApifyError, match='no usable size'):
        mod._download('https://cdn.example.com/file', tmp_path / 'file', {}, {},
                      'secret', mod.time.monotonic() + 30, allow_size_header=True)

    response.iter_content.assert_not_called()
    response.close.assert_called_once()
    assert not (tmp_path / 'file').exists()


def test_known_delivery_size_above_10gb_rejected_before_network(tmp_path, monkeypatch):
    session = Mock()
    monkeypatch.setattr(mod.requests, 'Session', session)
    with pytest.raises(mod.BudgetApifyError, match='exceeds the download size limit'):
        mod._download('https://cdn.example.com/file', tmp_path / 'file',
                      {'fileSizeBytes': 10_000_000_001}, {}, 'secret',
                      mod.time.monotonic() + 30)
    session.assert_not_called()


def test_event_prices_reserve_charge_even_when_usage_total_lags():
    run = {'usageTotalUsd': .004, 'chargedEventCounts': {'start': 1, 'download': 1},
           'pricingInfo': {'pricingModel': 'PAY_PER_EVENT', 'pricingPerEvent': {'actorChargeEvents':
               {'start': {'eventPriceUsd': .004}, 'download': {'eventPriceUsd': .25}}}}}
    assert mod._charge_reservation(run, 6) == pytest.approx(.254)
    run['usageTotalUsd'] = .30
    assert mod._charge_reservation(run, 6) == .30


def test_unknown_billing_or_extra_platform_usage_reserves_whole_cap():
    assert mod._charge_reservation({'usageTotalUsd': 0}, 6) == 6
    run = {'usageTotalUsd': .01, 'chargedEventCounts': {'unknown': 1},
           'pricingInfo': {'pricingModel': 'PAY_PER_EVENT', 'pricingPerEvent': {'actorChargeEvents': {}}}}
    assert mod._charge_reservation(run, 6) == 6
    run['pricingInfo']['isPPEPlatformUsagePaidByUser'] = True
    assert mod._charge_reservation(run, 6) == 6


@pytest.mark.parametrize("providers", ["[]", "{}", "not-json", '[{"actor":"example/a","adapter":"unknown","build":"1.0.0"}]'])
def test_missing_or_invalid_provider_definitions_fail_closed(monkeypatch, providers):
    monkeypatch.setenv("APIFY_YOUTUBE_PROVIDERS", providers)
    with pytest.raises(mod.BudgetApifyError, match="environment configuration"):
        mod._providers()


def test_provider_build_and_identity_come_from_environment(config, monkeypatch):
    provider = dict(actor="custom-owner/private-worker", adapter="kv_file", build="3.2.1", max_charge_usd=.1)
    monkeypatch.setenv("APIFY_YOUTUBE_PROVIDERS", json.dumps([provider]))
    assert mod._providers() == {provider["actor"]: provider}


def test_private_delivery_pins_fresh_version_and_checks_complete_size(tmp_path):
    from datetime import datetime, timezone
    client = Mock()
    now = datetime.now(timezone.utc)
    client.head_object.return_value = {'VersionId': 'new', 'ContentLength': 5, 'LastModified': now}
    stream = Mock(); stream.iter_chunks.return_value = [b'video']
    client.get_object.return_value = {'Body': stream}
    storage = (client, 'private-bucket', 'video.mp4', 'old', now)
    item = mod._retrieve_private_file(storage, tmp_path / 'file.part', 635, '1080', mod.time.monotonic() + 30)
    assert item['minQuality'] == 1080
    client.get_object.assert_called_once_with(Bucket='private-bucket', Key='video.mp4', VersionId='new')
    stream.close.assert_called_once()


@pytest.mark.parametrize('version,age,size', [
    ('old', 0, 5), ('null', 0, 5), ('new', 60, 5), ('new', 0, 0),
    ('new', 0, 10_000_000_001),
])
def test_private_delivery_rejects_stale_unversioned_or_empty_object(tmp_path, version, age, size):
    from datetime import datetime, timezone, timedelta
    client = Mock(); now = datetime.now(timezone.utc)
    client.head_object.return_value = {'VersionId': version, 'ContentLength': size, 'LastModified': now - timedelta(seconds=age)}
    with pytest.raises(mod.BudgetApifyError, match='fresh complete'):
        mod._retrieve_private_file((client, 'b', 'k', 'old', now), tmp_path / 'part', 635, '1080', mod.time.monotonic() + 30)
    client.get_object.assert_not_called()


def test_private_delivery_rejects_short_transfer_and_closes_stream(tmp_path):
    from datetime import datetime, timezone
    client = Mock(); now = datetime.now(timezone.utc)
    client.head_object.return_value = {'VersionId': 'new', 'ContentLength': 5, 'LastModified': now}
    stream = Mock(); stream.iter_chunks.return_value = [b'bad']; client.get_object.return_value = {'Body': stream}
    with pytest.raises(mod.BudgetApifyError, match='fresh complete'):
        mod._retrieve_private_file((client, 'b', 'k', None, now), tmp_path / 'part', 635, '1080', mod.time.monotonic() + 30)
    stream.close.assert_called_once()


@pytest.mark.parametrize('size', [2_007_450_000, 10_000_000_000])
def test_private_delivery_admits_large_objects_but_still_checks_completeness(tmp_path, size):
    from datetime import datetime, timezone
    client = Mock()
    now = datetime.now(timezone.utc)
    client.head_object.return_value = {
        'VersionId': 'new', 'ContentLength': size, 'LastModified': now,
    }
    stream = Mock()
    stream.iter_chunks.return_value = []
    client.get_object.return_value = {'Body': stream}

    with pytest.raises(mod.BudgetApifyError, match='fresh complete'):
        mod._retrieve_private_file((client, 'bucket', 'key', None, now),
                                   tmp_path / 'part', 635, '1080',
                                   mod.time.monotonic() + 30)

    client.get_object.assert_called_once_with(Bucket='bucket', Key='key', VersionId='new')
    stream.iter_chunks.assert_called_once()
    stream.close.assert_called_once()


def test_private_delivery_rejects_720_when_1080_requested(tmp_path, monkeypatch):
    probe = {'format': {'duration': '635'}, 'streams': [{'codec_type': 'video', 'width': 1280, 'height': 720}, {'codec_type': 'audio'}]}
    monkeypatch.setattr(mod.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout=json.dumps(probe)))
    with pytest.raises(mod.BudgetApifyError, match='incomplete or invalid'):
        mod._verify_media(tmp_path / 'video.mp4', dict(record(), minQuality=1080), mod.time.monotonic() + 30)


@pytest.mark.parametrize('status,error_code,error_stage,accepted', [
    ('completed', None, None, True),
    ('success', None, None, True),
    ('failed', 'MISSING_OUTPUT_FILE', 'delivery_failed', True),
    ('failed', 'AUTH_FAILED', 'delivery_failed', False),
    ('failed', 'MISSING_OUTPUT_FILE', 'task_timeout', False),
    ('timeout', 'TIMEOUT', 'task_timeout', False),
    ('failed', None, None, False),
])
def test_private_delivery_status_and_reporting_workaround(
    config, tmp_path, monkeypatch, status, error_code, error_stage, accepted
):
    actor = 'example/private-worker'
    config.apify_youtube_downloader_actor = actor
    monkeypatch.setenv('APIFY_YOUTUBE_PROVIDERS', json.dumps([dict(actor=actor, adapter='private_s3', build='1.0.0')]))
    monkeypatch.setattr('src.youtube_utils.get_youtube_video_info', lambda url: {'duration': 635})
    monkeypatch.setattr(mod, '_private_storage', lambda *a: (object(), {'quality': '1080'}))
    run = {'id': 'run1', 'status': 'SUCCEEDED', 'defaultDatasetId': 'ds1'}
    row = {'id': VIDEO, 'status': status, 'errorCode': error_code, 'errorStage': error_stage}
    monkeypatch.setattr(mod, '_api', lambda session, method, *a, **kw: {'data': run} if method == 'POST' else [row])
    retrieve = Mock(side_effect=lambda storage, path, *a: (path.write_bytes(b'video'), record())[1])
    monkeypatch.setattr(mod, '_retrieve_private_file', retrieve)
    verify = Mock()
    monkeypatch.setattr(mod, '_verify_media', verify)
    if accepted:
        assert mod.download_budget_apify(VIDEO, tmp_path, '1080', 'secret').read_bytes() == b'video'
        retrieve.assert_called_once()
        verify.assert_called_once()
        assert verify.call_args.args[:2] == (retrieve.call_args.args[1], record())
    else:
        with pytest.raises(mod.BudgetApifyError, match='complete private delivery'):
            mod.download_budget_apify(VIDEO, tmp_path, '1080', 'secret')
        retrieve.assert_not_called()
        verify.assert_not_called()


def test_completed_private_delivery_still_rejects_low_quality(config, tmp_path, monkeypatch):
    actor = 'example/private-worker'
    config.apify_youtube_downloader_actor = actor
    monkeypatch.setenv('APIFY_YOUTUBE_PROVIDERS', json.dumps([
        dict(actor=actor, adapter='private_s3', build='1.0.0')
    ]))
    monkeypatch.setattr('src.youtube_utils.get_youtube_video_info', lambda url: {'duration': 635})
    monkeypatch.setattr(mod, '_private_storage', lambda *a: (object(), {'quality': '1080'}))
    run = {'id': 'run1', 'status': 'SUCCEEDED', 'defaultDatasetId': 'ds1'}
    row = {'id': VIDEO, 'status': 'completed'}
    monkeypatch.setattr(mod, '_api', lambda session, method, *a, **kw: {'data': run} if method == 'POST' else [row])
    def retrieve(storage, path, *args):
        path.write_bytes(b'video')
        return dict(record(), minQuality=1080)
    monkeypatch.setattr(mod, '_retrieve_private_file', retrieve)
    probe = {'format': {'duration': '635'}, 'streams': [
        {'codec_type': 'video', 'width': 640, 'height': 360}, {'codec_type': 'audio'}
    ]}
    monkeypatch.setattr(mod.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout=json.dumps(probe)))
    with pytest.raises(mod.BudgetApifyError, match='received 640x360; minimum requested quality 1080p'):
        mod.download_budget_apify(VIDEO, tmp_path, '1080', 'secret')
    assert not list(tmp_path.iterdir())



def test_private_preflight_allows_hidden_missing_key_without_list_permission(config, tmp_path, monkeypatch):
    from botocore.exceptions import ClientError
    client = Mock()
    client.get_bucket_versioning.return_value = {"Status": "Enabled"}
    client.head_object.side_effect = ClientError({"Error": {"Code": "403"}}, "HeadObject")
    monkeypatch.setattr("boto3.client", lambda *a, **kw: client)
    values = {"BUCKET": "private-example-bucket", "REGION": "eu-central-1",
              "KEY_TEMPLATE": "media/{video_id}.mp4", "WRITER_ACCESS_KEY_ID": "test-writer",
              "WRITER_SECRET_ACCESS_KEY": "test-writer-secret", "READER_ACCESS_KEY_ID": "test-reader",
              "READER_SECRET_ACCESS_KEY": "test-reader-secret"}
    for name, value in values.items(): monkeypatch.setenv("APIFY_STORAGE_" + name, value)
    storage, body = mod._private_storage(VIDEO, "1080", 300)
    assert storage[3] is None
    assert body["quality"] == "1080"
    assert body["maxWaitSec"] == 270
    with pytest.raises(mod.BudgetApifyError, match="fresh complete"):
        mod._retrieve_private_file(storage, tmp_path / 'part', 635, "1080", mod.time.monotonic() + 30)


def test_signed_adapter_uses_configured_metadata_without_google_key(config, tmp_path, monkeypatch):
    config.apify_youtube_downloader_actor = SIGNED
    config.youtube_metadata_provider = "yt_dlp"
    metadata = Mock(return_value={"duration": 635})
    monkeypatch.setattr("src.youtube_utils._fetch_video_info_with_ytdlp", metadata)
    monkeypatch.setattr("src.youtube_utils._fetch_video_info_with_youtube_data_api", Mock(side_effect=AssertionError("Google metadata must not be required")))
    run = {"id": "run1", "status": "SUCCEEDED", "defaultDatasetId": "ds"}
    row = {"sourceUrl": "https://www.youtube.com/watch?v=" + VIDEO, "downloadUrl": "https://cdn.example.com/video.mp4"}
    monkeypatch.setattr(mod, "_api", lambda session, method, *a, **kw: {"data": run} if method == "POST" else [row])
    monkeypatch.setattr(mod, "_download", lambda url, target, *a, **kw: target.write_bytes(b"video"))
    monkeypatch.setattr(mod, "_verify_media", lambda *a: None)
    assert mod.download_budget_apify(VIDEO, tmp_path, "1080", "test-token").is_file()
    metadata.assert_called_once()


def test_router_key_preserves_ordered_apify_fallbacks_and_shared_budget(config, tmp_path, monkeypatch):
    monkeypatch.setenv('APIFY_EXPENSIVE_FALLBACKS', 'true')
    config.rapidapi_youtube_api_key = 'test-key'
    monkeypatch.setenv('APIFY_YOUTUBE_FALLBACK_ACTORS', URLS + ',' + SIGNED)
    calls = []
    def run(actor, *args):
        calls.append((actor, args[-1]))
        raise mod.BudgetApifyError('timed out', spent=.02 if actor == FILE else 1)
    monkeypatch.setattr(mod, '_run', run)
    with pytest.raises(mod.BudgetApifyError):
        mod.download_budget_apify(VIDEO, tmp_path, '1080', 'secret')
    assert calls == [(FILE, .02), (URLS, 5.98), (SIGNED, 4.98)]


def test_provider_timeout_aborts_before_next_fallback(config, tmp_path, monkeypatch):
    monkeypatch.setenv('APIFY_EXPENSIVE_FALLBACKS', 'true')
    # A stalled actor must not consume the extra file-transfer allowance.
    pool = [dict(p, run_timeout_seconds=300) for p in PROVIDERS]
    monkeypatch.setenv('APIFY_YOUTUBE_PROVIDERS', json.dumps(pool))
    monkeypatch.setenv('APIFY_YOUTUBE_FALLBACK_ACTORS', URLS)
    now = [1000.0]
    monkeypatch.setattr(mod.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(mod.time, 'sleep', lambda seconds: now.__setitem__(0, now[0] + seconds))
    events = []
    def api(session, method, path, deadline, **kwargs):
        if method == 'POST':
            if FILE.replace('/', '~') in path:
                assert kwargs['params']['timeout'] == 300
                assert deadline == 1300
                return {'data': {'id': 'stalled', 'status': 'RUNNING'}}
            events.append('fallback')
            assert now[0] == 1300
            return {'data': {'id': 'next', 'status': 'SUCCEEDED', 'defaultDatasetId': 'ds'}}
        if path == 'actor-runs/stalled':
            mod._remaining(deadline)
            return {'data': {'id': 'stalled', 'status': 'RUNNING'}}
        return [dict(record(), status='success', downloadUrl='https://cdn.example.com/video.mp4')]
    monkeypatch.setattr(mod, '_api', api)
    session = Mock()
    session.headers = {}
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    session.post.side_effect = lambda *a, **kw: (events.append('abort'), Mock())[1]
    monkeypatch.setattr(mod.requests, 'Session', lambda: session)
    monkeypatch.setattr(mod, '_download', lambda url, path, *a, **kw: path.write_bytes(b'video'))
    monkeypatch.setattr(mod, '_verify_media', lambda *a: None)
    assert mod.download_budget_apify(VIDEO, tmp_path, '1080', 'secret').is_file()
    assert events == ['abort', 'fallback']
    assert not list(tmp_path.glob('*/' + VIDEO + '.mp4.part'))

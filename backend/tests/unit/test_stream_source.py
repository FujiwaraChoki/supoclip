from pathlib import Path
from types import SimpleNamespace
import pytest
from src import stream_source as subject

@pytest.mark.parametrize(("url","expected"),[
("https://www.twitch.tv/videos/123456",("twitch","vod","123456")),
("https://clips.twitch.tv/FancyClip-abc",("twitch","clip","FancyClip-abc")),
("https://kick.com/creator/videos/12345678-1234-1234-1234-123456789abc",("kick","vod","12345678-1234-1234-1234-123456789abc")),
("https://kick.com/creator/clips/clip_abc",("kick","clip","clip_abc")),])
def test_detect_stream_source(url,expected): assert subject.detect_stream_source(url)==expected

@pytest.mark.parametrize("url",["https://twitch.tv/creator","https://kick.com/creator"])
def test_live_rejected(url):
    with pytest.raises(ValueError,match="Live streams aren't supported"): subject.detect_stream_source(url)

def config(**kw):
    defaults=dict(twitch_download_providers="proxy,direct,apify_twitch",kick_download_providers="proxy,direct_retry,apify_kick_hls,apify_kick_mp4",apify_stream_enabled=True,apify_api_token="token",apify_twitch_actor="twitch",apify_kick_hls_actor="kick-hls",apify_kick_mp4_actor="kick-mp4",apify_kick_max_video_size_mb=2000)
    return SimpleNamespace(**(defaults|kw))

def test_proxy_first_default_order():
    assert subject._provider_order("twitch",config())==["proxy","direct","apify_twitch"]
    assert subject._provider_order("kick",config())==["proxy","direct_retry","apify_kick_hls","apify_kick_mp4"]
    assert subject._provider_order("twitch", config(twitch_download_providers="apify_twitch,proxy,direct")) == ["proxy", "direct", "apify_twitch"]

def test_proxy_without_config_is_skipped(monkeypatch):
    monkeypatch.setattr(subject,"get_config",lambda:config(youtube_proxy_url="",youtube_max_height=1080,youtube_download_max_bytes=2000))
    with pytest.raises(subject.ProviderSkipped): subject._ydl_options("proxy",Path("out"))

def test_apify_without_token_is_skipped(monkeypatch):
    monkeypatch.setattr(subject,"get_config",lambda:config(apify_api_token=""))
    with pytest.raises(subject.ProviderSkipped): subject._actor_items("actor",{},.1)

def test_twitch_clip_skips_apify(monkeypatch):
    monkeypatch.setattr(subject,"get_config",lambda:config())
    monkeypatch.setattr(subject,"_actor_items",lambda *_: (_ for _ in ()).throw(AssertionError("actor called")))
    with pytest.raises(subject.ProviderSkipped): subject._apify_info("https://clips.twitch.tv/Placeholder", "apify_twitch")

def test_twitch_actor_shape(monkeypatch):
    monkeypatch.setattr(subject,"get_config",lambda:config())
    monkeypatch.setattr(subject,"_actor_items",lambda *_:[{"recordType":"vod_metadata","id":"123","title":"VOD","durationSeconds":1210,"vodHlsUrl":"https://media.test/master.m3u8"}])
    info=subject._apify_info("https://twitch.tv/videos/123","apify_twitch")
    assert (info["duration"],info["media_url"])==(1210,"https://media.test/master.m3u8")

def test_kick_hls_ready_and_failed_shapes(monkeypatch):
    monkeypatch.setattr(subject,"get_config",lambda:config())
    item={"type":"vod","id":"12345678-1234-1234-1234-123456789abc","durationSec":3600,"hlsUrl":"https://media.test/master.m3u8","status":"ready"}
    monkeypatch.setattr(subject,"_actor_items",lambda *_:[item])
    assert subject._apify_info("https://kick.com/user/videos/12345678-1234-1234-1234-123456789abc","apify_kick_hls")["duration"]==3600
    monkeypatch.setattr(subject,"_actor_items",lambda *_:[])
    with pytest.raises(RuntimeError,match="no ready media"): subject._apify_info("https://kick.com/user/videos/12345678-1234-1234-1234-123456789abc","apify_kick_hls")
    monkeypatch.setattr(subject,"_actor_items",lambda *_:[{"status":"failed","hlsUrl":None}])
    with pytest.raises(RuntimeError,match="no ready media"): subject._apify_info("https://kick.com/user/videos/12345678-1234-1234-1234-123456789abc","apify_kick_hls")

def test_parsebird_milliseconds_and_minimum_cap(monkeypatch):
    monkeypatch.setattr(subject,"get_config",lambda:config())
    captured={}
    def actor(_actor,payload,_charge):
        captured.update(payload); return [{"url":"https://kick.com/user/clips/clip_placeholder","id":"clip_placeholder","duration":41000,"download_url":"https://media.test/video.mp4","status":"success"}]
    monkeypatch.setattr(subject,"_actor_items",actor)
    info=subject._apify_info("https://kick.com/user/clips/clip_placeholder","apify_kick_mp4",41)
    assert info["duration"]==41
    assert captured["maxVideoSizeMb"]>=100

def test_id_and_duration_mismatches_rejected(monkeypatch):
    monkeypatch.setattr(subject,"get_config",lambda:config())
    monkeypatch.setattr(subject,"_actor_items",lambda *_:[{"id":"999","durationSeconds":100,"vodHlsUrl":"https://media.test/x","recordType":"vod_metadata"}])
    with pytest.raises(RuntimeError,match="id does not match"): subject._apify_info("https://twitch.tv/videos/123","apify_twitch")
    monkeypatch.setattr(subject,"_actor_items",lambda *_:[{"id":"123","durationSeconds":120,"vodHlsUrl":"https://media.test/x","recordType":"vod_metadata"}])
    with pytest.raises(RuntimeError,match="duration does not match"): subject._apify_info("https://twitch.tv/videos/123","apify_twitch",100)

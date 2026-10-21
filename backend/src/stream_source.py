"""Twitch and Kick validation and ordered yt-dlp/Apify fallbacks."""
from __future__ import annotations

import logging, math, re, shutil, subprocess, time, uuid
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import requests

from .config import get_config
from .youtube_source import CachedPiece, assemble_timeline, merge_ranges, ranges_from_segments
from .youtube_proxy import normalize_country, render_proxy_url, sticky_session_id

logger = logging.getLogger(__name__)
PRIVATE_VOD_MESSAGE = "This VOD is subscriber-only or private and can't be downloaded"
FETCH_FAILED_MESSAGE = "We couldn't fetch this video, try again later"
LIVE_MESSAGE = "Live streams aren't supported. Use a VOD or clip URL instead."
_TWITCH_VOD = re.compile(r"^/videos/(?P<id>\d+)/?$")
_TWITCH_CHANNEL_CLIP = re.compile(r"^/[A-Za-z0-9_]+/clip/(?P<id>[A-Za-z0-9_-]+)/?$")
_TWITCH_CLIP = re.compile(r"^/(?P<id>[A-Za-z0-9_-]+)/?$")
_KICK_VOD = re.compile(r"^/[A-Za-z0-9_-]+/videos/(?P<id>[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12})/?$")
_KICK_CLIP = re.compile(r"^/[A-Za-z0-9_-]+/clips/(?P<id>clip_[A-Za-z0-9_-]+)/?$")

class StreamSourceError(RuntimeError): pass
class ProviderSkipped(RuntimeError): pass

def detect_stream_source(url: str) -> tuple[str, str, str]:
    try: parsed = urlparse(url.strip())
    except (TypeError, ValueError): raise ValueError("Invalid video URL") from None
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password: raise ValueError("Invalid video URL")
    host, path = (parsed.hostname or "").lower().removeprefix("www."), parsed.path.rstrip("/") or "/"
    if host in {"twitch.tv", "m.twitch.tv", "go.twitch.tv"}:
        if m := _TWITCH_VOD.fullmatch(path): return "twitch", "vod", m.group("id")
        if m := _TWITCH_CHANNEL_CLIP.fullmatch(path): return "twitch", "clip", m.group("id")
        raise ValueError(LIVE_MESSAGE)
    if host == "clips.twitch.tv":
        if m := _TWITCH_CLIP.fullmatch(path): return "twitch", "clip", m.group("id")
        raise ValueError("Invalid Twitch clip URL")
    if host == "kick.com":
        if m := _KICK_VOD.fullmatch(path): return "kick", "vod", m.group("id")
        if m := _KICK_CLIP.fullmatch(path): return "kick", "clip", m.group("id")
        clip = parse_qs(parsed.query).get("clip", [""])[0]
        if re.fullmatch(r"clip_[A-Za-z0-9_-]+", clip) and re.fullmatch(r"/[A-Za-z0-9_-]+", path): return "kick", "clip", clip
        raise ValueError(LIVE_MESSAGE)
    raise ValueError("Only YouTube, Twitch VOD/clip, Kick VOD/clip, or uploaded video sources are supported")

def _provider_order(source: str, config=None) -> list[str]:
    config = config or get_config(); providers = [x.strip().lower() for x in getattr(config, f"{source}_download_providers").split(",") if x.strip()]
    allowed = {"proxy", "direct", "direct_retry", "apify_twitch", "apify_kick_hls", "apify_kick_mp4"}
    if invalid := set(providers)-allowed: raise StreamSourceError(f"Unsupported {source} download provider: {sorted(invalid)[0]}")
    # Apify is always a fallback, even if an operator accidentally lists it first.
    return [p for p in providers if not p.startswith("apify_")] + [p for p in providers if p.startswith("apify_")]

def _is_private_error(exc):
    return any(x in str(exc).lower() for x in ("sub-only", "subscriber-only", "subscribers only", "private video", "login required", "members-only", "not available due to privacy"))

def _ydl_options(provider, output, *, audio=False, ranges=None):
    c=get_config(); opts={"outtmpl":str(output),"noplaylist":True,"quiet":True,"no_warnings":True,"retries":3 if provider=="direct_retry" else 1,"fragment_retries":3,"max_filesize":c.youtube_download_max_bytes}
    if provider=="proxy":
        if not c.youtube_proxy_url: raise ProviderSkipped("proxy is not configured")
        opts["proxy"]=render_proxy_url(c.youtube_proxy_url,sticky_session_id(None,output.parent.parent.name),normalize_country(c.youtube_proxy_country))
    opts["format"]="bestaudio/best" if audio else f"bestvideo[height<={c.youtube_max_height}]+bestaudio/best[height<={c.youtube_max_height}]/best"
    if not audio: opts["merge_output_format"]="mkv"
    if ranges:
        opts["download_ranges"]=lambda _i,_y:[{"start_time":a,"end_time":b} for a,b in ranges]; opts["force_keyframes_at_cuts"]=True
    return opts

def _run_ytdlp(url, provider, output, *, download, audio=False, ranges=None):
    from yt_dlp import YoutubeDL
    if provider=="direct_retry": time.sleep(.25)
    with YoutubeDL(_ydl_options(provider,output,audio=audio,ranges=ranges)) as ydl: return ydl.extract_info(url,download=download)

def _actor_items(actor, run_input, charge):
    c=get_config()
    if not c.apify_stream_enabled or not c.apify_api_token: raise ProviderSkipped("Apify token/provider is not configured")
    try:
        from apify_client import ApifyClient
        run=ApifyClient(c.apify_api_token).actor(actor).call(run_input=run_input,timeout_secs=c.apify_stream_timeout_seconds,memory_mbytes=256,max_total_charge_usd=Decimal(str(charge)))
        if not run or str(run.get("status","")).upper() != "SUCCEEDED": raise RuntimeError(f"actor run failed: {(run or {}).get('status','no result')}")
        return list(ApifyClient(c.apify_api_token).dataset(run["defaultDatasetId"]).iterate_items())
    except ProviderSkipped: raise
    except Exception as exc: raise RuntimeError(f"Apify actor failed: {exc}") from exc

def _matches(requested, item):
    values=[item.get("id"),item.get("url"),item.get("inputUrl"),item.get("kickUrl")]
    return any(requested.lower() in str(v).lower() for v in values if v)

def _duration(item, provider):
    value=item.get("durationSeconds",item.get("durationSec",item.get("duration")))
    if value is None: return None
    value=float(value)
    return value/1000 if provider=="apify_kick_mp4" else value

def _apify_info(url, provider, expected_duration=None):
    source,kind,source_id=detect_stream_source(url); c=get_config()
    if provider=="apify_twitch":
        if kind=="clip": raise ProviderSkipped("Twitch clip media returned by this actor is unauthorized")
        items=_actor_items(c.apify_twitch_actor,{"mode":"url","urls":[url],"maxResults":1,"maxPages":1,"includeMediaUrls":True},.10)
        valid=[x for x in items if x.get("recordType")=="vod_metadata" and x.get("vodHlsUrl")]
        media_key="vodHlsUrl"
    elif provider=="apify_kick_hls":
        items=_actor_items(c.apify_kick_hls_actor,{"urls":[url],"maxItems":1},.10)
        valid=[x for x in items if x.get("status")=="ready" and x.get("hlsUrl")]; media_key="hlsUrl"
    else:
        cap=max(100,min(c.apify_kick_max_video_size_mb,math.ceil((expected_duration or 200)/60*30)))
        items=_actor_items(c.apify_kick_mp4_actor,{"startUrls":[{"url":url}],"quality":"720p60","maxVideoSizeMb":cap},2.0)
        valid=[x for x in items if x.get("status")=="success" and x.get("download_url")]; media_key="download_url"
    if not valid: raise RuntimeError("actor returned no ready media")
    item=valid[0]
    if not _matches(source_id,item): raise RuntimeError("actor result id does not match requested video")
    duration=_duration(item,provider)
    if expected_duration and duration and abs(duration-expected_duration)>max(5,expected_duration*.01): raise RuntimeError("actor result duration does not match requested video")
    return {"id":source_id,"title":item.get("title"),"duration":duration,"media_url":item[media_key],"provider":provider}

def fetch_stream_info(url: str) -> dict:
    source,_,_=detect_stream_source(url); failures=[]
    for provider in _provider_order(source):
        try:
            info=_apify_info(url,provider) if provider.startswith("apify_") else (_run_ytdlp(url,provider,Path("unused"),download=False) or {})
            if not info.get("duration"): raise RuntimeError("provider returned no duration")
            logger.info("Stream metadata fetched provider=%s source=%s",provider,source); return info
        except ProviderSkipped as exc: logger.info("Skipping stream provider provider=%s source=%s: %s",provider,source,exc)
        except Exception as exc:
            if _is_private_error(exc): raise StreamSourceError(PRIVATE_VOD_MESSAGE) from exc
            failures.append(exc); logger.warning("Stream metadata provider failed provider=%s source=%s: %s",provider,source,exc)
    logger.error("All stream metadata providers failed source=%s failures=%s",source,failures); raise StreamSourceError(FETCH_FAILED_MESSAGE)

def _probe(path_or_url: str | Path) -> float | None:
    try:
        result=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=nw=1:nk=1",str(path_or_url)],capture_output=True,text=True,check=True,timeout=60)
        return float(result.stdout.strip())
    except Exception: return None

def _stream_download(response, destination: Path, max_bytes: int):
    response.raise_for_status(); total=0
    with destination.open("wb") as handle:
        for chunk in response.iter_content(1024*1024):
            total += len(chunk)
            if total > max_bytes: raise RuntimeError("Video download exceeds the configured byte budget")
            handle.write(chunk)

def _download(url: str, *, audio=False, ranges=None, expected_duration=None) -> Path:
    source,kind,source_id=detect_stream_source(url); c=get_config(); root=Path(c.temp_dir)/"stream-work"/f"{source}-{source_id}-{uuid.uuid4().hex}"; root.mkdir(parents=True,exist_ok=True)
    failures=[]; expected=expected_duration
    for provider in _provider_order(source):
        attempt=root/provider; attempt.mkdir(exist_ok=True); template=attempt/("section-%(section_number)03d.%(ext)s" if ranges else "source.%(ext)s")
        try:
            if provider.startswith("apify_"):
                info=_apify_info(url,provider,expected)
                media=info["media_url"]; reported=info.get("duration")
                if reported: expected=expected or reported
                if provider=="apify_kick_mp4":
                    raw=attempt/"source.ts"; _stream_download(requests.get(media,stream=True,timeout=c.apify_stream_timeout_seconds),raw,c.youtube_download_max_bytes)
                    actual=_probe(raw)
                    if reported and actual and abs(actual-reported)>max(5,reported*.01): raise RuntimeError("downloaded media duration does not match actor result")
                    remux=attempt/"source.mp4"; subprocess.run(["ffmpeg","-y","-i",str(raw),"-c","copy",str(remux)],check=True,capture_output=True)
                    target=str(remux)
                else:
                    target=media
                    actual=_probe(media)
                    if reported and actual and abs(actual-reported)>max(5,reported*.01): raise RuntimeError("stream duration does not match actor result")
                # Actor URLs are direct-first; only use the configured proxy if direct media access fails.
                try: _run_ytdlp(target,"direct",template,download=True,audio=audio,ranges=ranges)
                except Exception:
                    _run_ytdlp(target,"proxy",template,download=True,audio=audio,ranges=ranges)
                if provider=="apify_kick_mp4":
                    raw.unlink(missing_ok=True); remux.unlink(missing_ok=True)
            else:
                info=_run_ytdlp(url,provider,template,download=True,audio=audio,ranges=ranges) or {}; expected=expected or info.get("duration")
            files=sorted(p for p in attempt.iterdir() if p.is_file() and p.suffix not in {".part",".ytdl",".ts"})
            if not files: raise RuntimeError("provider returned no media file")
            logger.info("Stream download completed provider=%s source=%s kind=%s",provider,source,kind)
            if ranges:
                if len(files)!=len(ranges): raise RuntimeError("provider did not return every requested section")
                dest=root/"selected-segments.mkv"; assemble_timeline([(CachedPiece(a,b,p),a,b) for p,(a,b) in zip(files,ranges)],dest); return dest
            return files[0]
        except ProviderSkipped as exc: shutil.rmtree(attempt,ignore_errors=True); logger.info("Skipping stream provider provider=%s source=%s: %s",provider,source,exc)
        except Exception as exc:
            shutil.rmtree(attempt,ignore_errors=True)
            if _is_private_error(exc): raise StreamSourceError(PRIVATE_VOD_MESSAGE) from exc
            failures.append(exc); logger.warning("Stream download provider failed provider=%s source=%s: %s",provider,source,exc)
    logger.error("All stream download providers failed source=%s failures=%s",source,failures); raise StreamSourceError(FETCH_FAILED_MESSAGE)

def download_stream_audio(url, duration=None): return _download(url,audio=True,expected_duration=duration)
def download_stream_video(url, duration=None): return _download(url,expected_duration=duration)
def download_stream_sections(url,segments,duration=None):
    ranges=merge_ranges(ranges_from_segments(segments),padding=get_config().youtube_section_padding_seconds,duration=duration)
    if not ranges: raise StreamSourceError("No valid video sections were selected")
    return _download(url,ranges=ranges,expected_duration=duration)

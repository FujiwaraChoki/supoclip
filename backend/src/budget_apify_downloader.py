"""Bounded Apify failover with verified media and one spending allowance per job."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import logging
import math
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
import time
from urllib.parse import quote, urljoin, urlsplit

import requests

from .config import get_config

logger = logging.getLogger(__name__)
ADAPTERS = {"kv_file", "url_objects", "signed_url", "kv_media", "private_s3"}


def _providers() -> dict:
    """Provider identities and pinned builds are deployment secrets, never defaults."""
    try:
        values = json.loads(os.getenv("APIFY_YOUTUBE_PROVIDERS", "[]"))
        if not isinstance(values, list) or not values:
            raise ValueError()
        providers = {}
        for value in values:
            actor = value["actor"].strip().lower()
            if (not re.fullmatch(r"[a-z0-9_.-]+/[a-z0-9_.-]+", actor)
                    or value["adapter"] not in ADAPTERS
                    or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value["build"])
                    or actor in providers):
                raise ValueError()
            cap = float(value.get("max_charge_usd", 6))
            if not math.isfinite(cap) or not 0 < cap <= 6:
                raise ValueError()
            timeout = value.get("run_timeout_seconds", 1200)
            if not isinstance(timeout, int) or not 60 <= timeout <= 1800:
                raise ValueError()
            providers[actor] = dict(value, actor=actor, max_charge_usd=cap)
        return providers
    except (KeyError, TypeError, ValueError, AttributeError):
        raise BudgetApifyError("Invalid Apify provider environment configuration.") from None


# Decimal GB, shared by streamed HTTP and versioned private-storage deliveries.
MAX_BYTES = 10_000_000_000
TERMINAL = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}
PERMANENT = {"INVALID_INPUT", "VIDEO_UNAVAILABLE", "VIDEO_PRIVATE", "AGE_RESTRICTED",
             "MEMBERS_ONLY", "GEO_BLOCKED", "DRM_PROTECTED", "OVER_MAX_MINUTES"}


class BudgetApifyError(RuntimeError):
    def __init__(self, message: str, *, permanent: bool = False, spent: float | None = None):
        super().__init__(message)
        self.permanent = permanent
        self.spent = spent


def _remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise BudgetApifyError("YouTube download timed out. Please try again later.")
    return left


def _api(session, method, path, deadline, **kwargs):
    # Provider responses/exception strings can contain credential-bearing URLs.
    try:
        response = session.request(method, "https://api.apify.com/v2/" + path,
                                   timeout=min(30, _remaining(deadline)),
                                   allow_redirects=False, **kwargs)
        try:
            if response.status_code not in {200, 201}:
                raise BudgetApifyError(f"YouTube download API returned HTTP {response.status_code}.")
            return response.json()
        finally:
            response.close()
    except (requests.RequestException, ValueError):
        raise BudgetApifyError("YouTube download API could not be reached.") from None


def _public_https(url: str):
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.port not in {None, 443}):
        raise BudgetApifyError("YouTube provider returned an insecure file address.")
    try:
        addresses = socket.getaddrinfo(parsed.hostname, 443, type=socket.SOCK_STREAM)
        if not addresses or not all(ipaddress.ip_address(a[4][0]).is_global for a in addresses):
            raise ValueError()
    except (OSError, ValueError):
        raise BudgetApifyError("YouTube provider returned an invalid file address.") from None
    return parsed


def _download(url, target, item, run, token, deadline, *, allow_size_header=False):
    expected = int(item.get("fileSizeBytes") or 0)
    if expected > MAX_BYTES or expected < 0 or (not expected and not allow_size_header):
        raise BudgetApifyError("YouTube file is empty or exceeds the download size limit.")
    digest = hashlib.sha256()
    total = 0
    try:
        with requests.Session() as delivery:
            delivery.trust_env = False
            for _ in range(5):
                parsed = _public_https(url)
                # Authenticate only a record in THIS run's store, never a CDN redirect.
                owned_prefix = f"/v2/key-value-stores/{run.get('defaultKeyValueStoreId')}/records/"
                headers = ({"Authorization": f"Bearer {token}"}
                           if parsed.hostname == "api.apify.com" and parsed.path.startswith(owned_prefix)
                           else {})
                response = delivery.get(url, headers=headers, stream=True, allow_redirects=False,
                                        timeout=min(60, _remaining(deadline)))
                try:
                    if response.is_redirect:
                        url = urljoin(url, response.headers["Location"])
                        continue
                    if response.status_code != 200:
                        raise BudgetApifyError(f"YouTube file delivery returned HTTP {response.status_code}.")
                    if allow_size_header and not expected:
                        expected = int(response.headers.get("Content-Length") or 0)
                        if not 0 < expected <= MAX_BYTES or response.headers.get("Content-Encoding") not in {None, "identity"}:
                            raise BudgetApifyError("YouTube file delivery returned no usable size.")
                    with target.open("wb") as output:
                        for chunk in response.iter_content(1024 * 1024):
                            _remaining(deadline)
                            if not chunk:
                                continue
                            total += len(chunk)
                            if total > expected or total > MAX_BYTES:
                                raise BudgetApifyError("YouTube file exceeds its reported size.")
                            digest.update(chunk)
                            output.write(chunk)
                    break
                finally:
                    response.close()
            else:
                raise BudgetApifyError("YouTube file delivery redirected too many times.")
    except requests.RequestException:
        raise BudgetApifyError("YouTube file transfer was interrupted.") from None
    if total != expected:
        raise BudgetApifyError("YouTube file transfer was incomplete.")
    checksum = item.get("sha256")
    if checksum and (not re.fullmatch(r"[a-fA-F0-9]{64}", str(checksum))
                     or digest.hexdigest() != checksum.lower()):
        raise BudgetApifyError("YouTube file checksum verification failed.")


def _verify_media(path: Path, item: dict, deadline: float):
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries",
             "format=duration:stream=codec_type,width,height,duration", "-of", "json", str(path)],
            capture_output=True, text=True, check=True, timeout=min(30, _remaining(deadline)),
        )
        media = json.loads(probe.stdout)
        duration = float(media["format"]["duration"])
        reported = item.get("durationSeconds")
        if reported is None and item.get("duration"):
            value = str(item["duration"])
            reported = sum(float(part) * 60 ** i for i, part in enumerate(reversed(value.split(":"))))
        expected = float(reported)
        tolerance = max(5, expected * .001)
        streams = media["streams"]
        if (not math.isfinite(expected) or expected <= 0 or not math.isfinite(duration)
                or abs(duration - expected) > tolerance
                or not {"audio", "video"}.issubset({s["codec_type"] for s in streams})):
            raise ValueError()
        for stream in streams:
            if stream.get("codec_type") not in {"video", "audio"}:
                continue
            if stream.get("duration") and abs(float(stream["duration"]) - expected) > tolerance:
                raise ValueError()
            if stream["codec_type"] == "video":
                if min(stream.get("width", 0), stream.get("height", 0)) <= 0:
                    raise ValueError()
                if item.get("minQuality") and min(stream["width"], stream["height"]) < item["minQuality"]:
                    raise BudgetApifyError(
                        "YouTube provider delivered incomplete or invalid media "
                        f"(received {stream['width']}x{stream['height']}; "
                        f"minimum requested quality {item['minQuality']}p)."
                    )
                for field in ("width", "height"):
                    if item.get(field) and int(item[field]) != stream[field]:
                        raise ValueError()
        for offset in (0, max(0, duration / 2 - 1), max(0, duration - 3)):
            sample = subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error", "-threads", "1", "-ss", str(offset),
                 "-i", str(path), "-t", "2", "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"],
                capture_output=True, text=True, timeout=min(40, _remaining(deadline)),
            )
            if sample.returncode or sample.stderr.strip():
                raise ValueError()
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError):
        raise BudgetApifyError("YouTube provider delivered incomplete or invalid media.") from None


def _charge_reservation(run: dict, ceiling: float) -> float:
    """Use the run's own event prices; usageTotalUsd can lag event charging."""
    try:
        pricing = run["pricingInfo"]
        if pricing["pricingModel"] != "PAY_PER_EVENT" or pricing.get("isPPEPlatformUsagePaidByUser"):
            return ceiling
        prices = pricing["pricingPerEvent"]["actorChargeEvents"]
        counts = run["chargedEventCounts"]
        if not isinstance(counts, dict) or not counts:
            return ceiling
        charged = 0.0
        for event, count in counts.items():
            count = float(count)
            if not math.isfinite(count) or count < 0:
                return ceiling
            if not count:
                continue
            price = float(prices[event]["eventPriceUsd"])
            if not math.isfinite(price) or price < 0:
                return ceiling
            charged += count * price
        observed = float(run["usageTotalUsd"])
        if not math.isfinite(observed) or observed < 0:
            return ceiling
        return min(ceiling, max(charged, observed))
    except (KeyError, TypeError, ValueError):
        return ceiling


def _private_storage(video_id, quality, run_timeout):
    """Only deployment-provided, private, versioned storage is eligible."""
    import boto3
    from botocore.config import Config as ClientConfig
    from botocore.exceptions import BotoCoreError, ClientError
    try:
        values = {key: os.environ["APIFY_STORAGE_" + key] for key in (
            "BUCKET", "REGION", "WRITER_ACCESS_KEY_ID", "WRITER_SECRET_ACCESS_KEY",
            "READER_ACCESS_KEY_ID", "READER_SECRET_ACCESS_KEY", "KEY_TEMPLATE")}
        key = values["KEY_TEMPLATE"].format(video_id=video_id)
        if "{video_id}" not in values["KEY_TEMPLATE"] or not key.endswith(".mp4"):
            raise ValueError()
        client = boto3.client("s3", region_name=values["REGION"],
            aws_access_key_id=values["READER_ACCESS_KEY_ID"],
            aws_secret_access_key=values["READER_SECRET_ACCESS_KEY"],
            config=ClientConfig(connect_timeout=10, read_timeout=30, retries={"max_attempts": 2}))
        bucket = values["BUCKET"]
        if client.get_bucket_versioning(Bucket=bucket).get("Status") != "Enabled":
            raise ValueError()
        try:
            before = client.head_object(Bucket=bucket, Key=key).get("VersionId")
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") not in {"403", "404", "AccessDenied", "NoSuchKey", "NotFound"}:
                raise
            # Without ListBucket, S3 hides a missing key behind AccessDenied.
            # Final retrieval still requires a fresh, readable version.
            before = None
        body = {"startUrls": [video_id], "outputType": "media", "quality": quality,
                "format": "mp4", "storageType": "aws", "s3Bucket": bucket,
                "s3Region": values["REGION"], "s3AccessKeyId": values["WRITER_ACCESS_KEY_ID"],
                "s3SecretAccessKey": values["WRITER_SECRET_ACCESS_KEY"],
                "maxWaitSec": max(30, run_timeout - 30), "maxConcurrency": 1}
        return (client, bucket, key, before, datetime.now(timezone.utc)), body
    except (KeyError, ValueError, BotoCoreError, ClientError):
        raise BudgetApifyError("Private download storage is not configured correctly.", spent=0) from None


def _retrieve_private_file(storage, target, duration, quality, deadline):
    from botocore.exceptions import BotoCoreError, ClientError
    client, bucket, key, before, started = storage
    try:
        head = client.head_object(Bucket=bucket, Key=key)
        version = head.get("VersionId")
        size = int(head["ContentLength"])
        if (not version or version == "null" or version == before
                or head["LastModified"].timestamp() < started.timestamp() - 1
                or not 0 < size <= MAX_BYTES):
            raise ValueError()
        # Pin the exact fresh version so a concurrent run cannot swap the file.
        response = client.get_object(Bucket=bucket, Key=key, VersionId=version)
        stream = response["Body"]
        total = 0
        try:
            with target.open("wb") as output:
                for chunk in stream.iter_chunks(chunk_size=1024 * 1024):
                    _remaining(deadline)
                    total += len(chunk)
                    if total > size:
                        raise ValueError()
                    output.write(chunk)
        finally:
            stream.close()
        if total != size:
            raise ValueError()
        return {"durationSeconds": duration, "minQuality": int(quality), "fileSizeBytes": size}
    except (BotoCoreError, ClientError, ValueError, KeyError, TypeError):
        raise BudgetApifyError("Private download storage returned no fresh complete video.") from None


def _run(actor, video_id, temp_dir, quality, token, run_timeout, charge_cap):
    config = get_config()
    provider = _providers()[actor]
    run_timeout = min(run_timeout, int(provider.get("run_timeout_seconds", run_timeout)))
    adapter = provider["adapter"]
    maximum_minutes = min(720, math.ceil(max(config.max_video_duration,
        config.pro_youtube_max_video_duration, config.scale_youtube_max_video_duration) / 60))
    expected_duration = None
    if adapter in {"signed_url", "private_s3"}:
        # Establish duration using the configured metadata provider before spending.
        from .youtube_utils import get_youtube_video_info
        try:
            info = get_youtube_video_info(f"https://www.youtube.com/watch?v={video_id}")
            expected_duration = float(info["duration"])
            if not math.isfinite(expected_duration) or expected_duration <= 0:
                raise ValueError()
        except Exception:
            raise BudgetApifyError("Cannot verify source duration for the download fallback.", spent=0) from None
    storage = None
    if adapter == "private_s3":
        storage, body = _private_storage(video_id, quality, run_timeout)
    elif adapter == "kv_file":
        body = {"videoUrls": [f"https://www.youtube.com/watch?v={video_id}"], "format": "video",
                "quality": quality + "p", "videoCodec": "h264", "storageProvider": "apify",
                "maxMinutes": maximum_minutes, "maxMegabytes": MAX_BYTES // 1_000_000,
                "maxAgeDays": 0}
    elif adapter == "url_objects":
        body = {"urls": [{"url": f"https://www.youtube.com/watch?v={video_id}"}],
                "quality": quality, "format": "default", "residentialProxyMode": "disabled"}
    elif adapter == "signed_url":
        body = {"videoUrls": [f"https://www.youtube.com/watch?v={video_id}"],
                "quality": quality + "p", "audioOnly": False}
    else:
        body = {"urls": [f"https://www.youtube.com/watch?v={video_id}"], "quality": quality,
                "subtitles": False, "maxVideos": 1, "maxMegabytes": min(1400, MAX_BYTES // 1_000_000)}
    run_deadline = time.monotonic() + run_timeout
    deadline = run_deadline + 240  # Reserve separate time for file transfer and verification.
    folder = Path(tempfile.mkdtemp(prefix=f"apify-{video_id}-", dir=temp_dir))
    part = folder / f"{video_id}.mp4.part"
    path = folder / f"{video_id}.mp4"
    run = None
    try:
        with requests.Session() as api:
            api.headers["Authorization"] = "Bearer " + token
            # One submission only, including if its response is ambiguous.
            run = _api(api, "POST", "acts/" + actor.replace("/", "~") + "/runs", run_deadline,
                       params={"build": provider["build"], "memory": 2048, "timeout": run_timeout,
                               "maxTotalChargeUsd": charge_cap, "restartOnError": "false"}, json=body)["data"]
            logger.info("Apify download %s started (cap $%.3f)", run["id"], charge_cap)
            poll_failures = 0
            while run["status"] not in TERMINAL:
                _remaining(run_deadline)
                time.sleep(min(3, _remaining(run_deadline)))
                try:
                    run = _api(api, "GET", "actor-runs/" + run["id"], run_deadline)["data"]
                    poll_failures = 0
                except BudgetApifyError:
                    poll_failures += 1
                    if poll_failures >= 3:
                        raise
            if run["status"] != "SUCCEEDED":
                raise BudgetApifyError("YouTube download " + run["status"].lower().replace("-", " ") + ".")
            items = _api(api, "GET", "datasets/" + run["defaultDatasetId"] + "/items", deadline,
                         params={"limit": 5})
            if not isinstance(items, list):
                raise BudgetApifyError("YouTube provider returned an invalid dataset.")
            if adapter == "private_s3":
                row = next((row for row in items if isinstance(row, dict)
                            and row.get("id") == video_id), None)
                # Titan now reports completed deliveries as "completed". Keep
                # its older false-failure workaround narrow; both paths still
                # require a fresh versioned object and verified media below.
                if not row or (row.get("status") not in {"success", "completed"} and not (
                        row.get("errorCode") == "MISSING_OUTPUT_FILE"
                        and row.get("errorStage") == "delivery_failed")):
                    raise BudgetApifyError("YouTube provider could not complete private delivery.")
                item = _retrieve_private_file(storage, part, expected_duration, quality, deadline)
                _verify_media(part, item, deadline)
                part.replace(path)
                logger.info("Verified private Apify video %s (%s bytes)", video_id, path.stat().st_size)
                return path
            item = next((row for row in items if isinstance(row, dict) and (row.get("videoId") == video_id
                         or (adapter == "signed_url" and row.get("sourceUrl") in {
                             f"https://www.youtube.com/watch?v={video_id}", f"https://youtu.be/{video_id}"
                         }))), None)
            if not item:
                raise BudgetApifyError("YouTube provider returned no matching video.")
            if adapter in {"kv_file", "kv_media"}:
                if adapter == "kv_media":
                    if item.get("ok") is not True:
                        raise BudgetApifyError("YouTube fallback could not deliver this video.")
                    key = item.get("mediaKey")
                else:
                    if item.get("resultType") != "file" or item.get("storage") != "apify":
                        raise BudgetApifyError("YouTube provider could not deliver this video.",
                                               permanent=item.get("error") in PERMANENT)
                    key = item.get("fileKey")
                if not isinstance(key, str) or not key.endswith(".mp4"):
                    raise BudgetApifyError("YouTube provider returned no video file.")
                file_url = ("https://api.apify.com/v2/key-value-stores/"
                            + quote(run["defaultKeyValueStoreId"], safe="") + "/records/" + quote(key, safe=""))
            else:
                if adapter == "url_objects" and item.get("status") != "success":
                    raise BudgetApifyError("YouTube fallback could not deliver this video.")
                file_url = item.get("downloadUrl")
                if not isinstance(file_url, str):
                    raise BudgetApifyError("YouTube fallback returned no video file.")
            if adapter == "signed_url":
                item = dict(item, durationSeconds=expected_duration)
            _download(file_url, part, item, run, token, deadline, allow_size_header=adapter == "signed_url")
            _verify_media(part, item, deadline)
            part.replace(path)
            logger.info("Verified Apify video %s (%s bytes)", video_id, path.stat().st_size)
            return path
    except BudgetApifyError as exc:
        # Reconcile final event counts/prices, not the potentially stale usage
        # snapshot from before file transfer. Unknown billing reserves the cap.
        exc.spent = charge_cap
        if run and run.get("status") in TERMINAL:
            try:
                with requests.Session() as billing:
                    billing.headers["Authorization"] = "Bearer " + token
                    final = _api(billing, "GET", "actor-runs/" + run["id"],
                                 min(deadline, time.monotonic() + 15))["data"]
                    exc.spent = _charge_reservation(final, charge_cap)
            except (BudgetApifyError, KeyError, TypeError):
                pass
        raise
    except (KeyError, TypeError, ValueError, OSError):
        raise BudgetApifyError("YouTube provider returned an invalid download result.", spent=charge_cap) from None
    finally:
        if run and run.get("status") not in TERMINAL:
            try:
                with requests.Session() as cleanup:
                    cleanup.headers["Authorization"] = "Bearer " + token
                    response = cleanup.post("https://api.apify.com/v2/actor-runs/" + run["id"] + "/abort", timeout=10)
                    response.close()
            except requests.RequestException:
                logger.warning("Could not abort bounded Apify run %s", run["id"])
        if not path.exists():
            shutil.rmtree(folder, ignore_errors=True)


def download_budget_apify(video_id: str, temp_dir: Path, quality: str, token: str) -> Path:
    config = get_config()
    actor = config.apify_youtube_downloader_actor.lower().strip()
    providers = _providers()
    if actor not in providers or quality not in {"360", "480", "720", "1080"}:
        raise BudgetApifyError("Unsupported Apify video downloader or quality.")
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) or not token:
        raise BudgetApifyError("YouTube download is not configured correctly.")
    temp_dir.mkdir(parents=True, exist_ok=True)
    fallback = os.getenv("APIFY_YOUTUBE_FALLBACK_ACTORS", "").strip().lower()
    if not fallback:
        fallback = os.getenv("APIFY_YOUTUBE_FALLBACK_ACTOR", "").strip().lower()
    candidates = list(dict.fromkeys([actor, *[value.strip() for value in fallback.split(",") if value.strip()]]))[:3]
    # The $6-cap fallback actors (the ones beyond the cheap primary) stay off
    # unless a deployment explicitly opts in. The primary actor is unchanged.
    expensive = os.getenv("APIFY_EXPENSIVE_FALLBACKS", "false").strip().lower() in {"1", "true", "yes", "on"}
    if not expensive and len(candidates) > 1:
        logger.info(
            "Skipping %s Apify fallback actor(s); set APIFY_EXPENSIVE_FALLBACKS=true to enable them",
            len(candidates) - 1,
        )
        candidates = candidates[:1]
    if any(candidate not in providers for candidate in candidates):
        raise BudgetApifyError("Unsupported Apify fallback configuration.")
    try:
        budget = min(6.0, float(os.getenv("APIFY_DOWNLOAD_MAX_CHARGE_USD", "6")))
        provider_cap = min(6.0, float(os.getenv("APIFY_FALLBACK_MAX_CHARGE_USD", "6")))
        if not all(math.isfinite(value) and value > 0 for value in (budget, provider_cap)):
            raise ValueError()
    except ValueError:
        raise BudgetApifyError("Invalid Apify download spending limit.") from None
    timeout = max(60, min(1800, config.apify_run_timeout_seconds))
    for index, candidate in enumerate(candidates):
        cap = min(providers[candidate]["max_charge_usd"], provider_cap, budget)
        if cap < .001:
            raise BudgetApifyError("YouTube download exhausted its spending allowance.")
        try:
            return _run(candidate, video_id, temp_dir, quality, token, timeout, cap)
        except BudgetApifyError as exc:
            budget -= exc.spent if exc.spent is not None else cap
            if exc.permanent or index == len(candidates) - 1:
                raise
            logger.warning("Apify provider attempt %s failed for %s; trying fallback: %s", index + 1, video_id, exc)
    raise BudgetApifyError("YouTube download is not configured correctly.")

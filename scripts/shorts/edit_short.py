"""Direct short-form editor: EDL (JSON) -> 1080x1920 H.264/AAC MP4 with burned captions.

No paid APIs and no LLM: YouTube ASR captions (json3, word starts) give the timing,
ffmpeg does the cut/crop/zoom/caption/loudness work. Runs anywhere with ffmpeg+libass,
numpy and yt-dlp, e.g. the supoclip-worker container:

    docker cp scripts/shorts supoclip-worker:/tmp/shorts
    docker exec supoclip-worker /app/.venv/bin/python -I /tmp/shorts/edit_short.py /tmp/shorts/take_bureau_001.json

EDL fields (times are absolute seconds in the original video):
    url, section [start, end], workdir, output, font, fontsdir, keywords[]
    shots[]:  {start, end, cx, cy, zoom}  crop centre (source px) + framing zoom per camera shot
    pieces[]: [start, end]                kept ranges in output order (cold open first)

Optional (THE TAKE BUREAU V4 style, see STYLE_GUIDE.md):
    style        path to a styles/*.json preset; enables face-safe kinetic captions,
                 campaign attribution and qc.json. Without it the legacy look renders unchanged.
    highlights   {"WORD": "yellow|green|cyan|pink|red"}   colour chosen by meaning
    caption_drop [asr_start, ...]        mis-timed ASR words to hide (both modes)
    preview      true                     allow a short (>2 s) isolated test render
"""

import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))  # python -I drops the script dir from sys.path
import kinetic_captions as kinetic  # noqa: E402

W, H = 1080, 1920
SR = 16000


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)


def fetch(edl, wd):
    """Download only the needed section + ASR captions (skipped when already present)."""
    src, subs = wd / "section.mp4", wd / "asr.en-orig.json3"
    ytdlp = [sys.executable, "-m", "yt_dlp", "--no-config", "--proxy", ""]
    if not subs.exists():
        run(ytdlp + ["--skip-download", "--write-auto-subs", "--sub-langs", "en-orig",
                     "--sub-format", "json3", "-o", str(wd / "asr"), edl["url"]])
    if not src.exists():
        a, b = edl["section"]
        run(ytdlp + ["-f", "bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]/bv*[height<=1080]+ba",
                     "--download-sections", f"*{a}-{b}", "--merge-output-format", "mp4",
                     "-o", str(src), edl["url"]])
    return src, subs


def load_words(subs):
    words = []
    for ev in json.loads(subs.read_text(encoding="utf-8"))["events"]:
        for s in ev.get("segs", []):
            text = s["utf8"].strip()
            if text and not text.startswith("["):  # drop [ __ ], [clears throat]
                words.append(((ev.get("tStartMs", 0) + s.get("tOffsetMs", 0)) / 1000, text))
    return sorted(words)


def probe_fps(src):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                          "stream=r_frame_rate", "-of", "csv=p=0", str(src)],
                         capture_output=True, text=True, check=True).stdout.strip()
    n, d = out.split("/")
    return int(n) / int(d)


def audio_rms(src):
    pcm = subprocess.run(["ffmpeg", "-v", "error", "-i", str(src), "-ac", "1", "-ar", str(SR),
                          "-f", "s16le", "-"], capture_output=True, check=True).stdout
    x = np.frombuffer(pcm, np.int16).astype(np.float32)
    hop = SR // 100  # 10 ms
    n = len(x) // hop
    return np.sqrt((x[: n * hop].reshape(n, hop) ** 2).mean(1))


def snap(t, rms, base, lo, hi, reach=0.08):
    """Move a cut to a quiet 10 ms frame near t (within +-reach), never outside [lo, hi]."""
    a, b = max(t - reach, lo), min(t + reach, hi)
    i0, i1 = int((a - base) * 100), int((b - base) * 100) + 1
    if i1 <= i0:
        return min(max(t, lo), hi)
    times = base + np.arange(i0, i1) / 100
    cost = rms[i0:i1] + 2000 * np.abs(times - t)  # quiet first, then closest to the requested cut
    return float(times[int(np.argmin(cost))])


def shot_at(shots, t):
    return next(s for s in shots if s["start"] <= t < s["end"])


def build_pieces(edl, rms, fps):
    """Snap cuts to silence and frame boundaries, then split at camera cuts."""
    base, shots = edl["section"][0], edl["shots"]
    frame = 1 / fps
    out = []
    for a, b in edl["pieces"]:
        sa, sb = shot_at(shots, a), shot_at(shots, b - 1e-3)
        a = snap(a, rms, base, sa["start"], b)
        b = snap(b, rms, base, a, sb["end"] - frame)
        # frame-align (relative to section start) so audio and video lengths match exactly
        a = base + round((a - base) * fps) / fps
        b = base + round((b - base) * fps) / fps
        cuts = [s["start"] for s in shots if a < s["start"] < b]
        for x, y in zip([a] + cuts, cuts + [b]):
            x = base + round((x - base) * fps) / fps
            y = base + round((y - base) * fps) / fps
            if y - x >= frame:
                out.append((x, y, shot_at(shots, x)))
    return out


def ass_time(t):
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def place_words(edl, pieces, words):
    """Place each kept word on the output timeline: [start, end, word, piece_index]."""
    drop = [float(x) for x in edl.get("caption_drop", [])]  # ASR word starts to hide (mis-timed words)
    # YouTube ASR starts run ~0.1 s early
    timed, off = [], 0.0
    for i, (a, b, _) in enumerate(pieces):
        for t, w in words:
            if a - 0.1 <= t < b - 0.02 and not any(abs(t - d) < 0.005 for d in drop):
                if timed and timed[-1][3] == i and timed[-1][2].lower().strip(",.") == w.lower().strip(",."):
                    continue  # caption stutters ("it it") once
                timed.append([off + max(t - a, 0), off + (b - a), w, i])
        off += b - a
    for cur, nxt in zip(timed, timed[1:]):
        if nxt[3] == cur[3]:  # a word lasts until the next one starts (ASR gives starts only)
            cur[1] = nxt[0]
    return timed


def build_ass(edl, pieces, words, path):
    keywords = {k.upper() for k in edl["keywords"]}
    gold = "&H0000B4FF&"  # ASS is BGR: #FFB400
    timed = place_words(edl, pieces, words)
    # chunks of <=3 words, break at sentence ends and at cuts
    chunks, cur = [], []
    for item in timed:
        if cur and (len(cur) == 3 or cur[-1][3] != item[3] or re.search(r"[.?!,]$", cur[-1][2])):
            chunks.append(cur)
            cur = []
        cur.append(item)
    if cur:
        chunks.append(cur)
    lines = []
    for i, ch in enumerate(chunks):
        start = ch[0][0]
        end = chunks[i + 1][0][0] if i + 1 < len(chunks) else ch[-1][1]
        end = min(end, ch[-1][1] + 0.6)
        parts = []
        for _, _, w, _ in ch:
            clean = re.sub(r'[",.!?]', "", w).upper()
            if not clean:
                continue
            parts.append(f"{{\\c{gold}}}{clean}{{\\c&H00FFFFFF&}}" if clean in keywords else clean)
        if parts:
            # shrink long lines so they stay on one line inside the 1080 px frame
            chars = len(" ".join(re.sub(r"\{[^}]*\}", "", p) for p in parts))
            fs = min(edl.get("font_size", 118), int((W - 140) / (0.36 * chars)))  # ~0.33 em/char measured for Poppins ExtraBold caps)
            pop = rf"{{\fs{fs}\fscx112\fscy112\t(0,90,\fscx100\fscy100)}}"
            lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Cap,,0,0,0,,{pop}{' '.join(parts)}")
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{edl["font"]},{edl.get("font_size", 96)},&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,0,0,0,0,100,100,1,0,1,7,3,2,70,70,{edl.get("caption_margin_v", 560)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
    return timed


def framings(pieces, punch_in=1.12):
    """Source crop (cw, ch, x, y) per piece; shared by the render and the caption face check."""
    out, prev_shot, punch = [], None, 1.0
    for s, e, shot in pieces:
        # jump cut inside one camera shot: alternate a punch-in so the cut reads as intentional
        punch = (punch_in if punch == 1.0 else 1.0) if shot is prev_shot else 1.0
        prev_shot = shot
        z = shot["zoom"] * punch
        cw, ch = round(608 / z / 2) * 2, round(1080 / z / 2) * 2
        x = min(max(round(shot["cx"] - cw / 2), 0), 1920 - cw)
        y = min(max(round(shot["cy"] - 0.3 * ch), 0), 1080 - ch)
        out.append((cw, ch, x, y))
    return out


def build_filter(pieces, crops, base, ass_path, fontsdir, pushin=(0.012, 0.05), audio=(-14, -1.5)):
    """pieces use absolute video times; the downloaded section file starts at `base`."""
    v, a, chains = [], [], []
    for i, ((s, e, shot), (cw, ch, x, y)) in enumerate(zip(pieces, crops)):
        d = e - s
        rate = min(pushin[0], pushin[1] / d)  # slow push-in, ~1.2%/s, never more than 5% per piece
        chains.append(
            f"[0:v]trim=start={s - base:.6f}:end={e - base:.6f},setpts=PTS-STARTPTS,crop={cw}:{ch}:{x}:{y},"
            f"scale={W}:{H}:flags=lanczos,"
            f"scale=w='2*trunc({W}*(1+{rate:.5f}*t)/2)':h=-2:eval=frame:flags=lanczos,"
            f"crop={W}:{H}:(iw-{W})/2:(ih-{H})*0.35,setsar=1[v{i}]")
        chains.append(
            f"[0:a]atrim=start={s - base:.6f}:end={e - base:.6f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:d=0.012,afade=t=out:st={max(d - 0.018, 0):.4f}:d=0.018[a{i}]")
        v.append(f"[v{i}]")
        a.append(f"[a{i}]")
    inter = "".join(x + y for x, y in zip(v, a))
    chains.append(f"{inter}concat=n={len(pieces)}:v=1:a=1[vc][ac]")
    chains.append(f"[vc]ass={ass_path}:fontsdir={fontsdir}[vout]")
    chains.append(f"[ac]highpass=f=70,loudnorm=I={audio[0]}:TP={audio[1]}:LRA=11,aresample=48000[aout]")
    return ";\n".join(chains)


def main(edl_path):
    edl = json.loads(Path(edl_path).read_text(encoding="utf-8"))
    wd = Path(edl["workdir"])
    wd.mkdir(parents=True, exist_ok=True)
    src, subs = fetch(edl, wd)
    fps = probe_fps(src)
    rms = audio_rms(src)
    pieces = build_pieces(edl, rms, fps)
    ass = wd / "captions.ass"
    style = kinetic.load_style(edl, edl_path) if edl.get("style") else None
    if style is None:  # legacy productions (001/002) render exactly as before
        crops = framings(pieces)
        build_ass(edl, pieces, load_words(subs), ass)
        fontsdir, extra = edl["fontsdir"], {}
    else:
        cam = style["camera"]
        crops = framings(pieces, cam["jump_cut_punch"])
        faces = kinetic.detect_faces(src, edl["section"][0], pieces, crops, style)
        qc = kinetic.build_ass(edl, style, place_words(edl, pieces, load_words(subs)), faces, ass)
        fontsdir = style["fontsdir"]
        extra = {"pushin": (cam["pushin_per_s"], cam["pushin_max"]),
                 "audio": (style["audio"]["lufs"], style["audio"]["true_peak"])}
        flagged = [c for c in qc if c["issues"]]
        print(f"captions: {len(qc)} checked, {len(flagged)} flagged, faces found in "
              f"{sum(f is not None for f in faces)}/{len(faces)} pieces")
        for c in flagged:
            print("  QC", c.get("start", c.get("piece")), c.get("text", ""), c["issues"])
    graph = wd / "graph.txt"
    graph.write_text(build_filter(pieces, crops, edl["section"][0], ass, fontsdir, **extra), encoding="utf-8")
    total = sum(e - s for s, e, _ in pieces)
    for s, e, shot in pieces:
        print(f"piece {s:.3f}-{e:.3f} ({e - s:.2f}s) shot@{shot['start']}")
    print(f"total {total:.2f}s, {len(pieces)} pieces")
    assert (2 if edl.get("preview") else 10) < total < 90, "edit length out of range"
    run(["ffmpeg", "-v", "error", "-stats", "-y", "-i", str(src), "-filter_complex_script", str(graph),
         "-map", "[vout]", "-map", "[aout]", "-r", f"{fps}",
         "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-profile:v", "high", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", edl["output"]])
    print("wrote", edl["output"])
    if style is not None:
        report = {"style": style["name"], "output": kinetic.check_output(edl["output"], style), "captions": qc}
        (wd / "qc.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
        print("qc:", report["output"], "->", wd / "qc.json")


if __name__ == "__main__":
    main(sys.argv[1])

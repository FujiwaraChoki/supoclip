"""TAKE_BUREAU_CLEAN_KINETIC_V1 captions for edit_short.py: face-safe layout + campaign attribution.

Used only when an EDL sets "style"; EDLs without it render exactly as before.

Per piece the output framing is sampled and faces are detected with the project's YuNet model
(Haar fallback). Every caption chunk gets its line layout fixed up front (measured from the TTF,
so no reflow), then a Y position: the style's target, pushed below the chin when needed and kept
above the attribution / platform UI. What cannot be solved by moving or shrinking is flagged in
qc.json for manual review instead of being rendered silently over the face.
"""

import json
import re
import struct
import subprocess
from pathlib import Path

import cv2
import numpy as np


# ---------- font metrics (no PIL/fontTools in the worker) ----------

class TTFMetrics:
    """Advance widths from a TTF's cmap (format 4) + hmtx, scaled the way libass sizes fonts."""

    def __init__(self, path):
        d = Path(path).read_bytes()
        n = struct.unpack(">H", d[4:6])[0]
        t = {}
        for i in range(n):
            rec = d[12 + 16 * i: 28 + 16 * i]
            t[rec[:4].decode("latin1")] = struct.unpack(">II", rec[8:16])
        head, hhea, hmtx, os2, cmap = (t[k][0] for k in ("head", "hhea", "hmtx", "OS/2", "cmap"))
        self.upem = struct.unpack(">H", d[head + 18: head + 20])[0]
        nh = struct.unpack(">H", d[hhea + 34: hhea + 36])[0]
        self.adv = [struct.unpack(">H", d[hmtx + 4 * i: hmtx + 4 * i + 2])[0] for i in range(nh)]
        win_a, win_d = struct.unpack(">HH", d[os2 + 74: os2 + 78])
        self.line_units = win_a + win_d  # libass maps \fs to winAscent+winDescent
        self._cmap = self._format4(d, cmap)

    @staticmethod
    def _format4(d, cmap):
        count = struct.unpack(">H", d[cmap + 2: cmap + 4])[0]
        for i in range(count):
            pid, eid, off = struct.unpack(">HHI", d[cmap + 4 + 8 * i: cmap + 12 + 8 * i])
            sub = cmap + off
            if (pid, eid) in ((3, 1), (0, 3), (0, 4)) and struct.unpack(">H", d[sub: sub + 2])[0] == 4:
                seg = struct.unpack(">H", d[sub + 6: sub + 8])[0] // 2
                arr = lambda o: struct.unpack(f">{seg}H", d[o: o + 2 * seg])  # noqa: E731
                ends = arr(sub + 14)
                starts = arr(sub + 16 + 2 * seg)
                deltas = arr(sub + 16 + 4 * seg)
                ro_pos = sub + 16 + 6 * seg
                ros = arr(ro_pos)
                return d, ends, starts, deltas, ros, ro_pos
        raise ValueError("font has no format-4 cmap")

    def glyph(self, ch):
        d, ends, starts, deltas, ros, ro_pos = self._cmap
        c = ord(ch)
        for i, end in enumerate(ends):
            if end >= c:
                if starts[i] > c:
                    return 0
                if ros[i] == 0:
                    return (c + deltas[i]) & 0xFFFF
                g = struct.unpack(">H", d[ro_pos + 2 * i + ros[i] + 2 * (c - starts[i]): ][:2])[0]
                return (g + deltas[i]) & 0xFFFF if g else 0
        return 0

    def width(self, text, fs, spacing=0):
        """Rendered width in px at ASS font size fs. shortcut: ignores kerning (~1-2%), margins absorb it."""
        units = sum(self.adv[min(self.glyph(c), len(self.adv) - 1)] for c in text)
        return units * fs / self.line_units + spacing * max(len(text) - 1, 0)


# ---------- style helpers ----------

def load_style(edl, edl_path):
    if not edl.get("style"):
        return None
    p = Path(edl["style"])
    if not p.is_absolute():
        p = Path(edl_path).parent / p
    return json.loads(p.read_text(encoding="utf-8"))


def ass_color(hex_rgb, alpha=1.0):
    """#RRGGBB + opacity -> &HAABBGGRR (ASS alpha: 00 opaque, FF transparent)."""
    r, g, b = hex_rgb[1:3], hex_rgb[3:5], hex_rgb[5:7]
    return f"&H{round((1 - alpha) * 255):02X}{b}{g}{r}".upper()


def highlight_map(edl, style):
    """EDL "highlights" {"WORD": "cyan"} (new) and legacy "keywords" [..] (default colour)."""
    colors = style["highlight"]["colors"]
    out = {k.upper(): style["highlight"]["default"] for k in edl.get("keywords", [])}
    out.update({k.upper(): v for k, v in edl.get("highlights", {}).items()})
    bad = {k: v for k, v in out.items() if v not in colors}
    assert not bad, f"unknown highlight colours: {bad}"
    return out


# ---------- face detection on the actual output framing ----------

def detect_faces(src, base, pieces, crops, style):
    """Union face box (output px) per piece, or None when no face was found."""
    W, H = style["frame"]["width"], style["frame"]["height"]
    lay = style["layout"]
    sw, sh = W // 2, H // 2  # detect at half size; faces stay far above YuNet's minimum
    try:
        det = cv2.FaceDetectorYN.create(lay["face_model"], "", (sw, sh), lay["face_score"], 0.3, 50)
    except cv2.error:
        det = None
    haar = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    out = []
    for (s, e, _), (cw, ch, x, y) in zip(pieces, crops):
        boxes = []
        for f in lay["face_samples"]:
            t = s + (e - s) * f - base
            raw = subprocess.run(
                ["ffmpeg", "-v", "error", "-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1",
                 "-vf", f"crop={cw}:{ch}:{x}:{y},scale={sw}:{sh}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                capture_output=True, check=True).stdout
            if len(raw) != sw * sh * 3:
                continue
            img = np.frombuffer(raw, np.uint8).reshape(sh, sw, 3)
            if det is not None:
                _, faces = det.detect(img)
                found = [] if faces is None else [f_[:4] for f_ in faces]
            else:
                gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                found = list(haar.detectMultiScale(gray, 1.1, 5, minSize=(40, 40)))
            boxes += [(bx * 2, by * 2, (bx + bw) * 2, (by + bh) * 2) for bx, by, bw, bh in found]
        if not boxes:
            out.append(None)
            continue
        x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
        x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
        # the slow push-in scales about (W/2, 0.35 H); include the box at the end of the piece too
        k, ay = lay["pushin_pad"], 0.35 * H
        x0, x1 = min(x0, W / 2 + (x0 - W / 2) * k), max(x1, W / 2 + (x1 - W / 2) * k)
        y0, y1 = min(y0, ay + (y0 - ay) * k), max(y1, ay + (y1 - ay) * k)
        out.append((x0, y0, x1, y1))
    return out


# ---------- chunking, layout and placement ----------

def chunk_words(timed, cap):
    """<= max_words / max_chars per chunk; break at sentence punctuation and at cuts."""
    chunks, cur = [], []
    for item in timed:
        text = " ".join(w for _, _, w, _ in cur + [item])
        if cur and (len(cur) >= cap["max_words"] or len(text) > cap["max_chars"]
                    or cur[-1][3] != item[3] or re.search(r"[.?!,;:]$", cur[-1][2])):
            chunks.append(cur)
            cur = []
        cur.append(item)
    if cur:
        chunks.append(cur)
    return chunks


def layout_lines(words, metrics, cap, max_w):
    """Fixed line split + font size, decided before anything animates (no reflow)."""
    sp = cap["letter_spacing"]
    one = " ".join(words)
    fs = min(cap["size_max"], cap["size_max"] * max_w / max(metrics.width(one, cap["size_max"], sp), 1))
    if fs >= cap["size_min"] or len(words) < 2 or cap["max_lines"] < 2:
        return [words], max(fs, cap["size_min"]) if len(words) > 1 else fs
    best = min(range(1, len(words)),
               key=lambda i: max(metrics.width(" ".join(words[:i]), 100, sp),
                                 metrics.width(" ".join(words[i:]), 100, sp)))
    lines = [words[:best], words[best:]]
    widest = max(metrics.width(" ".join(ln), cap["size_max"], sp) for ln in lines)
    return lines, min(cap["size_max"], cap["size_max"] * max_w / widest)


def place(n_lines, fs, face, style, limit_top, limit_bottom):
    """Caption centre Y (\\an5) and whether it is collision-free. Box = full line height (conservative)."""
    H, cap, lay = style["frame"]["height"], style["caption"], style["layout"]
    box_h = fs * cap["line_spacing"] * n_lines
    lo = max(limit_top, face[3] + lay["face_gap_px"] if face else 0) + box_h / 2
    hi = limit_bottom - box_h / 2
    y = max(lay["target_y"] * H, lo)
    return min(y, hi), y <= hi


def build_ass(edl, style, timed, faces, path):
    """timed: [start, end, word, piece_index] on the output timeline (edit_short.place_words)."""
    W, H = style["frame"]["width"], style["frame"]["height"]
    cap, lay, attr, hl = style["caption"], style["layout"], style["attribution"], style["highlight"]
    fontsdir = Path(style["fontsdir"])
    metrics = TTFMetrics(fontsdir / cap["font_file"])
    attr_metrics = TTFMetrics(fontsdir / attr["font_file"])
    highlights = highlight_map(edl, style)

    # caption column: symmetric around the centre, inside the platform side margins
    half = min(W / 2 - lay["safe_left"] * W, W / 2 - lay["safe_right"] * W)
    max_w = 2 * half - 2 * cap["outline_px"]

    attr_on = bool(attr.get("text"))
    attr_w = attr_metrics.width(attr["text"], attr["size"], attr["letter_spacing"]) if attr_on else 0
    attr_box = (W / 2 - attr_w / 2, attr["y"] * H - attr["size"] / 2,
                W / 2 + attr_w / 2, attr["y"] * H + attr["size"] / 2)
    # the attribution bounds the caption band from whichever side it sits on
    limit_top, limit_bottom = lay["safe_top"] * H, lay["safe_bottom"] * H
    if attr_on and attr["y"] > lay["target_y"]:
        limit_bottom = min(limit_bottom, attr_box[1] - attr["gap_px"])
    elif attr_on:
        limit_top = max(limit_top, attr_box[3] + attr["gap_px"])

    chunks = chunk_words(timed, cap)
    events, qc = [], []
    for i, ch in enumerate(chunks):
        start = ch[0][0]
        end = chunks[i + 1][0][0] if i + 1 < len(chunks) else ch[-1][1]
        end = min(end, ch[-1][1] + cap["hold_max_s"])
        words = [w for w in (re.sub(r'[",.!?;:]', "", w_).upper() for _, _, w_, _ in ch) if w]
        if not words or end <= start:
            continue
        lines, fs = layout_lines(words, metrics, cap, max_w)
        face = faces[ch[0][3]]
        issues = [] if face else ["no_face_detected"]
        y, ok = place(len(lines), fs, face, style, limit_top, limit_bottom)
        while not ok and fs - lay["shrink_step_px"] >= cap["size_min"]:
            fs -= lay["shrink_step_px"]
            y, ok = place(len(lines), fs, face, style, limit_top, limit_bottom)
        if not ok:
            issues.append("face_collision_unresolved")
        widest = max(metrics.width(" ".join(ln), fs, cap["letter_spacing"]) for ln in lines)
        if widest > max_w + 1:
            issues.append("too_wide")
        if fs < cap["size_min"]:
            issues.append("below_min_size")

        # one highlighted run per chunk (consecutive highlighted words count as one phrase)
        runs, prev, flat = 0, False, []
        for w in words:
            color = highlights.get(w)
            if color and not prev:
                runs += 1
            use = color if color and runs <= hl["max_runs_per_chunk"] else None
            if color and not use:
                issues.append(f"extra_highlight_dropped:{w}")
            flat.append((w, use))
            prev = bool(color)
        out_lines, k = [], 0
        for ln in lines:
            parts = []
            for w, color in flat[k:k + len(ln)]:
                parts.append(rf"{{\c{ass_color(hl['colors'][color])}}}{w}{{\c{ass_color(cap['base_color'])}}}"
                             if color else w)
            k += len(ln)
            out_lines.append(" ".join(parts))
        pop = rf"\fscx{cap['pop_from_pct']}\fscy{cap['pop_from_pct']}\t(0,{cap['pop_ms']},\fscx100\fscy100)"
        events.append(f"Dialogue: 1,{ass_time(start)},{ass_time(end)},Cap,,0,0,0,,"
                      rf"{{\blur{cap['blur']}\an5\pos({W // 2},{round(y)})\fs{round(fs)}{pop}}}" + r"\N".join(out_lines))
        box = (W / 2 - widest / 2, y - fs * len(lines) / 2, W / 2 + widest / 2, y + fs * len(lines) / 2)
        if attr_on and box[3] > attr_box[1] and box[1] < attr_box[3]:
            issues.append("attribution_collision")
        qc.append({"start": round(start, 3), "end": round(end, 3), "text": " / ".join(" ".join(ln) for ln in lines),
                   "fs": round(fs), "y": round(y), "box": [round(v) for v in box],
                   "face": [round(v) for v in face] if face else None, "issues": issues})

    total = timed[-1][1] if timed else 0
    if attr_on:
        events.append(f"Dialogue: 0,{ass_time(0)},{ass_time(total + 1)},Attr,,0,0,0,,"
                      rf"{{\an5\pos({W // 2},{round(attr['y'] * H)})}}{attr['text']}")
        for n, face in enumerate(faces):
            if face and face[3] > attr_box[1] and face[1] < attr_box[3] and face[2] > attr_box[0] and face[0] < attr_box[2]:
                qc.append({"piece": n, "issues": ["attribution_over_face"]})

    oc = ass_color(cap["outline_color"], cap["outline_alpha"])
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{cap["font"]},{cap["size_max"]},{ass_color(cap["base_color"])},&H00FFFFFF,{oc},{oc},0,0,0,0,100,100,{cap["letter_spacing"]},0,1,{cap["outline_px"]},{cap["shadow_px"]},5,0,0,0,1
Style: Attr,{attr["font"]},{attr["size"]},{ass_color(attr["color"], attr["alpha"])},&H00FFFFFF,{ass_color("#000000", 0.55)},&HFF000000,{-1 if attr.get("bold") else 0},0,0,0,100,100,{attr["letter_spacing"]},0,1,{attr["outline_px"]},0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return qc


def ass_time(t):
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


# ---------- final file checks ----------

def check_output(path, style):
    """Technical validity of the rendered MP4 (codec, size, fps, audio, loudness)."""
    info = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration:stream=codec_type,codec_name,width,height,pix_fmt,r_frame_rate,sample_rate",
         "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout)
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    a = next((s for s in info["streams"] if s["codec_type"] == "audio"), None)
    log = subprocess.run(["ffmpeg", "-nostats", "-i", str(path), "-af", "ebur128=framelog=quiet", "-f", "null", "-"],
                         capture_output=True, text=True).stderr
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", log)
    lufs = float(m[-1]) if m else None
    W, H = style["frame"]["width"], style["frame"]["height"]
    checks = {
        "h264": v["codec_name"] == "h264",
        "size": (v["width"], v["height"]) == (W, H),
        "yuv420p": v.get("pix_fmt") == "yuv420p",
        "aac_audio": bool(a) and a["codec_name"] == "aac",
        "loudness_within_1lu": lufs is not None and abs(lufs - style["audio"]["lufs"]) <= 1.0,
    }
    return {"duration": float(info["format"]["duration"]), "fps": v["r_frame_rate"], "lufs": lufs,
            "checks": checks, "ok": all(checks.values())}


if __name__ == "__main__":  # self-check of the font reader against the bundled fonts
    import sys
    m = TTFMetrics(sys.argv[1] if len(sys.argv) > 1 else "/app/fonts/Anton-Regular.ttf")
    assert m.width("W", 100) > m.width("I", 100) > 0, "advance widths look wrong"
    assert abs(m.width("AB", 100) - m.width("A", 100) - m.width("B", 100)) < 1e-6
    assert m.glyph("A") != 0 and m.glyph("\u4e00") == 0
    print("font metrics ok", round(m.width("THE TAKE BUREAU", 100)), "px @ fs100")

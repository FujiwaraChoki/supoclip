// @vitest-environment node
import { describe, expect, it } from "vitest";
import type { DrawOp } from "../host";
import { drawerErrors } from "../frame";
import { accentDrawerFor } from "../registry";
import { applyMat } from "../layout";
import { BURST, EMOJI, EMOJI_PEAK_T, burstCount, burstParticle, emojiLandScale, emojiPopAway } from "./emoji_drop";
import { accent, accentOps, expectGoldens, expectInsideFrame, prep, snap, templateScene } from "./wp7.testkit";

type SpriteOp = Extract<DrawOp, { op: "sprite" }>;
const sprites = (ops: DrawOp[]) => ops.filter((o): o is SpriteOp => o.op === "sprite");
const centre = (s: SpriteOp) => applyMat(s.transform, s.x + s.w / 2, s.y + s.h / 2);
const scaleOf = (s: SpriteOp) => Math.hypot(s.transform[0], s.transform[1]);

// "revenue" starts at 0.56 (line "Our revenue grew"), "300%" at 1.28 (line "300% in one").
const EMOJI_CUE = accent("b1.accent", "emoji", 0.56, 1.76, { text: "revenue", emoji: "💰" });
const BURST_CUE = accent("b2.burst", "emoji_burst", 1.28, 2.28, { text: "300%", emoji: "🔥", value: 8 });
const scene = (w = 1080, h = 1920, cues = [EMOJI_CUE, BURST_CUE]) => templateScene("emoji_drop", { width: w, height: h, cues }, { font: "Poppins-ExtraBold" });

describe("emoji_drop frameOps", () => {
  it("emoji: snapshots at 1080x1920 and inside the frame", () => {
    for (const t of [0.6, 0.65, 0.75, 0.9, 1.4, 1.68]) {
      const ops = accentOps(scene(1080, 1920, [EMOJI_CUE]), t);
      expect(snap(ops)).toMatchSnapshot(`t=${t}`);
      expectInsideFrame(ops, 1080, 1920);
    }
  });

  it("emoji_burst: snapshots at 1080x1920", () => {
    for (const t of [1.28, 1.32, 1.4, 1.6, 1.9]) expect(snap(accentOps(scene(1080, 1920, [BURST_CUE]), t))).toMatchSnapshot(`t=${t}`);
  });

  it("1920x1080: the emoji rests inside the frame (above the caption when there is no room below)", () => {
    const ops = accentOps(scene(1920, 1080, [EMOJI_CUE]), 1.2);
    expect(snap(ops)).toMatchSnapshot("1920x1080 t=1.2");
    expectInsideFrame(ops, 1920, 1080);
    const burst = accentOps(scene(1920, 1080, [BURST_CUE]), 1.3);
    expect(snap(burst)).toMatchSnapshot("1920x1080 burst t=1.3");
    expectInsideFrame(burst, 1920, 1080, 200);
  });

  it("the emoji is a 160u sprite under the key word, below the caption line", () => {
    const p = prep(scene(1080, 1920, [EMOJI_CUE]));
    const word = p.chunks[0].words[1];
    const [s] = sprites(accentOps(scene(1080, 1920, [EMOJI_CUE]), 1.4));
    expect(s.key).toBe("💰");
    expect(s.w).toBe(160);
    const [x, y] = centre(s);
    expect(x).toBeCloseTo(word.x + word.w / 2, 0);
    expect(y - 80).toBeGreaterThan(word.y + word.h);
  });

  it("does not move when the caption line under it changes", () => {
    const at = (t: number) => centre(sprites(accentOps(scene(1080, 1920, [EMOJI_CUE]), t))[0]);
    expect(at(1.2)[0]).toBeCloseTo(at(1.5)[0], 6);   // the next line starts at 1.28
    expect(at(1.2)[1]).toBeCloseTo(at(1.5)[1], 0);
  });

  it("burst: N sprites (from the cue value) at 96u launched upward from the word", () => {
    expect(sprites(accentOps(scene(1080, 1920, [BURST_CUE]), 1.4))).toHaveLength(8);
    expect(sprites(accentOps(scene(1080, 1920, [{ ...BURST_CUE, value: 5 }]), 1.4))).toHaveLength(5);
    expect(burstCount({ ...BURST_CUE, value: null })).toBe(5);
    const s = sprites(accentOps(scene(1080, 1920, [BURST_CUE]), 1.4));
    expect(s.every((x) => x.w === 96)).toBe(true);
    // Nothing after the 0.7 s fade.
    expect(sprites(accentOps(scene(1080, 1920, [BURST_CUE]), 1.28 + 0.75))).toHaveLength(0);
  });

  it("still lands when no on-screen caption holds the key word (caption position instead)", () => {
    const stray = accent("u1.accent", "emoji", 0.56, 1.76, { text: "zebra", emoji: "💰" });
    const late = accent("u2.accent", "emoji", 10, 11.2, { text: "later", emoji: "💰" });   // after the last caption
    for (const cue of [stray, late]) {
      const s = scene(1080, 1920, [cue]);
      const ops = sprites(accentOps(s, cue.start + 0.4));
      expect(ops).toHaveLength(1);
      expectInsideFrame(ops, 1080, 1920);
    }
  });

  it("registers emoji and emoji_burst in the overlay layer, without errors", () => {
    expect(accentDrawerFor("emoji")?.layer).toBe("overlay");
    expect(accentDrawerFor("emoji_burst")?.layer).toBe("overlay");
    const p = prep(scene());
    for (let t = 0; t < 2.4; t += 1 / 30) accentOps(scene(), t);
    expect(drawerErrors(p)).toEqual([]);
  });

  it("is deterministic", () => {
    expect(snap(accentOps(scene(), 1.5))).toEqual(snap(accentOps(scene(), 1.5)));
  });
});

describe("emoji motion", () => {
  it("spring ω=18 ζ=0.35: scale 0 → peak 1.15 ± 0.02 → 1", () => {
    let peak = 0, at = 0;
    for (let t = 0; t <= 1.2; t += 0.001) {
      const v = emojiLandScale(t);
      if (v > peak) { peak = v; at = t; }
    }
    expect(peak).toBeGreaterThan(1.13);
    expect(peak).toBeLessThan(1.17);
    expect(at).toBeCloseTo(EMOJI_PEAK_T, 2);
    expect(emojiLandScale(0)).toBe(0);
    expect(emojiLandScale(1.2)).toBeCloseTo(1, 2);
    // Continuous through the peak (the two halves of the reshaped spring meet there).
    expect(Math.abs(emojiLandScale(EMOJI_PEAK_T - 1e-6) - emojiLandScale(EMOJI_PEAK_T + 1e-6))).toBeLessThan(1e-4);
  });

  it("rises from 60u below and wobbles from ±10°, decaying", () => {
    const s0 = sprites(accentOps(scene(1080, 1920, [EMOJI_CUE]), 0.56 + 1 / 30))[0];
    const rest = sprites(accentOps(scene(1080, 1920, [EMOJI_CUE]), 1.5))[0];
    expect(centre(s0)[1] - centre(rest)[1]).toBeGreaterThan(30);
    expect(centre(s0)[1] - centre(rest)[1]).toBeLessThanOrEqual(EMOJI.riseFrom);
    const tilt = (s: SpriteOp) => Math.abs(Math.atan2(s.transform[1], s.transform[0]) * 180 / Math.PI);
    expect(tilt(s0)).toBeLessThanOrEqual(10);
    expect(tilt(s0)).toBeGreaterThan(7);
    expect(tilt(rest)).toBeLessThan(0.5);
  });

  it("pops away in the last 150 ms: 1 → 1.2 → 0, fading out", () => {
    expect(emojiPopAway(1.0, 1.2)).toEqual({ scale: 1, alpha: 1 });
    expect(emojiPopAway(1.05 + 0.05, 1.2).scale).toBeCloseTo(1.2);
    expect(emojiPopAway(1.2, 1.2).scale).toBeCloseTo(0, 9);
    expect(emojiPopAway(1.2, 1.2).alpha).toBeCloseTo(0, 9);
    const late = sprites(accentOps(scene(1080, 1920, [EMOJI_CUE]), 1.76 - 0.03))[0];
    expect(late.alpha).toBeCloseTo(0.36, 6);   // 1 − easeInQuad(0.12 / 0.15)
    expect(scaleOf(late)).toBeLessThan(0.8);
  });

  it("burst particles follow ballistics: seeded angle in [−150°, −30°], 900–1500u/s, gravity 2400u/s²", () => {
    const hash = (n: number) => (n * 0.754877) % 1;
    for (let i = 0; i < 8; i++) {
      const a = burstParticle(i, 0.1, [500, 1000], 1, hash), b = burstParticle(i, 0.2, [500, 1000], 1, hash);
      const angle = Math.atan2(a.y - 1000 - 0.5 * BURST.gravity * 0.01, a.x - 500) * 180 / Math.PI;
      expect(angle).toBeGreaterThanOrEqual(-150 - 1e-6);
      expect(angle).toBeLessThanOrEqual(-30 + 1e-6);
      const speed = Math.hypot(a.x - 500, a.y - 1000 - 0.5 * BURST.gravity * 0.01) / 0.1;
      expect(speed).toBeGreaterThanOrEqual(900 - 1e-6);
      expect(speed).toBeLessThanOrEqual(1500 + 1e-6);
      expect(b.alpha).toBeLessThan(a.alpha);
      expect(Math.abs(b.rot - a.rot)).toBeLessThanOrEqual(2 * Math.PI * 0.1 + 1e-9);
    }
    expect(burstParticle(0, 0.7, [0, 0], 1, hash).alpha).toBe(0);
  });
});

describe("emoji_drop raster goldens", () => {
  const golden = () => templateScene("emoji_drop", { width: 540, height: 960, cues: [EMOJI_CUE, BURST_CUE] }, { font: "Poppins-ExtraBold" });
  it("emoji", () => expectGoldens("emoji_drop__emoji", golden(), [0.66, 0.78, 1.1]));
  it("emoji_burst", () => expectGoldens("emoji_drop__emoji_burst", golden(), [1.36, 1.5, 1.75]));
});

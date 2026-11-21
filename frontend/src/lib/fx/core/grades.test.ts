// @vitest-environment node
import { describe, expect, it } from "vitest";
import { createCanvas } from "@napi-rs/canvas";
import { clockAt, frameOps, isPassthrough } from "./frame";
import type { DrawOp } from "./host";
import { applyColorMatrix, filterMatrix } from "./raster";
import { prepareScene } from "./scene";
import type { GradeId } from "./types";
import { fakeMeasureHost } from "./test-utils/fake-host";
import { expectGolden, renderSceneToPng } from "./test-utils/golden";
import { bareScene, faceTrack, snap } from "./picture/fixtures";
import { GRADE_SPECS, gradeHasExtras, gradeMatrix, gradeOps } from "./grades";

const GRADES = Object.keys(GRADE_SPECS) as GradeId[];
type PictureOp = Extract<DrawOp, { op: "picture" }>;

/** The pixel after napi's ctx.filter (the same Skia filter path Chrome and deno use). */
function napiFiltered(filter: string, rgb: [number, number, number]): number[] {
  const src = createCanvas(4, 4), dst = createCanvas(4, 4);
  const s = src.getContext("2d");
  s.fillStyle = `rgb(${rgb.join(",")})`;
  s.fillRect(0, 0, 4, 4);
  const d = dst.getContext("2d");
  d.filter = filter;
  d.drawImage(src, 0, 0);
  return Array.from(d.getImageData(1, 1, 1, 1).data.subarray(0, 3));
}

describe("grades", () => {
  it("gradeOps is the §11.4 filter list", () => {
    expect(gradeOps("none")).toBe("");
    expect(gradeOps("punchy")).toBe("contrast(1.12) saturate(1.25)");
    expect(gradeOps("warm")).toBe("sepia(0.18) saturate(1.1) brightness(1.03)");
    expect(gradeOps("teal_orange")).toBe("contrast(1.08) saturate(1.15)");
    expect(gradeOps("noir")).toBe("grayscale(1) contrast(1.3) brightness(0.95)");
    expect(gradeOps("vhs")).toBe("saturate(1.3) contrast(0.92)");
    expect(gradeOps("dream")).toBe("brightness(1.06) contrast(0.92) saturate(1.1)");
    expect(gradeMatrix("none")).toEqual([1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0]);
    for (const g of GRADES) expect(gradeMatrix(g)).toEqual(filterMatrix(gradeOps(g)) ?? gradeMatrix("none"));
  });

  it("gradeMatrix × a pixel ≈ ctx.filter on napi (±2/255) for every grade", () => {
    const pixels: [number, number, number][] = [[128, 128, 128], [150, 110, 90], [70, 95, 130], [180, 160, 140], [100, 140, 110], [60, 60, 70]];
    for (const g of GRADES) {
      for (const rgb of pixels) {
        const px = new Uint8ClampedArray([...rgb, 255]);
        applyColorMatrix(px, gradeMatrix(g));
        const want = g === "none" ? [...rgb] : napiFiltered(gradeOps(g), rgb);
        for (let c = 0; c < 3; c++) expect(Math.abs(px[c] - want[c]), `${g} ${rgb} channel ${c}`).toBeLessThanOrEqual(2);
      }
    }
  });

  it("the grade goes into the picture filter first; extras land in the pixel layer; VHS splits 2u", () => {
    for (const g of GRADES) {
      const p = prepareScene(bareScene({ grade: g, width: 540, height: 960 }), fakeMeasureHost);
      const ops = frameOps(p, clockAt(p, 1));
      const pics = ops.filter((o): o is PictureOp => o.op === "picture");
      expect(pics[0].filter).toBe(gradeOps(g));
      expect(isPassthrough(p, clockAt(p, 1))).toBe(g === "none");
      expect(ops.some((o) => o.op !== "picture" || o.blend === "screen")).toBe(gradeHasExtras(g));
      expect(snap(ops)).toMatchSnapshot(g);
    }
    const vhs = prepareScene(bareScene({ grade: "vhs", width: 540, height: 960 }), fakeMeasureHost);
    const ops = frameOps(vhs, clockAt(vhs, 1));
    expect(ops.slice(0, 3).map((o) => (o.op === "picture" ? [o.channel, o.dx] : null))).toEqual([["r", -1], ["g", 0], ["b", 1]]);
    expect(ops.some((o) => o.op === "scanlines")).toBe(true);
  });

  it("raster goldens per grade", () => {
    for (const g of GRADES) {
      if (g === "none") continue;
      expectGolden(`grade__${g}`, renderSceneToPng(bareScene({ grade: g, width: 540, height: 960, face_track: faceTrack() }), 1));
    }
  });
});

/** A short burst of confetti from one spot, for finishing something. No dependency, no layout cost. */

const COLORS = ["#ff5a1f", "#ff1f6b", "#8a3dff", "#1f7bff", "#12d69c", "#ffc83d"];
const DURATION_MS = 2200;

type Piece = { x: number; y: number; vx: number; vy: number; spin: number; angle: number; size: number; color: string; round: boolean };

export function celebrate(origin?: { x: number; y: number }) {
  if (typeof window === "undefined" || window.matchMedia?.("(prefers-reduced-motion: reduce)").matches) return;
  const canvas = document.createElement("canvas");
  canvas.setAttribute("aria-hidden", "true");
  Object.assign(canvas.style, { position: "fixed", inset: "0", width: "100vw", height: "100vh", pointerEvents: "none", zIndex: "60" });
  document.body.appendChild(canvas);
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = window.innerWidth * ratio;
  canvas.height = window.innerHeight * ratio;
  const context = canvas.getContext("2d");
  if (!context) {
    canvas.remove();
    return;
  }
  context.scale(ratio, ratio);

  const { innerWidth: width, innerHeight: height } = window;
  // One burst, from where it was earned: up and out of the button.
  const from = origin ?? { x: width / 2, y: height / 2 };
  const pieces: Piece[] = Array.from({ length: 90 }, () => {
    const direction = -Math.PI / 2 + (Math.random() - 0.5) * 2.2;
    const speed = 13 * (0.55 + Math.random() * 0.6);
    return {
      x: from.x,
      y: from.y,
      vx: Math.cos(direction) * speed,
      vy: Math.sin(direction) * speed,
      spin: (Math.random() - 0.5) * 0.4,
      angle: Math.random() * Math.PI,
      size: 6 + Math.random() * 6,
      color: COLORS[Math.floor(Math.random() * COLORS.length)],
      round: Math.random() < 0.3,
    };
  });

  const start = performance.now();
  const frame = (now: number) => {
    const elapsed = now - start;
    context.clearRect(0, 0, width, height);
    const fade = Math.max(0, 1 - Math.max(0, elapsed - DURATION_MS * 0.6) / (DURATION_MS * 0.4));
    for (const piece of pieces) {
      piece.vy += 0.38;
      piece.vx *= 0.985;
      piece.vy *= 0.985;
      piece.x += piece.vx;
      piece.y += piece.vy;
      piece.angle += piece.spin;
      context.save();
      context.globalAlpha = fade;
      context.translate(piece.x, piece.y);
      context.rotate(piece.angle);
      context.fillStyle = piece.color;
      if (piece.round) {
        context.beginPath();
        context.arc(0, 0, piece.size / 2.5, 0, Math.PI * 2);
        context.fill();
      } else {
        // Flat ribbons that seem to flip as they turn.
        context.fillRect(-piece.size / 2, -piece.size / 4, piece.size, (piece.size / 2) * Math.abs(Math.cos(piece.angle * 2)) + 1);
      }
      context.restore();
    }
    if (elapsed < DURATION_MS) requestAnimationFrame(frame);
    else canvas.remove();
  };
  requestAnimationFrame(frame);
}

"use client";

// The clip studio's "Edits" tab (dynamic edits) and the "Look" controls of the Effects tab.
//
// Settings changes go through the server's resolver (`POST .../editor/fx/resolve`, pure and fast) and land as ONE
// undoable change, compare-and-swap like AI edits: a result that arrives after the draft changed is resolved again on
// the latest draft instead of overwriting it. A draft that was never planned answers 409 NEEDS_PLAN; the panel then
// offers "Plan edits" (`POST .../fx/plan`, polled), copies the beats into the draft and resolves once more.
// Per-cue tweaks, music levels, grade and grain are plain local edits (the server only validates them on save).
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { Loader2, Play, RefreshCw, ScanFace, Sparkles, Trash2, X } from "lucide-react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { ApiRequestError } from "@/lib/api-error";
import {
  DEFAULT_DOC_FX,
  editDuration,
  inertDocFx,
  mappedFx,
  mappedFxBeats,
  timecode,
  type DocFx,
  type EditDocument,
  type EditorState,
} from "@/lib/editor/document";
import {
  DEFAULT_EDIT_STYLE,
  INTENSITIES,
  isFxTemplate,
  type AccentVariant,
  type Beat,
  type BeatKind,
  type EditBeats,
  type EditStyle,
  type FxCue,
  type GradeId,
  type Intensity,
  type SoundId,
  type TransitionStyle,
} from "@/lib/fx/core/types";
import { CENSORS, FX_NEEDS_FACE, GRADES, MOODS, type EditStylesInfo } from "@/lib/fx/edit-style-request";
import { cn } from "@/lib/utils";
import { Field, Range, Section } from "./studio-ui";
import { editorRequest } from "./use-editor";

type Translate = ReturnType<typeof useTranslations>;
type Update = (change: (doc: EditDocument) => EditDocument, group?: string) => void;

export const BEAT_KINDS: readonly BeatKind[] = [
  "hook", "emphasis", "number", "punchline", "swear", "emotional_turn", "payoff", "build",
];
export const SOUND_IDS: readonly SoundId[] = [
  "whoosh", "impact", "pop", "riser", "record_scratch", "boom", "cash_register", "ticker", "slap", "stamp", "glitch",
  "tape_stop",
];
const TRANSITION_STYLES: readonly TransitionStyle[] = ["whip", "flash", "glitch", "zoom_through", "spin", "light_leak"];
/** Accent looks a user may switch an accent to: text treatments every caption style can draw. */
const ACCENT_VARIANTS: readonly AccentVariant[] = [
  "circle", "underline", "strike", "arrow", "highlight", "slam", "decode", "kinetic",
];
/** == editor_document.FX_MAX_HOLD_SECONDS. */
const MAX_HOLD = 2.5;
const PLAN_POLL_MS = 1500;
const PLAN_GIVE_UP_MS = 240_000;
const ANALYSIS_POLL_MS = 4000;
const ANALYSIS_GIVE_UP_MS = 600_000;

/** Mapped ids of repeated pieces carry `@k`; the draft stores the id without it. */
export const storedId = (id: string) => id.replace(/@\d+$/, "");

/** The draft with `fx` changed, creating the container when it has none. */
export function withFx(doc: EditDocument, change: (fx: DocFx) => DocFx): EditDocument {
  return { ...doc, fx: change(doc.fx ?? DEFAULT_DOC_FX) };
}

/** The style a draft stores (off when it has none). */
export const storedStyle = (doc: EditDocument): EditStyle => doc.fx?.style ?? DEFAULT_EDIT_STYLE;

/**
 * What the user chose by hand survives a re-resolve: a picked grade, a grain amount, and music levels at the same
 * intensity (the resolver resets all three to its own defaults).
 */
export function keepLook(before: EditDocument, after: EditDocument): EditDocument {
  const was = before.fx, now = after.fx;
  if (!was || !now) return after;
  const fx: DocFx = { ...now };
  if (was.style.grade !== "auto" && now.style.grade === was.style.grade) fx.grade = was.style.grade;
  if (was.style.grain && now.style.grain && was.grain > 0) fx.grain = was.grain;
  if (was.music && now.music && was.style.intensity === now.style.intensity && was.music.mood === now.music.mood)
    fx.music = { ...now.music, gain_db: was.music.gain_db, duck_db: was.music.duck_db };
  return { ...after, fx };
}

/** "Remove all edits" switches every kind off; choosing an intensity or an animated caption style turns them back on. */
function enabledFor(doc: EditDocument, style: EditStyle): EditDocument {
  if (!doc.fx?.disabled.length) return doc;
  if (style.intensity === "off" && !isFxTemplate(doc.captions.template)) return doc;
  return { ...doc, fx: { ...doc.fx, disabled: [] } };
}

const same = (a: EditDocument, b: EditDocument) => JSON.stringify(a) === JSON.stringify(b);

export function fxErrorMessage(t: Translate, error: unknown): string {
  const code = error instanceof ApiRequestError ? error.code : null;
  switch (code) {
    case "DYNAMIC_EDITS_NOT_AVAILABLE": return t("fx.errors.notAvailable");
    case "EDIT_PLAN_LIMIT_REACHED": return t("fx.errors.planLimit");
    case "PLAN_FAILED": return t("fx.errors.planFailed");
    case "WORKER_UNAVAILABLE": return t("fx.errors.workerUnavailable");
    case "INVALID_EDIT_STYLE": return t("fx.errors.invalidStyle");
    default: return error instanceof Error && error.message ? error.message : t("fx.errors.resolveFailed");
  }
}

interface PlanStatus {
  status: "queued" | "running" | "done" | "failed";
  beats?: EditBeats;
  error?: { code?: string; message?: string };
}

export interface FxEditor {
  /** The style the controls show: the one being applied or waiting for a plan, else the stored one. */
  style: EditStyle;
  busy: "resolve" | "plan" | null;
  /** Set when the chosen style needs beats first; "Plan edits" applies it. */
  needsPlan: EditStyle | null;
  error: string | null;
  /** Re-resolve with `style` (after `change`, e.g. a beat edit or a caption template) as one undoable change. */
  apply: (style: EditStyle, change?: (doc: EditDocument) => EditDocument, options?: { keepChangeOnNeedsPlan?: boolean }) => Promise<void>;
  /** Change a setting: re-resolves, or only updates the pending style while the clip waits for a plan. */
  setStyle: (style: EditStyle) => void;
  /** Tag beats with the AI (fresh), then resolve `style`. */
  plan: (style?: EditStyle) => Promise<void>;
  cancel: () => void;
  dismiss: () => void;
  /** Face analysis for this clip. It lives here, not in the Edits tab, so leaving the tab doesn't drop the result. */
  analysis: AnalysisState;
  /** Queue the analysis for this render base and wait for it; `onReady` runs once it is done. */
  analyze: (base: string, onReady?: () => void) => Promise<void>;
}

type AnalysisState = "idle" | "running" | "done" | "failed";

/** State and requests behind the Edits tab, shared with the caption template picker (template changes resolve too). */
export function useFxEditor({ endpoint, doc, update }: {
  endpoint: string;
  doc: EditDocument;
  update: Update;
}): FxEditor {
  const t = useTranslations("editor");
  const docRef = useRef(doc);
  docRef.current = doc;
  const [pending, setPending] = useState<EditStyle | null>(null);
  const [needsPlan, setNeedsPlan] = useState<EditStyle | null>(null);
  const [busy, setBusy] = useState<FxEditor["busy"]>(null);
  const [error, setError] = useState<string | null>(null);
  const active = useRef<AbortController | null>(null);
  const [analysis, setAnalysis] = useState<AnalysisState>("idle");
  const analysisControl = useRef<AbortController | null>(null);

  // Another clip is another draft: drop whatever was in flight for the previous one.
  useEffect(() => {
    setPending(null);
    setNeedsPlan(null);
    setBusy(null);
    setError(null);
    setAnalysis("idle");
    return () => {
      active.current?.abort();
      analysisControl.current?.abort();
    };
  }, [endpoint]);

  const analyze = useCallback<FxEditor["analyze"]>(async (base, onReady) => {
    analysisControl.current?.abort();
    const control = new AbortController();
    analysisControl.current = control;
    setAnalysis("running");
    const started = Date.now();
    try {
      const { status } = await editorRequest<{ status: "queued" | "ready" }>(`${endpoint}/fx/analysis`, "POST", {}, control.signal);
      if (status !== "ready") {
        for (;;) {
          await new Promise((resolve) => setTimeout(resolve, ANALYSIS_POLL_MS));
          control.signal.throwIfAborted();
          if (Date.now() - started > ANALYSIS_GIVE_UP_MS) throw new Error("timeout");
          const latest = await editorRequest<{ fxAnalysis?: { status?: string; base?: string } }>(endpoint, "GET", undefined, control.signal);
          const job = latest.fxAnalysis;
          if (job?.base === base && job.status === "failed") throw new Error("failed");
          if (job?.base === base && job.status === "done") break;
        }
      }
      if (control.signal.aborted) return;
      setAnalysis("done");
      onReady?.();
    } catch {
      if (!control.signal.aborted) setAnalysis("failed");
    }
  }, [endpoint]);

  const begin = () => {
    active.current?.abort();
    const controller = new AbortController();
    active.current = controller;
    return controller;
  };
  const finish = (controller: AbortController) => {
    if (active.current !== controller) return;
    active.current = null;
    setBusy(null);
    setPending(null);
  };

  const resolveInto = useCallback(async (
    controller: AbortController,
    style: EditStyle,
    change: ((doc: EditDocument) => EditDocument) | undefined,
    keepChangeOnNeedsPlan: boolean,
  ) => {
    // Two tries: when the draft changed while the server worked, resolve again on the latest draft.
    for (let attempt = 0; attempt < 2; attempt++) {
      const base = docRef.current;
      const sent = enabledFor(change ? change(base) : base, style);
      let result: EditDocument;
      try {
        const response = await editorRequest<{ document: EditDocument }>(
          `${endpoint}/fx/resolve`, "POST", { document: sent, style }, controller.signal,
        );
        result = keepLook(sent, response.document);
      } catch (e) {
        if (controller.signal.aborted) return;
        if (e instanceof ApiRequestError && e.code === "NEEDS_PLAN") {
          setNeedsPlan(style);
          if (change && keepChangeOnNeedsPlan) update((current) => change(current));
          return;
        }
        setError(fxErrorMessage(t, e));
        return;
      }
      if (controller.signal.aborted) return;
      let accepted = false;
      update((current) => {
        if (!same(current, base)) return current;
        accepted = true;
        return result;
      });
      if (accepted || same(docRef.current, result)) {
        setNeedsPlan(null);
        return;
      }
    }
    setError(t("fx.errors.changedMeanwhile"));
  }, [endpoint, t, update]);

  const apply = useCallback<FxEditor["apply"]>(async (style, change, options) => {
    const controller = begin();
    setBusy("resolve");
    setPending(style);
    setError(null);
    try {
      await resolveInto(controller, style, change, !!options?.keepChangeOnNeedsPlan);
    } finally {
      finish(controller);
    }
  }, [resolveInto]);

  const plan = useCallback<FxEditor["plan"]>(async (chosen) => {
    const style = chosen ?? needsPlan ?? storedStyle(docRef.current);
    const controller = begin();
    setBusy("plan");
    setPending(style);
    setError(null);
    const started = Date.now();
    try {
      const { job_id } = await editorRequest<{ job_id: string }>(
        `${endpoint}/fx/plan`, "POST", { document: docRef.current }, controller.signal,
      );
      let beats: EditBeats | undefined;
      for (;;) {
        await new Promise((resolve) => setTimeout(resolve, PLAN_POLL_MS));
        controller.signal.throwIfAborted();
        if (Date.now() - started > PLAN_GIVE_UP_MS) {
          setError(t("fx.errors.timeout"));
          return;
        }
        const status = await editorRequest<PlanStatus>(`${endpoint}/fx/plan/${job_id}`, "GET", undefined, controller.signal);
        if (status.status === "failed") {
          setError(fxErrorMessage(t, new ApiRequestError({
            message: status.error?.message || t("fx.errors.planFailed"), traceId: null, code: status.error?.code ?? "PLAN_FAILED",
          })));
          return;
        }
        if (status.status === "done") {
          beats = status.beats;
          break;
        }
      }
      if (!beats) {
        setError(t("fx.errors.planFailed"));
        return;
      }
      const planned = beats;
      setBusy("resolve");
      await resolveInto(controller, style, (d) => withFx(d, (fx) => ({
        ...fx,
        beats: planned.beats,
        mood: planned.mood,
        sensitivity: planned.sensitivity,
        timing: planned.timing,
      })), false);
    } catch (e) {
      if (!controller.signal.aborted) setError(fxErrorMessage(t, e));
    } finally {
      finish(controller);
    }
  }, [endpoint, needsPlan, resolveInto, t]);

  const setStyle = useCallback((style: EditStyle) => {
    if (needsPlan && style.intensity !== "off") {
      setNeedsPlan(style);
      return;
    }
    setNeedsPlan(null);
    void apply(style);
  }, [apply, needsPlan]);

  const cancel = useCallback(() => {
    active.current?.abort();
    active.current = null;
    setBusy(null);
    setPending(null);
  }, []);

  return {
    style: pending ?? needsPlan ?? storedStyle(doc),
    busy,
    needsPlan,
    error,
    apply,
    setStyle,
    plan,
    cancel,
    dismiss: () => setError(null),
    analysis,
    analyze,
  };
}

// --------------------------------------------------------------------------------------------------- small parts

function OptionSwitch({ id, label, hint, checked, onChange, disabled, children }: {
  id: string;
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  children?: ReactNode;
}) {
  return (
    <div className="py-2.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 space-y-0.5">
          <Label htmlFor={id} className="text-sm font-normal">{label}</Label>
          {hint && <p className="text-xs leading-snug text-muted-foreground">{hint}</p>}
        </div>
        <Switch id={id} checked={checked} onCheckedChange={onChange} disabled={disabled} className="mt-0.5" />
      </div>
      {children}
    </div>
  );
}

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section aria-label={title}>
      <h4 className="text-xs font-medium text-muted-foreground">{title}</h4>
      <div className="divide-y">{children}</div>
    </section>
  );
}

function SmallSelect<T extends string>({ label, value, options, onChange, disabled, className }: {
  label: string;
  value: T;
  options: readonly { value: T; label: string }[];
  onChange: (value: T) => void;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <Select value={value} onValueChange={(next) => onChange(next as T)} disabled={disabled}>
      <SelectTrigger size="sm" aria-label={label} className={cn("w-full", className)}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {options.map((option) => (
          <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

// ----------------------------------------------------------------------------------------------- selected cue

/** Most freeze hold the draft may add (== editor_document.fx_hold_budget). */
export const holdBudget = (doc: EditDocument) => Math.min(1.6, 0.04 * Math.max(0, editDuration(doc)), MAX_HOLD);

/** Remove a cue: an AI cue is also remembered in `suppressed`, so re-planning never brings it back. */
export function deleteCue(doc: EditDocument, id: string): EditDocument {
  const cue = doc.fx?.cues.find((c) => c.id === id);
  if (!doc.fx || !cue) return doc;
  // An AI cue the user edited keeps its AI id: it must be suppressed too, or the next resolve brings it back.
  const auto = cue.origin === "auto" || !/^u\d+$/.test(cue.id);
  return withFx(doc, (fx) => ({
    ...fx,
    cues: fx.cues.filter((c) => c.id !== id),
    suppressed: auto && !fx.suppressed.includes(id) ? [...fx.suppressed, id] : fx.suppressed,
  }));
}

function CueEditor({ doc, cue, update, seek, onClose }: {
  doc: EditDocument;
  cue: FxCue;
  update: Update;
  seek: (time: number) => void;
  onClose: () => void;
}) {
  const t = useTranslations("editor");
  // Editing an AI cue makes it the user's: re-resolves keep user cues exactly as stored.
  const edit = (patch: Partial<FxCue>, field: string) =>
    update((d) => withFx(d, (fx) => ({
      ...fx,
      cues: fx.cues.map((c) => (c.id === cue.id ? ({ ...c, ...patch, origin: "user" } as FxCue) : c)),
    })), `fx-cue:${cue.id}:${field}`);
  const otherHolds = mappedFx(doc)
    .filter((c) => c.kind === "freeze" && storedId(c.id) !== cue.id)
    .reduce((total, c) => total + (c.kind === "freeze" ? c.hold : 0), 0);
  const holdMax = Math.max(0.2, Math.min(MAX_HOLD, holdBudget(doc) - otherHolds));

  let fields: ReactNode = null;
  switch (cue.kind) {
    case "accent":
      fields = (
        <>
          <Field label={t("fx.cue.variant")}>
            <SmallSelect
              label={t("fx.cue.variant")}
              value={cue.variant}
              options={[...new Set([cue.variant, ...ACCENT_VARIANTS])].map((v) => ({ value: v, label: t(`fx.variants.${v}`) }))}
              onChange={(variant) => edit({ variant }, "variant")}
            />
          </Field>
          <Field label={t("fx.cue.text")}>
            <Input className="h-8" maxLength={120} aria-label={t("fx.cue.text")} value={cue.text} onChange={(e) => edit({ text: e.target.value }, "text")} />
          </Field>
        </>
      );
      break;
    case "zoom":
      fields = (
        <>
          <Field label={t("fx.cue.mode")}>
            <ToggleGroup type="single" variant="outline" size="sm" className="w-full" aria-label={t("fx.cue.mode")} value={cue.mode}
              onValueChange={(mode) => mode && edit({ mode: mode as typeof cue.mode }, "mode")}>
              {(["punch", "push", "pull"] as const).map((mode) => (
                <ToggleGroupItem key={mode} value={mode} className="flex-1 text-xs">{t(`fx.cue.modes.${mode}`)}</ToggleGroupItem>
              ))}
            </ToggleGroup>
          </Field>
          <Range label={t("fx.cue.scale")} value={cue.scale} min={1} max={1.6} step={0.01} unit="×" onChange={(scale) => edit({ scale }, "scale")} />
          <Field label={t("fx.cue.anchor")}>
            <SmallSelect label={t("fx.cue.anchor")} value={cue.anchor}
              options={(["face", "center"] as const).map((v) => ({ value: v, label: t(`fx.cue.anchors.${v}`) }))}
              onChange={(anchor) => edit({ anchor }, "anchor")} />
          </Field>
        </>
      );
      break;
    case "shake":
      fields = <Range label={t("fx.cue.amount")} value={cue.amp} min={0} max={40} step={1} onChange={(amp) => edit({ amp }, "amp")} />;
      break;
    case "flash":
      fields = <Range label={t("fx.cue.amount")} value={Math.round(cue.peak * 100)} min={0} max={100} unit="%" onChange={(v) => edit({ peak: v / 100 }, "peak")} />;
      break;
    case "glitch":
    case "desaturate":
      fields = <Range label={t("fx.cue.amount")} value={Math.round(cue.amount * 100)} min={0} max={100} unit="%" onChange={(v) => edit({ amount: v / 100 }, "amount")} />;
      break;
    case "transition":
      fields = (
        <Field label={t("fx.cue.style")}>
          <SmallSelect label={t("fx.cue.style")} value={cue.style}
            options={TRANSITION_STYLES.map((v) => ({ value: v, label: t(`fx.transitions.${v}`) }))}
            onChange={(style) => edit({ style }, "style")} />
        </Field>
      );
      break;
    case "sfx":
      fields = (
        <>
          <Field label={t("fx.cue.sound")}>
            <SmallSelect label={t("fx.cue.sound")} value={cue.sound}
              options={SOUND_IDS.map((v) => ({ value: v, label: t(`fx.sounds.${v}`) }))}
              onChange={(sound) => edit({ sound, variant: 0 }, "sound")} />
          </Field>
          <Range label={t("fx.cue.gain")} value={cue.gain_db} min={-30} max={6} step={1} unit=" dB" onChange={(gain_db) => edit({ gain_db }, "gain")} />
        </>
      );
      break;
    case "bleep":
      fields = (
        <>
          <OptionSwitch id={`cue-tone-${cue.id}`} label={t("fx.cue.tone")} checked={cue.tone} onChange={(tone) => edit({ tone }, "tone")} />
          <Field label={t("fx.cue.cover")}>
            <SmallSelect label={t("fx.cue.cover")} value={cue.cover}
              options={(["none", "bar", "pixelate"] as const).map((v) => ({ value: v, label: t(`fx.cue.covers.${v}`) }))}
              onChange={(cover) => edit({ cover }, "cover")} />
          </Field>
        </>
      );
      break;
    case "speed":
      fields = <Range label={t("fx.cue.rate")} value={cue.rate} min={0.25} max={4} step={0.05} unit="×" onChange={(rate) => edit({ rate }, "rate")} />;
      break;
    case "freeze":
      fields = (
        <>
          {holdMax > 0.2 + 1e-6 && (
            <Range label={t("fx.cue.hold")} value={Math.min(cue.hold, holdMax)} min={0.2} max={holdMax} step={0.05} unit="s"
              onChange={(hold) => edit({ hold: Math.min(hold, holdMax) }, "hold")} />
          )}
          <p className="text-xs text-muted-foreground">{t("fx.cue.holdBudget", { seconds: holdBudget(doc).toFixed(1) })}</p>
          <Field label={t("fx.cue.label")}>
            <Input className="h-8" maxLength={40} aria-label={t("fx.cue.label")} value={cue.label} onChange={(e) => edit({ label: e.target.value }, "label")} />
          </Field>
        </>
      );
      break;
    case "sticker":
      fields = cue.sticker === "text" ? (
        <Field label={t("fx.cue.text")}>
          <Input className="h-8" maxLength={40} aria-label={t("fx.cue.text")} value={cue.text} onChange={(e) => edit({ text: e.target.value }, "text")} />
        </Field>
      ) : <p className="text-xs text-muted-foreground">{t("fx.cue.waitForIt")}</p>;
      break;
    case "reaction":
      fields = <p className="text-xs text-muted-foreground">{t("fx.cue.query", { query: cue.query })}</p>;
      break;
  }

  return (
    <div className="space-y-3 rounded-xl border bg-muted/30 p-3" role="group" aria-label={t("fx.cue.title")}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="text-sm font-semibold">{t(`fx.kinds.${cue.kind}`)}</p>
          <p className="text-xs text-muted-foreground tabular-nums">
            {t("fx.cue.at", { time: timecode(cue.start) })} · {cue.origin === "user" ? t("fx.cue.user") : t("fx.cue.auto")}
          </p>
        </div>
        <div className="flex shrink-0 items-center">
          <Button size="icon-sm" variant="ghost" className="size-7" aria-label={t("fx.cue.jump")} title={t("fx.cue.jump")} onClick={() => seek(cue.start)}>
            <Play className="size-3.5" />
          </Button>
          <Button size="icon-sm" variant="ghost" className="size-7 hover:text-destructive" aria-label={t("fx.cue.delete")} title={t("fx.cue.delete")}
            onClick={() => { update((d) => deleteCue(d, cue.id)); onClose(); }}>
            <Trash2 className="size-3.5" />
          </Button>
          <Button size="icon-sm" variant="ghost" className="size-7" aria-label={t("fx.cue.close")} title={t("fx.cue.close")} onClick={onClose}>
            <X className="size-3.5" />
          </Button>
        </div>
      </div>
      {fields}
      {cue.origin === "auto" && <p className="text-xs leading-snug text-muted-foreground">{t("fx.cue.editHint")}</p>}
    </div>
  );
}

// ------------------------------------------------------------------------------------------------ face analysis

/** True when something in the draft follows the speaker's face (zooms, doodles, mouth covers, behind text). */
function needsFace(doc: EditDocument, cues: readonly FxCue[]): boolean {
  if (isFxTemplate(doc.captions.template) && FX_NEEDS_FACE.has(doc.captions.template)) return true;
  return cues.some((c) => (c.kind === "zoom" && c.anchor === "face")
    || (c.kind === "accent" && (c.variant === "doodle" || c.variant === "behind"))
    || (c.kind === "bleep" && c.cover !== "none"));
}

const renderBase = (doc: EditDocument, asset: EditorState) =>
  asset.hasLayout && doc.framing.fit === "auto" && doc.framing.aspect === "vertical" ? "layout" : "clean";

function FaceAnalysis({ doc, asset, editor, onReady }: {
  doc: EditDocument;
  asset: EditorState;
  editor: FxEditor;
  onReady?: () => void;
}) {
  const t = useTranslations("editor");
  const state = editor.analysis;
  const base = renderBase(doc, asset);
  const cues = mappedFx(doc);
  // Text behind the speaker also needs the person matte, made by the same analysis.
  const missing = (needsFace(doc, cues) && asset.faceTrack?.base !== base)
    || (cues.some((c) => c.kind === "accent" && c.variant === "behind") && !asset.matteVersion);
  if (!missing && state !== "done") return null;

  const run = () => editor.analyze(base, onReady);

  return (
    <Section title={t("fx.face.title")} description={t("fx.face.body")}>
      {state === "done" ? (
        <p className="text-xs text-muted-foreground" role="status">{onReady ? t("fx.face.ready") : t("fx.face.readyReopen")}</p>
      ) : (
        <div className="space-y-2">
          <Button size="sm" variant="outline" className="w-full" disabled={state === "running"} onClick={() => void run()}>
            {state === "running" ? <Loader2 className="animate-spin" /> : <ScanFace />}
            {state === "running" ? t("fx.face.running") : t("fx.face.action")}
          </Button>
          {state === "failed" && <p className="text-xs text-destructive" role="alert">{t("fx.face.failed")}</p>}
        </div>
      )}
    </Section>
  );
}

// ---------------------------------------------------------------------------------------------------- the panel

interface PanelProps {
  doc: EditDocument;
  asset: EditorState;
  update: Update;
  /** Seek on the source (clean.mp4) timeline. */
  seek: (time: number) => void;
  editor: FxEditor;
  info: EditStylesInfo | null;
  /** Stored id of the cue selected on the timeline's FX lane. */
  cueId?: string | null;
  selectCue?: (id: string | null) => void;
  /** Called once a face analysis finished, so the workspace can reload the session's face track. */
  onAnalysisReady?: () => void;
}

/** The Edits tab: intensity, what the edits may use, the beats they land on, the selected edit, and removal. */
export function FxPanel({ doc, asset, update, seek, editor, info, cueId, selectCue, onAnalysisReady }: PanelProps) {
  const t = useTranslations("editor");
  const { style, busy } = editor;
  const fx = doc.fx;
  const intensities = INTENSITIES.filter((level) => !info?.enabled || info.intensities.includes(level));
  const active = style.intensity !== "off";
  const cues = useMemo(() => mappedFx(doc), [doc]);
  const beats = useMemo(() => mappedFxBeats(doc), [doc]);
  const selected = cueId ? fx?.cues.find((c) => c.id === cueId) ?? null : null;
  const set = <K extends keyof EditStyle>(key: K, value: EditStyle[K]) => editor.setStyle({ ...style, [key]: value });
  const moods = info?.enabled ? info.music_moods : MOODS;

  const toggle = (key: "sfx" | "zooms" | "shake" | "transitions" | "speed" | "wait_for_it" | "progress_bar" | "loop_ending" | "reactions") => (
    <OptionSwitch
      key={key}
      id={`fx-${key}`}
      label={t(`fx.options.${key}.label`)}
      hint={t(`fx.options.${key}.hint`)}
      checked={style[key]}
      onChange={(checked) => set(key, checked)}
      disabled={busy === "plan"}
    />
  );

  const editBeat = (change: (beats: Beat[]) => Beat[]) =>
    void editor.apply(storedStyle(doc), (d) => withFx(d, (current) => ({ ...current, beats: change(current.beats) })));

  return (
    <div className="space-y-6">
      <Section
        title={t("fx.title")}
        description={t("fx.description")}
        action={cues.length > 0 ? <Badge variant="secondary" className="tabular-nums">{t("fx.count", { count: cues.length })}</Badge> : undefined}
      >
        <Field label={t("fx.intensity.label")}>
          <ToggleGroup
            type="single"
            variant="outline"
            aria-label={t("fx.intensity.label")}
            className="w-full"
            value={style.intensity}
            disabled={busy === "plan"}
            onValueChange={(value) => value && value !== style.intensity && editor.setStyle({ ...style, intensity: value as Intensity })}
          >
            {intensities.map((level) => (
              <ToggleGroupItem key={level} value={level} className="flex-1 text-xs">{t(`fx.intensity.${level}`)}</ToggleGroupItem>
            ))}
          </ToggleGroup>
        </Field>
        <p className="flex min-h-4 items-center gap-1.5 text-xs text-muted-foreground" aria-live="polite">
          {busy === "resolve" ? <><Loader2 className="size-3 animate-spin" />{t("fx.applying")}</> : t(`fx.intensityHint.${style.intensity}`)}
        </p>

        {editor.needsPlan && busy !== "plan" && (
          <Alert>
            <Sparkles />
            <AlertTitle>{t("fx.needsPlan.title")}</AlertTitle>
            <AlertDescription>
              <p>{t("fx.needsPlan.body")}</p>
              <Button size="sm" className="mt-2 w-full" onClick={() => void editor.plan()}>
                <Sparkles />
                {t("fx.needsPlan.action")}
              </Button>
            </AlertDescription>
          </Alert>
        )}
        {busy === "plan" && (
          <div className="flex items-center justify-between gap-2 rounded-lg border p-3 text-sm" role="status">
            <span className="flex items-center gap-2"><Loader2 className="size-4 animate-spin" />{t("fx.planning")}</span>
            <Button size="sm" variant="ghost" className="h-7 px-2 text-xs" onClick={editor.cancel}>{t("fx.cancel")}</Button>
          </div>
        )}
        {editor.error && (
          <div className="flex items-start justify-between gap-2 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive" role="alert">
            <span>{editor.error}</span>
            <Button size="icon-sm" variant="ghost" className="size-6 shrink-0" aria-label={t("fx.dismiss")} onClick={editor.dismiss}><X className="size-3" /></Button>
          </div>
        )}
      </Section>

      {selected && (
        <CueEditor doc={doc} cue={selected} update={update} seek={seek} onClose={() => selectCue?.(null)} />
      )}

      {active && (
        <div className="space-y-4">
          <Group title={t("fx.groups.sound")}>
            {toggle("sfx")}
            <div className="space-y-2 py-2.5">
              <div className="space-y-0.5">
                <p className="text-sm">{t("fx.options.censor.label")}</p>
                <p className="text-xs leading-snug text-muted-foreground">{t("fx.options.censor.hint")}</p>
              </div>
              <SmallSelect label={t("fx.options.censor.label")} value={style.censor} disabled={busy === "plan"}
                options={CENSORS.map((v) => ({ value: v, label: t(`fx.censor.${v}`) }))}
                onChange={(censor) => set("censor", censor)} />
            </div>
          </Group>
          <Group title={t("fx.groups.music")}>
            <OptionSwitch id="fx-music" label={t("fx.options.music.label")} hint={t("fx.options.music.hint")} checked={style.music}
              onChange={(music) => set("music", music)} disabled={busy === "plan"}>
              {style.music && (
                <div className="mt-3 space-y-3">
                  <Field label={t("fx.musicMood")}>
                    <SmallSelect label={t("fx.musicMood")} value={style.music_mood} disabled={busy === "plan"}
                      options={(["auto", ...moods] as const).map((v) => ({ value: v, label: t(`fx.moods.${v}`) }))}
                      onChange={(mood) => set("music_mood", mood)} />
                  </Field>
                  {fx?.music ? (
                    <>
                      <Range label={t("fx.musicVolume")} value={fx.music.gain_db} min={-40} max={0} step={1} unit=" dB"
                        onChange={(gain_db) => update((d) => withFx(d, (f) => (f.music ? { ...f, music: { ...f.music, gain_db } } : f)), "fx-music-gain")} />
                      <Range label={t("fx.duckDepth")} value={-fx.music.duck_db} min={0} max={30} step={1} unit=" dB"
                        onChange={(depth) => update((d) => withFx(d, (f) => (f.music ? { ...f, music: { ...f.music, duck_db: -depth } } : f)), "fx-music-duck")} />
                    </>
                  ) : busy === null && !editor.needsPlan && (
                    <p className="text-xs text-muted-foreground">{t("fx.noMusicBed")}</p>
                  )}
                </div>
              )}
            </OptionSwitch>
          </Group>
          <Group title={t("fx.groups.motion")}>
            {(["zooms", "transitions"] as const).map(toggle)}
          </Group>
          <Group title={t("fx.groups.retention")}>
            {(["progress_bar", "loop_ending"] as const).map(toggle)}
            {(info?.reactions_available || style.reactions) && toggle("reactions")}
          </Group>
        </div>
      )}

      {beats.length > 0 && (
        <Section
          title={t("fx.beats.title")}
          description={t("fx.beats.description")}
          action={
            <Button size="sm" variant="outline" className="h-7 px-2 text-xs" disabled={busy !== null} onClick={() => void editor.plan(storedStyle(doc))}>
              <RefreshCw />
              {t("fx.replan")}
            </Button>
          }
        >
          <ol className="max-h-64 space-y-0.5 overflow-y-auto rounded-lg border p-1" aria-label={t("fx.beats.title")}>
            {beats.map((beat) => {
              const id = storedId(beat.id);
              const stored = fx?.beats.find((b) => b.id === id);
              if (!stored) return null;
              const time = timecode(beat.start);
              return (
                <li key={beat.id} className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-1.5 rounded-md px-1 py-1 hover:bg-accent/60">
                  <Button size="sm" variant="ghost" className="h-7 px-1.5 font-mono text-[11px] tabular-nums text-muted-foreground"
                    aria-label={t("fx.beats.jump", { time })} onClick={() => seek(stored.start)}>
                    {time}
                  </Button>
                  <div className="min-w-0">
                    <p className="truncate text-xs" title={stored.text}>{stored.text || "—"}</p>
                    <SmallSelect label={t("fx.beats.kind", { time })} value={stored.kind} className="mt-1 w-auto px-2 text-[11px] data-[size=sm]:h-6"
                      disabled={busy !== null}
                      options={[...new Set([stored.kind, ...BEAT_KINDS])].map((v) => ({ value: v, label: t(`fx.beats.kinds.${v}`) }))}
                      onChange={(kind) => editBeat((all) => all.map((b) => (b.id === id ? { ...b, kind, source: "user" } : b)))} />
                  </div>
                  <Button size="icon-sm" variant="ghost" className="size-7 text-muted-foreground hover:text-destructive"
                    aria-label={t("fx.beats.delete", { time })} disabled={busy !== null}
                    onClick={() => editBeat((all) => all.filter((b) => b.id !== id))}>
                    <Trash2 className="size-3.5" />
                  </Button>
                </li>
              );
            })}
          </ol>
        </Section>
      )}

      <FaceAnalysis doc={doc} asset={asset} editor={editor} onReady={onAnalysisReady} />

      {fx && (fx.cues.length > 0 || fx.beats.length > 0 || fx.music || fx.grade !== "none" || fx.grain > 0 || fx.style.intensity !== "off") && (
        <div className="space-y-1.5 border-t pt-4">
          <Button size="sm" variant="ghost" className="w-full text-destructive hover:bg-destructive/10 hover:text-destructive" disabled={busy !== null}
            onClick={() => {
              update((d) => ({ ...d, fx: inertDocFx(d.fx) }));
              selectCue?.(null);
              toast.info(t("fx.remove.done"));
            }}>
            <Trash2 />
            {t("fx.remove.action")}
          </Button>
          <p className="text-center text-xs text-muted-foreground">{t("fx.remove.hint")}</p>
        </div>
      )}
    </div>
  );
}

// ------------------------------------------------------------------------------------------- Effects tab: look

/** Colour grade and film grain, next to brightness and contrast in the Effects tab. */
export function FxLookControls({ doc, update, editor, grades }: {
  doc: EditDocument;
  update: Update;
  editor: FxEditor;
  grades?: readonly GradeId[];
}) {
  const t = useTranslations("editor");
  const fx = doc.fx;
  const active = (fx?.style.intensity ?? "off") !== "off";
  const value = active && fx?.style.grade === "auto" ? "auto" : fx?.grade ?? "none";
  const options = [...(active ? ["auto" as const] : []), ...(grades ?? GRADES)];
  return (
    <Section title={t("fx.look.title")} description={t("fx.look.description")}>
      <Field label={t("fx.look.grade")}>
        <SmallSelect
          label={t("fx.look.grade")}
          value={value}
          disabled={editor.busy !== null}
          options={options.map((v) => ({ value: v, label: t(`fx.look.grades.${v}`) }))}
          onChange={(grade) => {
            if (grade === "auto") void editor.apply({ ...storedStyle(doc), grade: "auto" });
            else update((d) => withFx(d, (f) => ({ ...f, grade, style: { ...f.style, grade } })));
          }}
        />
      </Field>
      <Range
        label={t("fx.look.grain")}
        value={Math.round((fx?.grain ?? 0) * 100)}
        min={0}
        max={100}
        unit="%"
        onChange={(amount) => update((d) => withFx(d, (f) => ({ ...f, grain: amount / 100, style: { ...f.style, grain: amount > 0 } })), "fx-grain")}
      />
    </Section>
  );
}

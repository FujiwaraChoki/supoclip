"use client";
import { useEffect, useMemo, useState } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import type { EditDocument } from "@/lib/editor/document";
import {
  alignTranslations,
  applyTranslation,
  missingTranslationsInCut,
  parseTranslationResponse,
  splitPhrases,
  splitSentences,
} from "@/lib/editor/translation";
import { editorMessage } from "./studio-ui";

const TARGET_LANGUAGE = "ru";
/** The client gives up a little after the server's own budget is spent. */
const CLIENT_TIMEOUT_MS = 200_000;

interface Props {
  doc: EditDocument;
  endpoint: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  update: (change: (doc: EditDocument) => EditDocument, group?: string) => void;
}

function formatTime(seconds: number) {
  return `${seconds.toFixed(2)}s`;
}

/**
 * The translation of the spoken phrases.
 *
 * The English transcript stays the basis of the synchronization: the panel only
 * holds the translation of every phrase, and the editor places the translated
 * words inside the boundaries of the phrase they belong to.
 */
export function TranslationPanel({
  doc,
  endpoint,
  open,
  onOpenChange,
  update,
}: Props) {
  const t = useTranslations("editor");
  const sourceWords = useMemo(
    () => doc.translation?.sourceWords ?? doc.words,
    [doc.translation, doc.words],
  );
  const phrases = useMemo(() => splitPhrases(sourceWords), [sourceWords]);
  const [mode, setMode] = useState<"auto" | "manual">("auto");
  const [rows, setRows] = useState<string[]>([]);
  const [manual, setManual] = useState("");
  const [needsReview, setNeedsReview] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    const existing = doc.translation?.phrases;
    setRows(
      phrases.map(
        (phrase, index) => existing?.[index]?.targetText ?? "",
      ),
    );
    setNeedsReview(false);
    setManual("");
  }, [open, doc.translation, phrases]);

  const setRow = (index: number, text: string) =>
    setRows((current) =>
      current.map((value, position) => (position === index ? text : value)),
    );

  const translate = async () => {
    setBusy(true);
    // The server caps the model call, but a request that never comes back
    // would leave the panel spinning forever.
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), CLIENT_TIMEOUT_MS);
    try {
      const response = await fetch(`${endpoint}/translate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          phrases: phrases.map((phrase) => ({ text: phrase.text })),
          targetLanguage: TARGET_LANGUAGE,
        }),
        signal: controller.signal,
      });
      const payload = parseTranslationResponse(await response.text());
      if (!response.ok) {
        throw new Error(
          payload.code === "translation_timeout"
            ? t("captions.translationTimeout")
            : payload.message ?? t("captions.translationFailed"),
        );
      }
      if (!payload.ok) throw new Error(t("captions.translationBadResponse"));
      const alignment = alignTranslations(phrases, payload.translations);
      setRows(alignment.texts);
      setNeedsReview(payload.needsReview || alignment.needsReview);
    } catch (error) {
      const failure = error as Error;
      toast.error(
        editorMessage(
          t,
          failure.name === "AbortError"
            ? t("captions.translationTimeout")
            : failure.message,
        ),
      );
    } finally {
      clearTimeout(timer);
      setBusy(false);
    }
  };

  const matchManual = () => {
    const alignment = alignTranslations(phrases, splitSentences(manual));
    setRows(alignment.texts);
    setNeedsReview(alignment.needsReview);
  };

  // A phrase without a translation that still reaches the cut would leave a
  // hole in the subtitles, so the translation cannot be applied yet.
  const missingInCut = useMemo(
    () =>
      missingTranslationsInCut(
        phrases,
        rows,
        doc.segments.map((segment) => ({
          start: segment.start,
          end: segment.end,
        })),
      ),
    [doc.segments, phrases, rows],
  );

  const apply = () => {
    if (missingInCut.length) return;
    update(
      (d) =>
        applyTranslation(
          d,
          sourceWords,
          phrases,
          rows,
          mode === "auto" ? "auto" : "manual",
        ),
      "translation",
    );
    onOpenChange(false);
    toast.success(t("captions.translationApplied"));
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle>{t("captions.translationTitle")}</DialogTitle>
          <DialogDescription>
            {t("captions.translationDescription")}
          </DialogDescription>
        </DialogHeader>
        <Tabs
          value={mode}
          onValueChange={(value) => setMode(value as "auto" | "manual")}
        >
          <TabsList className="grid w-full grid-cols-2">
            <TabsTrigger value="auto">{t("captions.translationAuto")}</TabsTrigger>
            <TabsTrigger value="manual">
              {t("captions.translationManual")}
            </TabsTrigger>
          </TabsList>
          <TabsContent value="auto" className="space-y-3 pt-3">
            <p className="text-xs text-muted-foreground">
              {t("captions.translationAutoHint")}
            </p>
            <Button size="sm" disabled={busy} onClick={translate}>
              {busy ? t("captions.translationBusy") : t("captions.translationRun")}
            </Button>
          </TabsContent>
          <TabsContent value="manual" className="space-y-3 pt-3">
            <Textarea
              className="min-h-32 bg-background text-sm"
              aria-label={t("captions.translationManualAria")}
              placeholder={t("captions.translationManualPlaceholder")}
              value={manual}
              onChange={(event) => setManual(event.target.value)}
            />
            <Button
              size="sm"
              variant="outline"
              disabled={!manual.trim()}
              onClick={matchManual}
            >
              {t("captions.translationMatch")}
            </Button>
          </TabsContent>
        </Tabs>
        {(needsReview || missingInCut.length > 0) && (
          <Alert>
            <AlertDescription>{t("captions.translationReview")}</AlertDescription>
          </Alert>
        )}
        <div className="max-h-72 space-y-2 overflow-y-auto rounded-lg border p-2">
          {phrases.map((phrase, index) => (
            <div
              key={`${phrase.index}-${phrase.start}`}
              className="grid gap-1 rounded-md border bg-muted/30 p-2 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)] sm:items-center sm:gap-2"
            >
              <div className="min-w-0">
                <p className="truncate text-xs font-medium">{phrase.text}</p>
                <p className="text-[10px] text-muted-foreground">
                  {formatTime(phrase.start)} – {formatTime(phrase.end)}
                </p>
              </div>
              <Input
                className="h-8 text-sm"
                aria-label={t("captions.translationPhrase", {
                  n: index + 1,
                })}
                value={rows[index] ?? ""}
                onChange={(event) => setRow(index, event.target.value)}
              />
            </div>
          ))}
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t("captions.translationCancel")}
          </Button>
          <Button onClick={apply} disabled={missingInCut.length > 0}>
            {t("captions.translationApply")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

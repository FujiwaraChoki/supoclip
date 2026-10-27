"use client";
import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Clapperboard, Loader2, ListPlus, Replace, Search, Trash2, Crosshair } from "lucide-react";
import { toast } from "sonner";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  brollMinimum,
  brollOf,
  clamp,
  insertBroll,
  placeBroll,
  timecode,
  type BRollClip,
  type BRollFootage,
  type EditDocument,
  type EditorState,
} from "@/lib/editor/document";
import { cn } from "@/lib/utils";
import { editorMessage, Field, Range, Section } from "./studio-ui";
import { editorRequest } from "./use-editor";

interface Props {
  doc: EditDocument;
  asset: EditorState;
  endpoint: string;
  /** Playhead on the source timeline; new footage goes here. */
  time: number;
  seek: (time: number) => void;
  update: (change: (doc: EditDocument) => EditDocument, group?: string) => void;
  selected: string | null;
  select: (id: string | null) => void;
}

const sortByStart = (clips: BRollClip[]) => [...clips].sort((a, b) => a.start - b.start);

export function BRollPanel({ doc, asset, endpoint, time, seek, update, selected, select }: Props) {
  const t = useTranslations("editor");
  const [available, setAvailable] = useState<boolean | null>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<BRollFootage[]>([]);
  const [searched, setSearched] = useState("");
  const [page, setPage] = useState(1);
  const [searching, setSearching] = useState(false);
  const [suggesting, setSuggesting] = useState(false);
  const [replacing, setReplacing] = useState(false);
  const clips = brollOf(doc);
  const clip = clips.find((b) => b.id === selected) ?? null;

  useEffect(() => {
    let cancelled = false;
    fetch("/api/broll/status")
      .then((r) => (r.ok ? r.json() : null))
      .then((data) => !cancelled && setAvailable(data?.configured === true))
      .catch(() => !cancelled && setAvailable(false));
    return () => {
      cancelled = true;
    };
  }, []);
  useEffect(() => {
    if (!clip) setReplacing(false);
  }, [clip]);

  const search = async (next = 1, text = query) => {
    const q = text.trim();
    if (!q) return;
    setSearching(true);
    try {
      const params = new URLSearchParams({ q, page: String(next), aspect: doc.framing.aspect });
      const data = await editorRequest<{ results: BRollFootage[] }>(`${endpoint}/broll/search?${params}`);
      setResults((current) => (next === 1 ? data.results : [...current, ...data.results]));
      setSearched(q);
      setPage(next);
    } catch (e) {
      toast.error(e instanceof Error ? editorMessage(t, e.message) : t("broll.searchFailed"));
    } finally {
      setSearching(false);
    }
  };

  const pick = (footage: BRollFootage) => {
    if (replacing && clip) {
      // New footage keeps the clip's place; it may be shorter than the old one.
      update((d) => ({
        ...d,
        broll: brollOf(d).map((b) =>
          b.id === clip.id
            ? {
                ...b,
                footage,
                offset: 0,
                query: searched || b.query,
                end: Math.max(b.start + brollMinimum(b), Math.min(b.end, b.start + footage.duration)),
              }
            : b,
        ),
      }));
      setReplacing(false);
      return;
    }
    let inserted: string | null = null;
    update((d) => {
      const result = insertBroll(d, footage, time, asset.duration, searched);
      inserted = result?.id ?? null;
      return result?.doc ?? d;
    });
    if (inserted) select(inserted);
    else toast.error(t("broll.noRoom"));
  };

  const suggest = async () => {
    setSuggesting(true);
    try {
      const data = await editorRequest<{ broll: BRollClip[] }>(`${endpoint}/broll/suggest`, "POST", {
        document: doc,
      });
      let added = 0;
      update((d) => {
        const current = brollOf(d);
        const fresh = data.broll.filter((s) => !current.some((b) => s.start < b.end && b.start < s.end));
        added = fresh.length;
        return fresh.length ? { ...d, broll: sortByStart([...current, ...fresh]) } : d;
      });
      if (added) toast.success(t("broll.suggested", { count: added }));
      else toast.info(t("broll.nothingSuggested"));
    } catch (e) {
      toast.error(e instanceof Error ? editorMessage(t, e.message) : t("broll.suggestFailed"));
    } finally {
      setSuggesting(false);
    }
  };

  const edit = (change: Partial<BRollClip>, group: string) =>
    clip &&
    update(
      (d) => ({ ...d, broll: brollOf(d).map((b) => (b.id === clip.id ? { ...b, ...change } : b)) }),
      group,
    );

  if (available === false && clips.length === 0)
    return (
      <Alert>
        <Clapperboard />
        <AlertDescription>{t("broll.unavailable")}</AlertDescription>
      </Alert>
    );

  return (
    <div className="space-y-6">
      <Section
        title={t("broll.title")}
        description={t("broll.description")}
        action={
          <Button size="sm" variant="outline" onClick={() => void suggest()} disabled={!available || suggesting}>
            {suggesting ? <Loader2 className="animate-spin" /> : <ListPlus />}
            {t("broll.suggest")}
          </Button>
        }
      >
        {clips.length > 0 ? (
          <ul className="space-y-1" aria-label={t("broll.listLabel")}>
            {clips.map((b) => (
              <li key={b.id}>
                <button
                  type="button"
                  aria-pressed={b.id === selected}
                  onClick={() => {
                    select(b.id);
                    seek(b.start);
                  }}
                  className={cn(
                    "flex w-full items-center gap-2.5 rounded-md border p-1.5 text-left text-xs transition-colors hover:bg-accent",
                    b.id === selected && "border-primary ring-1 ring-primary",
                  )}
                >
                  <span
                    aria-hidden
                    className="h-9 w-6 shrink-0 rounded-sm bg-muted bg-cover bg-center"
                    style={b.footage.thumbnail ? { backgroundImage: `url(${b.footage.thumbnail})` } : undefined}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium">{b.query || t("broll.untitled")}</span>
                    <span className="block tabular-nums text-muted-foreground">
                      {timecode(b.start)} – {timecode(b.end)}
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-muted-foreground">{t("broll.empty")}</p>
        )}
      </Section>

      {clip && (
        <Section
          title={t("broll.selectedTitle")}
          action={
            <Button
              size="sm"
              variant="ghost"
              className="h-7 px-2 text-xs hover:text-destructive"
              onClick={() => {
                update((d) => ({ ...d, broll: brollOf(d).filter((b) => b.id !== clip.id) }));
                select(null);
              }}
            >
              <Trash2 />
              {t("broll.remove")}
            </Button>
          }
        >
          <div className="flex gap-3">
            <div
              aria-hidden
              className="aspect-[9/16] w-16 shrink-0 rounded-md bg-muted bg-cover bg-center"
              style={clip.footage.thumbnail ? { backgroundImage: `url(${clip.footage.thumbnail})` } : undefined}
            />
            <div className="min-w-0 space-y-1 text-xs">
              <p className="truncate font-medium">{clip.query || t("broll.untitled")}</p>
              <p className="text-muted-foreground">
                {t.rich("broll.credit", {
                  name: clip.footage.credit || t("broll.creditUnknown"),
                  link: (chunks) => (
                    <a
                      className="underline underline-offset-2 hover:text-foreground"
                      href={clip.footage.pageUrl || "https://www.pexels.com"}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {chunks}
                    </a>
                  ),
                })}
              </p>
              <p className="tabular-nums text-muted-foreground">
                {t("broll.footageLength", { duration: timecode(clip.footage.duration) })}
              </p>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            {(["start", "end"] as const).map((edge) => (
              <Field key={edge} label={t(`broll.${edge}`)}>
                <Input
                  type="number"
                  step="0.01"
                  className="h-8 tabular-nums"
                  aria-label={t(`broll.${edge}Seconds`)}
                  value={Number(clip[edge].toFixed(2))}
                  onChange={(e) => {
                    const value = Number(e.target.value);
                    if (!Number.isFinite(value)) return;
                    update(
                      (d) =>
                        placeBroll(
                          d,
                          clip.id,
                          edge === "start" ? value : clip.start,
                          edge === "end" ? value : clip.end,
                          asset.duration,
                        ),
                      "broll-time",
                    );
                  }}
                />
              </Field>
            ))}
          </div>
          <Range
            label={t("broll.footageStart")}
            value={clip.offset}
            min={0}
            max={Math.max(0, clip.footage.duration - (clip.end - clip.start))}
            step={0.1}
            unit="s"
            onChange={(offset) =>
              edit({ offset: clamp(offset, 0, Math.max(0, clip.footage.duration - (clip.end - clip.start))) }, "broll-offset")
            }
          />
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="outline" onClick={() => seek(clip.start)}>
              <Crosshair />
              {t("broll.jump")}
            </Button>
            <Button
              size="sm"
              variant={replacing ? "default" : "outline"}
              aria-pressed={replacing}
              onClick={() => {
                setReplacing((value) => !value);
                if (!query && clip.query) {
                  setQuery(clip.query);
                  void search(1, clip.query);
                }
              }}
              disabled={!available}
            >
              <Replace />
              {t("broll.replace")}
            </Button>
          </div>
        </Section>
      )}

      <Section
        title={replacing ? t("broll.replaceTitle") : t("broll.searchTitle")}
        description={replacing ? t("broll.replaceDescription") : t("broll.searchDescription", { time: timecode(time) })}
      >
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void search(1);
          }}
        >
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("broll.searchPlaceholder")}
            aria-label={t("broll.searchLabel")}
            className="h-8"
            maxLength={80}
            disabled={!available}
          />
          <Button type="submit" size="sm" disabled={!available || searching || !query.trim()} aria-label={t("broll.searchButton")}>
            {searching ? <Loader2 className="animate-spin" /> : <Search />}
          </Button>
        </form>
        {results.length > 0 && (
          <div className="grid grid-cols-3 gap-1.5" role="list" aria-label={t("broll.resultsLabel")}>
            {results.map((footage) => (
              <button
                key={`${footage.id}-${footage.width}`}
                type="button"
                role="listitem"
                onClick={() => pick(footage)}
                aria-label={t(replacing ? "broll.useResult" : "broll.addResult", {
                  credit: footage.credit || t("broll.creditUnknown"),
                })}
                className="group relative aspect-[9/16] overflow-hidden rounded-md bg-muted bg-cover bg-center ring-offset-background outline-none hover:ring-2 hover:ring-primary focus-visible:ring-2 focus-visible:ring-ring"
                style={footage.thumbnail ? { backgroundImage: `url(${footage.thumbnail})` } : undefined}
              >
                <span className="absolute right-1 bottom-1 rounded bg-black/70 px-1 text-[10px] tabular-nums text-white">
                  {Math.round(footage.duration)}s
                </span>
              </button>
            ))}
          </div>
        )}
        {results.length > 0 && results.length % 18 === 0 && (
          <Button size="sm" variant="ghost" className="w-full" disabled={searching} onClick={() => void search(page + 1, searched)}>
            {t("broll.more")}
          </Button>
        )}
        {searched && !searching && results.length === 0 && (
          <p className="text-xs text-muted-foreground">{t("broll.noResults", { query: searched })}</p>
        )}
        <p className="text-[11px] text-muted-foreground">
          {t.rich("broll.attribution", {
            link: (chunks) => (
              <a className="underline underline-offset-2" href="https://www.pexels.com" target="_blank" rel="noreferrer">
                {chunks}
              </a>
            ),
          })}
        </p>
      </Section>
    </div>
  );
}

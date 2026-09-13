"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, RotateCcw, ShieldAlert } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "@/lib/toast";
import { formatSupportMessage, parseApiError } from "@/lib/api-error";

interface CategoryWordList {
  category: string;
  words: { severe: string[]; borderline: string[] };
  word_count: number;
  is_default: boolean;
}

const CATEGORY_LABELS: Record<string, string> = {
  sex: "Sex",
  drugs: "Drugs",
  violence: "Violence",
  profanity: "Profanity",
};

export default function ContentPolicySettingsPage() {
  const [categories, setCategories] = useState<CategoryWordList[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [drafts, setDrafts] = useState<Record<string, { severe: string; borderline: string }>>({});
  const [busyCategory, setBusyCategory] = useState<string | null>(null);

  const buildSupportError = useCallback(async (response: Response, fallbackMessage: string) => {
    const parsed = await parseApiError(response, fallbackMessage);
    return formatSupportMessage(parsed);
  }, []);

  const fetchWordLists = useCallback(async () => {
    try {
      const response = await fetch("/api/content-policy/word-lists", { cache: "no-store" });
      if (!response.ok) {
        throw new Error(await buildSupportError(response, "Failed to load word lists"));
      }
      const data = await response.json();
      const cats: CategoryWordList[] = data.categories || [];
      setCategories(cats);
      setDrafts(
        Object.fromEntries(
          cats.map((c) => [
            c.category,
            { severe: c.words.severe.join(", "), borderline: c.words.borderline.join(", ") },
          ])
        )
      );
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to load word lists");
    } finally {
      setIsLoading(false);
    }
  }, [buildSupportError]);

  useEffect(() => {
    void fetchWordLists();
  }, [fetchWordLists]);

  const handleSave = async (category: string) => {
    const draft = drafts[category];
    if (!draft) return;
    setBusyCategory(category);
    try {
      const words = {
        severe: draft.severe.split(",").map((w) => w.trim()).filter(Boolean),
        borderline: draft.borderline.split(",").map((w) => w.trim()).filter(Boolean),
      };
      const response = await fetch(`/api/content-policy/word-lists/${category}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ words }),
      });
      if (!response.ok) throw new Error(await buildSupportError(response, "Failed to save word list"));
      toast.success(`${CATEGORY_LABELS[category] ?? category} word list saved.`);
      await fetchWordLists();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to save word list");
    } finally {
      setBusyCategory(null);
    }
  };

  const handleReset = async (category: string) => {
    setBusyCategory(category);
    try {
      const response = await fetch(`/api/content-policy/word-lists/${category}/reset`, {
        method: "POST",
      });
      if (!response.ok) throw new Error(await buildSupportError(response, "Failed to reset word list"));
      toast.success(`${CATEGORY_LABELS[category] ?? category} word list reset to default.`);
      await fetchWordLists();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "Failed to reset word list");
    } finally {
      setBusyCategory(null);
    }
  };

  return (
    <div className="mx-auto max-w-3xl px-6 py-12 lg:px-12">
      <div className="mb-8 flex items-center gap-3">
        <Link href="/settings">
          <Button variant="ghost" size="icon">
            <ArrowLeft className="size-4" />
          </Button>
        </Link>
        <div>
          <h1 className="text-h1 font-bold tracking-[-0.01em]">Content Policy</h1>
          <p className="text-small text-muted-foreground">
            Edit the word lists used to flag sensitive terms in captions. Matched words are
            asterisked in exported captions — audio is never censored.
          </p>
        </div>
      </div>

      {isLoading ? (
        <div className="space-y-4">
          <Skeleton className="h-40 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
      ) : (
        <div className="space-y-6">
          {categories.map((cat) => {
            const draft = drafts[cat.category] ?? { severe: "", borderline: "" };
            return (
              <Card key={cat.category}>
                <CardHeader className="flex flex-row items-center justify-between">
                  <CardTitle className="flex items-center gap-2 text-h3">
                    <ShieldAlert className="size-4" />
                    {CATEGORY_LABELS[cat.category] ?? cat.category}
                    <Badge variant={cat.is_default ? "secondary" : "default"}>
                      {cat.is_default ? "Default" : "Custom"}
                    </Badge>
                  </CardTitle>
                  <span className="text-caption text-muted-foreground">
                    {cat.word_count} word{cat.word_count === 1 ? "" : "s"}
                  </span>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div>
                    <label className="text-caption text-muted-foreground">
                      Severe (flagged at Low sensitivity and above)
                    </label>
                    <Textarea
                      value={draft.severe}
                      onChange={(e) =>
                        setDrafts((prev) => ({
                          ...prev,
                          [cat.category]: { ...prev[cat.category], severe: e.target.value },
                        }))
                      }
                      placeholder="comma, separated, words"
                      className="mt-1"
                    />
                  </div>
                  <div>
                    <label className="text-caption text-muted-foreground">
                      Borderline (flagged only at High sensitivity)
                    </label>
                    <Textarea
                      value={draft.borderline}
                      onChange={(e) =>
                        setDrafts((prev) => ({
                          ...prev,
                          [cat.category]: { ...prev[cat.category], borderline: e.target.value },
                        }))
                      }
                      placeholder="comma, separated, words"
                      className="mt-1"
                    />
                  </div>
                  <div className="flex justify-end gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={busyCategory === cat.category}
                      onClick={() => handleReset(cat.category)}
                    >
                      <RotateCcw className="mr-1 size-3.5" />
                      Reset to default
                    </Button>
                    <Button
                      size="sm"
                      disabled={busyCategory === cat.category}
                      onClick={() => handleSave(cat.category)}
                    >
                      Save
                    </Button>
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}

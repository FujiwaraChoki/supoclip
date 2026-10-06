"use client";

import { Info } from "lucide-react";
import type { Ref } from "react";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";

// A concise warning for widely used languages; the supported list below includes
// every orthography reported by Fontconfig, not just this comparison set.
const COMMON_LANGUAGES = [
  "en", "es", "pt", "fr", "de", "it", "pl", "tr", "vi", "el", "ru", "uk",
  "ar", "fa", "he", "hi", "bn", "ta", "te", "th", "zh-cn", "zh-tw", "ja", "ko",
];
const languageNames = new Intl.DisplayNames(["en"], { type: "language" });

function languageName(code: string): string {
  try {
    return languageNames.of(code) ?? code;
  } catch {
    return code;
  }
}

function describeLanguages(codes: string[]): string {
  return [...new Set(codes.map(languageName))].sort().join(", ");
}

export function FontLanguageInfo({
  name,
  supportedLanguages,
  buttonRef,
  onBack,
}: {
  name: string;
  supportedLanguages?: string[] | null;
  buttonRef?: Ref<HTMLButtonElement>;
  onBack?: () => void;
}) {
  const unsupported = supportedLanguages == null ? [] : COMMON_LANGUAGES.filter(
    (code) => !supportedLanguages.includes(code),
  );
  const commonSupported = COMMON_LANGUAGES.filter((code) => supportedLanguages?.includes(code));

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          ref={buttonRef}
          aria-label={`Language support for ${name}`}
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-sm text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          onPointerDown={(event) => event.stopPropagation()}
          onClick={(event) => event.stopPropagation()}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") event.stopPropagation();
            if (event.key === "ArrowLeft" && onBack) {
              event.preventDefault();
              event.stopPropagation();
              onBack();
            }
          }}
        >
          <Info className="h-4 w-4" />
        </button>
      </PopoverTrigger>
      <PopoverContent side="right" align="start" className="z-[60] max-h-72 overflow-y-auto text-sm" aria-label={`Language support for ${name}`}>
        <h3 className="font-medium">{name}: language support</h3>
        {supportedLanguages == null ? (
          <p className="mt-2 text-muted-foreground">Language coverage could not be checked. Preview your captions to confirm the characters display correctly.</p>
        ) : (
          <>
            <p className="mt-2"><span className="font-medium">Supported: </span>{commonSupported.length ? describeLanguages(commonSupported) : supportedLanguages.length ? describeLanguages(supportedLanguages) : "No complete language coverage detected."}</p>
            {unsupported.length > 0 && (
              <p className="mt-2 text-amber-700 dark:text-amber-400"><span className="font-medium">Not fully supported (common languages): </span>{describeLanguages(unsupported)}.</p>
            )}
            {supportedLanguages.length > commonSupported.length && commonSupported.length > 0 && (
              <details className="mt-2">
                <summary className="cursor-pointer font-medium">All supported languages ({supportedLanguages.length})</summary>
                <p className="mt-1 text-muted-foreground">{describeLanguages(supportedLanguages)}</p>
              </details>
            )}
            <p className="mt-2 text-muted-foreground">Missing characters use a fallback font and may look different. Coverage is checked from this font file; preview captions for uncommon characters.</p>
          </>
        )}
      </PopoverContent>
    </Popover>
  );
}

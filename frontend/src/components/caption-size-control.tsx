import { FONT_SIZE_OPTIONS } from "@/lib/font-options";
import { cn } from "@/lib/utils";

export function CaptionSizeControl({ value, onChange, disabled = false }: {
  value: number | null; onChange: (value: number | null) => void; disabled?: boolean;
}) {
  return <fieldset disabled={disabled} className="space-y-2">
    <legend className="text-sm text-muted-foreground">Size</legend>
    <div className="grid grid-cols-4 gap-1.5">
      {FONT_SIZE_OPTIONS.map((option) => <button key={option.label} type="button"
        aria-pressed={value === option.value} onClick={() => onChange(option.value)}
        className={cn("rounded-md border px-2 py-1.5 text-xs font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring", value === option.value ? "border-primary bg-primary text-primary-foreground" : "border-input bg-background text-muted-foreground hover:bg-accent")}>
        {option.label}
      </button>)}
    </div>
  </fieldset>;
}

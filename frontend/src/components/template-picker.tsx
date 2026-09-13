"use client";

export type TemplateInfo = {
  id: string;
  name: string;
  description: string;
  animation: string;
  font_family: string;
  font_size: number;
  font_color: string;
  highlight_color: string;
  word_box?: boolean;
  word_box_color?: string | null;
  uppercase?: boolean;
  stroke_color?: string | null;
  background_color?: string | null;
  glow?: boolean;
};

/** Small CSS-only swatch approximating a caption template's look. */
function TemplateSwatch({ template }: { template: TemplateInfo }) {
  const label = template.uppercase ? "SAMPLE WORD" : "Sample word";
  const [first, second] = label.split(" ");

  return (
    <div
      className="flex h-16 w-full items-center justify-center rounded-md overflow-hidden"
      style={{ backgroundColor: template.background_color ?? "#1c1c1c" }}
    >
      <span
        className="text-sm font-bold px-1"
        style={{
          fontFamily: `'${template.font_family}', system-ui, sans-serif`,
          color: template.font_color,
          WebkitTextStroke: template.stroke_color ? `1px ${template.stroke_color}` : undefined,
          textShadow: template.glow ? `0 0 6px ${template.highlight_color}` : undefined,
        }}
      >
        {first}{" "}
        <span
          className="px-1 rounded"
          style={{
            color: template.word_box ? "#fff" : template.highlight_color,
            backgroundColor: template.word_box ? (template.word_box_color ?? template.highlight_color) : "transparent",
          }}
        >
          {second}
        </span>
      </span>
    </div>
  );
}

export function TemplatePicker({
  templates,
  selectedId,
  onSelect,
  disabled,
}: {
  templates: TemplateInfo[];
  selectedId: string;
  onSelect: (id: string) => void;
  disabled?: boolean;
}) {
  return (
    <div className="grid grid-cols-2 sm:grid-cols-3 gap-2.5">
      {templates.map((template) => {
        const isSelected = template.id === selectedId;
        return (
          <button
            key={template.id}
            type="button"
            disabled={disabled}
            onClick={() => onSelect(template.id)}
            className={`flex flex-col gap-1.5 rounded-lg border-2 p-2 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-50 ${
              isSelected
                ? "border-foreground"
                : "border-border hover:border-primary"
            }`}
          >
            <TemplateSwatch template={template} />
            <div>
              <p className="text-xs font-medium text-foreground">{template.name}</p>
              <p className="text-[10px] text-muted-foreground capitalize">{template.animation}</p>
            </div>
          </button>
        );
      })}
    </div>
  );
}

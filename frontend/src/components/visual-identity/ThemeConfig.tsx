"use client";

import { useState, useEffect, useCallback } from "react";
import { Palette, Type, AlignLeft, AlignCenter, AlignRight, ArrowUpDown, Minus, Plus } from "lucide-react";
import { api } from "@/lib/api";

interface ThemeConfigProps {
  theme: {
    text: string;
    font_family: string;
    font_size: number;
    font_color: string;
    position: "top" | "center" | "bottom";
    alignment: "left" | "center" | "right";
    line_spacing: number;
    margin: number;
  };
  onChange: (theme: Partial<ThemeConfigProps["theme"]>) => void;
  availableFonts: string[];
  disabled?: boolean;
  hookTitle?: string;
}

const FONT_OPTIONS = [
  { value: "Anton-Regular", label: "Anton (Bold, Condensed)" },
  { value: "BebasNeue-Regular", label: "Bebas Neue (Bold, Condensed)" },
  { value: "THEBOLDFONT", label: "The Bold Font" },
  { value: "TikTokSans-Regular", label: "TikTok Sans" },
  { value: "BarlowCondensed-Bold", label: "Barlow Condensed Bold" },
  { value: "Oswald-Variable-wght", label: "Oswald Variable" },
  { value: "LeagueSpartan", label: "League Spartan" },
  { value: "Montserrat-Variable-wght", label: "Montserrat Variable" },
];

const POSITION_OPTIONS = [
  { value: "top", label: "Top", icon: <ArrowUpDown className="w-4 h-4" /> },
  { value: "center", label: "Center", icon: <Type className="w-4 h-4" /> },
  { value: "bottom", label: "Bottom", icon: <ArrowUpDown className="w-4 h-4 rotate-180" /> },
] as const;

const ALIGNMENT_OPTIONS = [
  { value: "left", label: "Left", icon: <AlignLeft className="w-4 h-4" /> },
  { value: "center", label: "Center", icon: <AlignCenter className="w-4 h-4" /> },
  { value: "right", label: "Right", icon: <AlignRight className="w-4 h-4" /> },
] as const;

export function ThemeConfig({
  theme,
  onChange,
  availableFonts = [],
  disabled = false,
  hookTitle,
}: ThemeConfigProps) {
  const [localTheme, setLocalTheme] = useState(theme);
  const [debounceTimer, setDebounceTimer] = useState<NodeJS.Timeout | null>(null);

  // Sync with props
  useEffect(() => {
    setLocalTheme(theme);
  }, [theme]);

  const handleChange = useCallback(
    (key: keyof typeof localTheme, value: any) => {
      setLocalTheme((prev) => ({ ...prev, [key]: value }));

      // Debounce the onChange callback
      if (debounceTimer) clearTimeout(debounceTimer);
      const timer = setTimeout(() => {
        onChange({ [key]: value } as any);
      }, 300);
      setDebounceTimer(timer);
    },
    [onChange, debounceTimer]
  );

  const handleTextChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    handleChange("text", e.target.value);
  };

  const handleFontSizeChange = (value: number) => {
    handleChange("font_size", Math.max(24, Math.min(144, value)));
  };

  const handleLineSpacingChange = (value: number) => {
    handleChange("line_spacing", Math.max(0.8, Math.min(3, value)));
  };

  const handleMarginChange = (value: number) => {
    handleChange("margin", Math.max(0.02, Math.min(0.2, value)));
  };

  const allFonts = [
    ...new Set([
      ...FONT_OPTIONS.map((f) => f.value),
      ...availableFonts.filter((f) => !FONT_OPTIONS.map((fo) => fo.value).includes(f)),
    ]),
  ];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h3 className="text-lg font-semibold flex items-center gap-2">
          <Type className="w-5 h-5 text-primary" />
          Theme / Title
        </h3>
        {hookTitle && (
          <button
            type="button"
            onClick={() => handleChange("text", hookTitle)}
            disabled={disabled}
            className="px-3 py-1 text-xs bg-gray-100 text-gray-700 rounded hover:bg-gray-200 transition-colors disabled:opacity-50"
            title="Use AI-generated hook title"
          >
            Use AI Title
          </button>
        )}
      </div>

      {/* Theme Text */}
      <div>
        <label className="block text-sm font-medium mb-2">Theme Text</label>
        <textarea
          value={localTheme.text}
          onChange={handleTextChange}
          rows={3}
          disabled={disabled}
          placeholder="Enter the theme/title text for your clips..."
          className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary focus:border-primary disabled:opacity-50 disabled:bg-gray-50 resize-none"
        />
        <p className="text-xs text-gray-500 mt-1">
          This text will appear as a large title overlay on all generated clips.
          {hookTitle && " AI-generated hook title available above."}
        </p>
      </div>

      {/* Font Family */}
      <div>
        <label className="block text-sm font-medium mb-2 flex items-center gap-2">
          <Type className="w-4 h-4" />
          Font Family
        </label>
        <select
          value={localTheme.font_family}
          onChange={(e) => handleChange("font_family", e.target.value)}
          disabled={disabled}
          className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary focus:border-primary disabled:opacity-50 disabled:bg-gray-50"
        >
          {allFonts.map((font) => {
            const fontInfo = FONT_OPTIONS.find((f) => f.value === font);
            return (
              <option key={font} value={font}>
                {fontInfo?.label || font}
              </option>
            );
          })}
        </select>
        <p className="text-xs text-gray-500 mt-1">Anton is recommended for bold, condensed titles.</p>
      </div>

      {/* Font Size */}
      <div>
        <label className="block text-sm font-medium mb-2 flex items-center gap-2">
          <Type className="w-4 h-4" />
          Font Size: {localTheme.font_size}px
        </label>
        <div className="flex items-center gap-4">
          <input
            type="range"
            min="24"
            max="144"
            value={localTheme.font_size}
            onChange={(e) => handleFontSizeChange(Number(e.target.value))}
            disabled={disabled}
            className="flex-1 h-2 bg-gray-200 rounded-lg appearance-none cursor-pointer accent-primary"
          />
          <div className="flex items-center gap-2">
            <button
              onClick={() => handleFontSizeChange(localTheme.font_size - 4)}
              disabled={disabled || localTheme.font_size <= 24}
              className="p-1 border border-gray-300 rounded hover:bg-gray-100 disabled:opacity-50"
            >
              <Minus className="w-4 h-4" />
            </button>
            <span className="w-16 text-center font-mono text-sm">{localTheme.font_size}px</span>
            <button
              onClick={() => handleFontSizeChange(localTheme.font_size + 4)}
              disabled={disabled || localTheme.font_size >= 144}
              className="p-1 border border-gray-300 rounded hover:bg-gray-100 disabled:opacity-50"
            >
              <Plus className="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>

      {/* Font Color */}
      <div>
        <label className="block text-sm font-medium mb-2 flex items-center gap-2">
          <Palette className="w-4 h-4" />
          Font Color
        </label>
        <div className="flex items-center gap-4">
          <input
            type="color"
            value={localTheme.font_color}
            onChange={(e) => handleChange("font_color", e.target.value)}
            disabled={disabled}
            className="w-12 h-12 rounded border border-gray-300 cursor-pointer"
          />
          <input
            type="text"
            value={localTheme.font_color}
            onChange={(e) => handleChange("font_color", e.target.value)}
            disabled={disabled}
            className="flex-1 px-3 py-2 border border-gray-300 rounded-lg font-mono text-sm"
            placeholder="#FFFFFF"
          />
        </div>
      </div>

      {/* Position */}
      <div>
        <label className="block text-sm font-medium mb-2 flex items-center gap-2">
          <ArrowUpDown className="w-4 h-4" />
          Vertical Position
        </label>
        <div className="flex gap-2 flex-wrap">
          {POSITION_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => handleChange("position", opt.value)}
              disabled={disabled}
              className={`px-4 py-2 rounded-lg border-2 text-sm font-medium transition-all ${
                localTheme.position === opt.value
                  ? "border-primary bg-primary text-primary-foreground"
                  : "border-gray-300 hover:border-gray-400"
              } disabled:opacity-50`}
            >
              {opt.icon} {opt.label}
            </button>
          ))}
        </div>
      </div>

      {/* Alignment */}
      <div>
        <label className="block text-sm font-medium mb-2 flex items-center gap-2">
          <AlignCenter className="w-4 h-4" />
          Horizontal Alignment
        </label>
        <div className="flex gap-2 flex-wrap">
          {ALIGNMENT_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              type="button"
              onClick={() => handleChange("alignment", opt.value)}
              disabled={disabled}
              className={`px-4 py-2 rounded-lg border-2 text-sm font-medium transition-all ${
                localTheme.alignment === opt.value
                  ? "border-primary bg-primary text-primary-foreground"
                  : "border-gray-300 hover:border-gray-400"
              } disabled:opacity-50`}
            >
              {opt.icon} {opt.label}
            </button>
          ))}
        </div>
      </div>

      {/* Line Spacing */}
      <div>
        <label className="block text-sm font-medium mb-2 flex items-center gap-2">
          Line Spacing: {localTheme.line_spacing.toFixed(1)}
        </label>
        <div className="flex items-center gap-4">
          <input
            type="range"
            min="0.8"
            max="3"
            step="0.1"
            value={localTheme.line_spacing}
            onChange={(e) => handleLineSpacingChange(Number(e.target.value))}
            disabled={disabled}
            className="flex-1 h-2 bg-gray-200 rounded-lg appearance-none cursor-pointer accent-primary"
          />
          <span className="w-16 text-center font-mono text-sm">
            {localTheme.line_spacing.toFixed(1)}
          </span>
        </div>
        <p className="text-xs text-gray-500 mt-1">Spacing between text lines (1.0 = single, 1.5 = one and a half)</p>
      </div>

      {/* Margin */}
      <div>
        <label className="block text-sm font-medium mb-2 flex items-center gap-2">
          Margin from Edges: {(localTheme.margin * 100).toFixed(0)}%
        </label>
        <div className="flex items-center gap-4">
          <input
            type="range"
            min="0.02"
            max="0.2"
            step="0.01"
            value={localTheme.margin}
            onChange={(e) => handleMarginChange(Number(e.target.value))}
            disabled={disabled}
            className="flex-1 h-2 bg-gray-200 rounded-lg appearance-none cursor-pointer accent-primary"
          />
          <span className="w-16 text-center font-mono text-sm">
            {(localTheme.margin * 100).toFixed(0)}%
          </span>
        </div>
        <p className="text-xs text-gray-500 mt-1">Safe area margin from video edges</p>
      </div>
    </div>
  );
}
"use client";

import { useTranslations } from "next-intl";

import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import type { WorkflowSetting, WorkflowSettingValue } from "@/lib/workflows";

/** What the creator changed, per setting. A cleared number is absent, not zero. */
export type AdvancedValues = Record<string, WorkflowSettingValue>;

function isValid(setting: WorkflowSetting, value: unknown): value is WorkflowSettingValue {
  if (setting.type === "bool") return typeof value === "boolean";
  if (setting.type === "enum") return typeof value === "string" && (setting.choices ?? []).includes(value);
  return (
    typeof value === "number" && Number.isInteger(value) &&
    value >= (setting.min ?? value) && value <= (setting.max ?? value)
  );
}

/** Only what differs from the defaults and is valid: an empty result leaves the request as it always was. */
export function changedSettings(schema: readonly WorkflowSetting[], values: AdvancedValues): AdvancedValues {
  const changed: AdvancedValues = {};
  for (const setting of schema) {
    const value = values[setting.key];
    if (value !== undefined && value !== setting.default && isValid(setting, value)) changed[setting.key] = value;
  }
  return changed;
}

/** A number typed but outside its range or not whole, which the server would reject. */
export function invalidSetting(schema: readonly WorkflowSetting[], values: AdvancedValues): WorkflowSetting | null {
  return schema.find((setting) => values[setting.key] !== undefined && !isValid(setting, values[setting.key])) ?? null;
}

/**
 * The settings most people never touch, rendered from the schema
 * `GET /tasks/workflows` publishes for the chosen workflow.
 */
export function WorkflowAdvancedSettings({ schema, values, onChange, disabled }: {
  schema: readonly WorkflowSetting[];
  values: AdvancedValues;
  onChange: (values: AdvancedValues) => void;
  disabled?: boolean;
}) {
  const t = useTranslations("home.advanced");
  const changedCount = Object.keys(changedSettings(schema, values)).length;
  const set = (key: string, value: WorkflowSettingValue | undefined) => {
    const next = { ...values };
    if (value === undefined) delete next[key];
    else next[key] = value;
    onChange(next);
  };

  return (
    <div className="space-y-4">
      <div>
        <p className="text-sm font-medium">{t("title")}</p>
        <p className="text-xs text-muted-foreground">{t("body")}</p>
      </div>
      {schema.map((setting) => {
        const id = `workflow-setting-${setting.key}`;
        const label = t(`settings.${setting.key}.label`);
        const hint = t(`settings.${setting.key}.hint`);
        const value = values[setting.key];
        if (setting.type === "bool") {
          return (
            <div key={setting.key} className="flex items-center justify-between gap-4">
              <div>
                <label htmlFor={id} className="text-sm font-medium">{label}</label>
                <p className="text-xs text-muted-foreground">{hint}</p>
              </div>
              <Switch
                id={id}
                disabled={disabled}
                checked={typeof value === "boolean" ? value : setting.default === true}
                onCheckedChange={(checked) => set(setting.key, checked)}
              />
            </div>
          );
        }
        if (setting.type === "enum") {
          return (
            <div key={setting.key} className="space-y-1.5">
              <label htmlFor={id} className="text-sm font-medium">{label}</label>
              <Select
                disabled={disabled}
                value={typeof value === "string" ? value : String(setting.default)}
                onValueChange={(choice) => set(setting.key, choice)}
              >
                <SelectTrigger id={id} className="w-full"><SelectValue /></SelectTrigger>
                <SelectContent>
                  {(setting.choices ?? []).map((choice) => (
                    <SelectItem key={choice} value={choice}>{t(`choices.${choice}`)}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">{hint}</p>
            </div>
          );
        }
        const problem = value !== undefined && !isValid(setting, value);
        return (
          <div key={setting.key} className="space-y-1.5">
            <label htmlFor={id} className="text-sm font-medium">{label}</label>
            <Input
              id={id}
              type="number"
              inputMode="numeric"
              step={1}
              min={setting.min}
              max={setting.max}
              disabled={disabled}
              placeholder={t("auto")}
              aria-invalid={problem || undefined}
              value={typeof value === "number" && Number.isFinite(value) ? value : ""}
              onChange={(event) => set(setting.key, event.target.value === "" ? undefined : Number(event.target.value))}
            />
            <p className={problem ? "text-xs text-destructive" : "text-xs text-muted-foreground"}>
              {problem ? t("outOfRange", { min: setting.min ?? 0, max: setting.max ?? 0 }) : hint}
            </p>
          </div>
        );
      })}
      <button
        type="button"
        disabled={disabled || (changedCount === 0 && Object.keys(values).length === 0)}
        onClick={() => onChange({})}
        className="text-xs font-medium text-muted-foreground underline-offset-2 hover:text-foreground hover:underline disabled:pointer-events-none disabled:opacity-50"
      >
        {t("reset")}
      </button>
    </div>
  );
}

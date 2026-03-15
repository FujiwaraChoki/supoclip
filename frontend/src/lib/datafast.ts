"use client";

type DataFastValue = string | number | boolean | null | undefined;

export type DataFastMetadata = Record<string, DataFastValue>;

type DataFastIdentifyPayload = DataFastMetadata & {
  user_id: string;
};

declare global {
  interface Window {
    datafast?: {
      (...args: [string] | [string, Record<string, string>]): void;
      q?: IArguments[];
    };
  }
}

const dataFastWebsiteId = process.env.NEXT_PUBLIC_DATAFAST_WEBSITE_ID;
const dataFastDomain = process.env.NEXT_PUBLIC_DATAFAST_DOMAIN;

const isClient = () => typeof window !== "undefined";

export const isDataFastConfigured = Boolean(dataFastWebsiteId && dataFastDomain);

const sanitizeKey = (key: string) =>
  key
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]/g, "_")
    .slice(0, 64);

const sanitizeValue = (value: DataFastValue) => {
  if (value === null || value === undefined) {
    return null;
  }

  return String(value).slice(0, 255);
};

const sanitizeMetadata = (metadata?: DataFastMetadata) => {
  if (!metadata) {
    return undefined;
  }

  const sanitizedEntries = Object.entries(metadata)
    .map(([key, value]) => {
      const sanitizedKey = sanitizeKey(key);
      const sanitizedValue = sanitizeValue(value);

      if (!sanitizedKey || sanitizedValue === null) {
        return null;
      }

      return [sanitizedKey, sanitizedValue] as const;
    })
    .filter((entry): entry is readonly [string, string] => entry !== null)
    .slice(0, 10);

  if (sanitizedEntries.length === 0) {
    return undefined;
  }

  return Object.fromEntries(sanitizedEntries);
};

const sanitizeGoalName = (goalName: string) =>
  goalName
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9_-]/g, "_")
    .slice(0, 64);

export function track(goalName: string, metadata?: DataFastMetadata) {
  if (!isClient() || !isDataFastConfigured || !window.datafast) {
    return;
  }

  const sanitizedGoalName = sanitizeGoalName(goalName);
  if (!sanitizedGoalName) {
    return;
  }

  const sanitizedMetadata = sanitizeMetadata(metadata);
  if (sanitizedMetadata) {
    window.datafast(sanitizedGoalName, sanitizedMetadata);
    return;
  }

  window.datafast(sanitizedGoalName);
}

export function identify(metadata: DataFastIdentifyPayload) {
  if (!isClient() || !isDataFastConfigured || !window.datafast) {
    return;
  }

  const sanitizedMetadata = sanitizeMetadata(metadata);
  if (!sanitizedMetadata?.user_id) {
    return;
  }

  window.datafast("identify", sanitizedMetadata);
}

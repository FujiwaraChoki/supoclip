export function parseFalseEnabled(value: string | undefined): boolean {
  return value?.trim().toLowerCase() === "false";
}

export const monetizationEnabled = parseFalseEnabled(process.env.NEXT_PUBLIC_SELF_HOST);

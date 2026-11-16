/** Where a create response should send the person who submitted it. */

export const DUPLICATE_SOURCE_CODE = "DUPLICATE_SOURCE";

export function taskPathFromCreate(body: {
  task_id?: string;
  duplicate?: boolean;
  code?: string;
}): string {
  const id = body.task_id ?? "";
  if (body.duplicate || body.code === DUPLICATE_SOURCE_CODE) {
    return `/tasks/${id}?already=1`;
  }
  return `/tasks/${id}`;
}

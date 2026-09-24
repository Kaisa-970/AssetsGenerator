export type ParameterDraft = {
  fields: Record<string, string>;
  errors: Record<string, string>;
  json?: string;
};
export const emptyParameterDraft = (): ParameterDraft => ({
  fields: {},
  errors: {},
});

export function pendingParameterNodes(
  drafts: Record<string, ParameterDraft>,
  nodes: Record<string, unknown>,
): string[] {
  return Object.entries(drafts)
    .filter(
      ([id, draft]) =>
        id in nodes &&
        (draft.json !== undefined || Object.keys(draft.fields).length > 0),
    )
    .map(([id]) => id);
}

export function parseParameterObject(text: string): Record<string, unknown> {
  const value: unknown = JSON.parse(text);
  if (!value || typeof value !== "object" || Array.isArray(value))
    throw Error("参数必须是 JSON 对象");
  return value as Record<string, unknown>;
}

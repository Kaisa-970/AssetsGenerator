import type { Dispatch, SetStateAction } from "react";
import type { Adapter } from "./graph";

type Field = {
  type: string;
  enum?: unknown[];
  minimum?: number;
  maximum?: number;
};
export function ParameterForm({
  adapter,
  parameters,
  onChange,
  draft,
  errors,
  setDraft,
  setErrors,
  visibleFields,
  fieldLabelPrefix = "参数",
}: {
  adapter?: Adapter;
  visibleFields?: readonly string[];
  fieldLabelPrefix?: string;
  parameters: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
  draft: Record<string, string>;
  errors: Record<string, string>;
  setDraft: Dispatch<SetStateAction<Record<string, string>>>;
  setErrors: Dispatch<SetStateAction<Record<string, string>>>;
}) {
  const clearDraft = (name: string) => {
    setErrors((pending) => {
      const next = { ...pending };
      delete next[name];
      return next;
    });
    setDraft((pending) => {
      const next = { ...pending };
      delete next[name];
      return next;
    });
  };
  const properties = (adapter?.parameter_schema?.properties || {}) as Record<
    string,
    Field
  >;
  const fields = Object.entries(properties).filter(
    ([, field]) =>
      field.enum ||
      ["string", "integer", "number", "boolean", "array", "object"].includes(
        field.type,
      ),
  );
  if (!fields.length) return null;
  const effective = { ...adapter?.defaults, ...parameters };
  const required = new Set(
    ((adapter?.parameter_schema?.required || []) as unknown[]).filter(
      (name): name is string => typeof name === "string",
    ),
  );
  const missing = fields
    .filter(([name]) => required.has(name) && effective[name] === undefined)
    .map(([name]) => name);
  const shownFields = fields.filter(
    ([name]) => !visibleFields || visibleFields.includes(name),
  );
  const fixedFields = shownFields.filter(
    ([, field]) => field.enum?.length === 1,
  );
  const editableFields = shownFields.filter(
    ([, field]) => field.enum?.length !== 1,
  );
  const fixedMismatch = fixedFields.some(
    ([name, field]) =>
      JSON.stringify(effective[name]) !== JSON.stringify(field.enum![0]),
  );
  const renderFields = (items: typeof fields) =>
    items.map(([name, field]) => {
      const value = effective[name];
      const fixed = field.enum?.length === 1;
      const stale =
        fixed && JSON.stringify(value) !== JSON.stringify(field.enum![0]);
      return (
        <label key={name}>
          {name}
          {required.has(name) ? "（必填）" : ""}
          {fixed ? "（固定契约）" : ""}
          <small aria-label={`${fieldLabelPrefix} ${name} 来源`}>
            {Object.hasOwn(parameters, name)
              ? "显式配置"
              : Object.hasOwn(adapter?.defaults || {}, name)
                ? "沿用实现默认值"
                : "尚未提供"}
            {Object.hasOwn(draft, name) ? " · 编辑尚未应用" : ""}
          </small>
          {field.enum ? (
            <select
              aria-label={`${fieldLabelPrefix} ${name}`}
              disabled={fixed}
              value={field.enum.findIndex(
                (item) => JSON.stringify(item) === JSON.stringify(value),
              )}
              onChange={(e) =>
                onChange({
                  ...parameters,
                  [name]: field.enum![Number(e.target.value)],
                })
              }
            >
              <option value={-1} disabled>
                未设置或值不符合契约
              </option>
              {field.enum.map((option, i) => (
                <option key={i} value={i}>
                  {typeof option === "string" ? option : JSON.stringify(option)}
                </option>
              ))}
            </select>
          ) : ["array", "object"].includes(field.type) ? (
            <>
              <textarea
                aria-label={`${fieldLabelPrefix} ${name} JSON`}
                spellCheck={false}
                value={
                  draft[name] ??
                  (value === undefined ? "" : JSON.stringify(value, null, 2))
                }
                onChange={(e) => setDraft({ ...draft, [name]: e.target.value })}
              />
              <button
                type="button"
                disabled={!(name in draft)}
                onClick={() => {
                  try {
                    const parsed: unknown = JSON.parse(draft[name]);
                    if (
                      (field.type === "array" && !Array.isArray(parsed)) ||
                      (field.type === "object" &&
                        (parsed === null ||
                          typeof parsed !== "object" ||
                          Array.isArray(parsed)))
                    )
                      throw Error(`需要 ${field.type}`);
                    clearDraft(name);
                    onChange({ ...parameters, [name]: parsed });
                  } catch {
                    setErrors((pending) => ({
                      ...pending,
                      [name]: `${name} 需要有效的 ${field.type} JSON，尚未应用`,
                    }));
                  }
                }}
              >
                应用字段 · {name}
              </button>
            </>
          ) : field.type === "boolean" ? (
            <select
              aria-label={`${fieldLabelPrefix} ${name}`}
              value={value === undefined ? "" : String(value)}
              onChange={(e) =>
                onChange({
                  ...parameters,
                  [name]: e.target.value === "true",
                })
              }
            >
              <option value="" disabled>
                未设置
              </option>
              <option value="true">true</option>
              <option value="false">false</option>
            </select>
          ) : (
            <input
              aria-label={`${fieldLabelPrefix} ${name}`}
              type="text"
              title="Enter 确认；Esc 放弃本字段编辑；离开字段时自动校验"
              inputMode={field.type === "string" ? "text" : "decimal"}
              value={draft[name] ?? (value === undefined ? "" : String(value))}
              onChange={(e) => setDraft({ ...draft, [name]: e.target.value })}
              onKeyDown={(event) => {
                // Committing an IME candidate must not apply the parameter.
                if (
                  event.nativeEvent.isComposing ||
                  event.nativeEvent.keyCode === 229
                )
                  return;
                if (event.key === "Enter") {
                  event.preventDefault();
                  event.stopPropagation();
                  event.currentTarget.blur();
                } else if (event.key === "Escape") {
                  event.preventDefault();
                  event.stopPropagation();
                  clearDraft(name);
                }
              }}
              onBlur={() => {
                if (!(name in draft)) return;
                const raw = draft[name];
                const parsed = field.type === "string" ? raw : Number(raw);
                if (
                  field.type !== "string" &&
                  (!raw.trim() ||
                    !Number.isFinite(parsed) ||
                    (field.type === "integer" &&
                      !Number.isSafeInteger(parsed)) ||
                    (field.minimum !== undefined &&
                      Number(parsed) < field.minimum) ||
                    (field.maximum !== undefined &&
                      Number(parsed) > field.maximum))
                ) {
                  setErrors((pending) => ({
                    ...pending,
                    [name]: `${name} 不符合 ${field.type} 范围要求，尚未应用`,
                  }));
                  return;
                }
                clearDraft(name);
                onChange({ ...parameters, [name]: parsed });
              }}
            />
          )}
          {Object.hasOwn(draft, name) && (
            <span>
              {name} 尚未应用；应用或放弃编辑后才能启动新运行。
              <button
                type="button"
                onClick={() => {
                  clearDraft(name);
                }}
              >
                放弃字段编辑 · {name}
              </button>
            </span>
          )}
          {stale && (
            <span role="alert">
              {name} 与当前部署不一致。草稿值：
              {JSON.stringify(value) ?? "未设置"}； 当前要求：
              {JSON.stringify(field.enum![0])}。
              <button
                type="button"
                onClick={() =>
                  onChange({ ...parameters, [name]: field.enum![0] })
                }
              >
                使用当前部署值 · {name}
              </button>
            </span>
          )}
          {!fixed &&
            (Object.hasOwn(parameters, name) || Object.hasOwn(draft, name)) &&
            (Object.hasOwn(adapter?.defaults || {}, name) ||
              !required.has(name)) && (
              <button
                type="button"
                onClick={() => {
                  const next = { ...parameters };
                  delete next[name];
                  clearDraft(name);
                  onChange(next);
                }}
              >
                {Object.hasOwn(adapter?.defaults || {}, name)
                  ? "恢复默认值"
                  : "清除可选参数"}{" "}
                · {name}
              </button>
            )}
        </label>
      );
    });
  return (
    <section aria-label="参数表单">
      <div className="section-label">参数表单</div>
      <p>未覆盖的字段使用 Adapter 默认值。后端编译仍是最终校验。</p>
      {missing.length > 0 && (
        <p role="alert">
          缺少必填参数：{missing.join("、")}。填写并应用后才能运行。
        </p>
      )}
      {renderFields(editableFields)}
      {fixedFields.length > 0 && (
        <details
          key={`${adapter?.name}:${fixedMismatch}`}
          open={fixedMismatch || undefined}
        >
          <summary>
            固定配置 · {fixedFields.length} 项
            {fixedMismatch ? " · 需要核对" : ""}
          </summary>
          <p>由当前实现契约限定，不是可调的推理参数；更换模型后会重新核对。</p>
          {renderFields(fixedFields)}
        </details>
      )}
      {Object.entries(errors)
        .filter(([name]) => name in draft)
        .map(([name, message]) => (
          <p key={name} role="alert" aria-label={`参数错误 · ${name}`}>
            {message}
          </p>
        ))}
      <p>
        对象和数组按字段显式应用；嵌套内容由后端编译校验。也可在下方编辑完整
        JSON。
      </p>
    </section>
  );
}

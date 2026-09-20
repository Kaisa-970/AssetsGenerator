import { useEffect, useRef, useState } from "react";
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
}: {
  adapter?: Adapter;
  parameters: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
}) {
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [error, setError] = useState<{ field: string; message: string }>();
  const previous = useRef({ adapter, parameters });
  useEffect(() => {
    const old = previous.current;
    previous.current = { adapter, parameters };
    if (old.adapter !== adapter) {
      setDraft({});
      setError(undefined);
      return;
    }
    setDraft((pending) =>
      Object.fromEntries(
        Object.entries(pending).filter(
          ([name]) =>
            JSON.stringify(old.parameters[name]) ===
            JSON.stringify(parameters[name]),
        ),
      ),
    );
  }, [adapter, parameters]);
  const clearDraft = (name: string) => {
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
  return (
    <section aria-label="参数表单">
      <div className="section-label">参数表单</div>
      <p>未覆盖的字段使用 Adapter 默认值。后端编译仍是最终校验。</p>
      {fields.map(([name, field]) => {
        const value = effective[name];
        const fixed = field.enum?.length === 1;
        const stale =
          fixed && JSON.stringify(value) !== JSON.stringify(field.enum![0]);
        return (
          <label key={name}>
            {name}
            {fixed ? "（固定契约）" : ""}
            {field.enum ? (
              <select
                aria-label={`参数 ${name}`}
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
                    {typeof option === "string"
                      ? option
                      : JSON.stringify(option)}
                  </option>
                ))}
              </select>
            ) : ["array", "object"].includes(field.type) ? (
              <>
                <textarea
                  aria-label={`参数 ${name} JSON`}
                  spellCheck={false}
                  value={
                    draft[name] ??
                    (value === undefined ? "" : JSON.stringify(value, null, 2))
                  }
                  onChange={(e) =>
                    setDraft({ ...draft, [name]: e.target.value })
                  }
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
                      setError(undefined);
                      clearDraft(name);
                      onChange({ ...parameters, [name]: parsed });
                    } catch {
                      setError({
                        field: name,
                        message: `${name} 需要有效的 ${field.type} JSON，尚未应用`,
                      });
                    }
                  }}
                >
                  应用字段 · {name}
                </button>
              </>
            ) : field.type === "boolean" ? (
              <select
                aria-label={`参数 ${name}`}
                value={value === undefined ? "" : String(value)}
                onChange={(e) =>
                  onChange({ ...parameters, [name]: e.target.value === "true" })
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
                aria-label={`参数 ${name}`}
                type="text"
                inputMode={field.type === "string" ? "text" : "decimal"}
                value={
                  draft[name] ?? (value === undefined ? "" : String(value))
                }
                onChange={(e) => setDraft({ ...draft, [name]: e.target.value })}
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
                    setError({
                      field: name,
                      message: `${name} 不符合 ${field.type} 范围要求，尚未应用`,
                    });
                    return;
                  }
                  setError(undefined);
                  clearDraft(name);
                  onChange({ ...parameters, [name]: parsed });
                }}
              />
            )}
            {Object.hasOwn(draft, name) && (
              <span>
                {name} 尚未应用；保存、编译和运行使用已应用值。
                <button
                  type="button"
                  onClick={() => {
                    clearDraft(name);
                    setError((previous) =>
                      previous?.field === name ? undefined : previous,
                    );
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
            {Object.hasOwn(parameters, name) && (
              <button
                type="button"
                onClick={() => {
                  const next = { ...parameters };
                  delete next[name];
                  onChange(next);
                }}
              >
                移除覆盖 · {name}
              </button>
            )}
          </label>
        );
      })}
      {error && <p role="alert">{error.message}</p>}
      <p>
        对象和数组按字段显式应用；嵌套内容由后端编译校验。也可在下方编辑完整
        JSON。
      </p>
    </section>
  );
}

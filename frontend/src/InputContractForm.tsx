import type { Catalog, Port } from "./graph";
import { portKinds } from "./graph";

// Presets are projections of OperatorSpec, not a second port registry.
export function InputContractForm({
  port,
  catalog,
  onChange,
}: {
  port: Port;
  catalog: Catalog;
  onChange: (port: Port) => void;
}) {
  const change = (patch: Partial<Port>) => {
    const next = { ...port, ...patch };
    for (const key of Object.keys(next))
      if (next[key] === undefined || next[key] === "") delete next[key];
    onChange(next);
  };
  const kinds = portKinds(port);
  const carriers = port.carriers || [];
  const singleKind = kinds.length === 1 ? kinds[0] : "";
  const knownKinds = [
    ...new Set(
      [
        singleKind,
        "text",
        "rgb_image",
        "rgba_image",
        "binary_mask",
        "observation_bundle",
      ].filter(Boolean),
    ),
  ];
  const presets = [
    {
      label: "文本",
      contract: catalog.operators["text_segmentation@2"]?.inputs.text,
    },
    {
      label: "普通 RGB 图片（上传 JPG / PNG）",
      contract: catalog.operators["encode_png@1"]?.inputs.image,
    },
    {
      label: "透明 RGBA 图片（图生 Mesh 输入）",
      contract: catalog.operators["shape_generation@1"]?.inputs.image,
    },
    {
      label: "已编码的 RGB PNG（历史节点输出）",
      contract: catalog.operators["resize_image@1"]?.inputs.image,
    },
    {
      label: "二值遮罩 PNG",
      contract: catalog.operators["apply_binary_mask@1"]?.inputs.mask,
    },
  ].filter((p) => p.contract);
  return (
    <fieldset className="contract-form">
      <legend>输入类型与格式</legend>
      <label>
        快捷设置
        <select
          aria-label="输入格式快捷设置"
          value=""
          onChange={(e) => {
            const preset = presets[Number(e.target.value)];
            if (preset?.contract) onChange(structuredClone(preset.contract));
          }}
        >
          <option value="">选择完整输入格式…</option>
          {presets.map((p, i) => (
            <option key={i} value={String(i)}>
              {p.label}
            </option>
          ))}
        </select>
      </label>
      <p>
        快捷设置会替换此输入的完整契约，不转换文件；普通 RGB 上传请选择“普通 RGB
        图片”，图生 Mesh 请选“透明 RGBA 图片”。已有连线会重新编译检查。
      </p>
      <label>
        数据类型
        <select
          aria-label="输入数据类型"
          value={singleKind}
          onChange={(e) => change({ kind: e.target.value, kinds: undefined })}
        >
          {!singleKind && (
            <option value="" disabled>
              多个类型：{kinds.join(" / ")}（高级 JSON）
            </option>
          )}
          {knownKinds.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </select>
      </label>
      <label>
        载体
        <select
          aria-label="输入载体"
          value={carriers.length === 1 ? carriers[0] : ""}
          onChange={(e) =>
            change({ carriers: e.target.value ? [e.target.value] : undefined })
          }
        >
          <option value="">
            {carriers.length > 1
              ? `多个载体：${carriers.join(" / ")}（高级 JSON）`
              : "未声明"}
          </option>
          {[...new Set([...carriers, "artifact_ref", "structured"])].map(
            (c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ),
          )}
        </select>
      </label>
      <label>
        Schema 名称
        <input
          aria-label="输入 Schema 名称"
          value={port.schema_name || ""}
          placeholder="未声明"
          onChange={(e) => change({ schema_name: e.target.value })}
        />
      </label>
      <label>
        Schema 版本
        <input
          aria-label="输入 Schema 版本"
          value={port.schema_version || ""}
          placeholder="未声明"
          onChange={(e) => change({ schema_version: e.target.value })}
        />
      </label>
      <label>
        数量
        <select
          aria-label="输入基数"
          value={port.cardinality || "one"}
          onChange={(e) => change({ cardinality: e.target.value })}
        >
          {[
            ...new Set([
              port.cardinality || "one",
              "one",
              "zero_or_one",
              "one_or_more",
              "zero_or_more",
            ]),
          ].map((c) => (
            <option key={c} value={c}>
              {(
                {
                  one: "一个",
                  zero_or_one: "可选一个",
                  one_or_more: "至少一个",
                  zero_or_more: "零个或多个",
                } as Record<string, string>
              )[c] || c}
            </option>
          ))}
        </select>
      </label>
      <p>
        字段修改立即应用；未展示的坐标、单位等字段保持原值。复杂契约仍可使用高级
        JSON。
      </p>
    </fieldset>
  );
}

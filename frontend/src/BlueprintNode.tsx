import {
  memo,
  useEffect,
  useState,
  type Dispatch,
  type KeyboardEvent,
  type ReactNode,
  type SetStateAction,
} from "react";
import {
  Handle,
  Position,
  useUpdateNodeInternals,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import { ParameterForm } from "./ParameterForm";
import { executionStatus } from "./executionStatus";
import { parseReference, portKinds, type Adapter, type Port } from "./graph";
import "./BlueprintNode.css";

export type BlueprintNodeData = Record<string, unknown> & {
  nodeId: string;
  label: string;
  operatorLabel?: string;
  operator: string;
  inputs: Record<string, Port>;
  outputs: Record<string, Port>;
  status?: string;
  statusSource?: string;
  freshness?: string;
  failureSummary?: string;
  parameterSchema?: Adapter;
  parameters?: Record<string, unknown>;
  drafts?: Record<string, string>;
  errors?: Record<string, string>;
  setDraft?: Dispatch<SetStateAction<Record<string, string>>>;
  setErrors?: Dispatch<SetStateAction<Record<string, string>>>;
  onParametersChange?: (parameters: Record<string, unknown>) => void;
  backend?: string;
  backends?: { value: string; label: string }[];
  onBackendChange?: (value: string) => void;
  adapterId?: string;
  adapters?: { value: string; label: string }[];
  onAdapterChange?: (value: string) => void;
  inputSlot?: ReactNode;
  configurationBlockedReason?: string;
  inputBindings?: Record<string, string>;
  outputSummary?: { port: string; kind?: string }[];
  onViewOutputs?: () => void;
};

const readableOperators: Record<string, string> = {
  apply_binary_mask: "应用二值遮罩",
  assemble_asset: "组装资产",
  canonicalize_mesh: "规范化网格",
  encode_png: "编码图片",
  export_asset: "导出资产",
  generate_shape: "生成形状",
  qa_asset: "资产质量检查",
  shape_generation: "生成网格",
  text_segmentation: "文字分割",
  thumbnail: "生成缩略图",
  resize_image: "调整图片尺寸",
};

export function readableOperatorLabel(operator: string): string {
  const name = operator.split("@")[0];
  return readableOperators[name] || name.replaceAll("_", " ");
}

const kindLabels: Record<string, string> = {
  rgb_image: "图片",
  rgba_image: "透明图片",
  binary_mask: "遮罩",
  text: "文字",
  triangle_mesh: "网格",
  gltf_asset: "3D 资产",
  depth_map: "深度图",
  normal_map: "法线图",
  point_cloud: "点云",
  camera_parameters: "相机参数",
  observation_bundle: "多视图观测",
  mask_candidates: "遮罩候选",
  quality_report: "质量报告",
  asset_definition: "资产定义",
  asset_release: "发布资产",
  scene_definition: "场景",
  mesh: "网格",
};
const portLabels: Record<string, string> = {
  image: "图片",
  images: "图片集",
  mask: "遮罩",
  masks: "遮罩集",
  mesh: "网格",
  glb: "3D 资产",
  rgba: "透明图片",
  rgb: "图片",
  text: "文字",
  prompt: "提示词",
  observations: "观测",
  candidates: "候选",
  asset: "资产",
  report: "报告",
  quality: "质量",
  value: "输出",
  depth: "深度",
  cameras: "相机",
  scene: "场景",
};
export function blueprintPortLabel(name: string): string {
  return portLabels[name] || name;
}
export function blueprintBindingLabel(reference: string): string {
  const parsed = parseReference(reference);
  if (!parsed) return reference;
  const source = parsed.source.startsWith("input:")
    ? `输入 ${parsed.source.slice(6)}`
    : `${parsed.source} · ${parsed.handle}`;
  return `${source}${parsed.optional ? "（可选引用）" : ""}`;
}
export function blueprintKindLabel(port: Port): string {
  return portKinds(port)
    .map((kind) => kindLabels[kind] || kind)
    .join(" / ");
}
export function blueprintPortTooltip(name: string, port: Port): string {
  return [
    name,
    portKinds(port).join(" | "),
    `数量：${port.cardinality || "one"}`,
    port.schema_name
      ? `${port.schema_name}@${port.schema_version || "未声明版本"}`
      : "未声明 schema",
    port.frame_id ? `frame：${String(port.frame_id)}` : "",
    port.unit ? `unit：${String(port.unit)}` : "",
  ]
    .filter(Boolean)
    .join(" · ");
}
/** Keep native text editing and button activation while isolating graph shortcuts. */
export function protectBlueprintControlKeyboard(
  event: Pick<KeyboardEvent, "stopPropagation">,
): void {
  event.stopPropagation();
}

export function blueprintParameterFields(adapter?: Adapter): {
  primary: string[];
  all: string[];
} {
  const entries = Object.entries(
    adapter?.parameter_schema?.properties || {},
  ).filter(([, raw]) => {
    const field = raw as { type?: string; enum?: unknown[] };
    return (
      field.enum ||
      ["string", "integer", "number", "boolean", "array", "object"].includes(
        field.type || "",
      )
    );
  });
  return {
    all: entries.map(([name]) => name),
    primary: entries
      .filter(([, raw]) => (raw as { enum?: unknown[] }).enum?.length !== 1)
      .slice(0, 3)
      .map(([name]) => name),
  };
}

function BlueprintNodeBody({
  data,
  selected,
}: NodeProps<Node<BlueprintNodeData>>) {
  const [moreParameters, setMoreParameters] = useState(false);
  const [technicalOpen, setTechnicalOpen] = useState(false);
  const updateNodeInternals = useUpdateNodeInternals();
  const isInput =
    data.operator === "Pipeline input" || data.nodeId.startsWith("input:");
  const hasDrafts = Object.keys(data.drafts || {}).length > 0;
  const parameterFields = blueprintParameterFields(data.parameterSchema);
  const requiredParameters = new Set(
    (
      (data.parameterSchema?.parameter_schema?.required || []) as unknown[]
    ).filter((name): name is string => typeof name === "string"),
  );
  const effectiveParameters = {
    ...(data.parameterSchema?.defaults || {}),
    ...(data.parameters || {}),
  };
  // Presence is not validity: empty strings, false and zero are supplied values.
  // Backend compilation remains authoritative for schema constraints.
  const configuredParameters = parameterFields.all.filter(
    (name) => effectiveParameters[name] !== undefined,
  );
  const missingRequiredParameters = parameterFields.all.filter(
    (name) =>
      requiredParameters.has(name) && !configuredParameters.includes(name),
  );
  const requiredVisibleFields = parameterFields.all.filter((name) =>
    requiredParameters.has(name),
  );
  const primaryVisibleFields = Array.from(
    new Set([...requiredVisibleFields, ...parameterFields.primary]),
  );
  const inputNames = Object.keys(data.inputs);
  const connectedInputs = inputNames.filter((name) => {
    const reference = data.inputBindings?.[name];
    return typeof reference === "string" && reference.length > 0;
  });
  const missingInputs = inputNames.filter(
    (name) =>
      !connectedInputs.includes(name) &&
      ["one", "one_or_more"].includes(data.inputs[name].cardinality || "one"),
  );
  // This is catalog availability, not execution eligibility or service health.
  const implementationMessage = data.parameterSchema
    ? ""
    : data.backend || data.adapterId
      ? "当前实现未能匹配，请检查模型与实现配置"
      : (data.adapters?.length || 0) > 1 || (data.backends?.length || 0) > 0
        ? "请选择模型实现"
        : "尚未配置实现";
  const configurationMessages = [
    missingInputs.length
      ? `缺少输入：${missingInputs.map(blueprintPortLabel).join("、")}`
      : "",
    implementationMessage,
    missingRequiredParameters.length
      ? `缺少必填参数：${missingRequiredParameters.join("、")}`
      : "",
    hasDrafts ? "有未应用参数" : "",
    data.configurationBlockedReason || "",
  ].filter(Boolean);
  const layoutSignature = JSON.stringify([
    data.inputs,
    data.inputBindings,
    data.outputs,
    data.parameterSchema?.parameter_schema,
    data.parameters,
    data.drafts,
    data.errors,
    data.configurationBlockedReason,
    data.outputSummary,
    moreParameters,
    primaryVisibleFields,
    technicalOpen,
  ]);
  useEffect(() => {
    updateNodeInternals(data.nodeId);
  }, [data.nodeId, layoutSignature, updateNodeInternals]);
  const canEditParameters =
    data.onParametersChange && data.setDraft && data.setErrors;
  const ports = (
    entries: Record<string, Port>,
    direction: "input" | "output",
  ) =>
    Object.entries(entries).map(([name, port]) => (
      <div
        className={`blueprint-port blueprint-port-${direction}`}
        key={`${direction}:${name}`}
        title={blueprintPortTooltip(name, port)}
      >
        <Handle
          type={direction === "input" ? "target" : "source"}
          position={direction === "input" ? Position.Left : Position.Right}
          id={name}
          aria-label={`${data.label} ${direction === "input" ? "输入" : "输出"} ${name}`}
        />
        <span>{blueprintPortLabel(name)}</span>
        <small>{blueprintKindLabel(port)}</small>
        {direction === "input" &&
          ["zero_or_one", "zero_or_more", "many"].includes(
            port.cardinality || "",
          ) && <small>可选</small>}
        {direction === "input" && data.inputBindings?.[name] && (
          <small
            className="blueprint-binding"
            aria-label={`${data.nodeId} 输入 ${name} 来源`}
            title={data.inputBindings[name]}
          >
            ← {blueprintBindingLabel(data.inputBindings[name])}
          </small>
        )}
      </div>
    ));
  return (
    <article
      className={`blueprint-node${selected ? " blueprint-node-selected" : ""}${isInput ? " blueprint-node-input" : ""}`}
      aria-label={`节点 ${data.label}`}
    >
      <header className="blueprint-node-header blueprint-node-drag-handle">
        <span className="blueprint-node-category">
          {isInput ? "输入" : "处理"}
        </span>
        <strong title={data.nodeId}>
          {isInput
            ? data.label
            : data.operatorLabel || readableOperatorLabel(data.operator)}
        </strong>
        {!isInput && (
          <span className="blueprint-node-instance">· {data.nodeId}</span>
        )}
        {data.status && (
          <span
            className="blueprint-history-status"
            title={`来源运行：${data.statusSource || "导入的历史记录"}；不代表当前草稿已执行。`}
          >
            历史 · {executionStatus(data.status)}
          </span>
        )}
        {data.freshness &&
          (data.freshness.includes("需要更新") ||
            data.freshness.includes("上游需更新")) && (
            <span
              className="blueprint-history-status blueprint-stale-status"
              title="当前画布配置或上游已变化；历史结果仍可预览，但需要重新执行。"
            >
              需更新
            </span>
          )}
        {data.failureSummary && (
          <span
            className="blueprint-history-status blueprint-failure-status"
            title="详细错误、请求和远程作业信息位于运行记录。"
          >
            {data.failureSummary}
          </span>
        )}
      </header>
      {!isInput && inputNames.length > 0 && (
        <div
          className="blueprint-input-summary"
          aria-label={`输入状态 ${data.nodeId}`}
        >
          输入 {connectedInputs.length}/{inputNames.length} 已连接
          {missingInputs.length > 0 && (
            <span className="blueprint-input-missing">
              · 未连接：{missingInputs.map(blueprintPortLabel).join("、")}
            </span>
          )}
        </div>
      )}
      {!isInput && (
        <div
          className={`blueprint-config-summary${configurationMessages.length ? " blueprint-config-summary-warning" : ""}`}
          aria-label={`配置状态 ${data.nodeId}`}
        >
          <span className="blueprint-config-dot" aria-hidden="true" />
          {configurationMessages.length
            ? configurationMessages.join(" · ")
            : "配置已提供；编译后才能确认可执行"}
        </div>
      )}
      {!isInput && data.outputSummary && data.outputSummary.length > 0 && (
        <div
          className="blueprint-output-summary nodrag nowheel nopan"
          onKeyDown={protectBlueprintControlKeyboard}
          onPointerDown={(event) => event.stopPropagation()}
          aria-label={`输出状态 ${data.nodeId}`}
        >
          <span>
            输出 · {data.outputSummary.map((item) => item.port).join("、")}
          </span>
          {data.onViewOutputs && (
            <button type="button" onClick={data.onViewOutputs}>
              查看输出
            </button>
          )}
        </div>
      )}
      <div className="blueprint-ports">{ports(data.inputs, "input")}</div>
      <div
        className="blueprint-controls nodrag nowheel nopan"
        onKeyDown={protectBlueprintControlKeyboard}
        onKeyUp={protectBlueprintControlKeyboard}
        onPointerDown={(event) => event.stopPropagation()}
        onDoubleClick={(event) => event.stopPropagation()}
      >
        {isInput ? (
          data.inputSlot || <p className="blueprint-node-hint">选择输入来源</p>
        ) : (
          <>
            {data.onBackendChange && (
              <label className="blueprint-model-label">
                模型
                <select
                  aria-label={`节点 ${data.nodeId} 的模型`}
                  value={data.backend || ""}
                  disabled={hasDrafts || !!data.configurationBlockedReason}
                  onChange={(event) =>
                    data.onBackendChange?.(event.target.value)
                  }
                >
                  <option value="">
                    {(data.backends?.length || 0) > 1
                      ? "请选择模型实现"
                      : "服务默认实现"}
                  </option>
                  {(data.backends || [])
                    .filter((choice) => choice.value !== "")
                    .map((choice) => (
                      <option key={choice.value} value={choice.value}>
                        {choice.label}
                      </option>
                    ))}
                  {data.backend &&
                    !data.backends?.some(
                      (choice) => choice.value === data.backend,
                    ) && <option value={data.backend}>当前模型不可用</option>}
                </select>
              </label>
            )}
            {data.onAdapterChange &&
              (data.adapters?.length !== 1 ||
                (data.adapterId &&
                  data.adapterId !== data.adapters[0].value)) && (
                <label>
                  实现
                  <select
                    aria-label={`节点 ${data.nodeId} 的实现`}
                    value={data.adapterId || ""}
                    disabled={hasDrafts || !!data.configurationBlockedReason}
                    onChange={(event) =>
                      data.onAdapterChange?.(event.target.value)
                    }
                  >
                    <option value="">
                      {data.adapters?.length === 1
                        ? `自动绑定 · ${data.adapters[0].label}`
                        : "请选择实现"}
                    </option>
                    {data.adapters?.map((choice) => (
                      <option key={choice.value} value={choice.value}>
                        {choice.label}
                      </option>
                    ))}
                    {data.adapterId &&
                      !data.adapters?.some(
                        (choice) => choice.value === data.adapterId,
                      ) && (
                        <option value={data.adapterId}>
                          当前实现不可用 · {data.adapterId}
                        </option>
                      )}
                  </select>
                </label>
              )}
            {canEditParameters && (
              <fieldset
                disabled={!!data.configurationBlockedReason}
                className="blueprint-parameter-fields"
              >
                <div
                  className="parameter-summary"
                  aria-label={`参数状态 ${data.nodeId}`}
                >
                  参数 {configuredParameters.length}/
                  {parameterFields.all.length} 已提供
                  {hasDrafts && (
                    <span className="parameter-missing"> · 有未应用编辑</span>
                  )}
                  {missingRequiredParameters.length > 0 && (
                    <span className="parameter-missing">
                      · 缺少必填：{missingRequiredParameters.join("、")}
                    </span>
                  )}
                </div>
                {primaryVisibleFields.length > 0 && (
                  <div className="blueprint-parameter-heading">常用参数</div>
                )}
                <ParameterForm
                  adapter={data.parameterSchema}
                  visibleFields={
                    moreParameters ? undefined : primaryVisibleFields
                  }
                  parameters={data.parameters || {}}
                  draft={data.drafts || {}}
                  errors={data.errors || {}}
                  setDraft={data.setDraft!}
                  setErrors={data.setErrors!}
                  onChange={data.onParametersChange!}
                  fieldLabelPrefix="节点参数"
                />
                {parameterFields.all.length > primaryVisibleFields.length && (
                  <button
                    type="button"
                    aria-expanded={moreParameters}
                    onClick={() => setMoreParameters((open) => !open)}
                  >
                    {moreParameters ? "收起更多参数" : "更多参数"}
                  </button>
                )}
              </fieldset>
            )}
            {data.configurationBlockedReason && (
              <p role="alert">{data.configurationBlockedReason}</p>
            )}
          </>
        )}
        <details
          className="blueprint-technical"
          onToggle={(event) => setTechnicalOpen(event.currentTarget.open)}
        >
          <summary>技术信息</summary>
          <p>算子：{data.operator}</p>
          {data.adapterId && <p>实现：{data.adapterId}</p>}
          <p>节点：{data.nodeId}</p>
        </details>
      </div>
      <div className="blueprint-ports">{ports(data.outputs, "output")}</div>
    </article>
  );
}

export const BlueprintNode = memo(BlueprintNodeBody);

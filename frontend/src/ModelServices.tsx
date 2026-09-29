import { useEffect, useRef, useState } from "react";
import { portKinds, type Catalog, type ServiceCapability } from "./graph";

type Detection = {
  capability_availability?: Record<string, { installable: boolean; reason?: string | null }>;
  endpoint: string;
  descriptor_digest: string;
  descriptor: {
    display_name: string;
    operator?: string;
    capabilities?: ServiceCapability[];
    parameter_schema?: {
      properties?: Record<string, { type?: string; enum?: unknown[] }>;
      required?: string[];
    };
    defaults?: Record<string, unknown>;
    frame_id?: string;
    up_axis?: string;
    unit?: string;
  };
};

/** Normalize the new multi-capability descriptor while preserving v1 shape. */
function descriptorCapabilities(
  descriptor: Detection["descriptor"],
): ServiceCapability[] {
  if (descriptor.capabilities?.length) return descriptor.capabilities;
  return [
    {
      operator: descriptor.operator,
      display_name: descriptor.display_name,
      parameter_schema: descriptor.parameter_schema,
      defaults: descriptor.defaults,
      frame_id: descriptor.frame_id,
      up_axis: descriptor.up_axis,
      unit: descriptor.unit,
    },
  ];
}
function serviceCapabilities(
  service: NonNullable<Catalog["model_services"]>[number],
): ServiceCapability[] {
  if (service.capabilities?.length) return service.capabilities;
  return [
    {
      operator: service.operator,
      display_name: service.display_name,
      frame_id: service.frame_id,
      up_axis: service.up_axis,
      unit: service.unit,
    },
  ];
}
async function post(path: string, body: unknown, signal?: AbortSignal) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal,
  });
  const value = await response.json();
  if (!response.ok)
    throw Error(value.error || value.message || `HTTP ${response.status}`);
  return value;
}
export function ModelServices({
  catalog,
  onCatalog,
  onAddNode,
}: {
  catalog: Catalog;
  onCatalog: (catalog: Catalog) => void;
  onAddNode: (operator: string, backend: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [endpoint, setEndpoint] = useState("");
  const [detection, setDetection] = useState<Detection>();
  const [selectedCapabilityId, setSelectedCapabilityId] = useState<string>();
  const [addedCapabilityIds, setAddedCapabilityIds] = useState<Set<string>>(
    new Set(),
  );
  const [busy, setBusy] = useState<"detect" | "add">();
  const [addingCapability, setAddingCapability] = useState<string>();
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const request = useRef({
    sequence: 0,
    controller: undefined as AbortController | undefined,
  });
  useEffect(
    () => () => {
      request.current.controller?.abort();
    },
    [],
  );
  const invalidate = () => {
    request.current.sequence++;
    request.current.controller?.abort();
    setDetection(undefined);
    setSelectedCapabilityId(undefined);
    setAddedCapabilityIds(new Set());
    setBusy(undefined);
    setError("");
    setNotice("");
  };
  const detect = async () => {
    invalidate();
    const sequence = request.current.sequence;
    const controller = new AbortController();
    request.current.controller = controller;
    setBusy("detect");
    try {
      const value = await post(
        "/api/model-services/detect",
        { endpoint: endpoint.trim() },
        controller.signal,
      );
      if (sequence === request.current.sequence && !controller.signal.aborted)
        setDetection(value);
    } catch (e) {
      if (sequence === request.current.sequence && !controller.signal.aborted)
        setError(String(e));
    } finally {
      if (sequence === request.current.sequence) setBusy(undefined);
    }
  };
  const add = async (capabilityId = selectedCapabilityId) => {
    if (!detection || !capabilityId) return;
    setBusy("add");
    setError("");
    try {
      const value = await post("/api/model-services", {
        endpoint: detection.endpoint,
        descriptor_digest: detection.descriptor_digest,
        capability_id: capabilityId,
      });
      onCatalog(value.catalog);
      setAddedCapabilityIds((previous) => new Set(previous).add(capabilityId));
      setNotice(
        capabilities.length > 1
          ? `已添加 ${value.display_name} · ${capabilityId}。可以继续添加同一服务的其他能力。`
          : `已添加 ${value.display_name}，点击或拖动下方模型加入画布。`,
      );
      if (capabilities.length === 1) {
        setOpen(false);
        setDetection(undefined);
        setSelectedCapabilityId(undefined);
      }
    } catch (e) {
      setError(String(e));
      // A changed deployment must be detected again before confirming.
      setDetection(undefined);
    } finally {
      setAddingCapability(undefined);
      setBusy(undefined);
    }
  };
  const capabilities = detection
    ? descriptorCapabilities(detection.descriptor)
    : [];
  return (
    <section className="model-services" aria-label="模型服务">
      <button onClick={() => setOpen(!open)} aria-expanded={open}>
        ＋ 添加模型服务
      </button>
      <p className="muted">支持图生 Mesh 和已升级发现接口的 SAM3 文字分割。</p>
      {open && (
        <div className="model-service-form">
          <p>填写支持服务发现的模型地址。检测不会启动推理。</p>
          <p>
            SAM3 旧服务需先升级发现接口；其他未支持能力只可查看。
            普通模型网页或 Gradio 地址不能直接添加。
          </p>
          <label>
            服务地址
            <input
              aria-label="模型服务地址"
              placeholder="http://服务器:端口"
              value={endpoint}
              disabled={busy === "add"}
              onChange={(e) => {
                invalidate();
                setEndpoint(e.target.value);
              }}
            />
          </label>
          <button
            disabled={!endpoint.trim() || !!busy}
            onClick={() => void detect()}
          >
            {busy === "detect" ? "正在检测…" : "检测服务"}
          </button>
          {detection && (
            <section aria-label="检测到的模型">
              <strong>{detection.descriptor.display_name}</strong>
              <p>{capabilities.length} 项模型能力</p>
              {capabilities.map((capability, index) => {
                const operator = capability.operator
                  ? catalog.operators[capability.operator]
                  : undefined;
                const availability = detection.capability_availability?.[
                  capability.capability_id || capability.operator || ""
                ];
                const schema = capability.parameter_schema as
                  | {
                      properties?: Record<string, { type?: string }>;
                      required?: string[];
                    }
                  | undefined;
                const defaults = capability.defaults || {};
                return (
                  <article
                    key={
                      capability.capability_id || capability.operator || index
                    }
                    className="model-capability"
                  >
                    <strong>
                      {capability.display_name ||
                        capability.operator ||
                        `能力 ${index + 1}`}
                    </strong>
                    <p>{capability.operator || "未声明 Operator"}</p>
                    <p>
                      原生坐标：
                      {capability.frame_id ||
                        detection.descriptor.frame_id ||
                        "未声明"}{" "}
                      · 上轴{" "}
                      {capability.up_axis ||
                        detection.descriptor.up_axis ||
                        "未声明"}{" "}
                      · 单位{" "}
                      {capability.unit || detection.descriptor.unit || "未声明"}
                    </p>
                    {operator ? (
                      <>
                        <p>
                          输入：
                          {Object.entries(operator.inputs)
                            .map(
                              ([name, port]) =>
                                `${name} (${portKinds(port).join(" / ")})`,
                            )
                            .join("、")}
                        </p>
                        <p>
                          输出：
                          {Object.entries(operator.outputs)
                            .map(
                              ([name, port]) =>
                                `${name} (${portKinds(port).join(" / ")})`,
                            )
                            .join("、")}
                        </p>
                      </>
                    ) : (
                      <p role="alert">
                        当前目录没有对应输入输出契约，无法添加此能力。
                      </p>
                    )}
                    <div>
                      可配置参数：
                      {Object.entries(schema?.properties || {}).map(
                        ([name, field]) => (
                          <p key={name}>
                            {name} · {field.type}
                            {schema?.required?.includes(name) ? " · 必填" : ""}
                            {Object.hasOwn(defaults, name)
                              ? ` · 默认 ${JSON.stringify(defaults[name])}`
                              : ""}
                          </p>
                        ),
                      )}
                    </div>
                    {!availability?.installable && (
                      <p role="alert">仅查看，暂不可执行：{availability?.reason || "尚未取得后端安装资格，请重新检测。"}</p>
                    )}
                    <button
                      disabled={
                        !operator ||
                        !availability?.installable ||
                        !!busy ||
                        addedCapabilityIds.has(
                          capability.capability_id || capability.operator || "",
                        )
                      }
                      onClick={() => {
                        const capabilityId =
                          capability.capability_id || capability.operator;
                        setSelectedCapabilityId(capabilityId);
                        void add(capabilityId);
                      }}
                    >
                      {addedCapabilityIds.has(
                        capability.capability_id || capability.operator || "",
                      )
                        ? "已添加"
                        : busy === "add"
                        ? "正在添加…"
                        : capabilities.length === 1
                          ? "确认添加模型"
                          : "添加此能力"}
                    </button>
                  </article>
                );
              })}
              <p>协议检测通过；尚未验证生成质量。</p>
            </section>
          )}
          {error && <p role="alert">{error}</p>}
        </div>
      )}
      {notice && <p role="status">{notice}</p>}
      {(catalog.model_services || []).flatMap((service) =>
        serviceCapabilities(service).map((capability, index) => {
          const operator = capability.operator || service.operator;
          const key = `${service.backend}:${capability.capability_id || operator || index}`;
          return (
            <button
              className="catalog-item"
              key={key}
              draggable={Boolean(operator)}
              onDragStart={(e) => {
                e.dataTransfer.setData(
                  "application/model-service",
                  service.backend,
                );
                e.dataTransfer.setData(
                  "application/model-capability",
                  capability.capability_id || operator || "",
                );
                e.dataTransfer.effectAllowed = "move";
              }}
              onClick={() =>
                operator && onAddNode(operator, service.backend)
              }
            >
              <strong>{capability.display_name || service.display_name}</strong>
              <span>
                {operator
                  ? "模型服务 · 点击或拖入画布"
                  : "仅查看 · 当前版本暂不可执行"}
              </span>
              <span>{operator || "未知能力"}</span>
              <span>
                原生坐标：{capability.frame_id || service.frame_id || "未声明"}{" "}
                · 上轴 {capability.up_axis || service.up_axis || "未声明"} ·
                单位 {capability.unit || service.unit || "未声明"}
              </span>
            </button>
          );
        }),
      )}
    </section>
  );
}

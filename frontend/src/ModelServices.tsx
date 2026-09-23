import { useEffect, useRef, useState } from "react";
import { portKinds, type Catalog } from "./graph";

type Detection = {
  endpoint: string;
  descriptor_digest: string;
  descriptor: {
    display_name: string;
    operator: string;
    parameter_schema: {
      properties?: Record<string, { type?: string; enum?: unknown[] }>;
      required?: string[];
    };
    defaults: Record<string, unknown>;
    frame_id: string;
    up_axis: string;
    unit: string;
  };
};
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
  const [busy, setBusy] = useState<"detect" | "add">();
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
  const add = async () => {
    if (!detection) return;
    setBusy("add");
    setError("");
    try {
      const value = await post("/api/model-services", {
        endpoint: detection.endpoint,
        descriptor_digest: detection.descriptor_digest,
      });
      onCatalog(value.catalog);
      setOpen(false);
      setNotice(`已添加 ${value.display_name}，点击或拖动下方模型加入画布。`);
      setDetection(undefined);
    } catch (e) {
      setError(String(e));
      // A changed deployment must be detected again before confirming.
      setDetection(undefined);
    } finally {
      setBusy(undefined);
    }
  };
  const operator =
    detection && catalog.operators[detection.descriptor.operator];
  return (
    <section className="model-services" aria-label="模型服务">
      <button onClick={() => setOpen(!open)} aria-expanded={open}>
        ＋ 添加模型服务
      </button>
      {open && (
        <div className="model-service-form">
          <p>填写符合统一协议的图生 Mesh 服务地址。检测不会启动推理。</p>
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
              <p>{detection.descriptor.operator}</p>
              <p>
                原生坐标：{detection.descriptor.frame_id || "未声明"} · 上轴{" "}
                {detection.descriptor.up_axis || "未声明"} · 单位{" "}
                {detection.descriptor.unit || "未声明"}
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
                <p role="alert">当前目录没有对应输入输出契约，无法添加。</p>
              )}
              <div>
                可配置参数：
                {Object.entries(
                  detection.descriptor.parameter_schema.properties || {},
                ).map(([name, field]) => (
                  <p key={name}>
                    {name} · {field.type}
                    {detection.descriptor.parameter_schema.required?.includes(
                      name,
                    )
                      ? " · 必填"
                      : ""}
                    {Object.hasOwn(detection.descriptor.defaults, name)
                      ? ` · 默认 ${JSON.stringify(detection.descriptor.defaults[name])}`
                      : ""}
                  </p>
                ))}
              </div>
              <p>协议检测通过；尚未验证生成质量。</p>
              <button disabled={!operator || !!busy} onClick={() => void add()}>
                {busy === "add" ? "正在添加…" : "确认添加模型"}
              </button>
            </section>
          )}
          {error && <p role="alert">{error}</p>}
        </div>
      )}
      {notice && <p role="status">{notice}</p>}
      {(catalog.model_services || []).map((service) => (
        <button
          className="catalog-item"
          key={service.backend}
          draggable
          onDragStart={(e) => {
            e.dataTransfer.setData(
              "application/model-service",
              service.backend,
            );
            e.dataTransfer.effectAllowed = "move";
          }}
          onClick={() => onAddNode(service.operator, service.backend)}
        >
          <strong>{service.display_name}</strong>
          <span>模型服务 · 点击或拖入画布</span>
          <span>{service.operator}</span>
          <span>
            原生坐标：{service.frame_id} · 上轴 {service.up_axis} · 单位{" "}
            {service.unit}
          </span>
        </button>
      ))}
    </section>
  );
}

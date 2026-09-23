import { useEffect, useState } from "react";
export type Appearance = {
  primitive_count: number;
  textured_primitives: number;
  vertex_colored_primitives: number;
  postprocess_mode: string | null;
};
export function parseAppearance(raw: Appearance): Appearance {
  if (!raw || !Number.isInteger(raw.primitive_count) || raw.primitive_count < 1)
    throw Error("外观记录无效");
  for (const count of [raw.textured_primitives, raw.vertex_colored_primitives])
    if (!Number.isInteger(count) || count < 0 || count > raw.primitive_count)
      throw Error("外观计数无效");
  if (raw.postprocess_mode !== null && typeof raw.postprocess_mode !== "string")
    throw Error("后处理记录无效");
  return raw;
}
export function AppearanceBody({ value }: { value: Appearance }) {
  return (
    <>
      {value.postprocess_mode === "geometry_fallback_no_texture" && (
        <p role="status">生成已完成，但纹理后处理降级为纯几何输出。</p>
      )}
      <p>
        {value.textured_primitives > 0
          ? `含纹理的网格部分：${value.textured_primitives}/${value.primitive_count}`
          : "此输出未包含纹理。"}
      </p>
      <p>
        {value.vertex_colored_primitives > 0
          ? `含顶点颜色的网格部分：${value.vertex_colored_primitives}/${value.primitive_count}`
          : "此输出未包含顶点颜色，仍可能使用纯色材质。"}
      </p>
      {value.postprocess_mode === null && (
        <p>后处理原因未记录；不能据此判断是否发生降级。</p>
      )}
      <p>仅检查 GLB 的纹理引用和顶点颜色属性，不代表材质质量合格。</p>
    </>
  );
}
function MeshAppearance({
  runId,
  node,
  port,
}: {
  runId: string;
  node: string;
  port: string;
}) {
  const [value, setValue] = useState<Appearance>();
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    const url = `/api/runs/${encodeURIComponent(runId)}/appearance/${encodeURIComponent(node)}/${encodeURIComponent(port)}`;
    void fetch(url, {
      signal: controller.signal,
      cache: "no-store",
      redirect: "error",
    })
      .then(async (r) => {
        if (!r.ok) throw Error(`HTTP ${r.status}`);
        return parseAppearance(await r.json());
      })
      .then((v) => {
        if (!controller.signal.aborted) setValue(v);
      })
      .catch((e) => {
        if (!controller.signal.aborted) setError(String(e));
      });
    return () => controller.abort();
  }, [runId, node, port]);
  return (
    <section aria-label={`外观事实 · ${node} · ${port}`}>
      <p>
        来源：{runId} / {node} / {port}
      </p>
      {error ? (
        <p role="alert">无法核实外观事实：{error}</p>
      ) : value ? (
        <AppearanceBody value={value} />
      ) : (
        <p>正在核实外观事实…</p>
      )}
    </section>
  );
}
export function MeshAppearancePanel({
  runId,
  outputs,
}: {
  runId: string;
  outputs: { node_id: string; port: string; kind?: string }[];
}) {
  const meshes = outputs.filter(
    (o) => o.kind === "triangle_mesh" || o.kind === "gltf_asset",
  );
  if (!meshes.length) return null;
  return (
    <section aria-label="外观事实">
      <h3>外观事实</h3>
      {meshes.map((o) => (
        <MeshAppearance
          key={JSON.stringify([runId, o.node_id, o.port])}
          runId={runId}
          node={o.node_id}
          port={o.port}
        />
      ))}
    </section>
  );
}

export type Port = {
  kind?: string;
  kinds?: string[];
  cardinality?: string;
  carriers?: string[];
  schema_name?: string;
  schema_version?: string;
  [key: string]: unknown;
};
export type Operator = {
  name: string;
  version: string;
  inputs: Record<string, Port>;
  outputs: Record<string, Port>;
  relations?: unknown[];
};
export type Adapter = {
  name: string;
  version: string;
  operators: string[];
  parameter_schema?: Record<string, unknown>;
  defaults?: Record<string, unknown>;
  execution_kind?: string;
};
export type PipelineNode = {
  operator: string;
  adapter?: string;
  backend?: string;
  parameters?: Record<string, unknown>;
  inputs: Record<string, string>;
  [key: string]: unknown;
};
export type Pipeline = {
  pipeline: string;
  version: string;
  inputs: Record<string, Port>;
  nodes: Record<string, PipelineNode>;
  [key: string]: unknown;
};
export type Layout = Record<string, { x: number; y: number }>;
export type ServiceCapability = {
  capability_id?: string;
  operator?: string;
  display_name?: string;
  inputs?: Record<string, Port>;
  outputs?: Record<string, Port>;
  parameter_schema?: Record<string, unknown>;
  defaults?: Record<string, unknown>;
  frame_id?: string;
  up_axis?: string;
  unit?: string;
};
export type Catalog = {
  model_services?: {
    backend: string;
    display_name: string;
    endpoint: string;
    /** Legacy single-capability field; new services may expose capabilities[]. */
    operator?: string;
    descriptor_digest: string;
    frame_id?: string;
    up_axis?: string;
    unit?: string;
    capabilities?: ServiceCapability[];
  }[];
  execution_enabled?: boolean;
  execution_profile?: string;
  operators: Record<string, Operator>;
  adapters: Adapter[];
  backends?: (Adapter & { backend: string; adapter: string })[];
  templates: {
    id: string;
    label: string;
    pipeline: Pipeline;
    execution_ready?: boolean;
    execution_reason?: string;
    execution_level?: string;
    service_status?: string;
  }[];
};

/** Presentation only: backend identity remains the select value and plan binding. */
export function backendLabel(catalog: Catalog, backend: string): string {
  const name = catalog.model_services?.find(
    (service) => service.backend === backend,
  )?.display_name;
  return name && name !== backend ? `${name} · ${backend}` : backend;
}
export const inputId = (name: string) => `input:${name}`;
export function parseReference(
  ref: string,
): { source: string; handle: string; optional: boolean } | null {
  const optional = ref.endsWith("?");
  const value = optional ? ref.slice(0, -1) : ref;
  if (value.startsWith("pipeline.inputs."))
    return { source: inputId(value.slice(16)), handle: "value", optional };
  const match = value.match(/^(.+)\.outputs\.([^.]+)$/);
  return match ? { source: match[1], handle: match[2], optional } : null;
}
export function reference(source: string, handle: string): string {
  return source.startsWith("input:")
    ? `pipeline.inputs.${source.slice(6)}`
    : `${source}.outputs.${handle}`;
}
export function graphEdges(p: Pipeline) {
  return Object.entries(p.nodes).flatMap(([id, n]) =>
    Object.entries(n.inputs || {}).flatMap(([port, ref]) => {
      const parsed = parseReference(ref);
      return parsed
        ? [
            {
              id: `${id}:${port}`,
              source: parsed.source,
              sourceHandle: parsed.handle,
              target: id,
              targetHandle: port,
              label: parsed.optional ? "可选" : undefined,
            },
          ]
        : [];
    }),
  );
}
const ranges: Record<string, [number, number, boolean]> = {
  one: [1, 1, false],
  zero_or_one: [0, 1, false],
  one_or_more: [1, Infinity, true],
  zero_or_more: [0, Infinity, true],
  many: [0, Infinity, true],
};
export const portKinds = (port: Port): string[] =>
  port.kinds || (port.kind ? [port.kind] : []);
export function compatible(source: Port, target: Port): string | null {
  const sk = portKinds(source),
    tk = portKinds(target);
  if (!sk.length || !tk.length || sk.some((k) => !tk.includes(k)))
    return `类型不匹配：${sk.join("|")} → ${tk.join("|")}`;
  const a = ranges[source.cardinality || "one"],
    b = ranges[target.cardinality || "one"];
  if (!a || !b || a[2] !== b[2] || a[0] < b[0] || a[1] > b[1])
    return `基数不匹配：${source.cardinality || "one"} → ${target.cardinality || "one"}；数量范围或集合/单值不兼容，需要显式转换`;
  if (
    source.carriers &&
    target.carriers &&
    source.carriers.some((c) => !target.carriers!.includes(c))
  )
    return `载体不兼容：${source.carriers.join("|")} → ${target.carriers.join("|")}`;
  for (const key of ["schema_name", "schema_version"] as const)
    if (target[key] && target[key] !== source[key])
      return `${key} 不匹配：${source[key] || "未声明"} → ${target[key]}`;
  return null;
}
export function connectionError(
  p: Pipeline,
  c: Catalog,
  source: string,
  handle: string,
  target: string,
  port: string,
): string | null {
  if (source === target) return "节点不能连接自身";
  const src = source.startsWith("input:")
    ? p.inputs[source.slice(6)]
    : c.operators[p.nodes[source]?.operator]?.outputs[handle];
  const dst = c.operators[p.nodes[target]?.operator]?.inputs[port];
  if (!src || !dst) return "找不到端口契约";
  const err = compatible(src, dst);
  if (err) return `${source}.${handle} → ${target}.${port}：${err}`;
  const edges = graphEdges(p).filter(
    (e) => !(e.target === target && e.targetHandle === port),
  );
  const pending = [target],
    visited = new Set<string>();
  while (pending.length) {
    const id = pending.pop()!;
    if (id === source) return "该连接会形成循环";
    if (visited.has(id)) continue;
    visited.add(id);
    pending.push(...edges.filter((e) => e.source === id).map((e) => e.target));
  }
  return null;
}
export function bind(
  p: Pipeline,
  source: string,
  handle: string,
  target: string,
  port: string,
): Pipeline {
  return {
    ...p,
    nodes: {
      ...p.nodes,
      [target]: {
        ...p.nodes[target],
        inputs: {
          ...p.nodes[target].inputs,
          [port]: reference(source, handle),
        },
      },
    },
  };
}
export function removeNodes(p: Pipeline, ids: Set<string>): Pipeline {
  return {
    ...p,
    inputs: Object.fromEntries(
      Object.entries(p.inputs).filter(([id]) => !ids.has(inputId(id))),
    ),
    nodes: Object.fromEntries(
      Object.entries(p.nodes)
        .filter(([id]) => !ids.has(id))
        .map(([id, n]) => [
          id,
          {
            ...n,
            inputs: Object.fromEntries(
              Object.entries(n.inputs || {}).filter(
                ([, ref]) => !ids.has(parseReference(ref)?.source || ""),
              ),
            ),
          },
        ]),
    ),
  };
}
const isRecord = (value: unknown): value is Record<string, unknown> =>
  value !== null && typeof value === "object" && !Array.isArray(value);

export function validateInputPort(
  port: unknown,
  id: string,
): asserts port is Port {
  if (
    !isRecord(port) ||
    (port.kind !== undefined && typeof port.kind !== "string") ||
    (port.kinds !== undefined &&
      (!Array.isArray(port.kinds) ||
        port.kinds.some((kind) => typeof kind !== "string"))) ||
    (port.carriers !== undefined &&
      (!Array.isArray(port.carriers) ||
        port.carriers.some((carrier) => typeof carrier !== "string"))) ||
    !portKinds(port as Port).length
  )
    throw Error(`无效输入：${id}`);
}

export function validateDocument(value: unknown): Pipeline {
  if (!isRecord(value)) throw Error("YAML 顶层必须是 Pipeline 对象");
  const p = value as Pipeline;
  if (
    typeof p.pipeline !== "string" ||
    !p.pipeline ||
    !p.version ||
    !isRecord(p.inputs) ||
    !isRecord(p.nodes)
  )
    throw Error("需要 pipeline、version、inputs 和 nodes");
  for (const [id, n] of Object.entries(p.nodes)) {
    if (
      !/^[A-Za-z_][\w-]*$/.test(id) ||
      !isRecord(n) ||
      typeof n.operator !== "string" ||
      !isRecord(n.inputs) ||
      (n.parameters !== undefined && !isRecord(n.parameters)) ||
      Object.values(n.inputs).some((v) => typeof v !== "string")
    )
      throw Error(`无效节点：${id}`);
  }
  for (const [id, port] of Object.entries(p.inputs))
    validateInputPort(port, id);
  return { ...p, version: String(p.version) };
}

export function renameNode(
  p: Pipeline,
  oldId: string,
  newName: string,
): Pipeline {
  if (!/^[A-Za-z_][\w-]*$/.test(newName))
    throw Error("实例名称只接受字母开头的字母、数字、下划线和连字符");
  const isInput = oldId.startsWith("input:");
  const newId = isInput ? inputId(newName) : newName;
  if (newId === oldId) return p;
  if (isInput ? !!p.inputs[newName] : !!p.nodes[newName])
    throw Error("实例名称已存在");
  return {
    ...p,
    inputs: Object.fromEntries(
      Object.entries(p.inputs).map(([id, port]) => [
        isInput && inputId(id) === oldId ? newName : id,
        port,
      ]),
    ),
    nodes: Object.fromEntries(
      Object.entries(p.nodes).map(([id, node]) => [
        !isInput && id === oldId ? newName : id,
        {
          ...node,
          inputs: Object.fromEntries(
            Object.entries(node.inputs).map(([port, ref]) => {
              const parsed = parseReference(ref);
              return [
                port,
                parsed?.source === oldId
                  ? reference(newId, parsed.handle) +
                    (parsed.optional ? "?" : "")
                  : ref,
              ];
            }),
          ),
        },
      ]),
    ),
  };
}

/** Clear fixed deployment overrides when switching trusted implementations. */
export function selectBackend(
  node: PipelineNode,
  backend: string,
  catalog: Catalog,
): PipelineNode {
  const candidates = backend
    ? (catalog.backends || []).filter(
        (item) =>
          item.backend === backend && item.operators.includes(node.operator),
      )
    : catalog.adapters.filter((item) => item.operators.includes(node.operator));
  const keyOf = (item: Adapter) => `${item.name}@${item.version}`;
  const adapterKey = candidates.some((item) => keyOf(item) === node.adapter)
    ? node.adapter
    : candidates.length === 1
      ? keyOf(candidates[0])
      : undefined;
  const oldAdapter = node.backend
    ? catalog.backends?.find(
        (item) =>
          item.backend === node.backend && item.adapter === node.adapter,
      )
    : catalog.adapters.find((item) => keyOf(item) === node.adapter);
  const newAdapter = candidates.find((item) => keyOf(item) === adapterKey);
  const parameters = clearFixedParameters(node, [oldAdapter, newAdapter]);
  return {
    ...node,
    adapter: adapterKey,
    backend: backend || undefined,
    parameters,
  };
}

/** Duplicate applied configuration and upstream bindings, never downstream edges. */
export function duplicateNode(
  p: Pipeline,
  source: string,
): { pipeline: Pipeline; id: string } {
  if (!Object.hasOwn(p.nodes, source)) throw Error("找不到可复制的节点");
  let index = 1;
  let id = `${source}_copy`;
  while (Object.hasOwn(p.nodes, id)) id = `${source}_copy_${++index}`;
  return {
    id,
    pipeline: {
      ...p,
      nodes: { ...p.nodes, [id]: structuredClone(p.nodes[source]) },
    },
  };
}

function clearFixedParameters(
  node: PipelineNode,
  adapters: (Adapter | undefined)[],
) {
  const parameters = { ...node.parameters };
  for (const adapter of adapters) {
    const properties = adapter?.parameter_schema?.properties;
    if (!properties || typeof properties !== "object") continue;
    for (const [key, rule] of Object.entries(properties)) {
      if (
        rule &&
        typeof rule === "object" &&
        "enum" in rule &&
        Array.isArray(rule.enum) &&
        rule.enum.length === 1
      )
        delete parameters[key];
    }
  }
  delete parameters.profile_digest;
  return parameters;
}

export function selectAdapter(
  node: PipelineNode,
  adapter: string,
  catalog: Catalog,
): PipelineNode {
  const choices = node.backend
    ? (catalog.backends || []).filter((item) => item.backend === node.backend)
    : catalog.adapters;
  const resolve = (key?: string) =>
    choices.find(
      (item) =>
        `${item.name}@${item.version}` === key &&
        item.operators.includes(node.operator),
    );
  return {
    ...node,
    adapter: adapter || undefined,
    parameters: clearFixedParameters(node, [
      resolve(node.adapter),
      resolve(adapter),
    ]),
  };
}

/** Extract only the target and its ancestors; backend still compiles the result. */
export function pipelineThrough(pipeline: Pipeline, target: string): Pipeline {
  if (!pipeline.nodes[target]) throw Error("请选择一个算子节点");
  const keep = new Set<string>();
  const inputs = new Set<string>();
  function visit(id: string) {
    if (keep.has(id)) return;
    const node = pipeline.nodes[id];
    if (!node) throw Error(`上游节点不存在：${id}`);
    keep.add(id);
    for (const binding of Object.values(node.inputs)) {
      const ref = parseReference(binding);
      if (!ref) throw Error(`无效绑定：${binding}`);
      if (ref.source.startsWith("input:")) inputs.add(ref.source.slice(6));
      else visit(ref.source);
    }
  }
  visit(target);
  return {
    ...structuredClone(pipeline),
    nodes: Object.fromEntries(
      Object.entries(pipeline.nodes)
        .filter(([id]) => keep.has(id))
        .map(([id, node]) => [id, structuredClone(node)]),
    ),
    inputs: Object.fromEntries(
      Object.entries(pipeline.inputs).filter(([id]) => inputs.has(id)),
    ),
  };
}

/** Default layout follows dependencies, never JSON key order. Saved positions take precedence. */
export function dependencyLayout(p: Pipeline): Layout {
  const positions: Layout = {};
  const levels = new Map<string, number>();
  Object.keys(p.inputs).forEach((name, row) => {
    levels.set(inputId(name), 0);
    positions[inputId(name)] = { x: 30, y: 80 + row * 550 };
  });
  const pending = new Set(Object.keys(p.nodes).sort());
  while (pending.size) {
    let progressed = false;
    for (const id of pending) {
      const parents = Object.values(p.nodes[id].inputs || {})
        .map(parseReference)
        .filter(
          (ref) => ref && (ref.source in p.nodes || levels.has(ref.source)),
        )
        .map((ref) => ref!.source);
      if (parents.some((parent) => !levels.has(parent))) continue;
      levels.set(
        id,
        Math.max(1, ...parents.map((parent) => levels.get(parent)! + 1)),
      );
      pending.delete(id);
      progressed = true;
    }
    // Invalid cyclic drafts must remain editable; compilation reports the cycle.
    if (!progressed) {
      for (const id of pending) levels.set(id, 1);
      break;
    }
  }
  const rows = new Map<number, number>();
  for (const id of Object.keys(p.nodes).sort()) {
    const level = levels.get(id)!;
    const row = rows.get(level) || 0;
    positions[id] = { x: 30 + level * 430, y: 80 + row * 550 };
    rows.set(level, row + 1);
  }
  return positions;
}

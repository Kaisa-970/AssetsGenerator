import {
  emptyParameterDraft,
  pendingParameterNodes,
  parseParameterObject,
  type ParameterDraft,
} from "./parameterDrafts";
import { ModelServices } from "./ModelServices";
import { InputContractForm } from "./InputContractForm";
import { executionStatus } from "./executionStatus";
import { ParameterForm } from "./ParameterForm";
import React, {
  useEffect,
  useState,
  useRef,
  useCallback,
  useMemo,
} from "react";
import { createRoot } from "react-dom/client";
import {
  ReactFlow,
  applyNodeChanges,
  ReactFlowProvider,
  Background,
  Controls,
  MiniMap,
  Handle,
  Position,
  useReactFlow,
  type NodeProps,
  type Connection,
  type Node,
} from "@xyflow/react";
import { load, dump } from "js-yaml";
import {
  type Catalog,
  type Pipeline,
  type Layout,
  type Port,
  portKinds,
  inputId,
  graphEdges,
  dependencyLayout,
  connectionError,
  bind,
  removeNodes,
  renameNode,
  duplicateNode,
  validateDocument,
  validateInputPort,
  selectBackend,
  selectAdapter,
} from "./graph";
import "@xyflow/react/dist/style.css";
import "./style.css";
import { ExecutionPanel } from "./ExecutionPanel";
type Data = Record<string, unknown> & {
  label: string;
  operator: string;
  inputs: Record<string, Port>;
  outputs: Record<string, Port>;
  status?: string;
};
function OperatorNode({ data, selected }: NodeProps<Node<Data>>) {
  return (
    <div className={`op-node ${selected ? "selected" : ""}`}>
      <div className="node-top">
        <span>{data.operator === "Pipeline input" ? "输入" : "算子"}</span>
        {data.status && <em>历史 · {executionStatus(data.status)}</em>}
      </div>
      <strong>{data.label}</strong>
      <small>{data.operator}</small>
      <div className="ports">
        {Object.entries(data.inputs).map(([name, p]) => (
          <div className="port in" key={name}>
            <Handle type="target" position={Position.Left} id={name} />
            <b>{name}</b>
            <small>
              {portKinds(p).join(" | ")} · {p.cardinality || "one"}
              {p.schema_name
                ? ` · ${p.schema_name}@${p.schema_version || "未声明版本"}`
                : " · 格式未声明"}
            </small>
          </div>
        ))}
        {Object.entries(data.outputs).map(([name, p]) => (
          <div className="port out" key={name}>
            <Handle type="source" position={Position.Right} id={name} />
            <b>{name}</b>
            <small>
              {portKinds(p).join(" | ")} · {p.cardinality || "one"}
              {p.schema_name
                ? ` · ${p.schema_name}@${p.schema_version || "未声明版本"}`
                : " · 格式未声明"}
            </small>
          </div>
        ))}
      </div>
    </div>
  );
}
const nodeTypes = { operator: OperatorNode };
const initial: Pipeline = {
  pipeline: "my_asset_pipeline",
  version: "1",
  inputs: { image: { kind: "rgb_image", carriers: ["artifact_ref"] } },
  nodes: {},
};
async function api(path: string, options?: RequestInit) {
  const response = await fetch(path, {
    ...options,
    headers: { "Content-Type": "application/json", ...options?.headers },
  });
  const result = await response.json();
  if (!response.ok)
    throw Error(result.error || result.message || `HTTP ${response.status}`);
  return result;
}
function App() {
  const [catalog, setCatalog] = useState<Catalog>({
    operators: {},
    adapters: [],
    templates: [],
  });
  const [pipeline, setPipeline] = useState<Pipeline>(initial);
  const [layout, setLayout] = useState<Layout>({});
  const [selected, setSelected] = useState<string>();
  const [selectedEdges, setSelectedEdges] = useState<Set<string>>(new Set());
  const pipelineRef = useRef(pipeline);
  pipelineRef.current = pipeline;
  const layoutRef = useRef(layout);
  layoutRef.current = layout;
  const [connectionIssue, setConnectionIssue] = useState("");
  const [message, setMessage] = useState("正在读取节点目录…");
  const [draft, setDraft] = useState("my-pipeline");
  const [drafts, setDrafts] = useState<string[]>([]);
  const [result, setResult] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  const [compilePending, setCompilePending] = useState(true);
  const [compileMessage, setCompileMessage] = useState("正在检查当前配置…");
  const compileRequest = useRef<{
    sequence: number;
    controller?: AbortController;
  }>({ sequence: 0 });
  const [filter, setFilter] = useState("");
  const [registeredOnly, setRegisteredOnly] = useState(false);
  const [nodePreviewOpen, setNodePreviewOpen] = useState(true);
  const [previewHost, setPreviewHost] = useState<HTMLDivElement | null>(null);
  const [parameterDrafts, setParameterDrafts] = useState<
    Record<string, ParameterDraft>
  >({});
  const pendingParameterIds = pendingParameterNodes(
    parameterDrafts,
    pipeline.nodes,
  );
  const pendingParameterIdsRef = useRef(pendingParameterIds);
  pendingParameterIdsRef.current = pendingParameterIds;
  const configurationBlockedReason = pendingParameterIds.length
    ? `参数尚未应用：${pendingParameterIds.join("、")}。请返回配置应用或放弃编辑。`
    : undefined;
  useEffect(() => {
    setParameterDrafts((previous) => {
      const removed = Object.keys(previous).filter(
        (id) => !(id in pipeline.nodes),
      );
      if (!removed.length) return previous;
      return Object.fromEntries(
        Object.entries(previous).filter(([id]) => id in pipeline.nodes),
      );
    });
  }, [pipeline.nodes]);
  const selectedDraft =
    (selected && parameterDrafts[selected]) || emptyParameterDraft();
  const parameters =
    selectedDraft.json ??
    JSON.stringify(
      selected ? pipeline.nodes[selected]?.parameters || {} : {},
      null,
      2,
    );
  const changeParameterDraft = (
    id: string,
    change: (draft: ParameterDraft) => ParameterDraft,
  ) =>
    setParameterDrafts((previous) => ({
      ...previous,
      [id]: change(previous[id] || emptyParameterDraft()),
    }));
  const [inputSpec, setInputSpec] = useState("{}");
  const [tab, setTab] = useState("inspector");
  const [run, setRun] = useState<Record<string, unknown>>();
  const input = useRef<HTMLInputElement>(null);
  const runInput = useRef<HTMLInputElement>(null);
  const flow = useReactFlow();
  const update = useCallback(
    (change: Pipeline | ((previous: Pipeline) => Pipeline)) => {
      const next =
        typeof change === "function" ? change(pipelineRef.current) : change;
      pipelineRef.current = next;
      setPipeline(next);
      compileRequest.current.controller?.abort();
      compileRequest.current.sequence++;
      setResult(undefined);
      setCompilePending(true);
      setCompileMessage("正在检查当前配置…");
      setMessage("图已修改，正在自动检查当前配置。");
    },
    [],
  );
  const compileCurrent = useCallback(async (current: Pipeline) => {
    compileRequest.current.controller?.abort();
    const controller = new AbortController();
    const sequence = ++compileRequest.current.sequence;
    compileRequest.current.controller = controller;
    setCompilePending(true);
    setCompileMessage("正在检查当前配置…");
    const active = () =>
      !controller.signal.aborted &&
      sequence === compileRequest.current.sequence &&
      pipelineRef.current === current;
    try {
      const response = await api("/api/compile", {
        method: "POST",
        body: JSON.stringify({ pipeline: current }),
        signal: controller.signal,
      });
      if (!active()) return;
      setResult(response);
      setCompileMessage(
        response.ok
          ? response.execution_ready
            ? "编译通过；配置可执行，尚未启动。"
            : `编译通过；${response.execution_reason || "当前仅支持编辑，未启用执行入口。"}`
          : "编译未通过，请查看诊断。",
      );
    } catch (error) {
      if (active()) {
        setResult(undefined);
        setCompileMessage(`当前配置无法核实：${String(error)}`);
      }
    } finally {
      if (active()) setCompilePending(false);
    }
  }, []);
  useEffect(() => {
    const timer = window.setTimeout(() => void compileCurrent(pipeline), 250);
    return () => {
      window.clearTimeout(timer);
      compileRequest.current.controller?.abort();
    };
  }, [pipeline, compileCurrent]);
  useEffect(() => {
    Promise.all([api("/api/catalog"), api("/api/drafts")])
      .then(([c, d]) => {
        setCatalog(c);
        setDrafts(d.drafts);
        setMessage("选择模板或组合算子；配置改变后会自动检查。");
      })
      .catch((e) => setMessage(String(e)));
  }, []);
  useEffect(() => {
    setInputSpec(
      JSON.stringify(
        selected?.startsWith("input:")
          ? pipeline.inputs[selected.slice(6)]
          : {},
        null,
        2,
      ),
    );
  }, [selected, pipeline]);
  const guarded = async (action: () => Promise<void>) => {
    setBusy(true);
    try {
      await action();
    } catch (e) {
      setMessage(String(e));
    } finally {
      setBusy(false);
    }
  };
  const loadPipeline = (p: Pipeline, l: Layout = {}) => {
    if (pendingParameterIdsRef.current.length)
      throw Error("存在尚未应用的参数；请先应用或放弃编辑，再载入其他画布。");
    update(validateDocument(p));
    setParameterDrafts({});
    setLayout(l);
    setSelected(undefined);
    setRun(undefined);
    setTimeout(() => flow.fitView({ padding: 0.18 }), 50);
  };
  const addOperator = (
    key: string,
    position?: { x: number; y: number },
    backend?: string,
  ) => {
    const base = key.split("@")[0];
    let id = base;
    let i = 2;
    while (pipeline.nodes[id]) id = `${base}_${i++}`;
    const candidates = catalog.adapters.filter((a) =>
      a.operators.includes(key),
    );
    const adapter = backend
      ? catalog.backends?.find(
          (item) => item.backend === backend && item.operators.includes(key),
        )
      : candidates.length === 1
        ? candidates[0]
        : undefined;
    if (backend && !adapter) {
      setMessage("模型实现尚未注册，请重新检测服务。");
      return;
    }
    update({
      ...pipeline,
      nodes: {
        ...pipeline.nodes,
        [id]: {
          operator: key,
          ...(adapter
            ? {
                ...(backend
                  ? { backend }
                  : { adapter: `${adapter.name}@${adapter.version}` }),
                parameters: adapter.defaults || {},
              }
            : {}),
          inputs: {},
        },
      },
    });
    setLayout({
      ...layout,
      [id]: position || {
        x: Math.max(
          30,
          ...flow
            .getNodes()
            .map(
              (node) => node.position.x + (node.measured?.width || 300) + 80,
            ),
        ),
        y: 80,
      },
    });
    setSelected(id);
    if (!position)
      setTimeout(() => flow.fitView({ padding: 0.18, maxZoom: 1 }), 50);
  };
  const connect = (c: Connection) => {
    if (!c.sourceHandle || !c.targetHandle) return;
    const error = connectionError(
      pipeline,
      catalog,
      c.source,
      c.sourceHandle,
      c.target,
      c.targetHandle,
    );
    if (error) {
      setMessage(error);
      setConnectionIssue(error);
      if (c.source.startsWith("input:")) {
        setSelected(c.source);
        setTab("inspector");
      }
      return;
    }
    setConnectionIssue("");
    update(bind(pipeline, c.source, c.sourceHandle, c.target, c.targetHandle));
    setMessage("已连接。跨输入来源与空间关系由后端编译及运行校验。");
  };
  const selectedInputName = selected?.startsWith("input:")
    ? selected.slice(6)
    : undefined;
  const selectedInput = selectedInputName
    ? pipeline.inputs[selectedInputName]
    : undefined;
  const states = (
    run?.dag as { node_states?: Record<string, { status: string }> } | undefined
  )?.node_states;
  const declaredNodes = useMemo<Node<Data>[]>(() => {
    const positions = dependencyLayout(pipeline);
    return [
      ...Object.entries(pipeline.inputs).map(([id, p]) => ({
        id: inputId(id),
        type: "operator",
        position: layout[inputId(id)] || positions[inputId(id)],
        selected: selected === inputId(id),
        data: {
          label: id,
          operator: "Pipeline input",
          inputs: {},
          outputs: { value: p },
        },
      })),
      ...Object.entries(pipeline.nodes).map(([id, n]) => ({
        id,
        type: "operator",
        position: layout[id] || positions[id],
        selected: selected === id,
        data: {
          label: id,
          operator: n.operator,
          inputs: catalog.operators[n.operator]?.inputs || {},
          outputs: catalog.operators[n.operator]?.outputs || {},
          status: states?.[id]?.status,
        },
      })),
    ];
  }, [pipeline, layout, selected, catalog, states]);
  const [nodes, setNodes] = useState<Node<Data>[]>([]);
  useEffect(() => setNodes(declaredNodes), [declaredNodes]);
  const edges = useMemo(
    () =>
      graphEdges(pipeline).map((edge) => ({
        ...edge,
        selected: selectedEdges.has(edge.id),
      })),
    [pipeline, selectedEdges],
  );
  const node = selected ? pipeline.nodes[selected] : undefined;
  const adapters = node
    ? (node.backend
        ? (catalog.backends || []).filter((a) => a.backend === node.backend)
        : catalog.adapters
      ).filter((a) => a.operators.includes(node.operator))
    : [];
  const selectedAdapter = node?.adapter
    ? adapters.find((a) => `${a.name}@${a.version}` === node.adapter)
    : adapters.length === 1
      ? adapters[0]
      : undefined;
  return (
    <div className="app">
      <header>
        <div className="brand">
          <span className="brand-icon">◈</span>
          <div>
            <strong>资产管线</strong>
            <small>
              NODE EDITOR · {catalog.execution_enabled ? "B2" : "B1"}
            </small>
          </div>
        </div>
        <div className="identity">
          <input
            aria-label="管线名称"
            value={pipeline.pipeline}
            onChange={(e) => update({ ...pipeline, pipeline: e.target.value })}
          />
          <input
            className="version"
            aria-label="管线版本"
            value={pipeline.version}
            onChange={(e) => update({ ...pipeline, version: e.target.value })}
          />
        </div>
        <div className="toolbar">
          {catalog.execution_enabled && (
            <button
              aria-expanded={nodePreviewOpen}
              aria-controls="node-preview-window"
              onClick={() => setNodePreviewOpen((open) => !open)}
            >
              {nodePreviewOpen ? "收起节点预览" : "展开节点预览"}
            </button>
          )}
          <button onClick={() => input.current?.click()}>导入 YAML</button>
          <button
            onClick={() => {
              const url = URL.createObjectURL(
                new Blob([dump(pipeline)], { type: "application/yaml" }),
              );
              const a = document.createElement("a");
              a.href = url;
              a.download = `${pipeline.pipeline || "pipeline"}.yaml`;
              a.click();
              URL.revokeObjectURL(url);
            }}
          >
            导出 YAML
          </button>
          <button
            className="primary"
            disabled={busy}
            onClick={() => {
              setTab("plan");
              void compileCurrent(pipeline);
            }}
          >
            编译校验
          </button>
        </div>
      </header>
      {connectionIssue && (
        <div role="alert" className="connection-issue">
          <strong>连线未建立：</strong>
          {connectionIssue}
          <p>
            管线输入可在“配置 →
            输入类型与格式”中调整；实际数据格式不同需要显式转换节点。修改后重新连线，后端编译仍会校验。
          </p>
          <button onClick={() => setConnectionIssue("")}>关闭连线提示</button>
        </div>
      )}
      <div className="workspace">
        <aside className="catalog">
          <div className="section-label">节点目录</div>
          <input
            className="search"
            placeholder="搜索算子…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          />
          <label>
            <input
              type="checkbox"
              checked={registeredOnly}
              onChange={(e) => setRegisteredOnly(e.target.checked)}
            />
            只看已注册实现
          </label>
          <p>注册表示配置可绑定，不代表模型已验收。</p>
          <button
            className="add-input"
            onClick={() => {
              let name = "input";
              let i = 2;
              while (pipeline.inputs[name]) name = `input_${i++}`;
              update({
                ...pipeline,
                inputs: {
                  ...pipeline.inputs,
                  [name]: { kind: "rgb_image", carriers: ["artifact_ref"] },
                },
              });
              setSelected(inputId(name));
            }}
          >
            ＋ 管线输入
          </button>
          <div className="catalog-list" aria-label="可添加节点目录">
            <ModelServices
              catalog={catalog}
              onCatalog={(next) => {
                setCatalog(next);
                void compileCurrent(pipelineRef.current);
              }}
              onAddNode={(operator, backend) =>
                addOperator(operator, undefined, backend)
              }
            />
            {Object.entries(catalog.operators)
              .filter(
                ([key]) =>
                  key.toLowerCase().includes(filter.toLowerCase()) &&
                  (!registeredOnly ||
                    [...catalog.adapters, ...(catalog.backends || [])].some(
                      (adapter) => adapter.operators.includes(key),
                    )),
              )
              .map(([key, op]) => (
                <button
                  draggable
                  key={key}
                  className="catalog-item"
                  onDragStart={(e) => {
                    e.dataTransfer.setData("application/operator", key);
                    e.dataTransfer.effectAllowed = "move";
                  }}
                  onClick={() => addOperator(key)}
                >
                  <strong>{op.name}</strong>
                  <span>
                    {[...catalog.adapters, ...(catalog.backends || [])].some(
                      (adapter) => adapter.operators.includes(key),
                    )
                      ? "已注册实现"
                      : "仅契约 · 未配置实现"}
                  </span>
                  <span>
                    v{op.version} · {Object.keys(op.inputs).length} 输入 /{" "}
                    {Object.keys(op.outputs).length} 输出
                  </span>
                </button>
              ))}
            <div className="templates">
              <div className="section-label">示例管线</div>
              {catalog.templates.map((t) => (
                <button
                  key={t.id}
                  disabled={!!configurationBlockedReason}
                  onClick={() => {
                    if (
                      window.confirm("加载示例会替换当前未保存画布，继续？")
                    ) {
                      loadPipeline(t.pipeline);
                      setTab(catalog.execution_enabled ? "run" : "plan");
                    }
                  }}
                >
                  {t.label}
                  {t.execution_ready !== undefined && (
                    <small>
                      {
                        (
                          {
                            cpu: "CPU · 无需模型",
                            local: "本地模型",
                            remote: "远程服务",
                            unconfigured: "未配置实现",
                          } as Record<string, string>
                        )[t.execution_level || "unconfigured"]
                      }{" "}
                      · {t.execution_ready ? "配置可执行" : "暂不可运行"}
                    </small>
                  )}
                  {t.execution_reason && <small>{t.execution_reason}</small>}
                  {t.service_status && <small>{t.service_status}</small>}
                </button>
              ))}
            </div>
          </div>
        </aside>
        <main className="canvas">
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onEdgesChange={(changes) =>
              setSelectedEdges((old) => {
                const next = new Set(old);
                for (const change of changes) {
                  if (change.type === "select") {
                    if (change.selected) next.add(change.id);
                    else next.delete(change.id);
                  }
                }
                return next;
              })
            }
            nodeTypes={nodeTypes}
            onConnect={connect}
            onNodeClick={(_, n) => setSelected(n.id)}
            onPaneClick={() => setSelected(undefined)}
            onNodesChange={(changes) => {
              setNodes((previous) => applyNodeChanges(changes, previous));
              const removed = new Set(
                changes.filter((c) => c.type === "remove").map((c) => c.id),
              );
              if (removed.size) {
                update((previous) => removeNodes(previous, removed));
                setSelected((current) =>
                  current && removed.has(current) ? undefined : current,
                );
                setSelectedEdges(new Set());
                setLayout((old) =>
                  Object.fromEntries(
                    Object.entries(old).filter(([id]) => !removed.has(id)),
                  ),
                );
              }
            }}
            onEdgesDelete={(edges) => {
              update((previous) => {
                const next = structuredClone(previous);
                for (const e of edges)
                  if (e.targetHandle && next.nodes[e.target])
                    delete next.nodes[e.target].inputs[e.targetHandle];
                return next;
              });
              setSelectedEdges(new Set());
            }}
            onNodeDragStop={(_, moved, dragged) => {
              setLayout((old) => ({
                ...old,
                ...Object.fromEntries(
                  (dragged.length ? dragged : [moved]).map((n) => [
                    n.id,
                    n.position,
                  ]),
                ),
              }));
            }}
            onDragOver={(e) => {
              e.preventDefault();
              e.dataTransfer.dropEffect = "move";
            }}
            onDrop={(e) => {
              e.preventDefault();
              const serviceId = e.dataTransfer.getData(
                "application/model-service",
              );
              const service = catalog.model_services?.find(
                (item) => item.backend === serviceId,
              );
              if (service) {
                addOperator(
                  service.operator,
                  flow.screenToFlowPosition({ x: e.clientX, y: e.clientY }),
                  service.backend,
                );
                return;
              }
              const key = e.dataTransfer.getData("application/operator");
              if (catalog.operators[key])
                addOperator(
                  key,
                  flow.screenToFlowPosition({ x: e.clientX, y: e.clientY }),
                );
            }}
            fitView
            minZoom={0.15}
            maxZoom={2}
            deleteKeyCode={["Backspace", "Delete"]}
          >
            <Background gap={24} color="#dce3e9" />
            <Controls />
            <MiniMap pannable zoomable nodeColor="#bccddb" />
          </ReactFlow>
          {catalog.execution_enabled && (
            <div
              id="node-preview-window"
              className="node-preview-window"
              hidden={!nodePreviewOpen}
              ref={setPreviewHost}
            />
          )}
          {run && (
            <div
              role="status"
              aria-label="画布历史状态来源"
              style={{
                position: "absolute",
                top: 12,
                left: 12,
                zIndex: 5,
                background: "white",
                color: "#263747",
                padding: 10,
                maxWidth: "85%",
                overflowWrap: "anywhere",
              }}
            >
              画布徽标：导入的历史记录 {String(run.run_id || "未记录运行 ID")} ·
              仅按同名节点显示，不代表当前草稿已执行，也不代表预览来源。
              <button onClick={() => setRun(undefined)}>清除历史状态</button>
            </div>
          )}
          <div className="canvas-note">
            拖动连接端口 · Delete 删除节点 ·{" "}
            {catalog.execution_enabled ? "草稿与运行独立" : "不执行模型"}
          </div>
        </main>
        <aside className="inspector">
          <nav>
            <button
              className={tab === "inspector" ? "active" : ""}
              onClick={() => setTab("inspector")}
            >
              配置
            </button>
            <button
              className={tab === "plan" ? "active" : ""}
              onClick={() => setTab("plan")}
            >
              编译结果
            </button>
            {catalog.execution_enabled && (
              <button
                className={tab === "run" ? "active" : ""}
                onClick={() => setTab("run")}
              >
                运行
              </button>
            )}
          </nav>
          <p aria-label="当前配置编译状态" aria-live="polite">
            {compileMessage}
          </p>
          {configurationBlockedReason && (
            <section role="alert" aria-label="未应用参数">
              <p>{configurationBlockedReason}</p>
              {pendingParameterIds.map((id) => (
                <button
                  key={id}
                  onClick={() => {
                    setSelected(id);
                    setTab("inspector");
                  }}
                >
                  编辑待应用参数 · {id}
                </button>
              ))}
              <button onClick={() => setParameterDrafts({})}>
                放弃全部未应用参数编辑
              </button>
            </section>
          )}
          {catalog.execution_enabled && (
            <div hidden={tab !== "run"}>
              <ExecutionPanel
                pipeline={pipeline}
                configurationBlockedReason={configurationBlockedReason}
                selectedNode={selected}
                previewHost={previewHost}
                onLoadDraft={(draft) => {
                  if (
                    pipelineRef.current !== pipeline ||
                    layoutRef.current !== layout
                  )
                    throw Error(
                      "读取运行配置期间画布已修改，保留当前编辑；请重新载入。",
                    );
                  loadPipeline(draft);
                  setTab("plan");
                }}
                profile={catalog.execution_profile}
                executionReason={
                  compilePending
                    ? "正在检查当前配置…"
                    : !(result as { ok?: boolean } | undefined)?.ok
                      ? compileMessage
                      : (
                            result as {
                              execution_ready?: boolean;
                              execution_reason?: string;
                            }
                          ).execution_ready
                        ? undefined
                        : (result as { execution_reason?: string })
                            .execution_reason || "当前配置不适用于执行入口"
                }
              />
            </div>
          )}
          {tab === "run" ? null : tab === "plan" ? (
            <>
              <p>
                编译反馈为后端权威结果。静态契约通过不代表模型推理成功。修改图后自动重新编译；启动运行时后端再次校验。
              </p>
              {(
                (
                  result as
                    | {
                        diagnostics?: {
                          message: string;
                          node_id?: string;
                          port?: string;
                        }[];
                      }
                    | undefined
                )?.diagnostics || []
              ).map((item, index) => (
                <div key={index} className="run-node">
                  <p role="alert">{item.message}</p>
                  {item.port && <span>端口：{item.port}</span>}
                  {item.node_id &&
                    Object.hasOwn(pipeline.nodes, item.node_id) && (
                      <button
                        onClick={() => {
                          setSelected(item.node_id);
                          setTab("inspector");
                          void flow.fitView({
                            nodes: [{ id: item.node_id! }],
                            padding: 0.5,
                            maxZoom: 1,
                          });
                        }}
                      >
                        定位节点 · {item.node_id}
                      </button>
                    )}
                </div>
              ))}
              <pre>
                {result
                  ? JSON.stringify(result, null, 2)
                  : "尚无当前图的编译结果。"}
              </pre>
            </>
          ) : (
            <>
              <div className="section-label">草稿</div>
              <label>
                保存名称
                <input
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                />
              </label>
              <div className="row">
                <button
                  disabled={busy || !draft}
                  onClick={() =>
                    guarded(async () => {
                      await api(`/api/drafts/${encodeURIComponent(draft)}`, {
                        method: "PUT",
                        body: JSON.stringify({ pipeline, layout }),
                      });
                      setDrafts((await api("/api/drafts")).drafts);
                      setMessage(
                        pipelineRef.current !== pipeline ||
                          layoutRef.current !== layout
                          ? "已保存请求时的草稿；之后的编辑尚未保存。"
                          : "草稿已保存；未编译的草稿也可以保存。",
                      );
                    })
                  }
                >
                  保存草稿
                </button>
                <select
                  aria-label="加载草稿"
                  value=""
                  disabled={busy}
                  onChange={(e) => {
                    const name = e.target.value;
                    if (!name) return;
                    void guarded(async () => {
                      if (!window.confirm("加载草稿会替换当前画布，继续？"))
                        return;
                      const d = await api(
                        `/api/drafts/${encodeURIComponent(name)}`,
                      );
                      if (
                        pipelineRef.current !== pipeline ||
                        layoutRef.current !== layout
                      ) {
                        setMessage(
                          "加载期间画布已修改，保留当前编辑；请重新加载草稿。",
                        );
                        return;
                      }
                      loadPipeline(d.pipeline, d.layout);
                      setDraft(name);
                      setMessage("已加载草稿。");
                    });
                  }}
                >
                  <option value="">加载草稿…</option>
                  {drafts.map((n) => (
                    <option key={n}>{n}</option>
                  ))}
                </select>
              </div>
              <hr />
              {selected ? (
                <>
                  <div className="section-label">
                    {selected.startsWith("input:") ? "管线输入" : "节点实例"}
                  </div>
                  <label>
                    实例 ID
                    <input
                      key={selected}
                      defaultValue={selected.replace(/^input:/, "")}
                      onBlur={(e) => {
                        try {
                          const p = renameNode(
                            pipeline,
                            selected,
                            e.target.value,
                          );
                          const next = selected.startsWith("input:")
                            ? inputId(e.target.value)
                            : e.target.value;
                          setParameterDrafts((previous) => {
                            const nextDrafts = { ...previous };
                            if (next !== selected && previous[selected]) {
                              nextDrafts[next] = previous[selected];
                              delete nextDrafts[selected];
                            }
                            return nextDrafts;
                          });
                          update(p);
                          if (layout[selected])
                            setLayout({ ...layout, [next]: layout[selected] });
                          setSelected(next);
                        } catch (error) {
                          setMessage(String(error));
                          e.target.value = selected.replace(/^input:/, "");
                        }
                      }}
                    />
                  </label>
                  {node ? (
                    <>
                      <p>{node.operator}</p>
                      <label>
                        Adapter
                        <select
                          aria-label="Adapter"
                          disabled={pendingParameterIds.includes(selected)}
                          value={node.adapter || ""}
                          onChange={(e) =>
                            update({
                              ...pipeline,
                              nodes: {
                                ...pipeline.nodes,
                                [selected]: selectAdapter(
                                  node,
                                  e.target.value,
                                  catalog,
                                ),
                              },
                            })
                          }
                        >
                          <option value="">未指定 · 后端按唯一候选绑定</option>
                          {adapters.map((a) => (
                            <option key={`${a.name}@${a.version}`}>
                              {a.name}@{a.version}
                            </option>
                          ))}
                          {node.adapter &&
                            !adapters.some(
                              (a) => `${a.name}@${a.version}` === node.adapter,
                            ) && <option>{node.adapter}</option>}
                        </select>
                      </label>
                      <label>
                        Backend 配置
                        <select
                          disabled={pendingParameterIds.includes(selected)}
                          aria-label="节点 Backend"
                          value={node.backend || ""}
                          onChange={(e) =>
                            update((previous) => {
                              return {
                                ...previous,
                                nodes: {
                                  ...previous.nodes,
                                  [selected]: selectBackend(
                                    previous.nodes[selected],
                                    e.target.value,
                                    catalog,
                                  ),
                                },
                              };
                            })
                          }
                        >
                          <option value="">服务默认实现</option>
                          {[
                            ...new Set(
                              (catalog.backends || [])
                                .filter((b) =>
                                  b.operators.includes(node.operator),
                                )
                                .map((b) => b.backend),
                            ),
                          ].map((backend) => (
                            <option key={backend} value={backend}>
                              {backend}
                            </option>
                          ))}
                          {node.backend &&
                            !(catalog.backends || []).some(
                              (b) =>
                                b.backend === node.backend &&
                                b.operators.includes(node.operator),
                            ) && (
                              <option value={node.backend}>
                                {node.backend}（未安装或不兼容）
                              </option>
                            )}
                        </select>
                      </label>
                      <fieldset
                        disabled={selectedDraft.json !== undefined}
                        style={{ border: 0, padding: 0, margin: 0 }}
                      >
                        <ParameterForm
                          key={`${selected}:${node.adapter || ""}`}
                          adapter={selectedAdapter}
                          draft={selectedDraft.fields}
                          errors={selectedDraft.errors}
                          setDraft={(value) =>
                            changeParameterDraft(selected, (draft) => ({
                              ...draft,
                              fields:
                                typeof value === "function"
                                  ? value(draft.fields)
                                  : value,
                            }))
                          }
                          setErrors={(value) =>
                            changeParameterDraft(selected, (draft) => ({
                              ...draft,
                              errors:
                                typeof value === "function"
                                  ? value(draft.errors)
                                  : value,
                            }))
                          }
                          parameters={node.parameters || {}}
                          onChange={(value) =>
                            update((previous) => ({
                              ...previous,
                              nodes: {
                                ...previous.nodes,
                                [selected]: {
                                  ...previous.nodes[selected],
                                  parameters: value,
                                },
                              },
                            }))
                          }
                        />
                      </fieldset>
                      <label>
                        节点参数 · JSON
                        <textarea
                          spellCheck={false}
                          aria-label="节点参数 JSON"
                          value={parameters}
                          disabled={
                            Object.keys(selectedDraft.fields).length > 0
                          }
                          onChange={(e) =>
                            changeParameterDraft(selected, (draft) => ({
                              ...draft,
                              json: e.target.value,
                            }))
                          }
                        />
                      </label>
                      <button
                        disabled={
                          selectedDraft.json === undefined ||
                          Object.keys(selectedDraft.fields).length > 0
                        }
                        onClick={() => {
                          try {
                            const value = parseParameterObject(parameters);
                            if (
                              !value ||
                              typeof value !== "object" ||
                              Array.isArray(value)
                            )
                              throw Error("参数必须是 JSON 对象");
                            update({
                              ...pipeline,
                              nodes: {
                                ...pipeline.nodes,
                                [selected]: { ...node, parameters: value },
                              },
                            });
                            changeParameterDraft(selected, () =>
                              emptyParameterDraft(),
                            );
                            setMessage("参数已应用。");
                          } catch (e) {
                            setMessage(String(e));
                          }
                        }}
                      >
                        应用参数
                      </button>
                      <details>
                        <summary>参数契约</summary>
                        <pre>
                          {JSON.stringify(
                            selectedAdapter?.parameter_schema || {},
                            null,
                            2,
                          )}
                        </pre>
                      </details>
                      <details>
                        <summary>输入输出端口契约</summary>
                        <p>
                          以下来自当前
                          OperatorSpec。类型转换需要显式节点；多个输入的来源与空间关系还需后端校验。
                        </p>
                        {catalog.operators[node.operator] ? (
                          <pre>
                            {JSON.stringify(
                              {
                                inputs: catalog.operators[node.operator].inputs,
                                outputs:
                                  catalog.operators[node.operator].outputs,
                              },
                              null,
                              2,
                            )}
                          </pre>
                        ) : (
                          <p role="alert">
                            当前目录没有此 Operator 的端口契约。
                          </p>
                        )}
                      </details>
                      <details>
                        <summary>输入绑定与关系</summary>
                        <pre>
                          {JSON.stringify(
                            {
                              inputs: node.inputs,
                              relations:
                                catalog.operators[node.operator]?.relations ||
                                [],
                            },
                            null,
                            2,
                          )}
                        </pre>
                      </details>
                    </>
                  ) : (
                    <>
                      {selectedInput && selectedInputName && (
                        <InputContractForm
                          port={selectedInput}
                          catalog={catalog}
                          onChange={(value) => {
                            update((previous) => ({
                              ...previous,
                              inputs: {
                                ...previous.inputs,
                                [selectedInputName]: value,
                              },
                            }));
                          }}
                        />
                      )}
                      <label>
                        输入契约 · JSON（高级）
                        <textarea
                          aria-label="输入契约 · JSON"
                          value={inputSpec}
                          onChange={(e) => setInputSpec(e.target.value)}
                        />
                      </label>
                      <button
                        onClick={() => {
                          try {
                            const value = JSON.parse(inputSpec);
                            validateInputPort(value, selected.slice(6));
                            update({
                              ...pipeline,
                              inputs: {
                                ...pipeline.inputs,
                                [selected.slice(6)]: value,
                              },
                            });
                            setMessage("输入契约已更新。");
                          } catch (e) {
                            setMessage(String(e));
                          }
                        }}
                      >
                        应用输入契约
                      </button>
                    </>
                  )}
                  {node && (
                    <button
                      onClick={() => {
                        const copied = duplicateNode(
                          pipelineRef.current,
                          selected,
                        );
                        const position = nodes.find(
                          (item) => item.id === selected,
                        )?.position || { x: 0, y: 0 };
                        update(copied.pipeline);
                        setLayout((previous) => ({
                          ...previous,
                          [copied.id]: {
                            x: position.x + 80,
                            y: position.y + 100,
                          },
                        }));
                        setSelected(copied.id);
                        setMessage(
                          "已复制已应用配置及上游连接；下游连接未变，尚未执行。",
                        );
                      }}
                    >
                      复制节点配置
                    </button>
                  )}
                  <button
                    className="danger"
                    onClick={() => {
                      update(removeNodes(pipeline, new Set([selected])));
                      setSelected(undefined);
                    }}
                  >
                    删除所选节点
                  </button>
                </>
              ) : (
                <p>
                  选择一个节点查看端口、实现绑定和参数。同一算子可以拖入多次，每个实例独立配置。
                </p>
              )}
              <hr />
              <div className="section-label">历史状态 · 只读</div>
              <button onClick={() => runInput.current?.click()}>
                打开 BuildRun JSON
              </button>
              <p>
                {run
                  ? "已显示同名节点的历史状态；不表示当前草稿已执行。"
                  : "可导入运行记录观察节点状态，不影响草稿。"}
              </p>
              {run && (
                <button onClick={() => setRun(undefined)}>清除历史状态</button>
              )}
            </>
          )}
        </aside>
      </div>
      <footer role="status">
        <span className="dot" />
        {busy ? "处理中…" : message}
      </footer>
      <input
        hidden
        type="file"
        accept=".yaml,.yml"
        ref={input}
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file)
            void guarded(async () => {
              const p = validateDocument(load(await file.text()));
              if (
                pipelineRef.current !== pipeline ||
                layoutRef.current !== layout
              ) {
                setMessage(
                  "读取 YAML 期间画布已修改，保留当前编辑；请重新导入。",
                );
                return;
              }
              loadPipeline(p);
              setMessage("YAML 已导入，请编译校验。");
            });
          e.target.value = "";
        }}
      />
      <input
        hidden
        type="file"
        accept=".json"
        ref={runInput}
        onChange={(e) => {
          const file = e.target.files?.[0];
          if (file)
            void guarded(async () => {
              const value = JSON.parse(await file.text());
              if (!value?.dag?.node_states)
                throw Error("需要包含 dag.node_states 的 BuildRun");
              setRun(value);
              setMessage("历史运行状态已加载，仅供参考。");
            });
          e.target.value = "";
        }}
      />
    </div>
  );
}
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ReactFlowProvider>
      <App />
    </ReactFlowProvider>
  </React.StrictMode>,
);

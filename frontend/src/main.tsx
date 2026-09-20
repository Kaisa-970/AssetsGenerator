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
  connectionError,
  bind,
  removeNodes,
  renameNode,
  duplicateNode,
  validateDocument,
  selectBackend,
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
        {data.status && <em>{data.status}</em>}
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
            </small>
          </div>
        ))}
        {Object.entries(data.outputs).map(([name, p]) => (
          <div className="port out" key={name}>
            <Handle type="source" position={Position.Right} id={name} />
            <b>{name}</b>
            <small>
              {portKinds(p).join(" | ")} · {p.cardinality || "one"}
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
  const [message, setMessage] = useState("正在读取节点目录…");
  const [draft, setDraft] = useState("my-pipeline");
  const [drafts, setDrafts] = useState<string[]>([]);
  const [result, setResult] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  const [filter, setFilter] = useState("");
  const [registeredOnly, setRegisteredOnly] = useState(false);
  const [parameters, setParameters] = useState("{}");
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
      setResult(undefined);
    },
    [],
  );
  useEffect(() => {
    Promise.all([api("/api/catalog"), api("/api/drafts")])
      .then(([c, d]) => {
        setCatalog(c);
        setDrafts(d.drafts);
        setMessage("拖入算子并连接端口，完成后点击编译校验。");
      })
      .catch((e) => setMessage(String(e)));
  }, []);
  useEffect(() => {
    setParameters(
      JSON.stringify(
        selected ? pipeline.nodes[selected]?.parameters || {} : {},
        null,
        2,
      ),
    );
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
    update(validateDocument(p));
    setLayout(l);
    setSelected(undefined);
    setRun(undefined);
    setTimeout(() => flow.fitView({ padding: 0.18 }), 50);
  };
  const addOperator = (key: string, position?: { x: number; y: number }) => {
    const base = key.split("@")[0];
    let id = base;
    let i = 2;
    while (pipeline.nodes[id]) id = `${base}_${i++}`;
    const candidates = catalog.adapters.filter((a) =>
      a.operators.includes(key),
    );
    const adapter = candidates.length === 1 ? candidates[0] : undefined;
    update({
      ...pipeline,
      nodes: {
        ...pipeline.nodes,
        [id]: {
          operator: key,
          ...(adapter
            ? {
                adapter: `${adapter.name}@${adapter.version}`,
                parameters: adapter.defaults || {},
              }
            : {}),
          inputs: {},
        },
      },
    });
    setLayout({
      ...layout,
      [id]:
        position ||
        flow.screenToFlowPosition({
          x: window.innerWidth / 2,
          y: window.innerHeight / 2,
        }),
    });
    setSelected(id);
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
      return;
    }
    update(bind(pipeline, c.source, c.sourceHandle, c.target, c.targetHandle));
    setMessage("已连接。跨输入来源与空间关系由后端编译及运行校验。");
  };
  const states = (
    run?.dag as { node_states?: Record<string, { status: string }> } | undefined
  )?.node_states;
  const declaredNodes = useMemo<Node<Data>[]>(
    () => [
      ...Object.entries(pipeline.inputs).map(([id, p], i) => ({
        id: inputId(id),
        type: "operator",
        position: layout[inputId(id)] || { x: 30, y: 80 + i * 200 },
        selected: selected === inputId(id),
        data: {
          label: id,
          operator: "Pipeline input",
          inputs: {},
          outputs: { value: p },
        },
      })),
      ...Object.entries(pipeline.nodes).map(([id, n], i) => ({
        id,
        type: "operator",
        position: layout[id] || {
          x: 360 + (i % 3) * 340,
          y: 80 + Math.floor(i / 3) * 330,
        },
        selected: selected === id,
        data: {
          label: id,
          operator: n.operator,
          inputs: catalog.operators[n.operator]?.inputs || {},
          outputs: catalog.operators[n.operator]?.outputs || {},
          status: states?.[id]?.status,
        },
      })),
    ],
    [pipeline, layout, selected, catalog, states],
  );
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
  const selectedAdapter = node?.backend
    ? catalog.backends?.find(
        (b) => b.backend === node.backend && b.adapter === node.adapter,
      )
    : adapters.find((a) => `${a.name}@${a.version}` === node?.adapter);
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
            onClick={() =>
              guarded(async () => {
                const r = await api("/api/compile", {
                  method: "POST",
                  body: JSON.stringify({ pipeline }),
                });
                if (pipelineRef.current !== pipeline) {
                  setMessage("图已修改，忽略旧版本的编译结果；请重新编译。");
                  return;
                }
                setResult(r);
                setTab("plan");
                setMessage(
                  r.ok
                    ? catalog.execution_enabled
                      ? r.execution_ready
                        ? "编译通过；可在运行页创建新运行。"
                        : `编译通过；当前入口不可运行：${r.execution_reason || "执行条件未满足"}`
                      : "编译通过；此页面不启动推理。"
                    : "编译未通过，请查看诊断。",
                );
              })
            }
          >
            编译校验
          </button>
        </div>
      </header>
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
          <div className="catalog-list">
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
          </div>
          <div className="templates">
            <div className="section-label">示例管线</div>
            {catalog.templates.map((t) => (
              <button
                key={t.id}
                onClick={() => {
                  if (window.confirm("加载示例会替换当前未保存画布，继续？"))
                    loadPipeline(t.pipeline);
                }}
              >
                {t.label}
              </button>
            ))}
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
          {catalog.execution_enabled && (
            <div hidden={tab !== "run"}>
              <ExecutionPanel
                pipeline={pipeline}
                onLoadDraft={(draft) => {
                  loadPipeline(draft);
                  setTab("plan");
                }}
                profile={catalog.execution_profile}
                executionReason={
                  (result as { execution_reason?: string } | undefined)
                    ?.execution_reason
                }
              />
            </div>
          )}
          {tab === "run" ? null : tab === "plan" ? (
            <>
              <p>
                编译反馈为后端权威结果。静态契约通过不代表模型推理成功。修改图后需要重新编译；启动运行时后端再次校验。
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
                      setMessage("草稿已保存；未编译的草稿也可以保存。");
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
                          value={node.adapter || ""}
                          onChange={(e) =>
                            update({
                              ...pipeline,
                              nodes: {
                                ...pipeline.nodes,
                                [selected]: {
                                  ...node,
                                  adapter: e.target.value || undefined,
                                },
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
                      <ParameterForm
                        key={`${selected}:${node.adapter || ""}`}
                        adapter={selectedAdapter}
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
                      <label>
                        节点参数 · JSON
                        <textarea
                          spellCheck={false}
                          value={parameters}
                          onChange={(e) => setParameters(e.target.value)}
                        />
                      </label>
                      <button
                        onClick={() => {
                          try {
                            const value = JSON.parse(parameters);
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
                      <label>
                        输入契约 · JSON
                        <textarea
                          value={inputSpec}
                          onChange={(e) => setInputSpec(e.target.value)}
                        />
                      </label>
                      <button
                        onClick={() => {
                          try {
                            const value = JSON.parse(inputSpec);
                            if (!value || !portKinds(value).length)
                              throw Error("输入契约需要 kind");
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

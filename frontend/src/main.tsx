import { NodePaletteDialog } from "./NodePaletteDialog";
import { PanelResizeHandle } from "./PanelResizeHandle";
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
  useReactFlow,
  type Connection,
  type Node,
} from "@xyflow/react";
import { load, dump } from "js-yaml";
import {
  type Catalog,
  backendLabel,
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
import {
  BlueprintNode,
  blueprintBindingLabel,
  blueprintKindLabel,
  readableOperatorLabel,
  type BlueprintNodeData,
} from "./BlueprintNode";
import { HistoryResizeHandle } from "./HistoryResizeHandle";
import { executionFailureSummary, type NodeState } from "./executionApi";
type Data = BlueprintNodeData;
type QuickInputKind = "rgb" | "rgba" | "mask" | "text";
const quickInputDefinitions: Record<
  QuickInputKind,
  {
    label: string;
    detail: string;
    name: string;
    kinds: string[];
    carriers: string[];
  }
> = {
  rgb: {
    label: "图片输入",
    detail: "RGB 图片 · 上传或选择已有图片",
    name: "image",
    kinds: ["rgb_image"],
    carriers: ["artifact_ref"],
  },
  rgba: {
    label: "透明图片输入",
    detail: "RGBA 图片 · 带透明区域",
    name: "image",
    kinds: ["rgba_image"],
    carriers: ["artifact_ref"],
  },
  mask: {
    label: "遮罩输入",
    detail: "二值遮罩 · 作为处理节点输入",
    name: "mask",
    kinds: ["binary_mask"],
    carriers: ["artifact_ref"],
  },
  text: {
    label: "文字输入",
    detail: "文本提示 · 连接到文字分割节点",
    name: "text",
    kinds: ["text"],
    carriers: ["structured"],
  },
};
const nodeTypes = { operator: BlueprintNode };
function InputHost({
  name,
  register,
}: {
  name: string;
  register: (name: string, element: HTMLDivElement | null) => void;
}) {
  const ref = useCallback(
    (element: HTMLDivElement | null) => register(name, element),
    [name, register],
  );
  return <div ref={ref} aria-label={`输入 ${name} 的值`} />;
}
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
  const [workspaceRevision, setWorkspaceRevision] = useState(0);
  const [layout, setLayout] = useState<Layout>({});
  const [selected, setSelected] = useState<string>();
  const [selectedEdges, setSelectedEdges] = useState<Set<string>>(new Set());
  const pipelineRef = useRef(pipeline);
  pipelineRef.current = pipeline;
  const layoutRef = useRef(layout);
  layoutRef.current = layout;
  const [connectionIssue, setConnectionIssue] = useState("");
  const [message, setMessage] = useState("正在读取节点目录…");
  const [pendingLoad, setPendingLoad] = useState<
    | {
        pipeline: Pipeline;
        layout?: Layout;
        label: string;
        historicalInputs?: {
          run_id: string;
          snapshot_ref: { artifact_id: string };
          inputs: Record<
            string,
            { artifact_id: string; identity?: Record<string, unknown> }
          >;
        };
      }
    | undefined
  >();
  const [draft, setDraft] = useState("my-pipeline");
  const [drafts, setDrafts] = useState<string[]>([]);
  const [historicalInputSource, setHistoricalInputSource] = useState<{
    run_id: string;
    snapshot_ref: { artifact_id: string };
    inputs: Record<
      string,
      { artifact_id: string; identity?: Record<string, unknown> }
    >;
  }>();
  const draftToolsRef = useRef<HTMLDivElement>(null);
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
  const [catalogOpen, setCatalogOpen] = useState(true);
  const [nodePaletteOpen, setNodePaletteOpen] = useState(false);
  const [nodePaletteFilter, setNodePaletteFilter] = useState("");
  const [nodePalettePosition, setNodePalettePosition] = useState<
    { x: number; y: number } | undefined
  >();
  const nodePaletteTrigger = useRef<HTMLButtonElement>(null);
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [inspectorWidth, setInspectorWidth] = useState(280);
  // Keep the canvas as the primary workspace; previews open on demand from
  // the toolbar or a node's output action.
  const [nodePreviewOpen, setNodePreviewOpen] = useState(false);
  const [inputHosts, setInputHosts] = useState<Record<string, HTMLElement>>({});
  const [controlsHost, setControlsHost] = useState<HTMLDivElement | null>(null);
  const registerInputHost = useCallback(
    (name: string, element: HTMLDivElement | null) => {
      setInputHosts((previous) => {
        if (previous[name] === element || (!element && !previous[name]))
          return previous;
        const next = { ...previous };
        if (element) next[name] = element;
        else delete next[name];
        return next;
      });
    },
    [],
  );
  const [previewHost, setPreviewHost] = useState<HTMLDivElement | null>(null);
  const [parameterDrafts, setParameterDrafts] = useState<
    Record<string, ParameterDraft>
  >({});
  const parameterDraftsRef = useRef(parameterDrafts);
  parameterDraftsRef.current = parameterDrafts;
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
  const inspectorRef = useRef<HTMLElement>(null);
  useEffect(() => {
    if (tab === "inspector") inspectorRef.current?.scrollTo({ top: 0 });
  }, [selected, tab]);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyHeight, setHistoryHeight] = useState(300);
  const [minimapOpen, setMinimapOpen] = useState(false);
  const [panelRestore, setPanelRestore] = useState<{
    catalog: boolean;
    inspector: boolean;
    preview: boolean;
    history: boolean;
  }>();
  const [runView, setRunView] = useState<{
    runId: string;
    status: string;
    outputs: { node_id: string; port: string; kind?: string }[];
    states: Record<string, { status: string; failureSummary?: string }>;
  }>();
  const [nodeFreshness, setNodeFreshness] = useState<Record<string, string>>(
    {},
  );
  const [run, setRun] = useState<Record<string, unknown>>();
  const input = useRef<HTMLInputElement>(null);
  const runInput = useRef<HTMLInputElement>(null);
  const flow = useReactFlow();
  const focusNode = useCallback(
    (id: string) => {
      // Selection can change the node and panel dimensions. Measure after React
      // and ResizeObserver have committed those changes, then fit the whole card.
      // A fixed 0.75 zoom floor clips tall cards while the preview is expanded.
      requestAnimationFrame(() =>
        requestAnimationFrame(() => {
          void flow.fitView({
            nodes: [{ id }],
            padding: {
              top: "40px",
              bottom: "24px",
              left: "24px",
              right: "24px",
            },
            minZoom: 0.15,
            maxZoom: 1,
          });
        }),
      );
    },
    [flow],
  );
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
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const editing =
        target?.isContentEditable ||
        target?.tagName === "INPUT" ||
        target?.tagName === "TEXTAREA" ||
        target?.tagName === "SELECT";
      if (event.key === "Escape" && nodePaletteOpen) {
        event.preventDefault();
        setNodePaletteOpen(false);
        return;
      }
      if (
        event.shiftKey &&
        (event.key.toLowerCase() === "a" || event.code === "KeyA") &&
        !editing
      ) {
        event.preventDefault();
        openNodePalette();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [nodePaletteOpen]);
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
    setWorkspaceRevision((revision) => revision + 1);
    setParameterDrafts({});
    setLayout(l);
    const firstInput = Object.keys(p.inputs)[0];
    setSelected(firstInput ? inputId(firstInput) : undefined);
    setRun(undefined);
    setTimeout(() => {
      // Open at the first editable step. Fitting every input/downstream node
      // makes multi-input templates too small to operate; overview is explicit.
      const focusId = firstInput
        ? inputId(firstInput)
        : Object.keys(p.nodes)[0];
      if (focusId) focusNode(focusId);
    }, 80);
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
    const existingNodes = flow.getNodes();
    const selectedNode = selected
      ? existingNodes.find((item) => item.id === selected)
      : undefined;
    const placed = [
      ...existingNodes.map((item) => item.position),
      ...Object.values(layoutRef.current),
    ];
    const rightmost = placed.reduce(
      (maximum, item) => Math.max(maximum, item.x),
      0,
    );
    const fallbackPosition = selectedNode
      ? {
          x:
            selectedNode.position.x +
            (selectedNode.measured?.width || 340) +
            80,
          y: Math.max(80, selectedNode.position.y),
        }
      : {
          x: Math.max(
            rightmost + 420,
            flow.screenToFlowPosition({
              x: window.innerWidth / 2,
              y: Math.max(220, window.innerHeight / 2 - 150),
            }).x,
          ),
          y: flow.screenToFlowPosition({
            x: window.innerWidth / 2,
            y: Math.max(220, window.innerHeight / 2 - 150),
          }).y,
        };
    update((previous) => ({
      ...previous,
      nodes: {
        ...previous.nodes,
        [id]: {
          operator: key,
          ...(adapter
            ? {
                ...(backend
                  ? { backend }
                  : { adapter: `${adapter.name}@${adapter.version}` }),
                parameters: {},
              }
            : {}),
          inputs: {},
        },
      },
    }));
    const nextLayout = {
      ...layoutRef.current,
      [id]: position || fallbackPosition,
    };
    layoutRef.current = nextLayout;
    setLayout(nextLayout);
    setSelected(id);
    // Adding a node is an editing action. Bring the new node into the
    // workbench viewport so its model selector and primary parameters are
    // immediately usable instead of leaving the node below the fold.
    window.setTimeout(() => {
      focusNode(id);
    }, 0);
  };
  const availableQuickInputs = useMemo(() => {
    const result = new Map<QuickInputKind, { name: string; port: Port }>();
    for (const operator of Object.values(catalog.operators)) {
      for (const port of Object.values(operator.inputs)) {
        const kinds = port.kinds || (port.kind ? [port.kind] : []);
        if (port.cardinality && port.cardinality !== "one") continue;
        for (const kind of Object.keys(
          quickInputDefinitions,
        ) as QuickInputKind[]) {
          if (
            !result.has(kind) &&
            (!port.carriers ||
              port.carriers.some((carrier) =>
                quickInputDefinitions[kind].carriers.includes(carrier),
              )) &&
            kinds.some((value) =>
              quickInputDefinitions[kind].kinds.includes(value),
            )
          ) {
            result.set(kind, {
              name: quickInputDefinitions[kind].name,
              port: structuredClone(port),
            });
          }
        }
      }
    }
    return result;
  }, [catalog.operators]);
  const addInputNode = (kind: QuickInputKind) => {
    const choice = availableQuickInputs.get(kind);
    if (!choice?.port) {
      setMessage("当前目录没有这个输入类型的契约。");
      return;
    }
    let name = choice.name,
      i = 2;
    while (pipelineRef.current.inputs[name]) name = `${choice.name}_${i++}`;
    update((previous) => ({
      ...previous,
      inputs: {
        ...previous.inputs,
        [name]: structuredClone(choice.port!),
      },
    }));
    setSelected(inputId(name));
    setNodePaletteOpen(false);
    const selectedNode = selected
      ? flow.getNodes().find((item) => item.id === selected)
      : undefined;
    const nextLayout = {
      ...layoutRef.current,
      [inputId(name)]: selectedNode
        ? {
            x: selectedNode.position.x - 420,
            y: Math.max(80, selectedNode.position.y),
          }
        : flow.screenToFlowPosition({
            x: Math.max(180, window.innerWidth / 2 - 420),
            y: Math.max(220, window.innerHeight / 2 - 150),
          }),
    };
    layoutRef.current = nextLayout;
    setLayout(nextLayout);
  };
  const openNodePalette = (position?: { x: number; y: number }) => {
    setNodePalettePosition(position);
    setNodePaletteFilter("");
    setNodePaletteOpen(true);
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
        setInspectorOpen(true);
        setSelected(c.source);
        setTab("inspector");
      }
      return;
    }
    setConnectionIssue("");
    const previousBinding = pipeline.nodes[c.target]?.inputs[c.targetHandle];
    update(bind(pipeline, c.source, c.sourceHandle, c.target, c.targetHandle));
    setMessage(
      `${previousBinding ? `已替换 ${c.target}.${c.targetHandle} 的来源（原为 ${blueprintBindingLabel(previousBinding)}）` : "已连接"}。跨输入来源与空间关系由后端编译及运行校验。`,
    );
  };
  const selectedInputName = selected?.startsWith("input:")
    ? selected.slice(6)
    : undefined;
  const selectedInput = selectedInputName
    ? pipeline.inputs[selectedInputName]
    : undefined;
  const states =
    runView?.states ||
    (
      run?.dag as
        | {
            node_states?: Record<
              string,
              { status: string; failureSummary?: string }
            >;
          }
        | undefined
    )?.node_states;
  const declaredNodes = useMemo<Node<Data>[]>(() => {
    const positions = dependencyLayout(pipeline);
    return [
      ...Object.entries(pipeline.inputs).map(([id, p]) => ({
        id: inputId(id),
        type: "operator",
        dragHandle: ".blueprint-node-drag-handle",
        position: layout[inputId(id)] || positions[inputId(id)],
        selected: selected === inputId(id),
        data: {
          nodeId: inputId(id),
          label: id,
          inputSlot: <InputHost name={id} register={registerInputHost} />,
          operator: "Pipeline input",
          inputs: {},
          outputs: { value: p },
        },
      })),
      ...Object.entries(pipeline.nodes).map(([id, n]) => ({
        id,
        type: "operator",
        dragHandle: ".blueprint-node-drag-handle",
        position: layout[id] || positions[id],
        selected: selected === id,
        data: {
          nodeId: id,
          label: id,
          operatorLabel: readableOperatorLabel(
            catalog.operators[n.operator]?.name || n.operator,
          ),
          parameterSchema: (() => {
            const candidates = (
              n.backend
                ? (catalog.backends || []).filter(
                    (a) => a.backend === n.backend,
                  )
                : catalog.adapters
            ).filter((a) => a.operators.includes(n.operator));
            return n.adapter
              ? candidates.find((a) => `${a.name}@${a.version}` === n.adapter)
              : candidates.length === 1
                ? candidates[0]
                : undefined;
          })(),
          parameters: n.parameters || {},
          drafts: parameterDrafts[id]?.fields || {},
          errors: parameterDrafts[id]?.errors || {},
          configurationBlockedReason:
            parameterDrafts[id]?.json !== undefined
              ? "高级 JSON 尚未应用，请在属性中应用或放弃。"
              : undefined,
          setDraft: (value: React.SetStateAction<Record<string, string>>) =>
            changeParameterDraft(id, (draft) => ({
              ...draft,
              fields: typeof value === "function" ? value(draft.fields) : value,
            })),
          setErrors: (value: React.SetStateAction<Record<string, string>>) =>
            changeParameterDraft(id, (draft) => ({
              ...draft,
              errors: typeof value === "function" ? value(draft.errors) : value,
            })),
          onParametersChange: (value: Record<string, unknown>) =>
            update((previous) => ({
              ...previous,
              nodes: {
                ...previous.nodes,
                [id]: { ...previous.nodes[id], parameters: value },
              },
            })),
          backend: n.backend,
          adapterId: n.adapter,
          adapters: (n.backend
            ? (catalog.backends || []).filter((a) => a.backend === n.backend)
            : catalog.adapters
          )
            .filter((a) => a.operators.includes(n.operator))
            .map((a) => ({
              value: `${a.name}@${a.version}`,
              label: `${a.name}@${a.version}`,
            })),
          onAdapterChange: (value: string) =>
            update((previous) => ({
              ...previous,
              nodes: {
                ...previous.nodes,
                [id]: selectAdapter(previous.nodes[id], value, catalog),
              },
            })),
          backends: [
            ...new Set(
              (catalog.backends || [])
                .filter((b) => b.operators.includes(n.operator))
                .map((b) => b.backend),
            ),
          ].map((backend) => ({
            value: backend,
            label: backendLabel(catalog, backend),
          })),
          onBackendChange: (catalog.backends || []).some((b) =>
            b.operators.includes(n.operator),
          )
            ? (value: string) =>
                update((previous) => ({
                  ...previous,
                  nodes: {
                    ...previous.nodes,
                    [id]: selectBackend(previous.nodes[id], value, catalog),
                  },
                }))
            : undefined,
          operator: n.operator,
          inputs: catalog.operators[n.operator]?.inputs || {},
          outputs: catalog.operators[n.operator]?.outputs || {},
          inputBindings: n.inputs || {},
          status: states?.[id]?.status,
          failureSummary: states?.[id]?.failureSummary,
          statusSource:
            runView?.runId || (run?.run_id ? String(run.run_id) : undefined),
          freshness: nodeFreshness[id],
          outputSummary: runView?.outputs.filter(
            (output) => output.node_id === id,
          ),
          onViewOutputs: runView
            ? () => {
                setSelected(id);
                setNodePreviewOpen(true);
                setMessage(
                  `正在查看节点 ${id} 的输出；结果来自运行 ${runView.runId}。`,
                );
              }
            : undefined,
        },
      })),
    ];
  }, [
    pipeline,
    layout,
    selected,
    catalog,
    states,
    parameterDrafts,
    runView?.runId,
    run?.run_id,
    runView?.outputs,
    nodeFreshness,
    registerInputHost,
    update,
  ]);
  const [nodes, setNodes] = useState<Node<Data>[]>([]);
  useEffect(() => {
    setNodes((previous) => {
      const existing = new Map(previous.map((node) => [node.id, node]));
      return declaredNodes.map((node) => ({
        ...existing.get(node.id),
        ...node,
      }));
    });
  }, [declaredNodes]);
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
  const visibleOperators = Object.entries(catalog.operators).filter(
    ([key, op]) =>
      [
        key,
        op.name,
        readableOperatorLabel(op.name),
        ...Object.values(op.inputs).map(blueprintKindLabel),
        ...Object.values(op.outputs).map(blueprintKindLabel),
      ]
        .join(" ")
        .toLowerCase()
        .includes(filter.trim().toLowerCase()) &&
      (!registeredOnly ||
        [...catalog.adapters, ...(catalog.backends || [])].some((adapter) =>
          adapter.operators.includes(key),
        )),
  );
  const paletteQuery = nodePaletteFilter.trim().toLowerCase();
  const paletteOperators = Object.entries(catalog.operators).filter(
    ([key, op]) =>
      [key, op.name, readableOperatorLabel(op.name)]
        .join(" ")
        .toLowerCase()
        .includes(paletteQuery),
  );
  const paletteServices = (catalog.model_services || []).flatMap((service) => {
    const capabilities = service.capabilities?.length
      ? service.capabilities
      : [
          {
            operator: service.operator,
            display_name: service.display_name,
            unit: service.unit,
          },
        ];
    return capabilities
      .map((capability, index) => ({ service, capability, index }))
      .filter(({ service, capability }) =>
        [
          service.backend,
          service.display_name,
          service.operator,
          capability.capability_id,
          capability.operator,
          capability.display_name,
        ]
          .join(" ")
          .toLowerCase()
          .includes(paletteQuery),
      );
  });
  const paletteInputs = [...availableQuickInputs.keys()]
    .map((kind) => ({
      kind,
      ...quickInputDefinitions[kind],
    }))
    .filter(({ kind, label, detail }) =>
      [kind, label, detail].join(" ").toLowerCase().includes(paletteQuery),
    );
  return (
    <div className="app">
      {pendingLoad && (
        <div
          className="load-confirm"
          role="alertdialog"
          aria-label="确认替换画布"
        >
          <strong>{pendingLoad.label}</strong>
          <p>
            这会替换当前画布、节点位置和参数，并清空当前实际输入。替换后需要重新提供图片、文字等输入；取消会保留当前内容。
          </p>
          <button
            onClick={() => {
              try {
                loadPipeline(pendingLoad.pipeline, pendingLoad.layout);
                setHistoricalInputSource(pendingLoad.historicalInputs);
                setTab("inspector");
                setHistoryOpen(false);
                setMessage(`已${pendingLoad.label}。`);
                setPendingLoad(undefined);
              } catch (error) {
                setMessage(String(error));
              }
            }}
          >
            继续替换
          </button>
          <button onClick={() => setPendingLoad(undefined)}>取消</button>
        </div>
      )}
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
          <button
            disabled={!!configurationBlockedReason}
            onClick={() => {
              const nextPipeline = {
                pipeline: "my_asset_pipeline",
                version: "1",
                inputs: {},
                nodes: {},
              } satisfies Pipeline;
              setHistoricalInputSource(undefined);
              setPendingLoad({
                label: "新建空白管线",
                pipeline: nextPipeline,
              });
            }}
          >
            新建空白
          </button>
          <button
            onClick={() => {
              setInspectorOpen(true);
              setTab("inspector");
              requestAnimationFrame(() =>
                requestAnimationFrame(() => {
                  draftToolsRef.current?.scrollIntoView({ block: "start" });
                }),
              );
            }}
          >
            保存 / 加载
          </button>
          <button onClick={() => input.current?.click()}>导入 YAML</button>
          <button
            ref={nodePaletteTrigger}
            className="add-node-toolbar"
            onClick={() => openNodePalette()}
            aria-haspopup="dialog"
          >
            ＋ 添加节点
          </button>
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
              setInspectorOpen(true);
              setTab("plan");
              void compileCurrent(pipeline);
            }}
          >
            编译校验
          </button>
        </div>
      </header>
      {catalog.execution_enabled && (
        <div
          className="execution-toolbar"
          aria-label="运行工具栏"
          ref={setControlsHost}
        />
      )}
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
      {nodePaletteOpen && (
        <NodePaletteDialog
          onClose={() => {
            setNodePaletteOpen(false);
            requestAnimationFrame(() => nodePaletteTrigger.current?.focus());
          }}
        >
          <section
            className="node-palette"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <header>
              <div>
                <strong>添加节点</strong>
                <p>
                  选择一个节点，它会直接加入当前画布。数据端口仍通过拖线连接。
                </p>
              </div>
              <button
                aria-label="关闭添加节点"
                onClick={() => setNodePaletteOpen(false)}
              >
                ×
              </button>
            </header>
            <input
              autoFocus
              aria-label="搜索节点"
              placeholder="搜索图片、文字分割、生成网格…"
              value={nodePaletteFilter}
              onChange={(event) => setNodePaletteFilter(event.target.value)}
            />
            <div className="node-palette-grid">
              <div className="node-palette-section">
                <h3>输入</h3>
                {paletteInputs.map(({ kind, label, detail }) => (
                  <button key={kind} onClick={() => addInputNode(kind)}>
                    <strong>{label}</strong>
                    <span>{detail}</span>
                  </button>
                ))}
                {!paletteInputs.length && <p>没有匹配的输入类型。</p>}
              </div>
              <div className="node-palette-section">
                <h3>通用处理算子</h3>
                <p className="node-palette-section-note">
                  添加后在节点内选择模型与参数
                </p>
                {paletteOperators.map(([key, op]) => (
                  <button
                    key={key}
                    onClick={() => {
                      addOperator(key, nodePalettePosition);
                      setNodePaletteOpen(false);
                    }}
                  >
                    <strong>{readableOperatorLabel(op.name)}</strong>
                    <span>
                      {op.name} · {Object.keys(op.inputs).length} 输入 /{" "}
                      {Object.keys(op.outputs).length} 输出
                    </span>
                  </button>
                ))}
                {!paletteOperators.length && <p>没有匹配的处理算子。</p>}
              </div>
              <div className="node-palette-section">
                <h3>已配置模型服务</h3>
                <p className="node-palette-section-note">
                  添加后已预选 Backend
                </p>
                {paletteServices.map(({ service, capability, index }) => (
                  <button
                    key={`${service.backend}:${capability.capability_id || capability.operator || index}`}
                    disabled={!(capability.operator || service.operator)}
                    onClick={() => {
                      const operator = capability.operator || service.operator;
                      if (!operator) return;
                      addOperator(
                        operator,
                        nodePalettePosition,
                        service.backend,
                      );
                      setNodePaletteOpen(false);
                    }}
                  >
                    <strong>
                      {capability.display_name || service.display_name}
                    </strong>
                    <span>
                      {capability.operator || service.operator
                        ? capability.operator || service.operator
                        : "仅查看 · 当前版本暂不可执行"} ·{" "}
                      {capability.frame_id || service.frame_id || "未声明坐标"}{" "}
                      · {capability.unit || service.unit || "未声明单位"}
                    </span>
                  </button>
                ))}
                {!paletteServices.length && <p>暂未配置独立模型服务。</p>}
              </div>
            </div>
            <footer>
              通用算子在节点内选择模型；模型服务项会预选
              Backend。选中节点后可继续编辑参数。
            </footer>
          </section>
        </NodePaletteDialog>
      )}
      <nav className="workspace-panels" aria-label="工作区面板">
        <button
          aria-pressed={minimapOpen}
          onClick={() => setMinimapOpen((open) => !open)}
        >
          {minimapOpen ? "隐藏小地图" : "显示小地图"}
        </button>
        <button
          aria-pressed={!!panelRestore}
          onClick={() => {
            if (panelRestore) {
              setCatalogOpen(panelRestore.catalog);
              setInspectorOpen(panelRestore.inspector);
              setNodePreviewOpen(panelRestore.preview);
              setHistoryOpen(panelRestore.history);
              setPanelRestore(undefined);
            } else {
              setPanelRestore({
                catalog: catalogOpen,
                inspector: inspectorOpen,
                preview: nodePreviewOpen,
                history: historyOpen,
              });
              setCatalogOpen(false);
              setInspectorOpen(false);
              setNodePreviewOpen(false);
              setHistoryOpen(false);
            }
          }}
        >
          {panelRestore ? "恢复面板布局" : "专注画布"}
        </button>
        <label className="node-locator">
          定位节点
          <select
            aria-label="定位画布节点"
            value={selected || ""}
            onChange={(event) => {
              const id = event.target.value;
              if (!id) return;
              setSelected(id);
              focusNode(id);
            }}
          >
            <option value="">选择节点…</option>
            {Object.keys(pipeline.inputs).map((name) => (
              <option key={inputId(name)} value={inputId(name)}>
                输入 · {name}
              </option>
            ))}
            {Object.keys(pipeline.nodes).map((id) => (
              <option key={id} value={id}>
                处理 · {id}
              </option>
            ))}
          </select>
        </label>
        <button
          disabled={!selected}
          onClick={() => {
            if (selected) focusNode(selected);
          }}
        >
          聚焦所选节点
        </button>
        <button
          onClick={() => {
            void flow.fitView({ padding: 0.18, maxZoom: 1 });
          }}
        >
          查看全图
        </button>
        <button
          aria-expanded={catalogOpen}
          aria-controls="workspace-catalog"
          onClick={() => setCatalogOpen((open) => !open)}
        >
          {catalogOpen ? "收起节点目录" : "展开节点目录"}
        </button>
        <button
          aria-expanded={inspectorOpen}
          aria-controls="workspace-inspector"
          onClick={() => setInspectorOpen((open) => !open)}
        >
          {inspectorOpen ? "收起属性面板" : "展开属性面板"}
        </button>
        {catalog.execution_enabled && (
          <button
            onClick={() => {
              setInspectorOpen(true);
              setHistoryOpen(true);
            }}
          >
            运行记录与诊断
          </button>
        )}
      </nav>
      <div
        className={`workspace${catalogOpen ? "" : " catalog-collapsed"}${inspectorOpen ? "" : " inspector-collapsed"}`}
        style={
          { "--inspector-width": `${inspectorWidth}px` } as React.CSSProperties
        }
      >
        <aside id="workspace-catalog" className="catalog" hidden={!catalogOpen}>
          <div className="section-label">节点目录</div>
          <input
            className="search"
            aria-label="搜索算子"
            placeholder="搜索算子…"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            onKeyDown={(event) => {
              if (event.key !== "Enter" || visibleOperators.length !== 1)
                return;
              event.preventDefault();
              addOperator(visibleOperators[0][0]);
            }}
          />
          <div className="catalog-tools">
            <span>{visibleOperators.length} 个算子</span>
            {visibleOperators.length === 1 && (
              <span className="catalog-enter-hint">按 Enter 添加</span>
            )}
            {filter && (
              <button type="button" onClick={() => setFilter("")}>
                清除搜索
              </button>
            )}
          </div>
          <label>
            <input
              type="checkbox"
              checked={registeredOnly}
              onChange={(e) => setRegisteredOnly(e.target.checked)}
            />
            只看已注册实现
          </label>
          <p>注册表示配置可绑定，不代表模型已验收。</p>
          <label className="legacy-input-picker">
            快速添加输入
            <select
              aria-label="添加输入节点"
              value=""
              onChange={(event) => {
                const choice = availableQuickInputs.get(
                  event.target.value as QuickInputKind,
                );
                if (!choice?.port) return;
                let name = choice.name,
                  i = 2;
                while (pipelineRef.current.inputs[name])
                  name = `${choice.name}_${i++}`;
                update((previous) => ({
                  ...previous,
                  inputs: {
                    ...previous.inputs,
                    [name]: structuredClone(choice.port!),
                  },
                }));
                setSelected(inputId(name));
                const selectedNode = selected
                  ? flow.getNodes().find((item) => item.id === selected)
                  : undefined;
                setLayout((previous) => ({
                  ...previous,
                  [inputId(name)]: selectedNode
                    ? {
                        x: selectedNode.position.x - 420,
                        y: Math.max(80, selectedNode.position.y),
                      }
                    : flow.screenToFlowPosition({
                        x: Math.max(180, window.innerWidth / 2 - 420),
                        y: Math.max(220, window.innerHeight / 2 - 150),
                      }),
                }));
                window.setTimeout(() => {
                  focusNode(inputId(name));
                }, 0);
              }}
            >
              <option value="">选择输入类型…</option>
              {(["rgb", "rgba", "mask", "text"] as QuickInputKind[]).map(
                (kind) => (
                  <option
                    key={kind}
                    value={kind}
                    disabled={!availableQuickInputs.has(kind)}
                  >
                    {quickInputDefinitions[kind].label.replace("输入", "")}
                    {!availableQuickInputs.has(kind)
                      ? "（当前目录无兼容契约）"
                      : ""}
                  </option>
                ),
              )}
            </select>
          </label>
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
            {visibleOperators.map(([key, op]) => (
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
                <strong>{readableOperatorLabel(op.name)}</strong>
                <span>{op.name}</span>
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
                <span className="catalog-item-action">
                  点击添加到画布 · 也可拖动
                </span>
              </button>
            ))}
            {!visibleOperators.length && (
              <p className="catalog-empty">
                没有匹配的算子。尝试清除搜索或取消“只看已注册实现”。
              </p>
            )}
            <div className="templates">
              <div className="section-label">示例管线</div>
              {catalog.templates.map((t) => (
                <button
                  key={t.id}
                  disabled={!!configurationBlockedReason}
                  onClick={() => {
                    setHistoricalInputSource(undefined);
                    setPendingLoad({
                      label: `加载示例「${t.label}」`,
                      pipeline: t.pipeline,
                    });
                  }}
                >
                  <strong className="template-name">{t.label}</strong>
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
        <main className={`canvas${nodePreviewOpen ? " with-preview" : ""}`}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            // Viewport is user state; fitting is only done by explicit actions.
            fitView={false}
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
            zoomOnDoubleClick={false}
            onNodeDoubleClick={(_, node) => {
              setSelected(node.id);
              focusNode(node.id);
            }}
            onPaneClick={() => setSelected(undefined)}
            onPaneContextMenu={(event) => {
              event.preventDefault();
              openNodePalette(
                flow.screenToFlowPosition({
                  x: event.clientX,
                  y: event.clientY,
                }),
              );
            }}
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
                const capabilityId = e.dataTransfer.getData(
                  "application/model-capability",
                );
                const capability = service.capabilities?.find(
                  (item) => item.capability_id === capabilityId,
                );
                const operator = capability?.operator || service.operator;
                if (!operator) return;
                addOperator(
                  operator,
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
            minZoom={0.15}
            maxZoom={2}
            deleteKeyCode={["Backspace", "Delete"]}
          >
            <Background gap={24} color="#dce3e9" />
            <Controls />
            {minimapOpen && <MiniMap pannable zoomable nodeColor="#bccddb" />}
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
            双击标题聚焦 · 拖动连接端口 · Delete 删除节点 ·{" "}
            {catalog.execution_enabled ? "草稿与运行独立" : "不执行模型"}
          </div>
        </main>
        {inspectorOpen && (
          <PanelResizeHandle
            width={inspectorWidth}
            onChange={setInspectorWidth}
          />
        )}
        <aside
          id="workspace-inspector"
          ref={inspectorRef}
          className="inspector"
          hidden={!inspectorOpen}
        >
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
                className={historyOpen ? "active" : ""}
                onClick={() => setHistoryOpen(true)}
              >
                运行
              </button>
            )}
          </nav>
          <p aria-label="当前配置编译状态" aria-live="polite">
            {compileMessage}
          </p>
          <section className="inspector-guidance" aria-label="节点配置说明">
            <strong>节点内配置为主</strong>
            <p>
              在画布节点内编辑模型和常用参数；此处用于高级参数与诊断，内容与节点同步。
            </p>
          </section>
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
          {tab === "plan" ? (
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
                          focusNode(item.node_id!);
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
                    <section
                      className="inspector-advanced-config"
                      aria-label="高级配置与诊断"
                    >
                      <div className="inspector-subsection-label">
                        高级配置（与节点同步）
                      </div>
                      <p className="inspector-subsection-note">
                        节点内常用配置是主入口；这里保留完整参数
                        JSON、契约和诊断信息。
                      </p>
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
                            <option value="">
                              未指定 · 后端按唯一候选绑定
                            </option>
                            {adapters.map((a) => (
                              <option key={`${a.name}@${a.version}`}>
                                {a.name}@{a.version}
                              </option>
                            ))}
                            {node.adapter &&
                              !adapters.some(
                                (a) =>
                                  `${a.name}@${a.version}` === node.adapter,
                              ) && <option>{node.adapter}</option>}
                          </select>
                        </label>
                        <label>
                          模型 / Backend 配置
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
                                {backendLabel(catalog, backend)}
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
                        <details
                          key={`advanced-parameters:${selected}`}
                          open={selectedDraft.json !== undefined}
                        >
                          <summary>高级：完整参数 JSON</summary>
                          <p>
                            用于批量编辑或表单尚不支持的字段；应用后替换此节点的显式参数。
                          </p>
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
                          <button
                            disabled={selectedDraft.json === undefined}
                            onClick={() => {
                              changeParameterDraft(selected, (draft) => {
                                const { json: _json, ...remaining } = draft;
                                return remaining;
                              });
                              setMessage(
                                `已放弃 ${selected} 的 JSON 编辑，保留已应用参数。`,
                              );
                            }}
                          >
                            放弃当前节点 JSON 编辑
                          </button>
                        </details>
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
                                  inputs:
                                    catalog.operators[node.operator].inputs,
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
                    </section>
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
              <div className="section-label" ref={draftToolsRef}>
                草稿 · 保存与加载
              </div>
              <label>
                保存名称
                <input
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                />
              </label>
              <div className="row">
                <button
                  disabled={busy || !draft || !!configurationBlockedReason}
                  title={
                    configurationBlockedReason ||
                    "保存已应用的图、参数和布局；实际输入不保存"
                  }
                  onClick={() =>
                    guarded(async () => {
                      if (pendingParameterIdsRef.current.length)
                        throw Error("参数尚未应用，请先应用或放弃编辑再保存。");
                      const savedParameterDrafts = parameterDraftsRef.current;
                      await api(`/api/drafts/${encodeURIComponent(draft)}`, {
                        method: "PUT",
                        body: JSON.stringify({ pipeline, layout }),
                      });
                      setDrafts((await api("/api/drafts")).drafts);
                      setMessage(
                        pipelineRef.current !== pipeline ||
                          layoutRef.current !== layout ||
                          parameterDraftsRef.current !== savedParameterDrafts
                          ? "已保存请求时的草稿；之后的编辑尚未保存。"
                          : "已保存图和参数；实际输入未保存，重新加载后需重新提供。",
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
                      setPendingLoad({
                        label: `加载草稿「${name}」`,
                        pipeline: d.pipeline,
                        layout: d.layout,
                      });
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
      {catalog.execution_enabled && (
        <section
          className="run-history-dock"
          aria-label="运行记录与诊断面板"
          hidden={!historyOpen}
          style={{ flexBasis: `${historyHeight}px` }}
        >
          <HistoryResizeHandle
            height={historyHeight}
            onChange={setHistoryHeight}
          />
          <div className="run-history-heading">
            <strong>运行记录与诊断</strong>
            <span>查看历史不修改当前草稿；下次执行使用画布配置。</span>
            <button onClick={() => setHistoryOpen(false)}>收起运行记录</button>
          </div>
          <div className="run-history-content">
            <ExecutionPanel
              onOpenHistory={() => setHistoryOpen(true)}
              pipeline={pipeline}
              configurationBlockedReason={configurationBlockedReason}
              onEditBlockedConfiguration={() => {
                const id = pendingParameterIds[0];
                if (!id) return;
                setInspectorOpen(true);
                setTab("inspector");
                setSelected(id);
                focusNode(id);
              }}
              selectedNode={selected}
              previewHost={previewHost}
              onRunViewChange={setRunView}
              onFreshnessChange={setNodeFreshness}
              inputHosts={inputHosts}
              onLocateInput={(name) => {
                focusNode(inputId(name));
                const inputControls = Array.from(
                  inputHosts[name]?.querySelectorAll<HTMLElement>(
                    "textarea, input, select, button, summary",
                  ) || [],
                ).filter(
                  (element) =>
                    element.checkVisibility() &&
                    !element.hasAttribute("disabled"),
                );
                (
                  inputControls.find((element) =>
                    ["INPUT", "TEXTAREA", "SELECT"].includes(element.tagName),
                  ) || inputControls[0]
                )?.focus({ preventScroll: true });
              }}
              onLocateNode={(nodeId) => {
                if (!pipelineRef.current.nodes[nodeId]) return;
                setInspectorOpen(true);
                setTab("inspector");
                setSelected(nodeId);
                focusNode(nodeId);
              }}
              workspaceRevision={workspaceRevision}
              historicalInputSource={historicalInputSource}
              controlsHost={controlsHost}
              onHistoricalInputSourceChange={() =>
                setHistoricalInputSource(undefined)
              }
              onLoadDraft={(draft, historicalInputs) => {
                if (
                  pipelineRef.current !== pipeline ||
                  layoutRef.current !== layout
                )
                  throw Error(
                    "读取运行配置期间画布已修改，保留当前编辑；请重新载入。",
                  );
                setPendingLoad({
                  pipeline: draft,
                  label: "载入历史运行配置",
                  historicalInputs,
                });
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
        </section>
      )}
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
              setHistoricalInputSource(undefined);
              setPendingLoad({
                label: `导入 YAML「${file.name}」`,
                pipeline: p,
              });
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

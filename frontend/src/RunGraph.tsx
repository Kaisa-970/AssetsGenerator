import { useEffect, useState } from "react";
import {
  ReactFlow,
  ReactFlowProvider,
  Background,
  Controls,
  type Node,
  type Edge,
} from "@xyflow/react";

type Plan = {
  plan_id: string;
  bindings?: Record<
    string,
    {
      adapter: string;
      backend?: string;
      parameters: Record<string, unknown>;
      implementation_digest: string;
      spec_digest: string;
    }
  >;
  static_plan: {
    pipeline_name: string;
    nodes: {
      node_id: string;
      operator: string;
      inputs: Record<
        string,
        { source: string; node_id: string | null; port: string }
      >;
    }[];
    inputs: Record<string, unknown>;
    topological_order: string[];
    dependencies: Record<string, string[]>;
  };
};
export function RunGraph({
  runId,
  planId,
  states,
  onClose,
}: {
  runId: string;
  planId: string;
  states: Record<string, { status: string }>;
  onClose: () => void;
}) {
  const [plan, setPlan] = useState<Plan>();
  const [error, setError] = useState("");
  const [selectedNode, setSelectedNode] = useState<string>();
  useEffect(() => {
    const abort = new AbortController();
    setPlan(undefined);
    setSelectedNode(undefined);
    setError("");
    fetch(`/api/runs/${encodeURIComponent(runId)}/plan`, {
      signal: abort.signal,
    })
      .then(async (response) => {
        const value = await response.json();
        if (!response.ok) throw Error(value.error || "无法读取固定计划");
        if (value.plan_id !== planId) throw Error("计划身份与运行不一致");
        if (!abort.signal.aborted) setPlan(value);
      })
      .catch((e) => {
        if (!abort.signal.aborted) setError(String(e));
      });
    return () => abort.abort();
  }, [runId, planId]);
  const nodes: Node[] = [],
    edges: Edge[] = [];
  if (plan) {
    const graph = plan.static_plan;
    const levels: Record<string, number> = {},
      rows: Record<number, number> = {};
    Object.keys(graph.inputs).forEach((name, i) =>
      nodes.push({
        id: `input:${name}`,
        position: { x: 0, y: i * 130 },
        data: { label: `输入 · ${name}` },
      }),
    );
    for (const id of graph.topological_order) {
      levels[id] =
        Math.max(0, ...graph.dependencies[id].map((parent) => levels[parent])) +
        1;
      const row = rows[levels[id]] || 0;
      rows[levels[id]] = row + 1;
      const node = graph.nodes.find((n) => n.node_id === id)!;
      nodes.push({
        id,
        position: { x: levels[id] * 240, y: row * 130 },
        data: {
          label: `${id}\n${node.operator}\n${states[id]?.status || "未知"}`,
        },
        style: {
          whiteSpace: "pre-line",
          borderColor:
            states[id]?.status === "succeeded" ? "#20866c" : "#758396",
        },
      });
      for (const [port, binding] of Object.entries(node.inputs))
        edges.push({
          id: `${id}:${port}`,
          source:
            binding.source === "pipeline_input"
              ? `input:${binding.port}`
              : binding.node_id!,
          target: id,
          label: port,
        });
    }
  }
  return (
    <div
      className="run-graph-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="固定运行图"
    >
      <header>
        <strong>固定运行图 · {runId}</strong>
        <button onClick={onClose}>关闭运行图</button>
      </header>
      <p>
        此图来自该运行的不可变计划，只读显示持久化节点状态；不会修改草稿或启动计算。
      </p>
      {plan && selectedNode && (
        <details open className="run-binding-details">
          <summary>固定节点配置 · {selectedNode}</summary>
          {plan.bindings?.[selectedNode] ? (
            <pre>
              {JSON.stringify(
                {
                  operator: plan.static_plan.nodes.find(
                    (node) => node.node_id === selectedNode,
                  )?.operator,
                  ...plan.bindings[selectedNode],
                },
                null,
                2,
              )}
            </pre>
          ) : (
            <p>
              {selectedNode.startsWith("input:")
                ? "这是管线输入，不绑定 Adapter。"
                : "计划未提供此节点的绑定信息。"}
            </p>
          )}
        </details>
      )}
      {plan && <p>点击节点查看该运行固定的实现、参数与身份摘要。</p>}
      {error ? (
        <p role="alert">{error}</p>
      ) : plan ? (
        <div className="run-graph-canvas">
          <ReactFlowProvider key={runId}>
            <ReactFlow
              key={runId}
              nodes={nodes}
              edges={edges}
              nodesDraggable={false}
              nodesConnectable={false}
              elementsSelectable={false}
              onNodeClick={(_, node) => setSelectedNode(node.id)}
              deleteKeyCode={null}
              fitView
            >
              <Background />
              <Controls showInteractive={false} />
            </ReactFlow>
          </ReactFlowProvider>
        </div>
      ) : (
        <p>正在验证并读取固定计划…</p>
      )}
    </div>
  );
}

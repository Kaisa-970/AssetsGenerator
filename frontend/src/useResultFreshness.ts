import { useEffect, useRef, useState } from "react";
import type { Pipeline } from "./graph";
import { request } from "./executionApi";
export function useResultFreshness(
  effectivePipeline: Pipeline,
  selected: string,
  reuseResults: boolean,
) {
  const [nodeFreshness, setNodeFreshness] = useState<Record<string, string>>(
    {},
  );
  const sourcePlan = useRef<{ runId: string; plan: any } | undefined>(
    undefined,
  );
  useEffect(() => {
    if (!selected || !reuseResults) {
      setNodeFreshness({});
      return;
    }
    const controller = new AbortController();
    setNodeFreshness({});
    const timer = setTimeout(() => {
      void (async () => {
        const old =
          sourcePlan.current?.runId === selected
            ? sourcePlan.current.plan
            : await request(
                `/api/runs/${encodeURIComponent(selected)}/plan`,
                undefined,
                controller.signal,
              );
        sourcePlan.current = { runId: selected, plan: old };
        const compiled = await request(
          "/api/compile",
          { pipeline: effectivePipeline },
          controller.signal,
        );
        if (controller.signal.aborted) return;
        const stable = (v: any): string =>
          JSON.stringify(v, (_k, x) =>
            x && typeof x === "object" && !Array.isArray(x)
              ? Object.fromEntries(
                  Object.keys(x)
                    .sort()
                    .map((k) => [k, x[k]]),
                )
              : x,
          );
        const current = compiled.bound_plan;
        const statuses: Record<string, string> = {};
        const oldBound = old.plan || old;
        for (const n of current?.static_plan?.nodes || []) {
          const previous = oldBound.static_plan?.nodes?.find(
            (v: any) => v.node_id === n.node_id,
          );
          const dependencies =
            current.static_plan.dependencies[n.node_id] || [];
          statuses[n.node_id] = !previous
            ? "新节点 · 需要执行"
            : stable(oldBound.bindings?.[n.node_id]) !==
                  stable(current.bindings[n.node_id]) ||
                stable(previous.inputs) !== stable(n.inputs) ||
                previous.operator_contract_digest !== n.operator_contract_digest
              ? "配置已改变 · 需要更新"
              : dependencies.some(
                    (id: string) =>
                      statuses[id] !== "配置匹配 · 启动时核验输入和证据",
                  )
                ? "上游需更新 · 需要重新核验"
                : "配置匹配 · 启动时核验输入和证据";
        }
        setNodeFreshness(statuses);
      })().catch(() => {
        if (!controller.signal.aborted) setNodeFreshness({});
      });
    }, 250);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [effectivePipeline, selected, reuseResults]);

  return nodeFreshness;
}

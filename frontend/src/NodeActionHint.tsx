export type ActionAdvice = {
  can_request: boolean;
  eligibility: string;
  reason_code: string;
  detail: string;
  guidance?: string;
};
export type RunActionAdvice = {
  run_id: string;
  revision: number;
  resume?: ActionAdvice;
  nodes: Record<string, { retry?: ActionAdvice; guidance?: string }>;
};
export function NodeActionHint({ advice }: { advice?: ActionAdvice }) {
  return (
    <section aria-label="操作资格">
      <p>{advice?.detail || "操作资格待核实：尚未收到后端操作提示。"}</p>
      {advice?.guidance && <p>{advice.guidance}</p>}
      {advice?.eligibility === "requires_command_validation" && (
        <p>
          允许提交核验请求，不表示已允许重新推理。后端仍会检查输入、证据和旧作业状态。
        </p>
      )}
    </section>
  );
}

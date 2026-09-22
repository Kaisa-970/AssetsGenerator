import { afterEach, describe, expect, it, vi } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";
import { Children, isValidElement, createElement } from "react";
import {
  parseQualityEvidence,
  QualityEvidenceBody,
  QualityEvidencePanel,
  readQualityEvidence,
} from "./QualityEvidencePanel";
const report = {
  profile: "shape-v1",
  overall_status: "warn",
  checks: [
    {
      check_id: "render-back",
      applicable: false,
      status: "skipped",
      value: null,
      threshold_profile: "shape-v1",
      reason: "no registered cameras",
    },
  ],
};
const output = {
  node_id: "generate",
  port: "qa",
  kind: "quality_report",
  url: "/api/runs/run_A/outputs/generate/qa",
};
afterEach(() => vi.unstubAllGlobals());
describe("quality evidence boundary", () => {
  it("shows reported warning and skipped check independently without a pass claim", () => {
    const html = renderToStaticMarkup(
      createElement(QualityEvidenceBody, {
        evidence: parseQualityEvidence("quality_report", report),
      }),
    );
    expect(html).toContain("warn · 警告");
    expect(html).toContain("skipped · 未执行该项检查");
    expect(html).toContain("no registered cameras");
    expect(html).toContain("shape-v1");
    expect(html).not.toContain("通过");
  });
  it("preserves numeric zero and fail status", () => {
    const parsed = parseQualityEvidence("quality_report", {
      ...report,
      overall_status: "fail",
      checks: [
        { ...report.checks[0], status: "fail", applicable: true, value: 0 },
      ],
    });
    const html = renderToStaticMarkup(
      createElement(QualityEvidenceBody, { evidence: parsed }),
    );
    expect(html).toContain("值：0");
    expect(html).toContain("fail · 不通过");
  });
  it.each([
    null,
    [],
    {},
    { ...report, overall_status: "succeeded" },
    { ...report, checks: {} },
    { ...report, checks: [{ ...report.checks[0], status: "success" }] },
    { ...report, checks: [{ ...report.checks[0], value: {} }] },
    { ...report, checks: [{ ...report.checks[0], applicable: "false" }] },
    { ...report, checks: [{ ...report.checks[0], reason: [] }] },
    { ...report, checks: [report.checks[0], report.checks[0]] },
  ])("rejects malformed reports %j", (value) => {
    expect(() => parseQualityEvidence("quality_report", value)).toThrow();
  });
  it("shows relative scale and explicit component provenance", () => {
    const parsed = parseQualityEvidence("asset_definition", {
      asset_id: "asset-a",
      asset_version: "1",
      spatial: {
        scale_status: "relative",
        unit: "normalized",
        forward_status: "estimated",
      },
      component_provenance: [{ component_id: "body", source: "generated" }],
    });
    const html = renderToStaticMarkup(
      createElement(QualityEvidenceBody, { evidence: parsed }),
    );
    expect(html).toContain("相对尺度，未标定为米制");
    expect(html).toContain("normalized");
    expect(html).toContain("estimated · 估计方向");
    expect(html).toContain("body：generated");
  });
  it("renders missing boundary fields and absent reports as unevaluated", () => {
    expect(
      renderToStaticMarkup(
        createElement(QualityEvidenceBody, {
          evidence: parseQualityEvidence("asset_definition", {
            asset_id: "asset-a",
            asset_version: "1",
          }),
        }),
      ).match(/未评估/g)?.length,
    ).toBe(4);
    const html = renderToStaticMarkup(
      createElement(QualityEvidencePanel, { runId: "run_A", outputs: [] }),
    );
    expect(html).toContain("QA：未评估");
    expect(html).toContain("资产边界：未评估");
    expect(html).toContain("run_A");
  });
  it.each([
    { spatial: null },
    { spatial: { scale_status: "large" } },
    { spatial: { unit: 1 } },
    { spatial: { forward_status: false } },
    { component_provenance: {} },
    { component_provenance: [{ component_id: "body", source: "verified" }] },
  ])("rejects malformed boundary %j", (value) => {
    expect(() =>
      parseQualityEvidence("asset_definition", {
        asset_id: "asset-a",
        asset_version: "1",
        ...value,
      }),
    ).toThrow();
  });
  it("only GETs matching immutable output origin with cancellation and no redirects", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(new Response(JSON.stringify(report)));
    vi.stubGlobal("fetch", fetcher);
    const signal = new AbortController().signal;
    await readQualityEvidence("run_A", output, signal);
    expect(fetcher).toHaveBeenCalledWith(output.url, {
      signal,
      cache: "no-store",
      redirect: "error",
    });
    for (const url of [
      "https://example.com/qa",
      "/api/runs/run_B/outputs/generate/qa",
      output.url + "?x=1",
    ]) {
      await expect(
        readQualityEvidence("run_A", { ...output, url }, signal),
      ).rejects.toThrow("来源不符");
    }
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it("does not reinterpret a failed read or invalid JSON as unevaluated/pass", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(new Response("bad", { status: 400 }))
        .mockResolvedValueOnce(new Response("not json")),
    );
    const signal = new AbortController().signal;
    await expect(readQualityEvidence("run_A", output, signal)).rejects.toThrow(
      "HTTP 400",
    );
    await expect(
      readQualityEvidence("run_A", output, signal),
    ).rejects.toThrow();
  });
  it("changes child identity immediately when selected run changes", () => {
    const keys = (runId: string) =>
      Children.toArray(
        QualityEvidencePanel({ runId, outputs: [output] }).props.children,
      )
        .filter(isValidElement)
        .map((child) => child.key);
    expect(keys("run_A").at(-1)).not.toBe(keys("run_B").at(-1));
  });
});

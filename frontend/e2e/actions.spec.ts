import { expect, test } from "@playwright/test";

async function fixture(page: import("@playwright/test").Page) {
  let revision = 2;
  let actionRevision = 1;
  let actionRun = "dag_actions";
  let busy = false;
  let reason = "旧作业状态尚待核实";
  const posts: any[] = [];
  const advice = () => ({
    can_request: !busy,
    eligibility: busy ? "unavailable" : "requires_command_validation",
    reason_code: busy ? "editor_busy" : "retry_requires_validation",
    detail: busy ? "后台人工决定正在执行" : "可以提交核验请求，命令可能拒绝",
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/catalog")
      return route.fulfill({
        json: {
          operators: {},
          adapters: [],
          templates: [],
          execution_enabled: true,
        },
      });
    if (path === "/api/drafts") return route.fulfill({ json: { drafts: [] } });
    if (path === "/api/compile")
      return route.fulfill({ json: { ok: true, execution_ready: true } });
    if (path === "/api/runs")
      return route.fulfill({
        json: { runs: [{ run_id: "dag_actions", status: "failed" }] },
      });
    if (route.request().method() === "POST") {
      posts.push({ path, body: route.request().postDataJSON() });
      revision++;
      actionRevision = revision;
      reason = "retry_input_invalid: 上游证据刚刚丢失";
      return route.fulfill({ status: 400, json: { error: reason } });
    }
    return route.fulfill({
      json: {
        run: {
          run_id: "dag_actions",
          status: "failed",
          dag: {
            revision,
            node_states: {
              generate: {
                status: "failed",
                attempts: [
                  {
                    attempt: 1,
                    status: "failed",
                    error_code: "remote_transport_unknown",
                    error_detail: reason,
                    remote_binding: {
                      service_id: "service",
                      submission_key: "original-job",
                    },
                  },
                ],
              },
            },
          },
        },
        busy: false,
        outputs: [],
        actions: {
          run_id: actionRun,
          revision: actionRevision,
          resume: advice(),
          nodes: {
            generate: {
              retry: advice(),
              guidance: reason + "；远程作业状态未知，不允许强制重提。",
            },
          },
        },
      },
    });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "运行", exact: true }).click();
  await page.getByLabel("选择运行").selectOption("dag_actions");
  return {
    posts,
    current: () => {
      actionRevision = revision;
    },
    foreign: () => {
      actionRun = "dag_foreign";
    },
    owned: () => {
      actionRun = "dag_actions";
    },
    busy: () => {
      busy = true;
    },
  };
}

test("stale and foreign action projections never enable commands", async ({
  page,
}) => {
  const state = await fixture(page);
  const retry = page.getByRole("button", { name: "请求核验并重试 · generate" });
  const resume = page.getByRole("button", {
    name: "恢复 / 继续此运行",
    exact: true,
  });
  await expect(retry).toBeDisabled();
  await expect(resume).toBeDisabled();
  await expect(
    page.getByText("操作资格待核实：尚未收到后端操作提示。").first(),
  ).toBeVisible();
  state.current();
  state.foreign();
  await page.waitForTimeout(1700);
  await expect(retry).toBeDisabled();
  await expect(resume).toBeDisabled();
  state.owned();
  await expect(retry).toBeEnabled();
  await expect(
    page
      .getByText("允许提交核验请求，不表示已允许重新推理。", { exact: false })
      .first(),
  ).toBeVisible();
  await expect(
    page.getByText(/远程作业状态未知，不允许强制重提/),
  ).toBeVisible();
  expect(state.posts).toEqual([]);
});

test("backend busy advice disables requests even when this run is not busy", async ({
  page,
}) => {
  const state = await fixture(page);
  state.current();
  state.busy();
  await expect(page.getByText("后台人工决定正在执行").first()).toBeVisible();
  await expect(
    page.getByRole("button", { name: "请求核验并重试 · generate" }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "恢复 / 继续此运行", exact: true }),
  ).toBeDisabled();
  expect(state.posts).toEqual([]);
});

test("retry is only a revisioned request and refusal displays the new reason", async ({
  page,
}) => {
  const state = await fixture(page);
  state.current();
  const retry = page.getByRole("button", { name: "请求核验并重试 · generate" });
  await expect(retry).toBeEnabled();
  await retry.click();
  await expect.poll(() => state.posts.length).toBe(1);
  expect(state.posts[0]).toEqual({
    path: "/api/runs/dag_actions/retry",
    body: { node_id: "generate", expected_revision: 2 },
  });
  await expect(
    page.getByText(/retry_input_invalid: 上游证据刚刚丢失.*未自动重发/),
  ).toBeVisible();
  await expect(
    page.getByText(/retry_input_invalid: 上游证据刚刚丢失；远程作业/),
  ).toBeVisible();
  expect(state.posts).toHaveLength(1);
});

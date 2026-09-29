import { test, expect } from "@playwright/test";
test("human reuse checks first and sends exact explicit confirmation", async ({
  page,
}) => {
  const commands: any[] = [];
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body: any = {};
    if (path === "/api/catalog")
      body = {
        operators: {},
        adapters: [],
        templates: [],
        execution_enabled: true,
      };
    else if (path === "/api/compile")
      body = { ok: true, execution_ready: true };
    else if (path === "/api/drafts") body = { drafts: [] };
    else if (path === "/api/runs")
      body = {
        runs: [
          { run_id: "target", status: "waiting_for_input" },
          { run_id: "source", status: "succeeded" },
        ],
      };
    else if (path.endsWith("/reuse-decision")) {
      const sent = route.request().postDataJSON();
      commands.push(sent);
      body = {
        source_run_id: "source",
        node_id: "choose",
        source_snapshot: { artifact_id: "snapshot" },
        source_decision: { artifact_id: "decision" },
        source_reviewer: "first",
        payload: { accept: true },
      };
    } else
      body = {
        run: {
          run_id: "target",
          status: "waiting_for_input",
          dag: {
            revision: 3,
            node_states: {
              choose: { status: "waiting_for_input", attempts: [] },
            },
          },
        },
        outputs: [],
      };
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await page
    .getByRole("button", { name: "运行记录与诊断", exact: true })
    .click();
  await page.getByLabel("选择运行").selectOption("target");
  await page.getByLabel("决定来源运行 · choose").selectOption("source");
  await page
    .getByRole("button", { name: "检查旧选择是否适用", exact: true })
    .click();
  await expect(page.getByText(/原选择人/)).toBeVisible();
  expect(commands).toHaveLength(1);
  expect(commands[0].confirm).toBeUndefined();
  await expect(
    page.getByRole("button", { name: "确认沿用旧选择并继续", exact: true }),
  ).toBeDisabled();
  await page.getByLabel("本次确认人 · choose").fill("second");
  await page
    .getByRole("button", { name: "确认沿用旧选择并继续", exact: true })
    .click();
  await expect.poll(() => commands.length).toBe(2);
  expect(commands[1]).toMatchObject({
    confirm: true,
    reviewer: "second",
    source_snapshot: { artifact_id: "snapshot" },
    expected_revision: 3,
  });
  await page
    .getByRole("button", { name: "重试原确认请求", exact: true })
    .click();
  await expect.poll(() => commands.length).toBe(3);
  expect(commands[2]).toEqual(commands[1]);
  await page.reload();
  await page
    .getByRole("button", { name: "运行记录与诊断", exact: true })
    .click();
  await page.getByLabel("选择运行").selectOption("target");
  await page
    .getByRole("button", { name: "重试原确认请求", exact: true })
    .click();
  await expect.poll(() => commands.length).toBe(4);
  expect(commands[3]).toEqual(commands[1]);
});

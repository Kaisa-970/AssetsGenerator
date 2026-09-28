import { test, expect } from "@playwright/test";

test("text preview follows bound reference and hides stale responses", async ({
  page,
}) => {
  let releaseSlow!: () => void;
  const slow = new Promise<void>((resolve) => {
    releaseSlow = resolve;
  });
  const pipeline = {
    pipeline: "text",
    version: "1",
    inputs: { image: { kind: "rgb_image" }, text: { kind: "text" } },
    nodes: {},
  };
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (
      url.pathname === "/api/inputs/text" &&
      route.request().method() === "GET"
    ) {
      const id = url.searchParams.get("artifact_id");
      if (id === "slow") await slow;
      await route.fulfill({
        status: id === "missing" ? 400 : 200,
        json:
          id === "missing"
            ? { error: "missing" }
            : {
                artifact_id: id,
                text:
                  id === "chair-ref"
                    ? "chair"
                    : id === "slow"
                      ? "obsolete"
                      : "table",
              },
      });
      return;
    }
    const body =
      url.pathname === "/api/catalog"
        ? {
            operators: {},
            adapters: [],
            templates: [{ id: "text", label: "text-template", pipeline }],
            execution_enabled: true,
          }
        : url.pathname === "/api/inputs/text"
          ? { text_ref: { artifact_id: "chair-ref" } }
          : url.pathname === "/api/drafts"
            ? { drafts: [] }
            : { runs: [] };
    await route.fulfill({ json: body });
  });
  page.on("dialog", (dialog) => dialog.accept());
  await page.goto("/");
  await page
    .getByRole("button", { name: "text-template", exact: true })
    .click();
  await page.getByRole("button", { name: "继续替换", exact: true }).click();
  await page.getByLabel("定位画布节点", { exact: true }).selectOption("input:text");
  const editor = page.getByLabel("文本输入 text", { exact: true });
  const reference = page.getByLabel("输入 text Artifact ID", { exact: true });
  const bound = page.getByRole("region", { name: "已绑定文本 text" });
  await editor.fill("chair");
  await page.getByRole("button", { name: "应用文本", exact: true }).click();
  await expect(bound.locator("pre")).toHaveText("chair");
  await page
    .locator('[data-id="input:text"]')
    .getByText("高级：输入引用", { exact: true })
    .click();
  await reference.fill("table-ref");
  await expect(bound.locator("pre")).toHaveText("table");
  await expect(editor).toHaveValue("");
  await reference.fill("slow");
  await expect(bound).toContainText("正在读取");
  await reference.fill("missing");
  await expect(bound).toContainText("无法读取已绑定文本");
  releaseSlow();
  await expect(bound.locator("pre")).toHaveCount(0);
  await editor.fill("desk");
  await expect(reference).toHaveValue("");
  await expect(bound).toContainText("尚未绑定");
});

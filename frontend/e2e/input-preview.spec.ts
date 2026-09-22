import { test, expect } from "@playwright/test";
const png = Buffer.from(
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=",
  "base64",
);
test("upload defaults and local thumbnails revoke URLs without starting runs", async ({
  page,
}) => {
  let starts = 0;
  await page.addInitScript(() => {
    const created: string[] = [],
      revoked: string[] = [];
    Object.assign(window, { previewUrls: { created, revoked } });
    const make = URL.createObjectURL.bind(URL),
      revoke = URL.revokeObjectURL.bind(URL);
    URL.createObjectURL = (value) => {
      const url = make(value);
      created.push(url);
      return url;
    };
    URL.revokeObjectURL = (url) => {
      revoked.push(url);
      revoke(url);
    };
  });
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/runs" && route.request().method() === "POST") starts++;
    const body =
      path === "/api/catalog"
        ? {
            operators: {},
            adapters: [],
            templates: [],
            execution_enabled: true,
          }
        : path === "/api/drafts"
          ? { drafts: [] }
          : path === "/api/inputs/image"
            ? { image_ref: { artifact_id: "uploaded" } }
            : { runs: [] };
    await route.fulfill({ json: body });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "运行", exact: true }).click();
  await expect(page.getByLabel("图片来源", { exact: true })).toHaveValue(
    "upload",
  );
  await page
    .getByLabel("上传运行图片")
    .setInputFiles({ name: "first.png", mimeType: "image/png", buffer: png });
  const image = page.getByAltText("运行图片缩略图", { exact: true });
  await expect(image).toBeVisible();
  const first = await image.getAttribute("src");
  await page
    .getByLabel("上传运行图片")
    .setInputFiles({ name: "second.png", mimeType: "image/png", buffer: png });
  await expect(image).toBeVisible();
  await expect(image).not.toHaveAttribute("src", first!);
  await expect
    .poll(() =>
      page.evaluate(
        (url) => (window as any).previewUrls.revoked.includes(url),
        first,
      ),
    )
    .toBe(true);
  const second = await image.getAttribute("src");
  await page.getByLabel("图片来源", { exact: true }).selectOption("path");
  await expect(image).toHaveCount(0);
  await expect
    .poll(() =>
      page.evaluate(
        (url) => (window as any).previewUrls.revoked.includes(url),
        second,
      ),
    )
    .toBe(true);
  expect(starts).toBe(0);
});

test("multi-input image and mask thumbnails disappear when bindings change", async ({
  page,
}) => {
  const pipeline = {
    pipeline: "inputs",
    version: "1",
    inputs: {
      image: { kind: "rgb_image", carriers: ["artifact_ref"] },
      mask: { kind: "binary_mask", carriers: ["artifact_ref"] },
    },
    nodes: {},
  };
  let starts = 0;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/runs" && route.request().method() === "POST") starts++;
    const body =
      path === "/api/catalog"
        ? {
            operators: {},
            adapters: [],
            templates: [{ id: "inputs", label: "inputs", pipeline }],
            execution_enabled: true,
          }
        : path === "/api/drafts"
          ? { drafts: [] }
          : path === "/api/inputs/image"
            ? { image_ref: { artifact_id: "image" } }
            : path === "/api/inputs/mask"
              ? { mask_ref: { artifact_id: "mask" } }
              : { runs: [] };
    await route.fulfill({ json: body });
  });
  page.on("dialog", (dialog) => dialog.accept());
  await page.goto("/");
  await page.getByRole("button", { name: "inputs", exact: true }).click();
  await page.getByRole("button", { name: "运行", exact: true }).click();
  for (const name of ["image", "mask"]) {
    await page
      .getByLabel(`上传输入 ${name}`)
      .setInputFiles({
        name: name + ".png",
        mimeType: "image/png",
        buffer: png,
      });
    await expect(
      page.getByAltText(`输入 ${name}缩略图`, { exact: true }),
    ).toBeVisible();
  }
  await page
    .getByLabel("输入 mask Artifact ID", { exact: true })
    .fill("other_mask");
  await expect(
    page.getByAltText("输入 mask缩略图", { exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByAltText("输入 image缩略图", { exact: true }),
  ).toBeVisible();
  expect(starts).toBe(0);
});

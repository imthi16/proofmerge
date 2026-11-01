import { expect, test } from "@playwright/test";
import type { Page } from "@playwright/test";

// One server for both projects, and one verification at a time: run in order.
test.describe.configure({ mode: "serial" });

test.beforeEach(async ({ page }) => {
  page.on("pageerror", (error) => {
    throw error;
  });
});

async function noHorizontalScroll(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(0);
}

test("overview shows recorded verdict counts and links to filtered runs", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Overview" })).toBeVisible();
  await expect(page.getByRole("link", { name: "tenant-cache-take2" })).toBeVisible();
  await noHorizontalScroll(page);
  await page.getByRole("button", { name: /^BLOCK/ }).click();
  await expect(page).toHaveURL(/\/runs\?decision=BLOCK/);
  await expect(page.getByRole("button", { name: /^BLOCK/ })).toHaveAttribute("aria-pressed", "true");
  const badges = page.locator("tbody .badge");
  await expect(badges.first()).toHaveText(/BLOCK/);
  for (const text of await badges.allInnerTexts()) expect(text).toMatch(/BLOCK/);
});

test("runs search, deep links and the evidence explorer", async ({ page }) => {
  await page.goto("/runs");
  await page.getByRole("searchbox", { name: "Search runs" }).fill("intended-change");
  await expect(page).toHaveURL(/q=intended-change/);
  await expect(page.locator("tbody tr")).toHaveCount(1);
  await page.getByRole("link", { name: "intended-change" }).click();
  await expect(page).toHaveURL(/\/runs\/intended-change$/);
  await expect(page.getByText("Not conclusive — a human must review")).toBeVisible();
  await noHorizontalScroll(page);

  // A nested run id survives a reload of its deep link.
  await page.goto("/runs/2026-10-30-run3%2Fzero-price-unknown");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("zero-price-unknown");
  await page.reload();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("zero-price-unknown");

  await page.goto("/runs/tenant-cache-take2");
  await expect(page.getByText("Verified regression — do not merge")).toBeVisible();
  await page.getByRole("button", { name: /head attempt 1: FAIL/ }).click();
  await expect(page.getByLabel("Log for head attempt 1")).toContainText("DID NOT RAISE");
  await page.getByRole("button", { name: /^H2/ }).click();
  await expect(page).toHaveURL(/h=H2/);
  const href = await page.getByRole("link", { name: "Open static report" }).getAttribute("href");
  const response = await page.request.get(href!);
  expect(response.status()).toBe(200);
  expect(response.headers()["content-security-policy"]).toContain("default-src 'none'");
});

test("keyboard users can reach content and the theme persists", async ({ page, isMobile }) => {
  await page.goto("/runs");
  await expect(page.getByRole("searchbox", { name: "Search runs" })).toBeVisible();
  if (!isMobile) {
    await page.keyboard.press("Tab");
    await expect(page.getByRole("link", { name: "Skip to content" })).toBeFocused();
    await page.locator("body").press("/");
    await expect(page.getByRole("searchbox", { name: "Search runs" })).toBeFocused();
  }
  await page.getByLabel("Colour theme").selectOption("dark");
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
});

test("benchmarks and diagnostics render", async ({ page }) => {
  await page.goto("/benchmarks");
  await expect(page.getByRole("heading", { name: "2026-10-30-run3" })).toBeVisible();
  await expect(page.getByText("Against simple baselines")).toBeVisible();
  await page.getByRole("button", { name: "Held out", exact: true }).click();
  await expect(page.getByRole("button", { name: "Held out", exact: true })).toHaveAttribute("aria-pressed", "true");
  await noHorizontalScroll(page);
  await page.goto("/diagnostics");
  await expect(page.getByText("Docker daemon")).toBeVisible();
  await expect(page.getByText("Model API key")).toBeVisible();
  await noHorizontalScroll(page);
});

test("a PR verification streams live stages, then links its evidence", async ({ page }) => {
  await page.goto("/pull-requests");
  await expect(page.getByRole("heading", { name: /#12 Cache SKU prices/ })).toBeVisible();
  // The PR body is untrusted: it is shown as text.
  await page.getByText("Description (used as the intent)").click();
  await expect(page.getByText("<img src=x onerror=alert(1)>")).toBeVisible();
  expect(await page.locator("aside img").count()).toBe(0);

  await page.getByRole("link", { name: "Rename helper" }).click();
  await expect(page.getByRole("button", { name: "Verify this PR" })).toBeDisabled();
  await page.getByRole("link", { name: "Cache SKU prices per tenant" }).click();
  await page.getByRole("button", { name: "Verify this PR" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("button", { name: "Cancel" })).toBeFocused();
  await dialog.getByRole("button", { name: "Verify PR #12" }).click();

  await expect(page).toHaveURL(/\/verifications\/pr-12-/);
  await expect(page.getByText("Verdict pending")).toBeVisible();
  await expect(page.locator(".conn")).toHaveText("Live");
  // The verdict appears only after the deterministic stage; then the evidence link.
  await expect(page.getByText("Verified regression — do not merge")).toBeVisible({ timeout: 15_000 });
  await expect(page.locator(".stage-skipped")).toContainText("Test-tamper guard");
  await expect(page.locator(".stage-completed")).toHaveCount(12);  // fetch + 11 stages
  await page.getByRole("link", { name: "Open the evidence" }).click();
  await expect(page).toHaveURL(/\/runs\/pr-12-/);
  await expect(page.getByText("evidence intact")).toBeVisible();
  await noHorizontalScroll(page);
});

test("a request from another origin cannot start a run", async ({ request }) => {
  const forged = await request.post("/api/v1/verifications", {
    headers: { "Content-Type": "application/json", Origin: "https://evil.example" },
    data: { number: 12 },
  });
  expect(forged.status()).toBe(403);
});

test("an unknown verification explains itself", async ({ page }) => {
  await page.goto("/verifications/nope");
  await expect(page.getByText("This verification is not in memory")).toBeVisible();
});

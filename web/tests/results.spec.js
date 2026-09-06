import { test, expect } from "playwright/test";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";

async function capture(page, name) {
  if (!process.env.HIVE_UI_SCREENSHOTS) return;
  await mkdir(process.env.HIVE_UI_SCREENSHOTS, { recursive: true });
  await page.screenshot({ path: join(process.env.HIVE_UI_SCREENSHOTS, name), fullPage: true, animations: "disabled" });
}

test("results pages keep every landing reachable with honest evidence and exact-plan links", async ({ page }) => {
  let fail = false;
  const now = Date.now() / 1000;
  const rows = Array.from({ length: 23 }, (_, index) => ({
    project_id: "exports", project_name: "Exports", plan_id: "old-plan", work_item_id: `item-${index}`,
    title: `Delivery ${index + 1}`, repo: "https://github.com/acme/csv.git",
    merged_commit_sha: "a".repeat(40), merged_at: now - index * 100,
    validation: { status: index === 0 ? "different_commit" : "not_recorded", command: index === 0 ? "make check" : "",
      task_id: index === 0 ? "review" : "", commit_sha: index === 0 ? "b".repeat(40) : "" },
  }));
  await page.route("**/api/**", route => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/auth/me") return route.fulfill({ json: {
      user: { id: "owner", github_login: "owner" }, workspace: { id: "workspace" }, role: "admin", auth_mode: "github",
    } });
    if (url.pathname === "/api/version") return route.fulfill({ json: { version: "test" } });
    if (url.pathname === "/api/overview") return route.fulfill({ json: {
      paused: false, projects: [], live_tasks: [], subscriptions: [],
      totals: { needs_you: 0 }, attention: { count: 0, questions: [], human_todos: [], offers: [] },
      capacity: { machines: [] },
    } });
    if (url.pathname === "/api/deliveries") {
      if (fail) return route.fulfill({ status: 503, json: { detail: "Chief unavailable" } });
      const offset = Number(url.searchParams.get("offset") ?? 0);
      const limit = Number(url.searchParams.get("limit") ?? 20);
      return route.fulfill({ json: { observed_at: now, count: rows.length, offset, limit, items: rows.slice(offset, offset + limit) } });
    }
    return route.fulfill({ status: 404, json: { detail: "Unknown endpoint" } });
  });
  await page.goto("/results");
  await expect(page.getByRole("heading", { name: "Results", exact: true })).toBeVisible();
  await expect(page.getByText("23 recorded landings", { exact: true })).toBeVisible();
  const first = page.getByRole("article", { name: "Delivery 1", exact: true });
  await expect(first.getByText("Checked a different commit", { exact: true })).toBeVisible();
  await expect(first.getByText("Validation passed", { exact: true })).toHaveCount(0);
  await expect(first.getByRole("link", { name: "Inspect evidence" })).toHaveAttribute("href", "/p/exports?plan=old-plan&item=item-0");
  await page.getByRole("button", { name: "Next", exact: true }).click();
  await expect(page.getByRole("article", { name: "Delivery 23", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Next", exact: true })).toBeDisabled();
  await page.reload();
  await expect(page.getByRole("article", { name: "Delivery 23", exact: true })).toBeVisible();
  await capture(page, "results-desktop.png");
  fail = true;
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("may be out of date");
  fail = false;
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("article", { name: "Delivery 23", exact: true }).getByRole("link", { name: "Inspect evidence" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await capture(page, "results-mobile.png");
});

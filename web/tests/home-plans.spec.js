import { test, expect } from "playwright/test";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";

async function capture(page, name) {
  if (!process.env.HIVE_UI_SCREENSHOTS) return;
  await mkdir(process.env.HIVE_UI_SCREENSHOTS, { recursive: true });
  await page.screenshot({ path: join(process.env.HIVE_UI_SCREENSHOTS, `${name}.png`), fullPage: true, animations: "disabled" });
}

const project = {
  id: "exports", name: "Exports", spec_repo: "https://github.com/acme/exports.git",
  state: "working", state_reason: "Reviewing the CSV export before landing.", paused: false,
  created_at: 1, daily_budget_usd: 0, spend_today: 0,
  counts: { running: 1, questions: 0, blockers: 0 },
  plan: { id: "csv-plan", goal: "Export deterministic CSV with clear error messages", status: "approved",
    outcome: "running", counts: { total: 3, done: 1, cancelled: 1, remaining: 1 } },
};

async function setup(page, projects = [project]) {
  const paths = [];
  await page.route("**/api/**", route => {
    const path = new URL(route.request().url()).pathname;
    paths.push(path);
    if (path === "/api/auth/me") return route.fulfill({ json: {
      user: { id: "owner", github_login: "owner" }, workspace: { id: "workspace" }, role: "admin", auth_mode: "github",
    } });
    if (path === "/api/version") return route.fulfill({ json: { version: "test" } });
    if (path === "/api/overview") return route.fulfill({ json: {
      paused: false, projects, live_tasks: [], subscriptions: [],
      totals: { needs_you: 0, tasks_running: 1, agents_ready: 1, agents_total: 2,
        machines_online: 1, machines_total: 1, spend_today: 0, budget_today: 0 },
      attention: { count: 0, offers: [], questions: [], human_todos: [] },
      capacity: { machines: [] },
    } });
    return route.fulfill({ status: 404, json: { detail: `Unexpected endpoint: ${path}` } });
  });
  return paths;
}

test("home shows the plan goal and honest progress with a link to that exact iteration", async ({ page }) => {
  const paths = await setup(page);
  await page.goto("/");
  const card = page.getByRole("article").filter({ hasText: project.name });
  await expect(card.getByRole("heading", { name: project.plan.goal })).toBeVisible();
  await expect(card.getByText("1 landed", { exact: true })).toBeVisible();
  await expect(card.getByText("1 cancelled", { exact: true })).toBeVisible();
  await expect(card.getByText("1 remaining", { exact: true })).toBeVisible();
  await expect(card.getByRole("link", { name: /View progress/ })).toHaveAttribute("href", "/p/exports?plan=csv-plan");
  expect(paths.filter(path => path.includes("/plans/") || path.includes("/projects/"))).toEqual([]);
  await capture(page, "home-plan-progress");
});

test("finished and new projects stay actionable while fleet details are secondary", async ({ page }) => {
  const cancelled = { ...project, id: "docs", name: "Documentation", state: "idle", state_reason: "",
    counts: { running: 0, questions: 0, blockers: 0 },
    plan: { id: "docs-plan", goal: "Publish a deployment guide", status: "complete", outcome: "partial",
      counts: { total: 2, done: 0, cancelled: 2, remaining: 0 } } };
  const empty = { ...project, id: "reports", name: "Reports", state: "idle", state_reason: "", plan: null,
    counts: { running: 0, questions: 0, blockers: 0 } };
  await setup(page, [project, cancelled, empty]);
  await page.goto("/");
  const finishedCard = page.getByRole("article").filter({ hasText: cancelled.name });
  await expect(finishedCard.getByText("Execution ended with cancellations", { exact: true })).toBeVisible();
  await expect(finishedCard.getByText("0 landed", { exact: true })).toBeVisible();
  await expect(finishedCard.getByText("2 cancelled", { exact: true })).toBeVisible();
  await expect(finishedCard.getByRole("link", { name: /Inspect results/ })).toHaveAttribute("href", "/p/docs?plan=docs-plan");
  const newCard = page.getByRole("article").filter({ hasText: empty.name });
  await expect(newCard.getByText("No plan yet. Submit a plan to start an iteration.")).toBeVisible();
  await expect(newCard.getByRole("link", { name: /Submit a plan/ })).toHaveAttribute("href", "/p/reports");
  const capacity = page.getByRole("heading", { name: "capacity · machines & agents" });
  await expect(capacity).toBeHidden();
  await capture(page, "home-plans-desktop");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await expect(finishedCard.getByText("2 cancelled", { exact: true })).toBeVisible();
  await capture(page, "home-plans-mobile");
  await page.getByText("Capacity & activity", { exact: true }).click();
  await expect(capacity).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

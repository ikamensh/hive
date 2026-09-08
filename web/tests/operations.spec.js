import { test, expect } from "playwright/test";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";

async function capture(page, name) {
  if (!process.env.HIVE_UI_SCREENSHOTS) return;
  await mkdir(process.env.HIVE_UI_SCREENSHOTS, { recursive: true });
  await page.screenshot({ path: join(process.env.HIVE_UI_SCREENSHOTS, name), fullPage: true, animations: "disabled" });
}

const now = Date.now() / 1000;
const agent = { id: "claude-mac", backend: "claude", status: "ready", available: true,
  cooldown_until: 0, runner_id: "r-mac", usage_captured_at: now, usage_source: "provider",
  usage_windows: [{ kind: "session", model_scope: "", used_percent: 62, window_minutes: 300, resets_at: now + 14400 }] };
const machine = { id: "m-mac", name: "Mac Studio", hostname: "mac", kind: "local", device_kind: "laptop",
  online: true, last_seen: now, agents: [agent], runners_total: 1, runners_online: 1,
  runners_busy: 1, runners_idle: 0, runners_usable: 1 };
const task = { id: "task-1", project_id: "atlas", project_name: "Atlas", backend: "claude", model: "",
  kind: "work", title: "Repair API timeout", started_at: now - 720, issue_number: 0,
  runner_id: "r-mac", runner_name: "mac", machine_id: "m-mac", machine_name: "Mac Studio",
  runner_online: true, runner_last_seen: now, delivered: true, plan_id: "plan-atlas", work_item_id: "item-2", repo: "" };
const delivery = { project_id: "atlas", project_name: "Atlas", plan_id: "old-plan", work_item_id: "item-1",
  title: "Retry handling shipped", repo: "", merged_commit_sha: "abc123", merged_at: now - 1080,
  validation: { status: "passed", command: "pytest", task_id: "review-1", commit_sha: "abc123" } };
const overview = {
  execution: { working: 1, starting: 0, disconnected: 0 },
  observed_at: now, paused: false, tasks_pending: 0, projects: [], subscriptions: [],
  live_tasks: [task], recent_deliveries: { count: 1, items: [delivery] },
  capacity: { machines: [machine], machines_total: 1, machines_online: 1, agents_total: 1, agents_ready: 1,
    runners_total: 1, runners_online: 1, runners_busy: 1, runners_idle: 0, runners_usable: 1 },
  attention: { count: 0, offers: [], questions: [], human_todos: [] },
  totals: { tasks_running: 1, agents_ready: 1, agents_total: 1, machines_online: 1, machines_total: 1,
    needs_you: 0, spend_today: 12.7, budget_today: 0 },
};

async function setup(page, snapshot = structuredClone(overview)) {
  const state = { overview: snapshot, fail: false, mutations: [] };
  await page.route("**/api/**", route => {
    const { pathname } = new URL(route.request().url());
    if (pathname === "/api/auth/me") return route.fulfill({ json: {
      user: { id: "owner", github_login: "owner" }, workspace: { id: "workspace" }, role: "admin", auth_mode: "github",
    } });
    if (pathname === "/api/version") return route.fulfill({ json: { version: "test" } });
    if (pathname === "/api/inbox") return route.fulfill({ json: {
      items: [], total: 0, offset: 0, limit: 1, observed_at: now,
      counts: { all: 0, question: 0, todo: 0, approval: 0, blocked: 0, yours: 0, others: 0 },
    } });
    if (pathname === "/api/overview") return state.fail
      ? route.fulfill({ status: 503, json: { detail: "Chief unavailable" } })
      : route.fulfill({ json: state.overview });
    if (pathname === "/api/workspace") {
      const body = route.request().postDataJSON();
      state.mutations.push(body);
      state.overview.paused = body.paused;
      return route.fulfill({ json: { paused: body.paused } });
    }
    return route.fulfill({ status: 404, json: { detail: `Unexpected endpoint: ${pathname}` } });
  });
  return state;
}

test("overview connects live work to its machine and shows evidenced delivery and provider headroom", async ({ page }) => {
  await setup(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Hive is working", exact: true })).toBeVisible();
  const row = page.getByRole("row").filter({ hasText: "Mac Studio" });
  await expect(row).toContainText("Repair API timeout");
  await expect(row).toContainText("Claude");
  await expect(row.getByRole("link", { name: /Repair API timeout/ })).toHaveAttribute("href", /\/p\/atlas\?plan=plan-atlas/);
  await expect(page.getByText("38% remaining", { exact: true })).toBeVisible();
  const result = page.getByRole("link", { name: /Retry handling shipped/ });
  await expect(result).toHaveAttribute("href", /\/p\/atlas\?plan=old-plan/);
  await expect(page.getByText("Checks passed", { exact: true })).toBeVisible();
});

test("subscription summary shows each provider's shared 5-hour and weekly limits before model-specific quotas", async ({ page }) => {
  // Account headroom must stay visible even when a provider puts model-specific windows first.
  const snapshot = structuredClone(overview);
  snapshot.capacity.machines[0].agents[0].usage_windows = [
    { kind: "weekly_fable", model_scope: "fable", used_percent: 28, window_minutes: 10080, resets_at: now + 86400 },
    { kind: "weekly", model_scope: "", used_percent: 16, window_minutes: 10080, resets_at: now + 86400 },
    { kind: "session", model_scope: "", used_percent: 62, window_minutes: 300, resets_at: now + 14400 },
  ];
  snapshot.capacity.machines[0].agents.push({
    ...agent, id: "codex-mac", backend: "codex", usage_source: "app-server",
    usage_windows: [
      { kind: "session", model_scope: "spark", used_percent: 0, window_minutes: 300, resets_at: now + 14400 },
      { kind: "weekly", model_scope: "spark", used_percent: 0, window_minutes: 10080, resets_at: now + 86400 },
      { kind: "weekly", model_scope: "", used_percent: 44, window_minutes: 10080, resets_at: now + 86400 },
    ],
  });
  await setup(page, snapshot);
  await page.goto("/");
  const claude = page.getByRole("region", { name: "Claude limits on Mac Studio", exact: true });
  await expect(claude.getByRole("progressbar", { name: "5-hour: 38% remaining", exact: true })).toHaveAttribute("value", "38");
  await expect(claude.getByRole("progressbar", { name: "Weekly: 84% remaining", exact: true })).toHaveAttribute("value", "84");
  const codex = page.getByRole("region", { name: "Codex limits on Mac Studio", exact: true });
  await expect(codex.getByLabel("5-hour limit", { exact: true })).toContainText("Not reported");
  await expect(codex.getByRole("progressbar", { name: "Weekly: 56% remaining", exact: true })).toHaveAttribute("value", "56");
  await expect(codex.getByRole("progressbar")).toHaveCount(1);
  await expect(page.getByText("100% remaining", { exact: true })).toHaveCount(0);
  await capture(page, "subscription-limits-desktop.png");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await capture(page, "subscription-limits-mobile.png");
});

test("pause drains connected work and resume restores dispatch", async ({ page }) => {
  const state = await setup(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Pause Hive", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Hive is paused", exact: true })).toBeVisible();
  await expect(page.getByText(/1 connected task is finishing/)).toBeVisible();
  await page.getByRole("button", { name: "Resume Hive", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Hive is working", exact: true })).toBeVisible();
  expect(state.mutations).toEqual([{ paused: true }, { paused: false }]);
});

test("an assignment is starting until delivered and warns when its runner loses connection", async ({ page }) => {
  const snapshot = structuredClone(overview);
  snapshot.execution = { working: 0, starting: 1, disconnected: 0 };
  snapshot.live_tasks[0].delivered = false;
  const state = await setup(page, snapshot);
  await page.clock.install();
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Hive is starting work", exact: true })).toBeVisible();
  state.overview.execution = { working: 0, starting: 0, disconnected: 1 };
  state.overview.live_tasks[0].runner_online = false;
  await page.clock.fastForward(5000);
  await expect(page.getByRole("heading", { name: "Hive needs attention", exact: true })).toBeVisible();
  await expect(page.getByText("Heartbeat lost", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Pause Hive", exact: true }).click();
  await expect(page.getByText(/1 assigned task has lost connection/)).toBeVisible();
  await expect(page.getByText(/task is finishing/)).toHaveCount(0);
});

test("a failed observation preserves work context without claiming Hive is working", async ({ page }) => {
  const state = await setup(page);
  await page.clock.install();
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Hive is working", exact: true })).toBeVisible();
  state.fail = true;
  await page.clock.fastForward(5000);
  await expect(page.getByRole("heading", { name: "Hive status unavailable", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Pause Hive", exact: true })).toBeDisabled();
  await expect(page.getByRole("row").filter({ hasText: "Mac Studio" })).toContainText("Unknown");
  await expect(page.getByRole("link", { name: /Repair API timeout/ })).toBeVisible();
});

test("missing and expired usage never becomes invented headroom or validation", async ({ page }) => {
  const snapshot = structuredClone(overview);
  snapshot.capacity.machines[0].agents[0].usage_windows[0].resets_at = now - 10;
  snapshot.capacity.machines[0].agents.push({ id: "codex-mac", backend: "codex" });
  snapshot.recent_deliveries.items[0].validation.status = "different_commit";
  await setup(page, snapshot);
  await page.goto("/");
  await expect(page.getByText("Awaiting update", { exact: true })).toBeVisible();
  await expect(page.getByText("Usage not reported", { exact: true })).toBeVisible();
  await expect(page.getByRole("progressbar")).toHaveCount(0);
  await expect(page.getByText("Review evidence", { exact: true })).toBeVisible();
  await expect(page.getByText("Checks passed", { exact: true })).toHaveCount(0);
});

test("mobile keeps live work readable and navigation accessible without document overflow", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await setup(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Hive is working", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  const work = page.getByRole("link", { name: /Repair API timeout/ });
  await expect(work).toBeInViewport();
  expect(await work.evaluate(element => element.getBoundingClientRect().right <= window.innerWidth)).toBe(true);
  await page.getByRole("button", { name: "Open navigation", exact: true }).click();
  await expect(page.getByRole("link", { name: "Projects", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Close navigation", exact: true }).click();
  await expect(page.getByRole("link", { name: "Projects", exact: true })).toBeHidden();
});

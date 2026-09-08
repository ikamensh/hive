import { test, expect } from "playwright/test";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";

async function capture(page, name) {
  if (!process.env.HIVE_UI_SCREENSHOTS) return;
  await mkdir(process.env.HIVE_UI_SCREENSHOTS, { recursive: true });
  await page.screenshot({ path: join(process.env.HIVE_UI_SCREENSHOTS, name), fullPage: true, animations: "disabled" });
}

async function setup(page) {
  const now = Date.now() / 1000;
  const resource = (id, backend) => ({
    id, runner_id: "runner", machine_id: "laptop", backend, available: true,
    enabled: true, discovery_status: "ok", discovery_text: "CLI found", discovered_at: now,
    cli_path: `/usr/bin/${backend}`, cli_version: "1.0", usability_status: "usable",
    last_probe_at: now, last_probe_task_id: "", last_probe_text: "Access verified",
    cooldown_until: 0, last_exhaustion_at: 0, last_exhaustion_text: "",
    total_tasks: 3, total_cost_usd: 0, usage_windows: [], usage_captured_at: 0,
  });
  const resources = [resource("codex", "codex"), resource("claude", "claude")];
  const machine = { id: "laptop", name: "Laptop", hostname: "laptop", os: "macOS", arch: "arm64",
    machine_type: "", device_kind: "laptop", owner_user_id: "owner", last_seen: now };
  const runner = { id: "runner", name: "Runner", machine_id: "laptop", online: true, last_seen: now,
    backends: ["codex", "claude"] };
  const state = { requests: [], resources, denyProbe: true, failResources: false, overviewAge: 0 };
  await page.route("**/api/**", async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    state.requests.push({ path, method: request.method(), body: request.postDataJSON() });
    if (path === "/api/auth/me") return route.fulfill({ json: {
      user: { id: "owner", github_login: "owner" }, workspace: { id: "workspace" }, role: "admin", auth_mode: "github",
    } });
    if (path === "/api/version") return route.fulfill({ json: { version: "test" } });
    if (path === "/api/users") return route.fulfill({ json: [{ user: { id: "owner", github_login: "owner" }, role: "admin" }] });
    if (path === "/api/projects" || path === "/api/subscriptions") return route.fulfill({ json: [] });
    if (path === "/api/org-context") return route.fulfill({ json: { text: "Workspace conventions" } });
    if (path === "/api/overview") return route.fulfill({ json: {
      observed_at: now - state.overviewAge, tasks_pending: 1, paused: false, projects: [], subscriptions: [],
      execution: { working: 1, starting: 0, disconnected: 0 },
      live_tasks: [{ id: "task", title: "Export rows", project_id: "exports", project_name: "Exports",
        backend: "codex", kind: "resolve", runner_id: "runner", machine_id: "laptop", delivered: true,
        runner_online: true, runner_last_seen: now, started_at: now }],
      recent_deliveries: { count: 0, items: [] },
      totals: { tasks_running: 1, needs_you: 0, agents_ready: 2, agents_total: 2,
        machines_online: 1, machines_total: 1, spend_today: 0, budget_today: 0 },
      attention: { count: 0, questions: [], human_todos: [], offers: [] },
      capacity: { machines_total: 1, machines_online: 1, agents_total: 2, agents_ready: 2,
        runners_total: 1, runners_online: 1, runners_busy: 1, runners_usable: 1, runners_idle: 0,
        machines: [{ ...machine, online: true, agents: resources,
          runners_total: 1, runners_online: 1, runners_busy: 1, runners_usable: 1, runners_idle: 0 }] },
    } });
    if (path === "/api/resources" && state.failResources) return route.fulfill({ status: 503, json: { detail: "Resources unavailable" } });
    if (path === "/api/resources") return route.fulfill({ json: {
      cards: [{ machine, online: true, last_seen: now, runners: [runner], resources }],
      machines: [machine], runners: [runner], resources, subscription_candidates: [],
    } });
    if (path === "/api/resources/codex/probe") return route.fulfill(state.denyProbe
      ? { status: 403, json: { detail: "Check denied" } } : { json: { ok: true } });
    if (path === "/api/resources/codex" && request.method() === "PATCH") {
      Object.assign(resources[0], request.postDataJSON());
      resources[0].available = resources[0].enabled;
      return route.fulfill({ json: resources[0] });
    }
    return route.fulfill({ status: 404, json: { detail: `Unexpected endpoint: ${path}` } });
  });
  return state;
}

test("capacity distinguishes a busy runner from its usable agents and preserves repair controls", async ({ page }) => {
  const state = await setup(page);
  await page.setViewportSize({ width: 1280, height: 400 });
  await page.goto("/machines#laptop");
  await expect(page.getByRole("heading", { name: "Capacity", exact: true })).toBeVisible();
  await expect(page.getByLabel("Spare execution capacity")).toContainText("0");
  const machine = page.getByRole("article", { name: "Laptop", exact: true });
  await expect(machine).toHaveAttribute("id", new URL(page.url()).hash.slice(1));
  await expect(machine).toBeInViewport({ ratio: 1 });
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.evaluate(() => window.scrollTo(0, 0));
  await expect(machine.getByText("1 running", { exact: true })).toBeVisible();
  await expect(machine.getByText("2 usable agents", { exact: true })).toBeVisible();
  await capture(page, "capacity-overview.png");
  await machine.getByRole("button", { name: "Manage agents" }).click();
  const codex = page.getByRole("article", { name: "codex on Laptop", exact: true });
  await codex.getByRole("button", { name: "Check access" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Check denied" })).toBeVisible();
  state.denyProbe = false;
  await codex.getByRole("button", { name: "Check access" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "Check denied" })).toHaveCount(0);
  await codex.getByRole("button", { name: "Disable", exact: true }).click();
  await expect(codex.getByText("Disabled", { exact: true })).toBeVisible();
  await codex.getByRole("button", { name: "Enable", exact: true }).click();
  await expect(codex.getByText("Usable", { exact: true })).toBeVisible();
  expect(state.requests.filter(r => r.path === "/api/resources/codex" && r.method === "PATCH")
    .map(r => r.body.enabled)).toEqual([false, true]);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(codex.getByRole("button", { name: "Check access" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await capture(page, "capacity-detail-mobile.png");
});

test("provider-limit links survive reload and do not turn missing readings into free quota", async ({ page }) => {
  await setup(page);
  await page.goto("/machines?tab=limits");
  const limits = page.getByRole("region", { name: "Provider limits", exact: true });
  await expect(page.getByRole("button", { name: "Provider limits", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(limits.getByText("Usage not reported", { exact: true })).toHaveCount(2);
  await expect(limits.getByRole("progressbar")).toHaveCount(0);
  await page.reload();
  await expect(limits).toBeVisible();
  await capture(page, "capacity-limits.png");
  await page.getByRole("button", { name: "Machines", exact: true }).click();
  await expect(page.getByRole("article", { name: "Laptop", exact: true })).toBeVisible();
  await expect(page).not.toHaveURL(/tab=limits/);
});

test("full provider limits keep model quotas distinct and identify stale readings and their source", async ({ page }) => {
  // A separate model bucket must never masquerade as the shared account's available quota.
  const state = await setup(page);
  const now = Date.now() / 1000;
  Object.assign(state.resources[0], {
    usage_source: "app-server", usage_captured_at: now - 1800,
    usage_windows: [
      { kind: "session", model_scope: "spark", used_percent: 0, window_minutes: 300, resets_at: now + 7200 },
      { kind: "weekly", model_scope: "spark", used_percent: 0, window_minutes: 10080, resets_at: now + 86400 },
      { kind: "weekly", model_scope: "", used_percent: 44, window_minutes: 10080, resets_at: now + 86400 },
    ],
  });
  await page.goto("/machines?tab=limits");
  const codex = page.getByRole("region", { name: "Codex limits on Laptop", exact: true });
  await expect(codex.getByLabel("5-hour limit", { exact: true })).toContainText("Not reported");
  await expect(codex.getByRole("progressbar", { name: "Weekly: 56% last reported", exact: true })).toHaveAttribute("value", "56");
  await expect(codex.getByRole("progressbar", { name: "5-hour · spark: 100% last reported", exact: true })).toHaveAttribute("value", "100");
  await expect(codex.getByRole("progressbar", { name: "Weekly · spark: 100% last reported", exact: true })).toHaveAttribute("value", "100");
  await expect(codex.getByText("Source: Codex CLI", { exact: true })).toBeVisible();
  await expect(codex.getByText(/% remaining/)).toHaveCount(0);
  await capture(page, "capacity-scoped-limits.png");
});

test("a failed refresh shows last known capacity until retry restores a current observation", async ({ page }) => {
  await page.clock.install();
  const state = await setup(page);
  await page.goto("/machines");
  const machine = page.getByRole("article", { name: "Laptop", exact: true });
  await expect(machine.getByText("Heartbeat active", { exact: true })).toBeVisible();

  state.failResources = true;
  await page.clock.fastForward(4100);
  const stale = page.getByRole("alert").filter({ hasText: "Capacity status is unknown" });
  await expect(stale).toBeVisible();
  await expect(page.getByLabel("Last known capacity", { exact: true })).toContainText("1 / 1");
  await expect(machine.getByText("Status unknown", { exact: true })).toBeVisible();
  await expect(machine.getByText("Last known: 2 usable agents", { exact: true })).toBeVisible();
  await expect(machine.getByText("Heartbeat active", { exact: true })).toHaveCount(0);
  await expect(machine.getByText("Usable", { exact: true })).toHaveCount(0);
  await machine.getByRole("button", { name: "Manage agents" }).click();
  const codex = page.getByRole("article", { name: "codex on Laptop", exact: true });
  await expect(codex.getByRole("button", { name: "Check access" })).toBeDisabled();
  await expect(codex.getByRole("button", { name: "Disable", exact: true })).toBeEnabled();
  await capture(page, "capacity-stale.png");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await capture(page, "capacity-stale-mobile.png");

  state.failResources = false;
  await stale.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(stale).toHaveCount(0);
  await expect(machine.getByText("Heartbeat active", { exact: true })).toBeVisible();
  await expect(codex.getByRole("button", { name: "Check access" })).toBeEnabled();
});

test("an old observation cannot claim current capacity even when both requests succeed", async ({ page }) => {
  const state = await setup(page);
  state.overviewAge = 60;
  await page.goto("/machines");
  await expect(page.getByRole("alert").filter({ hasText: "Capacity status is unknown" })).toBeVisible();
  const machine = page.getByRole("article", { name: "Laptop", exact: true });
  await expect(machine.getByText("Status unknown", { exact: true })).toBeVisible();
  await expect(machine.getByText("Availability unknown", { exact: true })).toHaveCount(2);
  await expect(page.getByText("Chief unreachable", { exact: false })).toHaveCount(0);
});

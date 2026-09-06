import { test, expect } from "playwright/test";

async function workspace(page) {
  const now = Date.now() / 1000;
  await page.route("**/api/**", route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/auth/me") return route.fulfill({ json: { user: { id: "owner", github_login: "owner" }, workspace: { id: "workspace" }, role: "admin", auth_mode: "github" } });
    if (path === "/api/version") return route.fulfill({ json: { version: "test" } });
    if (path === "/api/inbox") return route.fulfill({ json: { items: [], total: 0, offset: 0, limit: 1, observed_at: now, counts: { all: 0, question: 0, todo: 0, approval: 0, blocked: 0, yours: 0, others: 0 } } });
    if (path === "/api/overview") return route.fulfill({ json: { observed_at: now, paused: false, projects: [],
      execution: { working: 0, starting: 0, disconnected: 0 }, tasks_pending: 0, live_tasks: [], subscriptions: [],
      recent_deliveries: { count: 0, items: [] }, attention: { count: 0, offers: [], questions: [], human_todos: [] },
      capacity: { machines: [], machines_total: 0, machines_online: 0, agents_total: 0, agents_ready: 0, runners_total: 0, runners_online: 0, runners_busy: 0, runners_idle: 0, runners_usable: 0 },
      totals: { tasks_running: 0, agents_ready: 0, agents_total: 0, machines_online: 0, machines_total: 0, needs_you: 0, spend_today: 0, budget_today: 0 } } });
    return route.fulfill({ status: 404, json: { detail: `Unexpected endpoint: ${path}` } });
  });
  await page.goto("/needs-you");
  await expect(page.getByRole("heading", { name: "You're all caught up" })).toBeVisible();
}

async function entirelyVisible(locator, viewport) {
  await expect(locator).toBeInViewport();
  const box = await locator.boundingBox();
  expect(box.y).toBeGreaterThanOrEqual(0);
  expect(box.y + box.height).toBeLessThanOrEqual(viewport.height);
  expect(box.x + box.width).toBeLessThanOrEqual(viewport.width);
}

for (const viewport of [{ width: 900, height: 360 }, { width: 700, height: 360 }]) {
  test(`account controls remain keyboard-accessible in ${viewport.width}px landscape`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await workspace(page);
    if (viewport.width <= 760) await page.getByRole("button", { name: "Open navigation" }).click();
    const account = page.getByRole("button", { name: /@owner/ });
    await account.focus();
    await entirelyVisible(account, viewport);
    await page.keyboard.press("Enter");
    const signOut = page.getByRole("button", { name: "Sign out", exact: true });
    await signOut.focus();
    await entirelyVisible(signOut, viewport);
    await page.keyboard.press("Escape");
    await expect(account).toBeFocused();
    await expect(signOut).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}

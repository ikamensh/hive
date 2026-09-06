import { test, expect } from "playwright/test";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";

async function capture(page, name) {
  if (!process.env.HIVE_UI_SCREENSHOTS) return;
  await mkdir(process.env.HIVE_UI_SCREENSHOTS, { recursive: true });
  await page.screenshot({ path: join(process.env.HIVE_UI_SCREENSHOTS, `${name}.png`), fullPage: true, animations: "disabled" });
}

async function setup(page, items) {
  const state = { items, fail: false, failAction: false, mutations: [], plans: {} };
  await page.route("**/api/**", async route => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    if (path === "/api/auth/me") return route.fulfill({ json: { user: { id: "owner", github_login: "owner" }, workspace: { id: "workspace", name: "Acme" }, role: "admin", auth_mode: "github" } });
    if (path === "/api/version") return route.fulfill({ json: { version: "test" } });
    if (path === "/api/overview") return route.fulfill({ json: {
      observed_at: Date.now() / 1000, paused: false, projects: [], live_tasks: [], tasks_pending: [], recent_deliveries: [], subscriptions: [],
      totals: { needs_you: state.items.length, tasks_running: 0, agents_ready: 0, agents_total: 0, machines_online: 0, machines_total: 0, spend_today: 0, budget_today: 0 },
      attention: { count: state.items.length, offers: [], questions: [], human_todos: [] }, capacity: { machines: [] },
    } });
    if (path === "/api/inbox") {
      if (state.fail) return route.fulfill({ status: 503, json: { detail: "Chief disconnected" } });
      const kind = url.searchParams.get("kind") ?? "all";
      const scope = url.searchParams.get("scope") ?? "all";
      const query = (url.searchParams.get("q") ?? "").toLowerCase();
      const offset = Number(url.searchParams.get("offset") ?? 0);
      const limit = Number(url.searchParams.get("limit") ?? 20);
      const filtered = state.items.filter(item => (kind === "all" || item.kind === kind) && (scope === "all" || item.waiting_on_other === (scope === "others")) && `${item.title} ${item.body}`.toLowerCase().includes(query));
      const counts = Object.fromEntries(["question", "todo", "approval", "blocked"].map(key => [key, state.items.filter(item => item.kind === key).length]));
      return route.fulfill({ json: { items: filtered.slice(offset, offset + limit), total: filtered.length, offset, limit, observed_at: Date.now() / 1000, counts: { ...counts, all: state.items.length, yours: state.items.filter(item => !item.waiting_on_other).length, others: state.items.filter(item => item.waiting_on_other).length } } });
    }
    if (request.method() === "POST") {
      if (state.failAction) return route.fulfill({ status: 503, json: { detail: "Action could not be saved" } });
      state.mutations.push({ path, body: request.postDataJSON() });
      const id = path.split("/")[3];
      state.items = state.items.filter(item => ![item.question?.id, item.todo?.id, item.plan_item?.id, item.plan?.id].includes(id));
      return route.fulfill({ json: { ok: true } });
    }
    if (path.startsWith("/api/plans/") && state.plans[path.split("/")[3]]) return route.fulfill({ json: state.plans[path.split("/")[3]] });
    if (path === "/api/resources") return route.fulfill({ json: { cards: [], resources: [], machines: [], runners: [], subscriptions: [] } });
    return route.fulfill({ status: 404, json: { detail: `Unexpected endpoint ${path}` } });
  });
  return state;
}

function question(index) {
  return { id: `question:q${index}`, kind: "question", title: `Decision ${index}`, body: `Which matching rule should item ${index} use?`, project_id: "search", project_name: "Search", created_at: Date.now() / 1000 - 60,
    can_act: true, waiting_on_other: false, assignee_user_id: "", assignee_name: "", question: { id: `q${index}`, text: `Decision ${index}`, project_id: "search", created_at: 1, status: "open" } };
}

// Reading another question must not discard an answer the operator is composing.
test("inbox keeps separate answer drafts while switching questions and filters", async ({ page }) => {
  const state = await setup(page, [question(1), question(2)]);
  await page.goto("/needs-you");
  await page.getByLabel("Your answer").fill("Keep the original spelling.");
  await page.getByRole("button", { name: /Decision 2/ }).click();
  await expect(page.getByLabel("Your answer")).toHaveValue("");
  await page.getByLabel("Your answer").fill("Use case-insensitive matching.");
  await page.getByRole("button", { name: /Decision 1/ }).click();
  await expect(page.getByLabel("Your answer")).toHaveValue("Keep the original spelling.");
  await page.getByLabel("Search inbox").fill("not found");
  await expect(page.getByRole("heading", { name: "No matching items" })).toBeVisible();
  await page.getByLabel("Search inbox").clear();
  await expect(page.getByLabel("Your answer")).toHaveValue("Keep the original spelling.");
  await page.getByRole("button", { name: /Decision 2/ }).click();
  await expect(page.getByLabel("Your answer")).toHaveValue("Use case-insensitive matching.");
  await capture(page, "inbox-preserved-draft");
  await page.getByRole("button", { name: "Send answer" }).click();
  expect(state.mutations).toContainEqual({ path: "/api/questions/q2/answer", body: { answer: "Use case-insensitive matching." } });
  await expect(page.getByLabel("Your answer")).toHaveValue("Keep the original spelling.");
});

// The real page and API client preserve form input on failures, expose all pages,
// and route mutations to the correct question even when the selected row changes.
test("inbox pages, filters, and resolves a selected question", async ({ page }) => {
  const state = await setup(page, Array.from({ length: 15 }, (_, index) => question(index)));
  await page.goto("/needs-you");
  await expect(page.getByRole("heading", { name: "Inbox 15" })).toBeVisible();
  await expect(page.getByText("1–12 of 15 items")).toBeVisible();
  await expect(page.getByRole("button", { name: "Next inbox page" })).toBeInViewport();
  await capture(page, "inbox-desktop");
  await page.getByRole("button", { name: "Next inbox page" }).click();
  await expect(page.getByText("13–15 of 15 items")).toBeVisible();
  await page.getByRole("button", { name: /Decision 14/ }).click();
  await page.getByLabel("Your answer").fill("Use exact matching.");
  state.failAction = true;
  await page.getByRole("button", { name: "Send answer" }).click();
  await expect(page.getByRole("alert")).toHaveText("Action could not be saved");
  await expect(page.getByLabel("Your answer")).toHaveValue("Use exact matching.");
  state.failAction = false;
  await page.getByRole("button", { name: "Send answer" }).click();
  await expect(page.getByText("13–14 of 14 items")).toBeVisible();
  expect(state.mutations).toContainEqual({ path: "/api/questions/q14/answer", body: { answer: "Use exact matching." } });
  await page.getByLabel("Search inbox").fill("Decision 6");
  await expect(page.getByText("1–1 of 1 items")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Decision 6", exact: true })).toBeVisible();
});

test("owner routing and failed refresh stay explicit on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const mine = { ...question(1), id: "todo:mine", kind: "todo", title: "Renew CLI login", body: "Run login on your machine", question: undefined, todo: { id: "mine", kind: "access", resolution: { check: "probe" } } };
  const other = { ...mine, id: "todo:other", title: "Repair Alice's runner", can_act: false, waiting_on_other: true, assignee_name: "alice", assignee_user_id: "alice", todo: { id: "other", kind: "infra" } };
  const state = await setup(page, [mine, other]);
  await page.goto("/needs-you");
  await page.getByLabel("Inbox ownership").selectOption("others");
  await expect(page.getByRole("heading", { name: "Repair Alice's runner" })).toBeVisible();
  await expect(page.getByText("Waiting on @alice.", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Mark done" })).toHaveCount(0);
  await capture(page, "inbox-mobile");
  await expect(page.getByRole("link", { name: "Open Capacity" })).toHaveAttribute("href", "/machines");
  await page.getByLabel("Inbox ownership").selectOption("yours");
  await expect(page.getByRole("button", { name: "Mark done" })).toBeEnabled();
  state.fail = true;
  await expect(page.getByRole("alert")).toContainText("out of date", { timeout: 7000 });
  await expect(page.getByRole("button", { name: "Mark done" })).toBeDisabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  state.fail = false;
  await page.getByRole("button", { name: "Retry", exact: true }).click();
  await expect(page.getByRole("button", { name: "Mark done" })).toBeEnabled();
  await page.getByRole("button", { name: "Mark done" }).click();
  await expect(page.getByRole("heading", { name: "No matching items" })).toBeVisible();
});

test("plan approvals show work before starting and blocked work opens the exact plan", async ({ page }) => {
  const plan = { id: "plan-1", project_id: "search", goal: "Improve search", status: "draft" };
  const approval = { ...question(1), id: "approval:plan-1", kind: "approval", title: plan.goal,
    body: "Review this plan before starting.", question: undefined, plan };
  const blocked = { ...question(2), id: "blocked:item-2", kind: "blocked", title: "Matching rules",
    body: "Clarify the fallback matching rule", question: undefined,
    plan: { id: "old-plan", status: "approved" }, plan_item: { id: "item-2" } };
  const state = await setup(page, [approval, blocked]);
  state.plans[plan.id] = { plan, items: [{ id: "item-1", title: "Implement exact matching", story: "Users find exact catalog matches", status: "proposed" }] };
  await page.goto("/needs-you");
  await expect(page.getByText("Implement exact matching")).toBeVisible();
  await expect(page.getByRole("link", { name: "Open plan" })).toHaveAttribute("href", "/p/search?plan=plan-1");
  await page.getByRole("button", { name: "Approve and start plan" }).click();
  await expect(page.getByRole("heading", { name: "Matching rules" })).toBeVisible();
  expect(state.mutations[0].path).toBe("/api/plans/plan-1/approve");
  await expect(page.getByRole("link", { name: "Open plan" })).toHaveAttribute("href", "/p/search?plan=old-plan");
  await page.getByRole("button", { name: "Retry work" }).click();
  await expect(page.getByRole("heading", { name: "You're all caught up" })).toBeVisible();
  expect(state.mutations[1].path).toBe("/api/plan-items/item-2/retry");
});

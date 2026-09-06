import { test, expect } from "playwright/test";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";
import { emptyOverview, emptyInbox } from "./fixtures.js";

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
    if (url.pathname === "/api/overview") return route.fulfill({ json: emptyOverview() });
    if (url.pathname === "/api/inbox") return route.fulfill({ json: emptyInbox() });
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
  await expect(first.getByRole("link", { name: "Inspect evidence" })).toHaveAttribute("href", "/p/exports?plan=old-plan&item=item-0&evidence=1");
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

// Delivery navigation reveals a receipt immediately and keeps that exact plan
// after later work starts, rather than landing on an editable item or closed list.
test("delivery links open the matching evidence in active and historical plans", async ({ page }) => {
  const project = { id: "exports", name: "Exports", spec_repo: "https://github.com/acme/csv.git", member_repos: [],
    state: "working", paused: false, archived: false, autonomy: "pr", daily_budget_usd: 0, included_only: true,
    validation_command: "make check", intake_conversation_id: "" };
  const plan = { id: "delivered-plan", project_id: project.id, goal: "Reliable CSV exports", status: "approved",
    proposed_by: "human", created_at: 1, approved_at: 2, finished_at: 0, spec_ref: "", revision: 1, input_digest: "revision-1" };
  const item = { id: "csv-landing", plan_id: plan.id, order: 1, repo: project.spec_repo, title: "Export quoted rows",
    story: "Export rows with commas", constraints: "", notes: "", status: "done", parked_reason: "", authored_by: "human", edited_by_human: false };
  const other = { ...item, id: "other-item", order: 0, title: "Export unquoted rows", status: "queued" };
  const sha = "a".repeat(40);
  const delivery = { project_id: project.id, project_name: project.name, plan_id: plan.id, work_item_id: item.id,
    title: item.title, repo: item.repo, merged_commit_sha: sha, merged_at: 3,
    validation: { status: "passed", command: "make check", task_id: "review-receipt", commit_sha: sha } };
  const result = { schema_version: 1, kind: "plan_result", project_id: project.id, plan_id: plan.id,
    revision: 1, input_digest: plan.input_digest, goal: plan.goal, status: "approved", terminal: false,
    outcome: "running", counts: { total: 2, done: 1, cancelled: 0, remaining: 1 }, captured_at: 3, questions: [], todos: [],
    items: [{ ...other, merged_commit_sha: "", merged_at: 0, attempts: [] },
      { ...item, merged_commit_sha: sha, merged_at: 3, attempts: [{ task_id: "review-receipt", kind: "review", status: "done",
        verdict: "accept", backend: "codex", model: "", result_text: "Quoted-row acceptance verified.", trace_blob: "", artifact_blobs: [],
        validation: { command: "make check", exit_code: 0, output: "Quoted-row tests passed", commit_sha: sha } }] }] };
  let currentPlan = { plan, items: [other, item] };
  await page.route("**/api/**", route => {
    const path = new URL(route.request().url()).pathname;
    const json = path === "/api/auth/me" ? { user: { id: "owner", github_login: "owner" }, workspace: { id: "workspace" }, role: "admin", auth_mode: "github" }
      : path === "/api/version" ? { version: "test" }
      : path === "/api/overview" ? emptyOverview()
      : path === "/api/inbox" ? emptyInbox()
      : path === "/api/deliveries" ? { observed_at: 3, count: 1, offset: 0, limit: 20, items: [delivery] }
      : path === `/api/projects/${project.id}` ? { project, plan: currentPlan, intake_scouts: [], workstreams: [], work_items: [], tasks: [], questions: [], human_todos: [], conversations: [], issue_runs: [], stories: [], findings: [], test_episodes: [] }
      : path === `/api/plans/${plan.id}` ? { plan, items: [other, item] }
      : path === `/api/plans/${plan.id}/result` ? result
      : path === `/api/plans/${plan.id}/readiness` ? { plan_id: plan.id, revision: 1, observed_at: 3, ready: false, checks: [] } : null;
    return route.fulfill({ status: json ? 200 : 404, json: json ?? { detail: `Unexpected endpoint: ${path}` } });
  });
  await page.goto("/results");
  await page.getByRole("link", { name: "Inspect evidence" }).click();
  await expect(page.getByRole("button", { name: "Results & evidence" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText("Quoted-row tests passed", { exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: /View commit/ })).toHaveAttribute("href", `https://github.com/acme/csv/commit/${sha}`);
  await expect(page.getByText("No execution attempts recorded.", { exact: true })).toBeHidden();
  await page.getByRole("button", { name: "Work items", exact: true }).click();
  await expect(page.getByRole("button", { name: "Work items", exact: true })).toHaveAttribute("aria-pressed", "true");
  plan.status = "complete";
  result.status = "complete"; result.terminal = true; result.outcome = "partial";
  other.status = "cancelled"; result.items[0].status = "cancelled";
  result.counts.cancelled = 1; result.counts.remaining = 0;
  currentPlan = { plan: { ...plan, id: "new-plan", status: "draft", goal: "A newer iteration" }, items: [] };
  await page.reload();
  await expect(page.getByRole("heading", { name: "Reliable CSV exports" })).toBeVisible();
  await expect(page.getByText("Quoted-row tests passed", { exact: true })).toBeVisible();
  await expect(page.getByText("A newer iteration", { exact: true })).toHaveCount(0);
  await capture(page, "delivery-selected-evidence.png");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await expect(page.getByText("Quoted-row tests passed", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  if (process.env.HIVE_UI_SCREENSHOTS) await page.screenshot({
    path: join(process.env.HIVE_UI_SCREENSHOTS, "delivery-selected-evidence-mobile.png"), animations: "disabled",
  });
});

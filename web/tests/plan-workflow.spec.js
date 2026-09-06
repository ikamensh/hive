import { test, expect } from "playwright/test";
import { mkdir, readFile } from "node:fs/promises";
import { join } from "node:path";

async function capture(page, name) {
  if (!process.env.HIVE_UI_SCREENSHOTS) return;
  await mkdir(process.env.HIVE_UI_SCREENSHOTS, { recursive: true });
  await page.screenshot({ path: join(process.env.HIVE_UI_SCREENSHOTS, `${name}.png`), fullPage: true, animations: "disabled" });
}

// Exercise the real project page and HTTP client. The API fixture controls only
// execution progress; plan parsing/revisions/landing are covered by backend e2e tests.
const project = {
  id: "csv", name: "CSV tools", spec_repo: "https://github.com/acme/csv.git",
  member_repos: [], state: "idle", paused: false, archived: false,
  autonomy: "pr", daily_budget_usd: 0, included_only: true,
  validation_command: "make check", intake_conversation_id: "",
};
const detail = {
  project, intake_scouts: [], workstreams: [], work_items: [], tasks: [],
  questions: [], human_todos: [], conversations: [], issue_runs: [],
  stories: [], findings: [], test_episodes: [],
};
const plan = {
  id: "csv-plan", project_id: "csv", goal: "Export deterministic CSV",
  revision: 1, input_digest: "abc123", status: "draft", proposed_by: "human",
  created_at: 1, approved_at: 0, finished_at: 0, spec_ref: "",
};
const item = {
  id: "csv-item", plan_id: plan.id, order: 0, repo: project.spec_repo,
  title: "Export rows", story: "Users can export rows", constraints: "No dependencies",
  notes: "Cover commas and empty input", status: "proposed", parked_reason: "",
  authored_by: "human", edited_by_human: false,
};

async function setup(page, initial = null) {
  const state = { payload: initial, requests: [], rejectSubmit: false, result: null, failResult: false,
    project: { ...project }, projectPlan: null, failWiring: false };
  await page.route("**/api/**", async route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    state.requests.push({ path, method: request.method(), body: request.postDataJSON() });
    if (path === "/api/auth/me") return route.fulfill({ json: {
      user: { id: "owner", github_login: "owner" }, workspace: { id: "workspace" }, role: "admin", auth_mode: "github",
    } });
    if (path === "/api/overview") return route.fulfill({ json: {
      projects: [], live_tasks: [], subscriptions: [],
      totals: { needs_you: 0, tasks_running: 0, agents_ready: 0, agents_total: 0,
        machines_online: 0, machines_total: 0, spend_today: 0, budget_today: 0 },
      attention: { offers: [], questions: [], human_todos: [] },
      capacity: { machines: [] },
    } });
    if (path === "/api/version") return route.fulfill({ json: { version: "test" } });
    if (path === "/api/resources") return route.fulfill({ json: { cards: [] } });
    if (path === "/api/projects" && request.method() === "POST") return route.fulfill({ json: state.project });
    if (path === "/api/projects/csv") {
      if (request.method() === "PATCH") {
        if (state.failWiring) return route.fulfill({ status: 400, json: { detail: "Repository access denied" } });
        Object.assign(state.project, request.postDataJSON());
        return route.fulfill({ json: state.project });
      }
      return route.fulfill({ json: { ...detail, project: state.project, plan: state.projectPlan ?? state.payload } });
    }
    if (path === "/api/projects/csv/plan/submit") {
      if (state.rejectSubmit) return route.fulfill({ status: 400, json: { detail: "A plan must contain at least one item" } });
      state.payload = { plan: { ...plan }, items: [{ ...item }] };
      return route.fulfill({ json: { ...state.payload, accepted_revision: 1 } });
    }
    if (path === "/api/plans/csv-plan/approve") {
      state.payload.plan.status = "approved";
      state.payload.items[0].status = "resolving";
      return route.fulfill({ json: { ...state.payload, notes: [] } });
    }
    if (path === "/api/plans/csv-plan") return route.fulfill({ json: state.payload });
    if (path === "/api/plans/csv-plan/result") return route.fulfill(state.failResult
      ? { status: 503, json: { detail: "Chief unavailable" } } : { json: state.result });
    if (path === "/api/plans/csv-plan/document") return route.fulfill({ contentType: "text/plain", body: 'version = 1\nplan_id = "csv-plan"\nrevision = 1\n' });
    return route.fulfill({ status: 404, json: { detail: `Unexpected endpoint: ${path}` } });
  });
  return state;
}

test("submit a file, correct a rejected submission, and start without intake", async ({ page }) => {
  const state = await setup(page);
  await page.goto("/p/csv");
  const source = "# Goal\nExport deterministic CSV\n\n## Export rows\nCover commas and empty input.\n";
  await page.getByLabel("Plan file").setInputFiles({ name: "plan.md", mimeType: "text/markdown", buffer: Buffer.from(source) });
  await expect(page.getByLabel("Plan document")).toHaveValue(source);
  await capture(page, "submit-plan");
  state.rejectSubmit = true;
  await page.getByRole("button", { name: "Submit for review", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("at least one item");
  await expect(page.getByLabel("Plan document")).toHaveValue(source);
  state.rejectSubmit = false;
  await page.getByRole("button", { name: "Submit for review", exact: true }).click();
  await expect(page.getByRole("heading", { name: plan.goal })).toBeVisible();
  await expect(page.getByRole("button", { name: /Approve all 1 & start/ })).toBeVisible();
  await page.getByRole("button", { name: /Approve all 1 & start/ }).click();
  await expect(page.getByRole("button", { name: /Export rows.*building/ })).toBeVisible();
  await capture(page, "monitor-plan");
  expect(state.requests.filter(r => r.path.endsWith("/plan/submit")).at(-1).body).toEqual({ source, start: false });
  expect(state.requests.some(r => r.path.includes("intake") || r.path.includes("propose"))).toBe(false);
});

test("new project goes to submission and a setup retry does not create another project", async ({ page }) => {
  const state = await setup(page);
  await page.goto("/");
  await page.getByRole("button", { name: /new project create a project/ }).click();
  await page.getByLabel("name", { exact: true }).fill("CSV tools");
  await page.getByLabel("Repository URL").fill(project.spec_repo);
  await page.getByLabel("Validation command").fill("make check");
  await capture(page, "new-plan-project");
  state.failWiring = true;
  await page.getByRole("button", { name: "Create project", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("Repository access denied");
  state.failWiring = false;
  await page.getByRole("button", { name: "Retry setup" }).click();
  await expect(page.getByRole("button", { name: "Submit for review" })).toBeVisible();
  expect(state.requests.filter(r => r.path === "/api/projects" && r.method === "POST")).toHaveLength(1);
  expect(state.project.validation_command).toBe("make check");
  expect(state.requests.some(r => r.path.includes("intake"))).toBe(false);
});

test("completion updates live, preserves cancellation evidence, and remains inspectable after another plan starts", async ({ page }) => {
  const state = await setup(page, { plan: { ...plan, status: "approved" }, items: [
    { ...item, status: "reviewing" }, { ...item, id: "docs", title: "Document usage", status: "cancelled", order: 1 },
  ] });
  const sha = "a".repeat(40);
  state.result = {
    schema_version: 1, kind: "plan_result", project_id: project.id, plan_id: plan.id,
    revision: 1, input_digest: "abc123", goal: plan.goal, status: "complete", terminal: true,
    outcome: "partial", counts: { total: 2, done: 1, cancelled: 1, remaining: 0 },
    captured_at: 2, questions: [], todos: [],
    items: [{ ...item, status: "done", merged_commit_sha: sha, merged_at: 2, attempts: [{
      task_id: "review-1", kind: "review", status: "done", verdict: "accept", backend: "codex",
      model: "", result_text: "CSV quoting and empty input verified.", trace_blob: "", artifact_blobs: [],
      validation: { command: "make check", exit_code: 0, output: "12 passed", commit_sha: sha },
    }] }, { ...item, id: "docs", title: "Document usage", status: "cancelled", merged_commit_sha: "", attempts: [] }],
  };
  await page.goto("/p/csv?plan=csv-plan");
  await expect(page.getByRole("button", { name: /Export rows.*reviewing/ })).toBeVisible();
  state.payload.plan.status = "complete";
  state.payload.items[0].status = "done";
  await expect(page.getByRole("heading", { name: "Partially delivered" })).toBeVisible();
  await expect(page.getByText("1 cancelled", { exact: true })).toBeVisible();
  await page.getByText("Export rows", { exact: true }).click();
  await expect(page.getByText("CSV quoting and empty input verified.")).toBeVisible();
  await expect(page.getByText("12 passed", { exact: true })).toBeVisible();
  await capture(page, "plan-result");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "Download result JSON" }).scrollIntoViewIfNeeded();
  await expect(page.getByRole("button", { name: "Download result JSON" })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await capture(page, "plan-result-mobile");
  await expect(page.getByRole("link", { name: /View commit/ })).toHaveAttribute("href", `https://github.com/acme/csv/commit/${sha}`);
  const downloaded = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download result JSON" }).click();
  const download = await downloaded;
  expect(download.suggestedFilename()).toBe("csv-plan-result.json");
  expect(JSON.parse(await readFile(await download.path(), "utf8"))).toEqual(state.result);
  const exported = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export plan", exact: true }).click();
  expect(await readFile(await (await exported).path(), "utf8")).toContain('plan_id = "csv-plan"');
  state.projectPlan = { plan: { ...plan, id: "next-plan", goal: "Next iteration" }, items: [] };
  await page.reload();
  await expect(page.getByRole("heading", { name: "Partially delivered" })).toBeVisible();
  state.failResult = true;
  await page.reload();
  await expect(page.getByRole("alert")).toContainText("Chief unavailable");
  await expect(page.getByRole("heading", { name: "Partially delivered" })).toHaveCount(0);
});

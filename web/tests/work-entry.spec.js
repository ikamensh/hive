import { test, expect } from "playwright/test";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";

async function capture(page, name) {
  if (!process.env.HIVE_UI_SCREENSHOTS) return;
  await mkdir(process.env.HIVE_UI_SCREENSHOTS, { recursive: true });
  await page.screenshot({
    path: join(process.env.HIVE_UI_SCREENSHOTS, `${name}.png`),
    fullPage: true,
    animations: "disabled",
  });
}

// Public journeys preserve a broad goal and approve missing specs through the
// existing conversation API; the fixture replaces only the remote chief.
async function fixture(page) {
  const state = {
    requests: [],
    failSetup: false,
    conversation: null,
    plan: null,
    project: {
      id: "garden",
      name: "Garden",
      spec_repo: "https://github.com/acme/garden.git",
      member_repos: [],
      state: "intake",
      paused: false,
      archived: false,
      autonomy: "pr",
      daily_budget_usd: 10,
      included_only: false,
      validation_command: "npm test",
      intake_conversation_id: "",
      created_at: 1,
    },
  };
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    const body = request.postDataJSON();
    state.requests.push({ path, method: request.method(), body });
    const json = (value) => route.fulfill({ json: value });
    if (path === "/api/auth/me")
      return json({
        user: { id: "owner", github_login: "owner" },
        workspace: { id: "workspace" },
        role: "admin",
        auth_mode: "dev",
      });
    if (path === "/api/version") return json({ version: "test" });
    if (path === "/api/github/repos") return json([]);
    if (path === "/api/overview")
      return json({
        projects: [],
        live_tasks: [],
        subscriptions: [],
        totals: {
          needs_you: 0,
          tasks_running: 0,
          agents_ready: 0,
          agents_total: 0,
          machines_online: 0,
          machines_total: 0,
          spend_today: 0,
          budget_today: 0,
        },
        attention: { offers: [], questions: [], human_todos: [] },
        capacity: { machines: [] },
      });
    if (path === "/api/projects")
      return json(
        request.method() === "POST" ? state.project : [state.project],
      );
    if (path.endsWith("/plans")) return json({ plans: [] });
    if (path === "/api/projects/garden") {
      if (request.method() === "PATCH") {
        if (state.failSetup)
          return route.fulfill({
            status: 400,
            json: { detail: "Repository access denied" },
          });
        Object.assign(state.project, body);
        return json(state.project);
      }
      return json({
        project: state.project,
        intake_scouts: [{ backend: "codex", model: "" }],
        workstreams: [],
        work_items: [],
        tasks: [],
        questions: [],
        human_todos: [],
        conversations: state.conversation ? [state.conversation] : [],
        issue_runs: [],
        stories: [],
        findings: [],
        test_episodes: [],
        plan: state.plan,
      });
    }
    if (path === "/api/projects/garden/intake/start") {
      state.conversation = {
        id: "brief",
        project_id: "garden",
        status: "open",
        backend: "codex",
        model: "",
        created_at: 1,
        transcript: [
          {
            role: "assistant",
            text: "Mission: help people grow food.\n\nNext iteration: track planting dates.",
          },
        ],
        latest_brief: "Track planting dates",
      };
      state.project.intake_conversation_id = "brief";
      return json(state.conversation);
    }
    if (path === "/api/conversations/brief/message") {
      if (body.action === "approve") state.conversation.status = "finalizing";
      return json({ conversation: state.conversation });
    }
    if (path === "/api/projects/garden/plan/submit") {
      state.plan = {
        plan: {
          id: "garden-plan",
          project_id: "garden",
          goal: "Track planting dates",
          status: "draft",
          proposed_by: "human",
          created_at: 1,
          revision: 1,
        },
        items: [
          {
            id: "planting",
            plan_id: "garden-plan",
            title: "Record planting dates",
            status: "proposed",
            story: "Gardeners can save a planting date",
            constraints: "",
            notes: "",
            repo: state.project.spec_repo,
            order: 0,
          },
        ],
      };
      return json({ ...state.plan, accepted_revision: 1 });
    }
    if (path === "/api/plans/garden-plan") return json(state.plan);
    if (path === "/api/plans/garden-plan/readiness")
      return json({
        plan_id: "garden-plan",
        revision: 1,
        observed_at: 1,
        ready: true,
        checks: [],
      });
    return route.fulfill({
      status: 404,
      json: { detail: `Unexpected endpoint: ${path}` },
    });
  });
  return state;
}

test("AI design retains a new project's goal across setup retry and approves through the conversation", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.goto("/p/garden");
  await page.getByRole("button", { name: "New work", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "New work" });
  await dialog.getByRole("button", { name: /Design with AI/ }).click();
  await dialog
    .getByRole("combobox", { name: "Project", exact: true })
    .selectOption("new");
  await dialog.getByLabel("Project name").fill("Garden");
  await dialog.getByLabel("Repository URL").fill(state.project.spec_repo);
  await dialog
    .getByLabel("What do you want to achieve?")
    .fill("Help a beginner know what to plant and when.");
  await capture(page, "new-work-design");
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await capture(page, "new-work-design-mobile");
  await page.setViewportSize({ width: 1200, height: 850 });
  state.failSetup = true;
  await dialog
    .getByRole("button", { name: "Start designing", exact: true })
    .click();
  await expect(dialog.getByRole("alert")).toContainText(
    "Repository access denied",
  );
  state.failSetup = false;
  await dialog.getByRole("button", { name: "Retry setup" }).click();
  await expect(
    page.getByText("Mission: help people grow food.", { exact: false }),
  ).toBeVisible();
  expect(
    state.requests.filter(
      (r) => r.path === "/api/projects" && r.method === "POST",
    ),
  ).toHaveLength(1);
  expect(
    state.requests.find(
      (r) => r.path === "/api/projects" && r.method === "POST",
    ).body.spec_text,
  ).toBe("Help a beginner know what to plant and when.");
  await page
    .getByRole("button", { name: "Approve brief & prepare plan", exact: true })
    .click();
  await expect(
    page.getByText("Saving the agreed specification", { exact: false }),
  ).toBeVisible();
  await capture(page, "project-design-conversation");
  expect(
    state.requests.some(
      (r) =>
        r.path === "/api/conversations/brief/message" &&
        r.body.action === "approve",
    ),
  ).toBe(true);
  expect(state.requests.some((r) => r.path.endsWith("/intake/finalize"))).toBe(
    false,
  );
});

test("prepared work reaches the shared plan review without starting AI design", async ({
  page,
}) => {
  const state = await fixture(page);
  await page.goto("/p/garden");
  await page.getByRole("button", { name: "New work", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "New work" });
  await dialog
    .getByRole("button", { name: "Continue to plan", exact: true })
    .click();
  await expect(page).toHaveURL(/new=plan/);
  const source =
    "# Goal\nTrack planting dates\n\n## Record planting dates\nUse the existing database.\n";
  await page.getByLabel("Plan file").setInputFiles({
    name: "garden.md",
    mimeType: "text/markdown",
    buffer: Buffer.from(source),
  });
  await page
    .getByRole("button", { name: "Submit for review", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Track planting dates", exact: true }),
  ).toBeVisible();
  await expect(page).toHaveURL(/plan=garden-plan/);
  await capture(page, "project-plan-review");
  expect(
    state.requests.find((request) => request.path.endsWith("/plan/submit"))
      .body,
  ).toEqual({ source, start: false });
  expect(
    state.requests.some(
      (request) =>
        request.path.includes("intake") || request.path.includes("/approve"),
    ),
  ).toBe(false);
});

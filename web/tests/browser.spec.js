import { test, expect } from "playwright/test";
import { mkdir } from "node:fs/promises";
import { join } from "node:path";

async function screenshot(page, name) {
  if (!process.env.HIVE_UI_SCREENSHOTS) return;
  await mkdir(process.env.HIVE_UI_SCREENSHOTS, { recursive: true });
  await page.screenshot({ path: join(process.env.HIVE_UI_SCREENSHOTS, `${name}.png`), fullPage: true });
}

const markdown = [
  "# Issue review",
  "A **bold** result with [a useful link](https://example.com/docs).",
  "- First step\n- Second step",
  "```js\nconst ready = true;\n```",
].join("\n\n");

const auth = {
  user: { id: "operator", github_login: "operator", display_name: "Operator" },
  workspace: { id: "workspace", name: "Hive" },
  role: "admin",
  auth_mode: "github",
};
const overview = {
  paused: false, projects: [], live_tasks: [], subscriptions: [],
  capacity: { machines: [], machines_total: 0, machines_online: 0, agents_total: 0, agents_ready: 0 },
  attention: { count: 0, offers: [], questions: [], human_todos: [] },
  totals: { tasks_running: 0, agents_ready: 0, agents_total: 0, machines_online: 0, machines_total: 0, needs_you: 0, spend_today: 0, budget_today: 0 },
};

test("Markdown preserves useful formatting while refusing executable issue content", async ({ page }) => {
  await page.goto("/tests/harness.html");
  await page.waitForFunction(() => window.harness);
  await page.evaluate(markdown => window.harness.markdown([
    markdown,
    '<img alt="unsafe" src="missing-image" onerror="window.injected = true">',
    '<a href="javascript:window.injected=true">unsafe link</a>',
  ].join("\n\n")), markdown);
  await expect(page.getByRole("heading", { name: "Issue review" })).toBeVisible();
  await expect(page.locator("strong")).toHaveText("bold");
  await expect(page.locator("li")).toHaveCount(2);
  await expect(page.locator("pre code")).toContainText("const ready = true");
  await expect(page.getByRole("link", { name: "a useful link" })).toHaveAttribute("href", "https://example.com/docs");
  expect(await page.getByAltText("unsafe").getAttribute("onerror")).toBeNull();
  expect(await page.getByText("unsafe link").getAttribute("href")).toBeNull();
  expect(await page.evaluate(() => window.injected)).toBeUndefined();
  await page.evaluate(markdown => window.harness.markdown(markdown), markdown);
  await expect(page.getByAltText("unsafe")).toHaveCount(0);
  await screenshot(page, "markdown");
});

test("an expired session clears the signed-in screen and offers sign-in", async ({ page }) => {
  await page.clock.install();
  let expired = false;
  let heldOverview;
  await page.route("**/api/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (expired && path === "/api/overview") {
      heldOverview = route;
      return;
    }
    if (expired && path !== "/api/version") {
      return route.fulfill({ status: 401, json: { detail: "session expired" } });
    }
    const json = path === "/api/auth/me" ? auth : path === "/api/overview" ? overview : { version: "test" };
    return route.fulfill({ json });
  });
  await page.goto("/");
  await expect(page.getByRole("button", { name: /@operator/ })).toBeVisible();
  await page.waitForFunction(() => JSON.parse(localStorage.getItem("hive-overview:workspace:operator"))?.projects);
  expired = true;
  await page.clock.fastForward(4_000);
  await expect.poll(() => !!heldOverview).toBe(true);
  await page.clock.fastForward(26_000);
  await expect(page.getByRole("link", { name: "Continue with GitHub" })).toBeVisible();
  await expect(page.getByRole("button", { name: /@operator/ })).toHaveCount(0);
  await expect(page.getByRole("alert")).toHaveCount(0);
  expect(await page.evaluate(() => JSON.parse(localStorage.getItem("hive-overview:workspace:operator")))).toBeNull();
  // Even a late successful response from the expired session must stay discarded.
  await heldOverview.fulfill({ json: overview });
  await page.evaluate(() => new Promise(requestAnimationFrame));
  expect(await page.evaluate(() => localStorage.getItem("hive-overview:workspace:operator"))).toBeNull();
  await expect(page.getByRole("link", { name: "Continue with GitHub" })).toBeVisible();
  await screenshot(page, "expired-login");
});

test("a refresh result stays current when an older poll finishes later", async ({ page }) => {
  await page.goto("/tests/harness.html");
  await page.waitForFunction(() => window.harness);
  await page.evaluate(() => window.harness.poll());
  await page.waitForFunction(() => window.harness.requests().length === 1);
  await page.evaluate(() => { void window.harness.refresh(); });
  await page.waitForFunction(() => window.harness.requests().length === 2);
  await page.evaluate(() => window.harness.resolve(1, "done"));
  await expect(page.locator("output")).toHaveText('{"data":"done","failed":false}');
  await page.evaluate(() => window.harness.resolve(0, "running"));
  // Flush the completed request and React's next paint before checking for rollback.
  await page.evaluate(() => new Promise(requestAnimationFrame));
  await expect(page.locator("output")).toHaveText('{"data":"done","failed":false}');
});

test("a late failure cannot replace a successful refresh with an error", async ({ page }) => {
  await page.goto("/tests/harness.html");
  await page.waitForFunction(() => window.harness);
  await page.evaluate(() => window.harness.poll());
  await page.waitForFunction(() => window.harness.requests().length === 1);
  await page.evaluate(() => { void window.harness.refresh(); });
  await page.evaluate(() => window.harness.resolve(1, "ready"));
  await expect(page.locator("output")).toHaveText('{"data":"ready","failed":false}');
  await page.evaluate(() => window.harness.reject(0, "old connection failed"));
  await page.evaluate(() => new Promise(requestAnimationFrame));
  await expect(page.locator("output")).toHaveText('{"data":"ready","failed":false}');
});

test("switching projects clears old data and ignores the previous project's refresh", async ({ page }) => {
  await page.goto("/tests/harness.html");
  await page.waitForFunction(() => window.harness);
  await page.evaluate(() => window.harness.poll({ name: "first" }));
  await page.waitForFunction(() => window.harness.requests().length === 1);
  await page.evaluate(() => window.harness.resolve(0, "first project"));
  await expect(page.locator("output")).toContainText("first project");
  await page.evaluate(() => { void window.harness.refresh(); });
  await page.evaluate(() => window.harness.poll({ name: "second" }));
  await page.waitForFunction(() => window.harness.requests().length === 3);
  await expect(page.locator("output")).toHaveText('{"data":null,"failed":false}');
  await page.evaluate(() => window.harness.resolve(2, "second project"));
  await expect(page.locator("output")).toContainText("second project");
  await page.evaluate(() => window.harness.resolve(1, "obsolete first project"));
  await page.evaluate(() => new Promise(requestAnimationFrame));
  await expect(page.locator("output")).toHaveText('{"data":"second project","failed":false}');
});

for (const ending of ["disabled", "unmounted"]) {
  test(`a poll that is ${ending} cannot repopulate cached data from an in-flight refresh`, async ({ page }) => {
    await page.goto("/tests/harness.html");
    await page.waitForFunction(() => window.harness);
    await page.evaluate(() => window.harness.poll({ cacheKey: "poll-test" }));
    await page.waitForFunction(() => window.harness.requests().length === 1);
    await page.evaluate(() => window.harness.resolve(0, "saved"));
    await expect(page.locator("output")).toContainText("saved");
    await page.evaluate(() => { void window.harness.refresh(); });
    await page.evaluate(ending => {
      if (ending === "disabled") window.harness.poll({ enabled: false, cacheKey: "poll-test" });
      else window.harness.unmount();
    }, ending);
    if (ending === "disabled") {
      await expect(page.locator("output")).toHaveText('{"data":null,"failed":false}');
    } else {
      await expect(page.locator("output")).toHaveCount(0);
    }
    await page.evaluate(() => window.harness.resolve(1, "obsolete"));
    await page.evaluate(() => new Promise(requestAnimationFrame));
    expect(await page.evaluate(() => JSON.parse(localStorage.getItem("poll-test")))).toBe("saved");
  });
}

// Browser regression for the OpenCode-only intake fixture.
// Start VITE_MOCK=1 npm run dev, then:
// node scripts/verify-intake.cjs http://127.0.0.1:5173 /tmp/hive-intake
const assert = require("node:assert/strict");
const { chromium } = require("playwright");

(async () => {
  const url = process.argv[2] || "http://127.0.0.1:5173";
  const output = process.argv[3] || "/tmp/hive-intake";
  const browser = await chromium.launch();
  try {
    const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(`${url}/p/p-muse`, { waitUntil: "networkidle" });
    const ready = page.locator(".scout-ready");
    await ready.waitFor();
    assert.match(await ready.innerText(), /opencode · opencode\/muse-spark-1\.3-contributor-free/);
    assert.equal(await page.locator(".scout-blocked").count(), 0);
    await page.screenshot({ path: `${output}-desktop.png`, fullPage: true });

    await page.setViewportSize({ width: 390, height: 844 });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
    await page.screenshot({ path: `${output}-narrow.png`, fullPage: true });

    await page.getByRole("button", { name: "start intake", exact: true }).click();
    const conversation = page.locator(".intake-brief header .muted");
    await conversation.waitFor();
    assert.equal(await conversation.innerText(), "opencode opencode/muse-spark-1.3-contributor-free");
    assert.equal(await page.getByRole("button", { name: "intake started", exact: true }).isDisabled(), true);
    assert.deepEqual(errors, []);
    await page.screenshot({ path: `${output}-running.png`, fullPage: true });
    console.log(`OpenCode intake passed; screenshots: ${output}-{desktop,narrow,running}.png`);
  } finally {
    await browser.close();
  }
})().catch((error) => { console.error(error); process.exitCode = 1; });

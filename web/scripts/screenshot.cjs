// Grab a full-page PNG of a URL, for visually verifying UI changes.
//   node scripts/screenshot.cjs <url> <out.png> [--click "text"]...
// Each --click clicks the first element containing that text (tiles, buttons)
// before the shot, so drill-down states are reachable without a real backend.
const { chromium } = require("playwright");

(async () => {
  const args = process.argv.slice(2);
  const clicks = [];
  const positional = [];
  for (let i = 0; i < args.length; i++) {
    if (args[i] === "--click") clicks.push(args[++i]);
    else positional.push(args[i]);
  }
  const url = positional[0] || "http://localhost:5173/";
  const out = positional[1] || "/tmp/hive-web.png";
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await page.goto(url, { waitUntil: "networkidle" });
  for (const text of clicks) {
    await page.getByText(text, { exact: false }).first().click();
    await page.waitForTimeout(400);
  }
  await page.waitForTimeout(600);
  await page.screenshot({ path: out, fullPage: true });
  await browser.close();
  console.log("wrote", out);
})();

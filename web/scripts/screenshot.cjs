// Grab a full-page PNG of a URL. Usage: node scripts/screenshot.cjs <url> <out.png>
const { chromium } = require("playwright");

(async () => {
  const url = process.argv[2] || "http://localhost:5173/";
  const out = process.argv[3] || "/tmp/hive-web.png";
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
  await page.goto(url, { waitUntil: "networkidle" });
  await page.waitForTimeout(600);
  await page.screenshot({ path: out, fullPage: true });
  await browser.close();
  console.log("wrote", out);
})();

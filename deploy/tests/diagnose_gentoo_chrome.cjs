/* Browser fixture uses the actual dashboard JS through an isolated TLS Nginx proxy. */
"use strict";
const {chromium} = require(process.env.PLAYWRIGHT_LIB || "playwright-core");
(async() => {
  const browser = await chromium.launch({
    executablePath: "/usr/bin/google-chrome",
    headless: true,
    args: [
      "--no-sandbox", "--disable-dev-shm-usage", "--no-proxy-server",
      "--host-resolver-rules=MAP sirussofts.com 127.0.0.1"
    ]
  });
  try {
    const context = await browser.newContext({
      ignoreHTTPSErrors: true,
      httpCredentials: {
        username: "admin",
        password: "test-password-for-fixture-12345"
      }
    });
    const page = await context.newPage();
    const cdp = await context.newCDPSession(page);
    await cdp.send("Network.enable");
    cdp.on("Network.webSocketHandshakeResponseReceived", x =>
      console.log("Chrome WebSocket response:", x.response.status, x.response.statusText));
    cdp.on("Network.webSocketFrameError", x =>
      console.log("Chrome WebSocket frame error:", x.errorMessage));
    cdp.on("Network.webSocketClosed", x =>
      console.log("Chrome WebSocket closed"));
    page.on("console", m => console.log("Chrome console:", m.type(), m.text().slice(0,250)));
    page.on("pageerror", e => console.log("Chrome page error:", e.message));
    page.on("response", r => {
      if (r.url().includes("/live-token") || r.url().includes("dashboard_live.js"))
        console.log("Chrome HTTP:", r.status(), new URL(r.url()).pathname);
    });
    const res = await page.goto("https://sirussofts.com/Gentoo/", {
      waitUntil: "domcontentloaded", timeout: 20000
    });
    console.log("Chrome dashboard HTTP:", res.status());
    await page.waitForTimeout(7000);
    const status = await page.locator("#live-status").textContent();
    console.log("Chrome dashboard live status:", status);
    console.log("Chrome dashboard CPU:", await page.locator("#live-cpu").textContent());
    if (!status.includes("connected (WebSocket)")) {
      throw new Error("Chrome does not maintain real WebSocket; status: " + status);
    }
    console.log("PASS Chromium signed WebSocket over isolated HTTPS Nginx path");
  } finally {await browser.close();}
})().catch(e => {console.error("FAIL Chrome fixture:", e.message);process.exitCode=1;});

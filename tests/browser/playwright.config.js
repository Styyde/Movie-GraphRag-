const fs = require("node:fs");
const path = require("node:path");
const { defineConfig } = require("@playwright/test");

if (!fs.existsSync("/.dockerenv")) {
  throw new Error("Run browser tests in Docker: docker compose --profile test run --rm browser-tests");
}

const artifacts = process.env.BROWSER_ARTIFACTS || "/artifacts";

module.exports = defineConfig({
  testDir: __dirname,
  testMatch: "*.spec.js",
  workers: 1,
  fullyParallel: false,
  retries: 0,
  timeout: 360000,
  expect: { timeout: 15000 },
  outputDir: path.join(artifacts, "test-results"),
  reporter: [["list"], ["junit", { outputFile: path.join(artifacts, "results.xml") }]],
  use: {
    baseURL: process.env.API_BASE_URL || "http://api:8000",
    browserName: "chromium",
    headless: true,
    viewport: { width: 1440, height: 1000 },
    locale: "fr-FR",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
});

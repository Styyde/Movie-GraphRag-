// Real Chromium, HTTP, Neo4j and Groq. No routing, interception or response substitution.
const fs = require("node:fs");
const path = require("node:path");
const { test, expect } = require("@playwright/test");

const artifacts = process.env.BROWSER_ARTIFACTS || "/artifacts";
let pageErrors = [];
const isQuestion = (response) => response.url().split("?")[0].endsWith("/questions")
  && response.request().method() === "POST";

test.beforeAll(async ({ playwright }) => {
  fs.mkdirSync(artifacts, { recursive: true });
  const api = await playwright.request.newContext({
    baseURL: process.env.API_BASE_URL || "http://api:8000", timeout: 10000,
  });
  const deadline = Date.now() + 120000;
  try {
    while (true) {
      try {
        const response = await api.get("/ready");
        if (response.ok() && (await response.json()).ready === true) break;
      } catch { /* Startup may still be in progress; never replace the service. */ }
      if (Date.now() >= deadline) {
        throw new Error("Real API must be ready with the actual Neo4j snapshot, E5 and Groq configuration");
      }
      await new Promise((resolve) => setTimeout(resolve, 1000));
    }
  } finally { await api.dispose(); }
});

test.beforeEach(async ({ page }) => {
  pageErrors = [];
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  const response = await page.goto("/", { waitUntil: "domcontentloaded" });
  expect(response.ok()).toBeTruthy();
  await expect(page.locator("#readiness")).toContainText(/prêt/i, { timeout: 30000 });
});

test.afterEach(async () => { expect(pageErrors).toEqual([]); });

async function submit(page, question, limit = 5, keyboard = false) {
  await page.locator("#question").fill(question);
  await page.locator("#limit").selectOption(String(limit));
  const pending = page.waitForResponse(isQuestion, { timeout: 300000 });
  if (keyboard) {
    await page.locator("#question").focus();
    await page.keyboard.press("Tab");
    await expect(page.locator("#limit")).toBeFocused();
    await page.keyboard.press("Tab");
    await expect(page.locator("#search-submit")).toBeFocused();
    await page.keyboard.press("Enter");
  } else await page.locator("#search-submit").click();
  const response = await pending;
  expect(response.ok(), `Actual /questions response: HTTP ${response.status()}`).toBeTruthy();
  return response.json();
}

async function assertCards(page, body, entityType = "Movie") {
  const cards = page.locator("#results .result-card");
  await expect(cards).toHaveCount(body.results.length);
  const expected = body.results.map((item) => String(item.id)).sort();
  const actual = await cards.evaluateAll((items) => items.map((card) => card.dataset.entityId));
  expect(actual.sort()).toEqual(expected);
  const types = await cards.evaluateAll((items) => items.map((card) => card.dataset.entityType));
  expect([...new Set(types)]).toEqual([entityType]);
  await expect(page.locator("#answer")).not.toBeEmpty();
  await expect(page.locator("#status-message")).toHaveAttribute("role", "status");
}

test("graph keyboard journey and inception sources", async ({ page, request }) => {
  await page.screenshot({ path: path.join(artifacts, "desktop-home.png"), fullPage: true });
  const body = await submit(page,
    "Quels films ont été réalisés par Christopher Nolan avec Cillian Murphy ?", 5, true);
  expect(body.status, JSON.stringify(body)).toBe("ok");
  expect(body.strategy).toBe("GRAPH");
  expect(body.results.map((item) => item.id).sort((a, b) => a - b)).toEqual([155, 272, 27205, 49026]);
  await assertCards(page, body);
  const inception = page.locator('.result-card[data-entity-id="27205"]');
  await expect(inception).toContainText("Inception");
  const proof = inception.locator("details").filter({ has: page.locator("summary", { hasText: "Pourquoi ce résultat ?" }) });
  await proof.locator("summary").click();
  await expect(proof).toHaveAttribute("open", "");
  await expect(proof).toContainText("Christopher Nolan");
  await expect(proof).toContainText("Cillian Murphy");
  const details = inception.locator("details").filter({ has: page.locator("summary", { hasText: "Détails du film" }) });
  await details.locator("summary").click();
  const source = await request.get("/movies/27205");
  expect(source.ok()).toBeTruthy();
  const movie = await source.json();
  await expect(details).toContainText(movie.overview);
  expect(movie.genre_names.length).toBeGreaterThan(0);
  for (const genre of movie.genre_names) await expect(details).toContainText(genre);
  await page.screenshot({ path: path.join(artifacts, "desktop-graph-evidence.png"), fullPage: true });
});

test("mobile hybrid journey preserves real evidence", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: path.join(artifacts, "mobile-home.png"), fullPage: true });
  const body = await submit(page, "Quels films de Nolan avec DiCaprio parlent des rêves ?");
  expect(body.status, JSON.stringify(body)).toBe("ok");
  expect(body.strategy).toBe("HYBRID_GRAPH_FIRST");
  expect(body.results.map((item) => item.id)).toEqual([27205]);
  await assertCards(page, body);
  const proof = page.locator("#results details").filter({ has: page.locator("summary", { hasText: "Pourquoi ce résultat ?" }) });
  await proof.locator("summary").click();
  await expect(proof).toHaveAttribute("open", "");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1),
    "Mobile horizontal overflow").toBeTruthy();
  const proofs = new Map(body.evidence.map((item) => [item.id, item]));
  const kinds = new Set(body.results.flatMap((item) => item.evidence_ids.map((ref) => proofs.get(ref).kind)));
  expect(kinds.has("relation")).toBeTruthy();
  expect(kinds.has("overview") || kinds.has("keyword")).toBeTruthy();
  if (body.warnings.length) await expect(page.locator("#warnings")).not.toBeEmpty();
  await page.screenshot({ path: path.join(artifacts, "mobile-hybrid-evidence.png"), fullPage: true });
});

test("clarification chooses actual tom holland then handles absent entity", async ({ page }) => {
  const body = await submit(page, "Quels films ont Tom Holland au casting ?");
  expect(body.status, JSON.stringify(body)).toBe("needs_clarification");
  const form = page.locator("#clarification-form");
  await expect(form).toBeVisible();
  const ambiguous = body.resolved_entities.filter((entity) => entity.status === "ambiguous");
  expect(ambiguous).toHaveLength(1);
  const mention = ambiguous[0].mention.ref;
  await form.locator(`input[type="radio"][name="${mention}"][value="1136406"]`).check();
  const pending = page.waitForResponse(isQuestion, { timeout: 300000 });
  await form.getByRole("button", { name: "Confirmer les choix" }).click();
  const resolved = await (await pending).json();
  expect(resolved.status, JSON.stringify(resolved)).toBe("ok");
  expect(resolved.resolved_entities.some((entity) => entity.canonical_id === 1136406)).toBeTruthy();
  await assertCards(page, resolved);
  const absent = await submit(page, "Quels films sont réalisés par UnePersonneInexistanteXYZ ?");
  expect(absent.status, JSON.stringify(absent)).toBe("entity_not_found");
  await expect(page.locator("#results .result-card")).toHaveCount(0);
  await expect(page.locator("#clarification-form")).not.toBeVisible();
  await expect(page.locator("#status-message")).toContainText(/absent|introuvable|aucun|données/i);
});

test("real director pagination reuses cached previous page", async ({ page }) => {
  const first = await submit(page, "Quels réalisateurs ont travaillé avec Leonardo DiCaprio ?");
  expect(first.status, JSON.stringify(first)).toBe("ok");
  expect(first.coverage.total_candidates).toBe(17);
  await assertCards(page, first, "Person");
  await expect(page.locator("#previous-page")).toBeDisabled();
  await expect(page.locator("#next-page")).toBeEnabled();
  const pending = page.waitForResponse(isQuestion, { timeout: 300000 });
  await page.locator("#next-page").click();
  const second = await (await pending).json();
  expect(second.status, JSON.stringify(second)).toBe("ok");
  expect(second.coverage.offset).toBe(5);
  await assertCards(page, second, "Person");
  const firstIds = new Set(first.results.map((item) => item.id));
  expect(second.results.some((item) => firstIds.has(item.id))).toBeFalsy();
  await expect(page.locator("#previous-page")).toBeEnabled();
  const requests = [];
  const observe = (request) => {
    if (request.method() === "POST" && request.url().split("?")[0].endsWith("/questions")) requests.push(request.url());
  };
  page.on("request", observe);
  try {
    await page.locator("#previous-page").click();
    await assertCards(page, first, "Person");
    await expect(page.locator("#page-label")).toHaveText("Page 1");
    await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    expect(requests, "Cached previous page must not trigger another analysis/Groq request").toEqual([]);
  } finally { page.removeListener("request", observe); }
});

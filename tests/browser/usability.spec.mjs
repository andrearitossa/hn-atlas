import { test, expect } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', response => {
    if (response.url().startsWith('http://127.0.0.1:8791/') && response.status() >= 400) {
      errors.push(`${response.status()} ${response.url()}`);
    }
  });
  page.__usabilityErrors = errors;
});

test.afterEach(async ({ page }) => {
  expect(page.__usabilityErrors, 'Browser errors or missing public assets').toEqual([]);
});

async function fitsScreen(page) {
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1),
    'Page should not overflow horizontally').toBe(true);
}

test('home map loads, zooms, and opens a topic from search', async ({ page }) => {
  await page.goto('/');
  const dot = page.locator('#map .topic-dot').first();
  await expect(dot).toBeVisible();
  const name = (await dot.getAttribute('aria-label')).replace(/^Explore /, '');
  const layer = page.locator('#map > g').first();
  const before = await layer.getAttribute('transform');
  await page.locator('#map-section').getByRole('button', { name: 'Zoom in', exact: true }).click();
  await expect.poll(() => layer.getAttribute('transform')).not.toBe(before);
  await page.locator('#map-section').getByRole('button', { name: 'Reset zoom', exact: true }).click();
  await page.getByRole('searchbox', { name: 'Find a topic on the map', exact: true }).fill(name);
  await page.locator('#map-results').getByRole('button', { name, exact: true }).click();
  await expect(page.locator('#map-preview')).toBeVisible();
  await fitsScreen(page);
  await page.locator('#map-preview a.preview-open').click();
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(name);
});

test('browse, search, clear with keyboard, and open a topic', async ({ page }) => {
  await page.goto('/#/topics');
  const cards = page.locator('.topic-card');
  await expect(cards.first()).toBeVisible();
  const name = await cards.first().locator('strong').innerText();
  const search = page.getByRole('searchbox', { name: 'Search topics', exact: true });
  await search.fill('zzzz_nonexistent_topic_982374');
  await expect(page.getByText('No topics found', { exact: true })).toBeVisible();
  await search.press('Escape');
  await expect(cards.first()).toBeVisible();
  await search.fill(name);
  await expect(cards.first().locator('strong')).toHaveText(name);
  await page.getByRole('button', { name: 'A–Z', exact: true }).click();
  await expect(page.getByRole('button', { name: 'A–Z', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await fitsScreen(page);
  await cards.first().click();
  await expect(page).toHaveURL(/\/topic\/[^/]+\/$/);
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(name);
  await page.reload();
  await expect(page.getByRole('heading', { level: 1 })).toHaveText(name);
  await fitsScreen(page);
});

test('all timeline levels work offline from the initial HTML', async ({ page, context }) => {
  await page.goto('/#/topics');
  await page.locator('.topic-card').first().click();
  await expect(page.getByRole('heading', { level: 1 })).toBeVisible();
  const requests = [];
  page.on('request', request => requests.push(request.url()));
  await context.setOffline(true);
  const timeline = page.locator('#topic-history');
  for (const [key, name] of [['month', 'Month by month'], ['week', 'Week by week']]) {
    const count = await page.locator(`#zt-${key}`).evaluate(template => template.content.querySelectorAll('.zt-period').length);
    expect(count).toBeGreaterThan(0);
    await timeline.getByRole('button', { name: 'Zoom in', exact: true }).click();
    await expect(page.locator('#zt-scale-name')).toHaveText(name);
    await expect(page.locator('#zt-periods .zt-period')).toHaveCount(count);
    await expect(page.locator('#zt-periods .post').first()).toBeVisible();
    await expect(page.locator('#timeline-status')).toBeEmpty();
  }
  await page.getByRole('button', { name: 'Reset view', exact: true }).click();
  await expect(page.locator('#zt-scale-name')).toHaveText('Year by year');
  expect(requests, 'Changing timeline detail must not request any files').toEqual([]);
});

test('story filters recover from empty results and feedback is keyboard dismissible', async ({ page }) => {
  await page.goto('/#/topics');
  await page.locator('.topic-card').first().click();
  await page.getByRole('combobox', { name: 'Story period', exact: true }).selectOption('0');
  await expect(page.locator('#story-results .post').first()).toBeVisible();
  await page.getByRole('searchbox', { name: 'Search stories', exact: true }).fill('zzzz_nonexistent_story_982374');
  await page.locator('#story-search').getByRole('button', { name: 'Search', exact: true }).click();
  await expect(page.locator('#story-status')).toContainText('No stories match');
  await page.getByRole('button', { name: 'Clear filters', exact: true }).click();
  await expect(page.getByRole('searchbox', { name: 'Search stories', exact: true })).toHaveValue('');
  await page.getByRole('combobox', { name: 'Story period', exact: true }).selectOption('0');
  await expect(page.locator('#story-results .post').first()).toBeVisible();
  await page.getByRole('combobox', { name: 'Sort stories', exact: true }).selectOption('newest');
  await expect(page.locator('#archive-context')).toContainText('Newest');
  await page.getByRole('button', { name: 'Feedback', exact: true }).click();
  await expect(page.getByRole('dialog')).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'Your feedback', exact: true })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).not.toBeVisible();
  await fitsScreen(page);
});

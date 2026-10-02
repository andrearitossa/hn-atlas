import {test, expect} from '@playwright/test';

for (const path of ['/', '/topic/current/', '/analytics/', '/about/', '/404.html']) {
  test(`shared footer and feedback work on ${path}`, async ({page}) => {
    await page.goto(path);
    const footer = page.locator('.site-footer');
    await expect(footer).toBeVisible();
    expect(await footer.evaluate(el => el.parentElement === document.body)).toBe(true);
    const before = await footer.boundingBox();
    await page.locator('main').evaluate(el => { el.scrollTop = el.scrollHeight; });
    expect(await footer.boundingBox()).toEqual(before);
    expect(Math.abs(before.y + before.height - page.viewportSize().height)).toBeLessThan(2);
    await expect(footer.getByRole('link', {name:'About', exact:true})).toHaveAttribute('href', '/about/');
    await expect(footer.getByRole('link')).toHaveCount(1);
    await footer.getByRole('button', {name:'Feedback', exact:true}).click();
    await expect(page.locator('#feedback-dialog')).toBeVisible();
    await expect(page.locator('.feedback-send')).toBeEnabled();
    await page.keyboard.press('Escape');
    await expect(page.locator('#feedback-dialog')).not.toBeVisible();
    await footer.getByRole('link', {name:'About', exact:true}).click();
    await expect(page).toHaveURL(/\/about\/$/);
    await expect(page.getByRole('heading', {name:'Good ideas lead somewhere.'})).toBeVisible();
  });
}

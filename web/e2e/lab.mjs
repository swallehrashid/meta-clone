// Shared by the legacy dashboard suites: the old all-in-one lab now lives in the back office (/admin -> "Chart lab").
// Set LAB_ADMIN_PASSWORD to the admin password (LAB_ADMIN_USER defaults to "admin").
export async function openChartLab(page, base) {
  await page.goto(`${base}/admin`)
  await page.waitForSelector('[data-testid=login], [data-testid=admin]')
  if (await page.$('[data-testid=login]')) {
    const pw = process.env.LAB_ADMIN_PASSWORD
    if (!pw) throw new Error('set LAB_ADMIN_PASSWORD (the back-office password) to run the dashboard e2e suites')
    await page.fill('[data-testid=login-user]', process.env.LAB_ADMIN_USER ?? 'admin')
    await page.fill('[data-testid=login-pass]', pw)
    await page.click('[data-testid=login-submit]')
    await page.waitForSelector('[data-testid=admin]')
  }
  await page.click('[data-testid=tab-chart]')
}

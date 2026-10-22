import { test } from "@playwright/test";

// Playwright: a locator action waits for its element and fails the test when
// it never appears, so a click-through flow is checked.
test("opens the queue page", async ({ page }) => {
  await page.goto("/queue");
  await page.getByRole("button", { name: "Drain" }).click();
});

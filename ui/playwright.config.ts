import { defineConfig, devices } from '@playwright/test';

const viewports = {
  desktop: { width: 1920, height: 1080 },
  laptop: { width: 1440, height: 900 },
  smallLaptop: { width: 1280, height: 720 },
  tablet: { width: 768, height: 1024 },
  mobile: { width: 390, height: 844 },
  smallMobile: { width: 360, height: 800 },
};

export default defineConfig({
  testDir: './e2e',
  outputDir: '../qa-results/playwright-artifacts',
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  reporter: [
    ['list'],
    ['json', { outputFile: '../qa-results/playwright-results.json' }],
    ['html', { outputFolder: '../qa-results/playwright-report', open: 'never' }],
  ],
  use: {
    baseURL: 'http://127.0.0.1:4173',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
  },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 4173',
    url: 'http://127.0.0.1:4173',
    reuseExistingServer: false,
    timeout: 30_000,
  },
  projects: Object.entries(viewports).map(([name, viewport]) => ({
    name,
    use: { ...devices['Desktop Chrome'], viewport },
  })),
});
// Runs speed.html in Chromium and prints a Markdown table.
import {build, preview} from 'vite';
import {chromium} from '@playwright/test';
// Production build: React development checks would unfairly slow the React-based renderers.
const config = {configFile: false, root: import.meta.dirname, logLevel: 'silent', resolve: {dedupe: ['react', 'react-dom']}};
await build({...config, build: {outDir: 'dist', emptyOutDir: true, rollupOptions: {input: 'speed.html'}}});
const server = await preview({...config, preview: {port: 4319, host: '127.0.0.1'}, build: {outDir: 'dist'}});
const browser = await chromium.launch(process.env.STREAM_BROWSER_PATH ? {executablePath: process.env.STREAM_BROWSER_PATH} : {});
const page = await browser.newPage();
page.on('pageerror', e => console.error(e));
const cdp = await page.context().newCDPSession(page);
await cdp.send('Performance.enable');
// Main-thread busy time (scripting + style + layout + paint tasks) from Chrome's own counters.
await page.exposeFunction('cpuMs', async () => {
  const {metrics} = await cdp.send('Performance.getMetrics');
  return metrics.find(m => m.name === 'TaskDuration').value * 1000;
});
await page.goto('http://127.0.0.1:4319/speed.html');
await page.waitForFunction(() => window.runBench);
// Three rounds; report the median main-thread time per renderer.
const rounds = [];
for (let i = 0; i < 3; i++) rounds.push(await page.evaluate(only => window.runBench(only), process.env.ONLY ?? null));
const median = values => [...values].sort((a, b) => a - b)[1];
const results = rounds[0].map((r, k) => ({...r,
  cpuMs: median(rounds.map(x => x[k].cpuMs)), maxMs: median(rounds.map(x => x[k].maxMs)), lastAvgMs: median(rounds.map(x => x[k].lastAvgMs))}));
console.log(`Document: ${results[0].chars} characters in ${results[0].chunks} chunks; ${await browser.version()}\n`);
console.log('| Renderer | Main-thread time, whole reply | Slowest update | Avg update, last 20 chunks | Characters rendered |\n|---|--:|--:|--:|--:|');
for (const r of results) console.log(`| ${r.name} | ${r.cpuMs} ms | ${r.maxMs} ms | ${r.lastAvgMs} ms | ${r.renderedChars} |`);
await browser.close();
await server.httpServer.close();


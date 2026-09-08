#!/usr/bin/env node
/**
 * Records docs/report.gif: the HTML output, driven like a person would use it.
 *
 * The demo.gif shows the tool running. This one shows what it produces --
 * the ranked dashboard, then one company's redline with the filters working --
 * which is the half that a screenshot cannot convey and the half that decides
 * whether the output is worth generating.
 *
 *   dd-to-signal demo -o build/site
 *   node scripts/record-report-gif.mjs
 */

import { mkdirSync, statSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { chromium } from '@playwright/test';
import gifenc from 'gifenc';
import pngjs from 'pngjs';

const { GIFEncoder, applyPalette, quantize } = gifenc;
const { PNG } = pngjs;

const SITE = resolve('build/site');
const OUT = 'docs/report.gif';
const WIDTH = 900;
const HEIGHT = 620;

const browser = await chromium.launch();
const page = await browser.newPage({
  viewport: { width: WIDTH, height: HEIGHT },
  deviceScaleFactor: 1,
});

const frames = [];

async function shoot(delay = 90) {
  const png = PNG.sync.read(await page.screenshot({ type: 'png' }));
  frames.push({ data: new Uint8Array(png.data), delay });
}

/** Draw a cursor the viewer can follow, since a screenshot has none. */
async function installCursor() {
  await page.evaluate(() => {
    const cursor = document.createElement('div');
    cursor.id = '__cursor';
    cursor.style.cssText =
      'position:fixed;z-index:2147483647;width:22px;height:22px;' +
      'pointer-events:none;transition:none';
    cursor.innerHTML =
      '<svg viewBox="0 0 24 24" width="22" height="22">' +
      '<path d="M5 3l14 8.5-6 1.2L10.5 19z" fill="#fff" stroke="#111" ' +
      'stroke-width="1.4" stroke-linejoin="round"/></svg>';
    document.body.append(cursor);
    window.__moveCursor = (x, y) => {
      cursor.style.left = `${x}px`;
      cursor.style.top = `${y}px`;
    };
    window.__at = { x: 60, y: 80 };
  });
}

async function glide(to, steps = 6) {
  const from = (await page.evaluate(() => window.__at)) ?? { x: 60, y: 80 };
  for (let i = 1; i <= steps; i++) {
    const x = from.x + ((to.x - from.x) * i) / steps;
    const y = from.y + ((to.y - from.y) * i) / steps;
    await page.mouse.move(x, y);
    await page.evaluate(({ x, y }) => window.__moveCursor(x, y), { x, y });
    await shoot(55);
  }
  await page.evaluate((at) => (window.__at = at), to);
}

async function glideTo(selector, steps = 6) {
  const box = await page.locator(selector).first().boundingBox();
  if (!box) throw new Error(`no element for ${selector}`);
  const to = { x: box.x + box.width / 2, y: box.y + box.height / 2 };
  await glide(to, steps);
  return to;
}

/** Scroll in small increments so the GIF shows movement rather than a jump. */
async function scrollBy(total, steps = 6) {
  for (let i = 0; i < steps; i++) {
    await page.mouse.wheel(0, total / steps);
    await page.waitForTimeout(40);
    await shoot(60);
  }
}

async function open(file) {
  await page.goto(pathToFileURL(`${SITE}/${file}`).href, { waitUntil: 'load' });
  await installCursor();
}

// 1. The ranked dashboard: six companies, sorted by how much moved.
await open('index.html');
await shoot(1400);
await scrollBy(260, 5);
await shoot(900);

// 2. Into Apple's redline.
await glideTo('table.scan a[href$=".html"]');
await shoot(700);
await page.locator('table.scan a[href$=".html"]').first().click();
await page.waitForLoadState('load');
await installCursor();
await shoot(1500);

// 3. The metrics and the topic chips.
await scrollBy(240, 5);
await shoot(1100);

// 4. Filter down to the paragraphs that are new this year.
await glideTo('.panel:not([hidden]) .filter[data-kind="added"]');
await page.locator('.panel:not([hidden]) .filter[data-kind="added"]').click();
await shoot(1800);

// 5. And read one.
await scrollBy(300, 5);
await shoot(2600);

await browser.close();

const encoder = GIFEncoder();
for (const frame of frames) {
  const palette = quantize(frame.data, 256, { format: 'rgb444' });
  const index = applyPalette(frame.data, palette, 'rgb444');
  encoder.writeFrame(index, WIDTH, HEIGHT, { palette, delay: frame.delay });
}
encoder.finish();

mkdirSync(dirname(OUT), { recursive: true });
writeFileSync(OUT, encoder.bytes());

const mb = (statSync(OUT).size / 1048576).toFixed(1);
console.log(`  wrote ${OUT}  ${frames.length} frames  ${mb} MB`);

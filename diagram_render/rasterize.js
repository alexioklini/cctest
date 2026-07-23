#!/usr/bin/env node
// rasterize.js — screenshot a (post-processed) mermaid SVG file to PNG.
//
// Usage: node rasterize.js <svg-path> <out-png> [cssWidth] [scale] [background]
//
// Part of the brand-gradient pipeline (engine/tools/image_gen.py
// _run_mmdc_pipeline): mmdc renders SVG, Python injects the gradient <defs>,
// this script rasterizes the result — because mmdc offers no hook to add SVG
// defs before its own screenshot. Uses the puppeteer that ships as a
// mermaid-cli dependency (no extra install). Mirrors mmdc raster semantics:
// cssWidth = page/CSS width the diagram is laid out at, scale =
// deviceScaleFactor, background 'transparent' → alpha PNG.
const fs = require('fs');
const path = require('path');
const puppeteer = require(path.join(__dirname, 'node_modules', 'puppeteer'));

(async () => {
  const [svgPath, outPath, widthArg, scaleArg, bgArg] = process.argv.slice(2);
  if (!svgPath || !outPath) {
    console.error('usage: rasterize.js <svg-path> <out-png> [width] [scale] [background]');
    process.exit(2);
  }
  const width = Math.max(200, parseInt(widthArg || '2000', 10) || 2000);
  const scale = Math.min(5, Math.max(1, parseFloat(scaleArg || '2') || 2));
  const bg = (bgArg || 'white') === 'transparent' ? 'transparent' : '#ffffff';
  const svg = fs.readFileSync(svgPath, 'utf8');

  const browser = await puppeteer.launch({ headless: true });
  try {
    const page = await browser.newPage();
    await page.setViewport({ width, height: 600, deviceScaleFactor: scale });
    const html = '<!doctype html><html><head><meta charset="utf-8"><style>' +
      '*{margin:0;padding:0}body{background:' + bg + '}' +
      'svg{display:block;width:' + width + 'px !important;height:auto !important;max-width:none !important}' +
      '</style></head><body>' + svg + '</body></html>';
    await page.setContent(html, { waitUntil: 'networkidle0' });
    // Embedded data-URI fonts (fa icon subset) must be ready before shooting.
    await page.evaluate(() => (document.fonts ? document.fonts.ready : Promise.resolve()));
    const el = await page.$('svg');
    if (!el) throw new Error('no <svg> element after load');
    // Chrome's compositor caps captures at 16384 physical px per side; beyond
    // that the capture-beyond-viewport path TILES the paint and stacks the
    // diagram twice into one PNG (seen with a tall graph at width 2800 ×
    // scale 3 → 18918 physical px). Clamp the effective scale so BOTH sides
    // stay under the cap, size the viewport to the whole element, and capture
    // strictly in-viewport — a single clean paint at any diagram size.
    const box = await el.boundingBox();
    const cssH = Math.ceil((box && box.height) || 600);
    const MAXPX = 16000;
    const eff = Math.max(0.2, Math.min(scale, MAXPX / width, MAXPX / cssH));
    await page.setViewport({ width, height: cssH, deviceScaleFactor: eff });
    await el.screenshot({ path: outPath, omitBackground: bg === 'transparent', captureBeyondViewport: false });
  } finally {
    await browser.close();
  }
})().catch((e) => { console.error(e && e.message ? e.message : e); process.exit(1); });

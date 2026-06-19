import { ReadableStream } from 'node:stream/web';
if (!global.ReadableStream) global.ReadableStream = ReadableStream;

import fs from 'fs/promises';
import path from 'path';
import { fileURLToPath } from 'url';
import puppeteer from 'puppeteer';

const [,, inPath, outPath = 'out.pdf'] = process.argv;
if (!inPath) {
  console.error('Usage: node html-to-pdf.mjs <input.html|.md> [output.pdf]');
  process.exit(1);
}

const html = await fs.readFile(inPath, 'utf8');

const cssFile = path.join(
  path.dirname(fileURLToPath(import.meta.url)),
  'node_modules/github-markdown-css/github-markdown-light.css'
);
const css = await fs.readFile(cssFile, 'utf8');

const fullDoc = `
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<style>${css}</style>
<style>body{margin:2rem;}</style>
</head>
<body class="markdown-body">
${html}
</body>
</html>`;

const browser = await puppeteer.launch({ headless: 'new' });
const page = await browser.newPage();

await page.setViewport({ width: 1000, height: 800 });

await page.setContent(fullDoc, { waitUntil: 'networkidle0' });

await page.pdf({
  path: outPath,
  format: 'Letter',
  margin: { top: '20mm', bottom: '20mm', left: '20mm', right: '20mm' },
  printBackground: true,
  scale: 0.8
});

await browser.close();

console.log(`PDF written to ${outPath}`);

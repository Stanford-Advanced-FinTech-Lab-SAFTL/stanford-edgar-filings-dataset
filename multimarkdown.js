#!/usr/bin/env node

const fs = require('fs');
const md = require('markdown-it')({
  html: true
})
  .use(require('markdown-it-multimd-table'), {
    rowspan: true,
    multiline: true,
    headerless: true
  })
  .use(require('markdown-it-sup'))
  .use(require('markdown-it-sub'));

const input = process.argv[2];
if (!input) {
  console.error('Usage: ./multimarkdown.js <file.md>');
  process.exit(1);
}

const text = fs.readFileSync(input, 'utf8');
process.stdout.write(md.render(text));

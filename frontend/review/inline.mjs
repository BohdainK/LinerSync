// Folds the built JS and CSS into one HTML file that opens anywhere, with no server.
import { readFile, writeFile } from "node:fs/promises";
import path from "node:path";

const dir = path.resolve(import.meta.dirname, "../dist-review");
const read = (file) => readFile(path.join(dir, file), "utf8");
let html = await read("index.html");

for (const [tag, file] of [...html.matchAll(/<link rel="stylesheet"[^>]*href="\.\/(assets\/[^"]+\.css)">/g)]) {
  html = html.replace(tag, () => "");
  const css = await read(file);
  html = html.replace("</head>", () => `<style>${css}</style>\n</head>`);
}
html = html.replace(/<link rel="modulepreload"[^>]*>/g, "");
for (const [tag, file] of [...html.matchAll(/<script type="module"[^>]*src="\.\/(assets\/[^"]+\.js)"><\/script>/g)]) {
  // Inline modules need no fetch, so this works from file:// too.
  const js = (await read(file)).replace(/<\/script/gi, "<\\/script");
  html = html.replace(tag, () => "");
  html = html.replace("</body>", () => `<script type="module">${js}</script>\n</body>`);
}

const out = path.join(dir, "linersync-ui-review.html");
await writeFile(out, html);
console.log(`${out} (${(html.length / 1024).toFixed(0)} KB)`);

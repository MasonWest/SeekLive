// Run with Node.js and sharp available: node tools/generate_icons.cjs
// All installed-app icons derive from the editable SVG; no source JPG is needed.
const fs = require('node:fs/promises');
const path = require('node:path');
const sharp = require('sharp');

async function main() {
  const web = path.resolve(__dirname, '../web');
  const svg = await fs.readFile(path.join(web, 'favicon.svg'), 'utf8');
  // Platforms supply their own corner masks. PNGs need an opaque full-bleed base.
  const square = svg;
  // Inset the enlarged stack for launcher masks, keeping its full silhouette safe.
  const maskable = square.replace('scale(1.34)', 'scale(1.12)');
  const out = path.join(web, 'icons');
  await fs.mkdir(out, { recursive: true });
  for (const [name, size, source] of [
    ['icon-192.png', 192, square],
    ['icon-512.png', 512, square],
    ['apple-touch-icon.png', 180, square],
    ['icon-maskable-512.png', 512, maskable],
  ]) {
    await sharp(Buffer.from(source), { density: 288 }).resize(size, size).png()
      .toFile(path.join(out, name));
    console.log(`${name}: ${size}x${size}`);
  }
}

main().catch(error => { console.error(error); process.exitCode = 1; });

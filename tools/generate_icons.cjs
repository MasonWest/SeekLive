// Run with Node.js and sharp available: node tools/generate_icons.cjs
// All installed-app icons derive from the editable SVG; no source JPG is needed.
const fs = require('node:fs/promises');
const path = require('node:path');
const sharp = require('sharp');

async function main() {
  const web = path.resolve(__dirname, '../web');
  const svg = await fs.readFile(path.join(web, 'favicon.svg'), 'utf8');
  // Platforms supply their own corner masks. PNGs need an opaque full-bleed base.
  const square = svg.replace('rx="116" fill="url(#bg)"', 'fill="url(#bg)"');
  // Keep the entire photograph stack within the central maskable safe circle.
  const maskable = square.replace('<g filter="url(#shadow)">',
    '<g transform="translate(256 256) scale(.82) translate(-256 -256)" filter="url(#shadow)">');
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

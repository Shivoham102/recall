import sharp from 'sharp';
import { readFileSync, writeFileSync, mkdirSync } from 'fs';
import { fileURLToPath } from 'url';
import path from 'path';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const fontPath = path.join(__dirname, 'fonts', 'Inter-Regular.woff');
const fontB64 = readFileSync(fontPath).toString('base64');

const FONT_FACE = `@font-face {
  font-family: 'Inter';
  src: url('data:font/woff;base64,${fontB64}') format('woff');
  font-weight: 400;
  font-style: normal;
}`;

function sidebarSvg() {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="164" height="314">
  <style>${FONT_FACE}</style>
  <rect width="164" height="314" fill="#08080f"/>
  <circle cx="82" cy="118" r="12" fill="#00e5ff" opacity="0.08"/>
  <circle cx="82" cy="118" r="5" fill="#00e5ff"/>
  <text x="82" y="168" font-family="Inter, sans-serif" font-size="24" font-weight="400"
        fill="#00e5ff" text-anchor="middle" letter-spacing="4">Recall</text>
  <text x="82" y="192" font-family="Inter, sans-serif" font-size="10" font-weight="400"
        fill="#6464a0" text-anchor="middle" letter-spacing="1">Your AI memory</text>
  <line x1="56" y1="288" x2="108" y2="288" stroke="#00e5ff" stroke-width="1" opacity="0.25"/>
</svg>`;
}

function headerSvg() {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="150" height="57">
  <style>${FONT_FACE}</style>
  <rect width="150" height="57" fill="#08080f"/>
  <circle cx="20" cy="29" r="3" fill="#00e5ff"/>
  <text x="32" y="34" font-family="Inter, sans-serif" font-size="15" font-weight="400"
        fill="#00e5ff" letter-spacing="2">Recall</text>
  <line x1="0" y1="56" x2="150" y2="56" stroke="#00e5ff" stroke-width="1" opacity="0.15"/>
</svg>`;
}

function pngToBmp24(rgba, w, h) {
  const rowSize = Math.floor((w * 3 + 3) / 4) * 4;
  const pixelDataSize = rowSize * h;
  const buf = Buffer.alloc(54 + pixelDataSize, 0);

  // BITMAPFILEHEADER
  buf.write('BM', 0, 'ascii');
  buf.writeUInt32LE(54 + pixelDataSize, 2);
  buf.writeUInt32LE(54, 10);

  // BITMAPINFOHEADER
  buf.writeUInt32LE(40, 14);
  buf.writeInt32LE(w, 18);
  buf.writeInt32LE(h, 22); // positive = bottom-up (required by NSIS)
  buf.writeUInt16LE(1, 26);
  buf.writeUInt16LE(24, 28);
  buf.writeUInt32LE(0, 30); // BI_RGB
  buf.writeUInt32LE(pixelDataSize, 34);
  buf.writeInt32LE(2835, 38);
  buf.writeInt32LE(2835, 42);

  for (let y = 0; y < h; y++) {
    const srcRow = h - 1 - y; // flip: BMP bottom-up
    for (let x = 0; x < w; x++) {
      const si = (srcRow * w + x) * 4;
      const di = 54 + y * rowSize + x * 3;
      buf[di]     = rgba[si + 2]; // B
      buf[di + 1] = rgba[si + 1]; // G
      buf[di + 2] = rgba[si];     // R
    }
  }
  return buf;
}

async function svgToBmp(svgStr, width, height, outPath) {
  const png = await sharp(Buffer.from(svgStr))
    .resize(width, height, { fit: 'fill' })
    .png()
    .toBuffer();

  const { data, info } = await sharp(png).raw().toBuffer({ resolveWithObject: true });

  if (info.width !== width || info.height !== height) {
    throw new Error(`Dimension mismatch: got ${info.width}x${info.height}, expected ${width}x${height}`);
  }

  writeFileSync(outPath, pngToBmp24(data, width, height));
  console.log(`✓ ${path.basename(outPath)} (${width}×${height}, ${Math.round((54 + Math.floor((width * 3 + 3) / 4) * 4 * height) / 1024)}KB)`);
}

async function svgToPng(svgStr, width, height, outPath) {
  await sharp(Buffer.from(svgStr))
    .resize(width, height, { fit: 'fill' })
    .png()
    .toFile(outPath);
  console.log(`✓ ${path.basename(outPath)} (${width}×${height})`);
}

function dmgBackgroundSvg() {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="660" height="400">
  <style>${FONT_FACE}</style>
  <rect width="660" height="400" fill="#08080f"/>
  <!-- subtle grid lines -->
  <line x1="0" y1="399" x2="660" y2="399" stroke="#00e5ff" stroke-width="1" opacity="0.1"/>
  <line x1="0" y1="0" x2="660" y2="0" stroke="#00e5ff" stroke-width="1" opacity="0.05"/>
  <!-- top accent -->
  <rect x="0" y="0" width="660" height="2" fill="#00e5ff" opacity="0.15"/>
  <!-- app icon placeholder zone (left: x=180) -->
  <circle cx="180" cy="200" r="52" fill="#00e5ff" opacity="0.04"/>
  <circle cx="180" cy="200" r="3" fill="#00e5ff" opacity="0.3"/>
  <!-- applications folder zone (right: x=480) -->
  <circle cx="480" cy="200" r="52" fill="#00e5ff" opacity="0.04"/>
  <circle cx="480" cy="200" r="3" fill="#00e5ff" opacity="0.3"/>
  <!-- drag arrow -->
  <text x="330" y="207" font-family="Inter, sans-serif" font-size="22"
        fill="#00e5ff" text-anchor="middle" opacity="0.5">→</text>
  <!-- wordmark -->
  <text x="330" y="340" font-family="Inter, sans-serif" font-size="13"
        fill="#6464a0" text-anchor="middle" letter-spacing="3">RECALL</text>
  <line x1="290" y1="352" x2="370" y2="352" stroke="#00e5ff" stroke-width="1" opacity="0.2"/>
  <!-- drag hint -->
  <text x="330" y="375" font-family="Inter, sans-serif" font-size="11"
        fill="#6464a0" text-anchor="middle" opacity="0.6">Drag to Applications to install</text>
</svg>`;
}

const outDir = path.join(__dirname, '..', 'src-tauri', 'assets', 'installer');
mkdirSync(outDir, { recursive: true });

await svgToBmp(sidebarSvg(), 164, 314, path.join(outDir, 'sidebar.bmp'));
await svgToBmp(headerSvg(),  150,  57,  path.join(outDir, 'header.bmp'));

const dmgDir = path.join(__dirname, '..', 'src-tauri', 'assets', 'dmg');
mkdirSync(dmgDir, { recursive: true });
await svgToPng(dmgBackgroundSvg(), 660, 400, path.join(dmgDir, 'background.png'));

console.log('Done.');

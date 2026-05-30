// Derive the web favicon/logo from the same captured orb idle frame used for
// the app icons, so every logo is the exact shader orb. Trimmed tighter than
// the app icon (favicons want less padding). Run: node app/scripts/gen-web-favicon.mjs
import sharp from "sharp";
import { fileURLToPath } from "url";
import { dirname, resolve } from "path";

const here = dirname(fileURLToPath(import.meta.url));
const src = resolve(here, "../src-tauri/icons/orb-master.png");
const out = resolve(here, "../../web/public/favicon.png");

await sharp(src)
  .trim({ threshold: 8 })
  .resize(256, 256, { fit: "contain", background: { r: 0, g: 0, b: 0, alpha: 0 } })
  .png()
  .toFile(out);

console.log("wrote", out);

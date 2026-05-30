// Produce the app-icon master: the captured orb frame, trimmed of its
// transparent halo padding and re-centered to fill ~85% of a 1024 frame
// (the raw capture sits at ~56%, too small on a taskbar).
// Run: node app/scripts/gen-app-icon.mjs
import sharp from "sharp";
import { fileURLToPath } from "url";
import { dirname, resolve } from "path";

const here = dirname(fileURLToPath(import.meta.url));
const src = resolve(here, "../src-tauri/icons/orb-master.png");
const out = resolve(here, "../src-tauri/icons/orb-icon.png");

const SIZE = 1024;
const INNER = 1010; // ~98% — taskbar icons need to fill the frame
const pad = Math.round((SIZE - INNER) / 2);

const orb = await sharp(src)
  .trim({ threshold: 40 }) // cut the low-alpha halo so the solid orb dominates

  .resize(INNER, INNER, { fit: "contain", background: { r: 0, g: 0, b: 0, alpha: 0 } })
  .toBuffer();

await sharp(orb)
  .extend({ top: pad, bottom: pad, left: pad, right: pad, background: { r: 0, g: 0, b: 0, alpha: 0 } })
  .png()
  .toFile(out);

console.log("wrote", out);

// The logo is the exact orb idle frame (same capture as the app icons),
// rasterized to /favicon.png. Kept as a sized <img> so it matches everywhere.
export default function LogoIcon({ size = 28 }: { size?: number }) {
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src="/favicon.png"
      width={size}
      height={size}
      alt=""
      aria-hidden="true"
      style={{ display: 'block' }}
    />
  )
}

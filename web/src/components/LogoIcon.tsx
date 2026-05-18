export default function LogoIcon({ size = 28 }: { size?: number }) {
  const id = `logo-${size}`
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 28 28"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <defs>
        <radialGradient id={`${id}-base`} cx="50%" cy="50%" r="50%" gradientUnits="objectBoundingBox">
          <stop offset="0%"   stopColor="#1122bb" />
          <stop offset="28%"  stopColor="#1122bb" />
          <stop offset="78%"  stopColor="#05081e" />
        </radialGradient>
        <radialGradient id={`${id}-hl`} cx="36%" cy="33%" r="44%" gradientUnits="objectBoundingBox">
          <stop offset="0%" stopColor="#b8d4ff" stopOpacity="0.9" />
          <stop offset="100%" stopColor="#b8d4ff" stopOpacity="0" />
        </radialGradient>
        <radialGradient id={`${id}-mid`} cx="66%" cy="66%" r="52%" gradientUnits="objectBoundingBox">
          <stop offset="0%" stopColor="#3366ee" stopOpacity="0.8" />
          <stop offset="100%" stopColor="#3366ee" stopOpacity="0" />
        </radialGradient>
        <filter id={`${id}-glow`} x="-50%" y="-50%" width="200%" height="200%">
          <feGaussianBlur in="SourceGraphic" stdDeviation="1.2" />
        </filter>
      </defs>
      {/* Outer glow */}
      <circle cx="14" cy="14" r="11" fill="#00e5ff" opacity="0.22" filter={`url(#${id}-glow)`} />
      {/* Orb layers */}
      <circle cx="14" cy="14" r="11" fill={`url(#${id}-base)`} />
      <circle cx="14" cy="14" r="11" fill={`url(#${id}-mid)`} />
      <circle cx="14" cy="14" r="11" fill={`url(#${id}-hl)`} />
    </svg>
  )
}

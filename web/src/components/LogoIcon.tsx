export default function LogoIcon({
  size = 28,
  opacity,
}: {
  size?: number
  opacity?: number
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 28 28"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <path
        d="M14 3 A11 11 0 1 1 25 14"
        stroke="#00e5ff"
        strokeWidth="2.2"
        strokeLinecap="round"
        opacity={opacity}
      />
      <path
        d="M21 10 L25 14 L21 17"
        stroke="#00e5ff"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
        opacity={opacity}
      />
      <circle cx="14" cy="14" r="2.5" fill="#00e5ff" opacity={opacity} />
    </svg>
  )
}

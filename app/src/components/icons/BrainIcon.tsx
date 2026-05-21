interface Props {
  className?: string;
  size?: number;
}

export function BrainIcon({ className, size = 14 }: Props) {
  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M9.5 4.5A2.75 2.75 0 0 0 6.8 6.2 3.25 3.25 0 0 0 4 9.1a3.1 3.1 0 0 0 .4 5.5 3.4 3.4 0 0 0-.1 2.2A3.25 3.25 0 0 0 9.5 19.5" />
      <path d="M14.5 4.5A2.75 2.75 0 0 1 17.2 6.2 3.25 3.25 0 0 1 20 9.1a3.1 3.1 0 0 1-.4 5.5 3.4 3.4 0 0 1 .1 2.2A3.25 3.25 0 0 1 14.5 19.5" />
      <path d="M12 5v14" />
      <path d="M10 9.5h4" />
      <path d="M10.5 13h3" />
    </svg>
  );
}

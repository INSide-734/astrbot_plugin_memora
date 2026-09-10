import type { SVGProps } from "react";

type MemoraLogoProps = SVGProps<SVGSVGElement> & { size?: number };

export function MemoraLogo({ size = 24, className, ...props }: MemoraLogoProps) {
  return (
    <svg
      role="img"
      aria-label="Memora"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      {...props}
    >
      <path d="M12 4.25c-1.5-1.45-3.1-2.05-4.65-1.7C5.05 3.07 3.5 5.03 3.5 7.5v4.25c0 2.5 1.55 4.75 3.85 5.65 1.62.64 3.22.23 4.65-1.08" />
      <path d="M12 4.25c1.5-1.45 3.1-2.05 4.65-1.7C18.95 3.07 20.5 5.03 20.5 7.5v4.25c0 2.5-1.55 4.75-3.85 5.65-1.62.64-3.22.23-4.65-1.08" />
      <path d="M12 4.25v12.1" />
      <path d="m7.1 8.2 2.1 1.45m-2.1 2.85 2.1-.9m7.7-3.4-2.1 1.45m2.1 2.85-2.1-.9" />
      <circle cx="7.1" cy="8.2" r="1.05" fill="currentColor" stroke="none" />
      <circle cx="16.9" cy="8.2" r="1.05" fill="currentColor" stroke="none" />
      <circle cx="12" cy="16.35" r="1.05" fill="currentColor" stroke="none" />
    </svg>
  );
}

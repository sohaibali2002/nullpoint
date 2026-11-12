import Link from "next/link";

export function Mark({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" fill="none" aria-hidden="true">
      <circle cx="30" cy="36" r="17" stroke="#E9ECF3" strokeWidth="3.6" />
      <circle cx="54" cy="13" r="5.2" fill="#5FE3C0" />
    </svg>
  );
}

export function Logo({ href = "/", compact = false }: { href?: string; compact?: boolean }) {
  return (
    <Link href={href} className="focus-ring inline-flex items-center gap-2.5 rounded-md">
      <Mark size={24} />
      {!compact && (
        <span className="text-[17px] font-semibold tracking-[-0.03em] text-text">
          nullpoint
        </span>
      )}
    </Link>
  );
}

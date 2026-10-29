import type { CSSProperties } from "react";
import { cn, initials, orgColor } from "@/lib/utils";

/* ------------------------------------------------------------------
   Strata illustration kit.
   Every illustration is composed from one primitive: an isometric
   plate (a rhombus with a thin side). Layers of plates = strata =
   organizations and collections. Line art uses --ink-3 / --line-strong,
   with a single accent element marking "the answer".
------------------------------------------------------------------- */

type Pt = [number, number];
const pts = (p: Pt[]) => p.map(([x, y]) => `${x},${y}`).join(" ");

function plate(cx: number, cy: number, w: number, h: number, t: number) {
  return {
    top: pts([[cx - w, cy], [cx, cy - h], [cx + w, cy], [cx, cy + h]]),
    left: pts([[cx - w, cy], [cx, cy + h], [cx, cy + h + t], [cx - w, cy + t]]),
    right: pts([[cx, cy + h], [cx + w, cy], [cx + w, cy + t], [cx, cy + h + t]]),
  };
}

function Plate({
  cx,
  cy,
  w,
  h,
  t = 4,
  fill = "var(--surface)",
  side = "var(--surface-3)",
  stroke = "var(--line-strong)",
  style,
}: {
  cx: number;
  cy: number;
  w: number;
  h: number;
  t?: number;
  fill?: string;
  side?: string;
  stroke?: string;
  style?: CSSProperties;
}) {
  const p = plate(cx, cy, w, h, t);
  return (
    <g strokeLinejoin="round" strokeWidth="1" stroke={stroke} style={style}>
      <polygon points={p.left} fill={side} />
      <polygon points={p.right} fill={side} style={{ filter: "brightness(0.96)" }} />
      <polygon points={p.top} fill={fill} />
    </g>
  );
}

/* ---------------------------------- Brand ---------------------------------- */

export function StrataMark({ size = 24, className }: { size?: number; className?: string }) {
  return (
    <svg viewBox="0 0 28 28" width={size} height={size} className={className} aria-hidden="true">
      <path d="M4 19.25 14 23.5l10-4.25L14 15 4 19.25Z" fill="var(--accent)" />
      <path d="M4 14 14 18.25 24 14 14 9.75 4 14Z" fill="currentColor" opacity=".38" />
      <path d="M4 8.75 14 13l10-4.25L14 4.5 4 8.75Z" fill="currentColor" />
    </svg>
  );
}

export function Wordmark({ className }: { className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-2 text-ink", className)}>
      <StrataMark size={22} />
      <span className="font-display text-[22px] leading-none tracking-tight">Strata</span>
    </span>
  );
}

/* ---------------------------------- Org mark ---------------------------------- */

/** Org avatar: a tinted tile with two strata lines and the org initial. */
export function OrgMark({ name, hue, size = 28, className }: { name: string; hue: number; size?: number; className?: string }) {
  const letter = initials(name).slice(0, 1);
  return (
    <span
      className={cn("relative inline-grid shrink-0 place-items-center overflow-hidden font-semibold text-white", className)}
      style={{ width: size, height: size, borderRadius: Math.round(size * 0.28), background: orgColor(hue), fontSize: size * 0.44 }}
      aria-hidden="true"
    >
      <svg viewBox="0 0 28 28" className="absolute inset-0 h-full w-full" preserveAspectRatio="none">
        <path d="M-2 21 C 6 18, 12 24, 30 18" stroke="white" strokeOpacity=".22" strokeWidth="1.5" fill="none" />
        <path d="M-2 25.5 C 8 22, 14 28, 30 23" stroke="white" strokeOpacity=".16" strokeWidth="1.5" fill="none" />
      </svg>
      <span className="relative leading-none">{letter}</span>
    </span>
  );
}

export function Avatar({ name, hue, size = 26, className }: { name: string; hue: number; size?: number; className?: string }) {
  return (
    <span
      className={cn("inline-grid shrink-0 place-items-center rounded-full font-medium", className)}
      style={{
        width: size,
        height: size,
        fontSize: size * 0.4,
        background: `hsl(${hue} 40% var(--org-l) / .16)`,
        color: `hsl(${hue} 45% calc(var(--org-l) - 6%))`,
        boxShadow: `inset 0 0 0 1px hsl(${hue} 40% var(--org-l) / .28)`,
      }}
      aria-hidden="true"
    >
      {initials(name)}
    </span>
  );
}

/* ---------------------------------- Glyphs ---------------------------------- */

export const GLYPHS = ["stack", "book", "gear", "chart", "shield", "people", "code", "globe"] as const;

/** 24px duotone glyphs for collections and agents. */
export function Glyph({ name, size = 20, className }: { name: string; size?: number; className?: string }) {
  const tint = { fill: "currentColor", fillOpacity: 0.14 } as const;
  const body: Record<string, React.ReactNode> = {
    stack: (
      <>
        <path d="M3.5 8 12 4.5 20.5 8 12 11.5Z" {...tint} />
        <path d="M3.5 8 12 4.5 20.5 8 12 11.5Z" />
        <path d="m3.5 12 8.5 3.5 8.5-3.5M3.5 16l8.5 3.5 8.5-3.5" />
      </>
    ),
    book: (
      <>
        <path d="M5 4.5h11.5a1.5 1.5 0 0 1 1.5 1.5v13.5H6.5A1.5 1.5 0 0 1 5 18V4.5Z" {...tint} />
        <path d="M5 18V5.5a1 1 0 0 1 1-1h12v15H6.5A1.5 1.5 0 0 1 5 18Zm0 0a1.5 1.5 0 0 1 1.5-1.5H18" />
        <path d="M9.5 4.5v6l1.75-1.25L13 10.5v-6" />
      </>
    ),
    gear: (
      <>
        <path d="m12 3.5 7.36 4.25v8.5L12 20.5l-7.36-4.25v-8.5L12 3.5Z" {...tint} />
        <path d="m12 3.5 7.36 4.25v8.5L12 20.5l-7.36-4.25v-8.5L12 3.5Z" />
        <circle cx="12" cy="12" r="3" />
      </>
    ),
    chart: (
      <>
        <rect x="5" y="12" width="3.5" height="7" rx=".75" {...tint} />
        <rect x="10.25" y="7.5" width="3.5" height="11.5" rx=".75" {...tint} />
        <rect x="15.5" y="4.5" width="3.5" height="14.5" rx=".75" {...tint} />
        <path d="M3.5 19.5h17M6.75 12v7M12 7.5V19M17.25 4.5V19" />
      </>
    ),
    shield: (
      <>
        <path d="M12 3.5 19 6v5.5c0 4.2-2.9 7.6-7 9-4.1-1.4-7-4.8-7-9V6l7-2.5Z" {...tint} />
        <path d="M12 3.5 19 6v5.5c0 4.2-2.9 7.6-7 9-4.1-1.4-7-4.8-7-9V6l7-2.5Z" />
        <path d="m9 12 2.2 2.2L15.25 10" />
      </>
    ),
    people: (
      <>
        <circle cx="9" cy="8.5" r="3" {...tint} />
        <circle cx="9" cy="8.5" r="3" />
        <path d="M3.75 19c.5-3 2.6-4.75 5.25-4.75s4.75 1.75 5.25 4.75" />
        <path d="M15.25 5.75a3 3 0 0 1 0 5.5M17 14.5c1.6.7 2.8 2.2 3.25 4.5" />
      </>
    ),
    code: (
      <>
        <rect x="3.5" y="4.5" width="17" height="15" rx="2.5" {...tint} />
        <rect x="3.5" y="4.5" width="17" height="15" rx="2.5" />
        <path d="m9 9.5-2.5 2.5L9 14.5M15 9.5l2.5 2.5-2.5 2.5M12.75 8.75l-1.5 6.5" />
      </>
    ),
    globe: (
      <>
        <circle cx="12" cy="12" r="8.5" {...tint} />
        <circle cx="12" cy="12" r="8.5" />
        <path d="M12 3.5c-2.3 2.3-3.5 5.1-3.5 8.5s1.2 6.2 3.5 8.5c2.3-2.3 3.5-5.1 3.5-8.5s-1.2-6.2-3.5-8.5ZM3.75 9.25h16.5M3.75 14.75h16.5" />
      </>
    ),
  };
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      className={className}
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {body[name] ?? body.stack}
    </svg>
  );
}

/** Glyph in a soft tile, tinted with the org hue. */
export function GlyphTile({ name, hue, size = 36 }: { name: string; hue: number; size?: number }) {
  return (
    <span
      className="inline-grid shrink-0 place-items-center rounded-[10px]"
      style={{
        width: size,
        height: size,
        background: `hsl(${hue} 40% var(--org-l) / .12)`,
        color: `hsl(${hue} 45% calc(var(--org-l) - 4%))`,
        boxShadow: `inset 0 0 0 1px hsl(${hue} 40% var(--org-l) / .22)`,
      }}
    >
      <Glyph name={name} size={Math.round(size * 0.56)} />
    </span>
  );
}

/* ------------------------------ Empty states ------------------------------ */

const frame = "h-auto w-full max-w-[240px] text-ink-3";

/** A query line descending through four strata to a marked find. */
export function ExcavationArt({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 240 170" className={cn(frame, className)} aria-hidden="true">
      <Plate cx={120} cy={128} w={86} h={30} t={6} fill="var(--surface-2)" />
      <Plate cx={120} cy={104} w={86} h={30} t={6} />
      <Plate cx={120} cy={80} w={86} h={30} t={6} fill="var(--surface-2)" />
      <Plate cx={120} cy={56} w={86} h={30} t={6} />
      {/* contour lines on the top plate */}
      <g fill="none" stroke="var(--line-strong)" strokeWidth="1">
        <ellipse cx="132" cy="56" rx="34" ry="11" />
        <ellipse cx="136" cy="56" rx="20" ry="6.5" />
        <ellipse cx="139" cy="56" rx="8" ry="2.6" />
      </g>
      {/* descending query */}
      <path d="M92 6 V 104" stroke="var(--accent)" strokeWidth="1.5" strokeLinecap="round" className="dash-flow" fill="none" />
      <circle cx="92" cy="6" r="3" fill="var(--surface)" stroke="var(--accent)" strokeWidth="1.5" />
      <path d="m92 98 6 6-6 6-6-6Z" fill="var(--accent)" />
      <path d="M100 104h22" stroke="var(--accent)" strokeOpacity=".5" strokeWidth="1" strokeDasharray="2 3" />
      <rect x="124" y="99" width="34" height="10" rx="3" fill="var(--accent-soft)" />
    </svg>
  );
}

/** Three feeds — file, web, repo — flowing into a single block. */
export function ConduitArt({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 240 170" className={cn(frame, className)} aria-hidden="true">
      <g fill="none" stroke="var(--line-strong)" strokeWidth="1.5" strokeLinecap="round">
        <path d="M44 40 C 96 40, 104 78, 150 82" />
        <path d="M44 85 H 150" />
        <path d="M44 130 C 96 130, 104 92, 150 88" />
      </g>
      <path d="M44 85 H 150" stroke="var(--accent)" strokeWidth="1.5" className="dash-flow" fill="none" />
      {[40, 85, 130].map((y, i) => (
        <g key={y}>
          <circle cx="30" cy={y} r="15" fill="var(--surface)" stroke="var(--line-strong)" />
          <g transform={`translate(${30 - 9} ${y - 9}) scale(0.9)`} fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
            {i === 0 && <path d="M5.25 2.75h5.9l3.6 3.6v9.9c0 .55-.45 1-1 1h-8.5c-.55 0-1-.45-1-1V3.75c0-.55.45-1 1-1ZM11 2.75V6.5h3.75" />}
            {i === 1 && (
              <>
                <circle cx="10" cy="10" r="7.25" />
                <path d="M10 2.75c-2 2-3 4.4-3 7.25s1 5.25 3 7.25c2-2 3-4.4 3-7.25s-1-5.25-3-7.25ZM3.25 10h13.5" />
              </>
            )}
            {i === 2 && (
              <>
                <circle cx="6" cy="4.75" r="1.75" />
                <circle cx="6" cy="15.25" r="1.75" />
                <circle cx="14" cy="6.75" r="1.75" />
                <path d="M6 6.5v7M14 8.5v.75a3 3 0 0 1-3 3H8.25A2.25 2.25 0 0 0 6 14.5" />
              </>
            )}
          </g>
        </g>
      ))}
      <Plate cx={190} cy={104} w={38} h={14} t={5} fill="var(--surface-2)" />
      <Plate cx={190} cy={90} w={38} h={14} t={5} />
      <Plate cx={190} cy={76} w={38} h={14} t={5} fill="var(--accent-soft)" stroke="var(--accent)" />
    </svg>
  );
}

/** Fanned plates — collections. */
export function FanArt({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 240 170" className={cn(frame, className)} aria-hidden="true">
      <g transform="rotate(-8 120 100)">
        <Plate cx={100} cy={112} w={62} h={22} t={5} fill="var(--surface-2)" />
      </g>
      <g transform="rotate(4 120 100)">
        <Plate cx={134} cy={96} w={62} h={22} t={5} />
      </g>
      <Plate cx={118} cy={74} w={62} h={22} t={5} fill="var(--accent-soft)" stroke="var(--accent)" />
      <g stroke="var(--accent)" strokeWidth="1.25" strokeLinecap="round" fill="none" opacity=".8">
        <path d="M98 74h40M104 79h28" />
      </g>
    </svg>
  );
}

/** Compass rose over a plate, with an orbit — agents. */
export function CompassArt({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 240 170" className={cn(frame, className)} aria-hidden="true">
      <Plate cx={120} cy={120} w={80} h={27} t={6} fill="var(--surface-2)" />
      <ellipse cx="120" cy="120" rx="52" ry="17" fill="none" stroke="var(--line-strong)" strokeDasharray="2 4" />
      <g transform="translate(120 66)">
        <circle r="30" fill="var(--surface)" stroke="var(--line-strong)" />
        <circle r="22" fill="none" stroke="var(--line)" />
        <path d="M0 -26 7 0 0 26 -7 0Z" fill="var(--surface-3)" stroke="var(--line-strong)" strokeLinejoin="round" />
        <path d="M0 -26 7 0 -7 0Z" fill="var(--accent)" />
        <path d="M-26 0 0 -5 26 0 0 5Z" fill="none" stroke="var(--line-strong)" strokeLinejoin="round" />
        <circle r="2.5" fill="var(--surface)" stroke="currentColor" />
      </g>
      <path d="M120 96 V 116" stroke="var(--line-strong)" strokeDasharray="2 3" />
    </svg>
  );
}

/** Magnifier resting on empty plates — no results. */
export function NoResultsArt({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 240 170" className={cn(frame, className)} aria-hidden="true">
      <Plate cx={110} cy={110} w={72} h={24} t={5} fill="var(--surface-2)" />
      <Plate cx={110} cy={92} w={72} h={24} t={5} />
      <g transform="translate(150 50)">
        <circle r="24" fill="var(--surface)" stroke="var(--ink-3)" strokeWidth="2" />
        <path d="M17 17 34 34" stroke="var(--ink-3)" strokeWidth="5" strokeLinecap="round" />
        <path d="M-9 -2h18" stroke="var(--line-strong)" strokeWidth="2" strokeLinecap="round" />
      </g>
    </svg>
  );
}

/**
 * Hero composition for auth screens: three organizations as tinted strata,
 * contour lines, and a single query thread connecting answers across them.
 */
export function AuthArt({ className }: { className?: string }) {
  const orgs = [
    { cy: 300, hue: 168 },
    { cy: 230, hue: 214 },
    { cy: 160, hue: 24 },
  ];
  return (
    <svg viewBox="0 0 480 480" className={cn("h-full w-full", className)} aria-hidden="true" preserveAspectRatio="xMidYMid meet">
      <defs>
        <clipPath id="auth-top">
          <polygon points={plate(240, 160, 170, 62, 0).top} />
        </clipPath>
      </defs>
      {orgs.map((o) => (
        <Plate
          key={o.cy}
          cx={240}
          cy={o.cy}
          w={170}
          h={62}
          t={12}
          fill={`hsl(${o.hue} 38% var(--org-l) / .16)`}
          side={`hsl(${o.hue} 30% var(--org-l) / .32)`}
          stroke={`hsl(${o.hue} 35% var(--org-l) / .7)`}
        />
      ))}
      {/* contour map on the top org */}
      <g clipPath="url(#auth-top)" fill="none" stroke="hsl(24 38% var(--org-l) / .55)" strokeWidth="1">
        {[70, 54, 38, 24, 12].map((r, i) => (
          <ellipse key={r} cx={268 - i * 3} cy={156 + i} rx={r * 1.6} ry={r * 0.56} />
        ))}
      </g>
      {/* the query thread: enters from above, touches each stratum */}
      <path
        d="M196 40 V 160 M196 160 C 196 190, 250 196, 250 230 M250 230 C 250 262, 214 268, 214 300 V 330"
        fill="none"
        stroke="var(--accent)"
        strokeWidth="1.75"
        strokeLinecap="round"
        className="dash-flow"
      />
      <circle cx="196" cy="40" r="5" fill="var(--bg)" stroke="var(--accent)" strokeWidth="1.75" />
      {[
        [196, 160],
        [250, 230],
        [214, 300],
      ].map(([x, y]) => (
        <path key={`${x}-${y}`} d={`M${x} ${y - 7} l7 7 -7 7 -7 -7Z`} fill="var(--accent)" />
      ))}
      {/* the answer card */}
      <g transform="translate(150 380)">
        <rect width="180" height="78" rx="12" fill="var(--surface)" stroke="var(--line-strong)" />
        <rect x="16" y="16" width="96" height="7" rx="3.5" fill="var(--ink)" opacity=".8" />
        <rect x="16" y="32" width="148" height="5" rx="2.5" fill="var(--line-strong)" />
        <rect x="16" y="43" width="128" height="5" rx="2.5" fill="var(--line-strong)" />
        {[0, 1, 2].map((i) => (
          <g key={i} transform={`translate(${16 + i * 26} 56)`}>
            <rect width="22" height="12" rx="4" fill={`hsl(${orgs[2 - i].hue} 38% var(--org-l) / .18)`} />
            <text x="11" y="9" textAnchor="middle" fontSize="8" fontFamily="var(--font-mono)" fill={`hsl(${orgs[2 - i].hue} 45% calc(var(--org-l) - 8%))`}>
              {i + 1}
            </text>
          </g>
        ))}
      </g>
      <path d="M214 330 V 380" stroke="var(--accent)" strokeWidth="1.75" strokeDasharray="2 4" />
    </svg>
  );
}

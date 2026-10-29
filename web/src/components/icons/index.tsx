import type { SVGProps } from "react";

/**
 * Strata icon set. Drawn by hand on a 20×20 grid: 1.5px strokes, round caps
 * and joins, 2.75px safe margin. Filled marks are reserved for state
 * (stop, dots). Add new icons on the same grid — never import a library.
 */
export type IconProps = SVGProps<SVGSVGElement> & { size?: number };

function make(name: string, body: React.ReactNode, opts: { fill?: boolean } = {}) {
  const Icon = ({ size = 18, strokeWidth = 1.5, ...rest }: IconProps) => (
    <svg
      viewBox="0 0 20 20"
      width={size}
      height={size}
      fill={opts.fill ? "currentColor" : "none"}
      stroke={opts.fill ? "none" : "currentColor"}
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      {body}
    </svg>
  );
  Icon.displayName = `Icon${name}`;
  return Icon;
}

export const IconChat = make(
  "Chat",
  <>
    <path d="M4 4.75h12c.69 0 1.25.56 1.25 1.25v7c0 .69-.56 1.25-1.25 1.25H9.25L5.5 16.9v-2.65H4c-.69 0-1.25-.56-1.25-1.25V6c0-.69.56-1.25 1.25-1.25Z" />
    <path d="M6.5 8.25h7M6.5 11h4" />
  </>,
);

export const IconSearch = make(
  "Search",
  <>
    <circle cx="8.75" cy="8.75" r="5.25" />
    <path d="m12.75 12.75 4.25 4.25" />
    <path d="M6.1 7.6a2.9 2.9 0 0 1 2.1-1.75" opacity=".55" />
  </>,
);

export const IconPlus = make("Plus", <path d="M10 4.5v11M4.5 10h11" />);
export const IconSend = make("Send", <path d="M10 15.75V4.75M5.5 9.25 10 4.75l4.5 4.5" />);
export const IconStop = make("Stop", <rect x="5.5" y="5.5" width="9" height="9" rx="2" />, { fill: true });
export const IconArrowRight = make("ArrowRight", <path d="M4.5 10h11M11 5.5l4.5 4.5-4.5 4.5" />);
export const IconArrowLeft = make("ArrowLeft", <path d="M15.5 10h-11M9 5.5 4.5 10 9 14.5" />);
export const IconChevronDown = make("ChevronDown", <path d="m6 8 4 4 4-4" />);
export const IconChevronRight = make("ChevronRight", <path d="m8 6 4 4-4 4" />);
export const IconChevronUpDown = make("ChevronUpDown", <path d="m7 7.75 3-3 3 3M7 12.25l3 3 3-3" />);
export const IconCheck = make("Check", <path d="m4.75 10.5 3.5 3.5 7-8" />);
export const IconX = make("X", <path d="m5.5 5.5 9 9M14.5 5.5l-9 9" />);

/** Three offset plates — the strata motif. Used for collections. */
export const IconLayers = make(
  "Layers",
  <>
    <path d="M2.75 6.5 10 3.5l7.25 3L10 9.5 2.75 6.5Z" />
    <path d="m2.75 10 7.25 3 7.25-3" />
    <path d="m2.75 13.5 7.25 3 7.25-3" />
  </>,
);

/** Three feeds converging into a store. */
export const IconSources = make(
  "Sources",
  <>
    <path d="M2.75 5h3.1c.5 0 .98.22 1.3.6l2.6 3.15M2.75 10h6.75M2.75 15h3.1c.5 0 .98-.22 1.3-.6l2.6-3.15" />
    <rect x="11.25" y="6.25" width="6" height="7.5" rx="1.5" />
  </>,
);

/** Compass rose — an agent that navigates on your behalf. */
export const IconAgent = make(
  "Agent",
  <>
    <path d="M10 2.75v2.5M10 14.75v2.5M2.75 10h2.5M14.75 10h2.5" />
    <path d="m10 6.25 3.75 3.75L10 13.75 6.25 10 10 6.25Z" />
    <circle cx="10" cy="10" r=".9" fill="currentColor" stroke="none" />
  </>,
);

export const IconPeople = make(
  "People",
  <>
    <circle cx="7.5" cy="7.25" r="2.6" />
    <path d="M2.9 16.25c.45-2.55 2.3-4.15 4.6-4.15s4.15 1.6 4.6 4.15" />
    <path d="M12.9 4.9a2.6 2.6 0 0 1 0 4.7M14.6 12.45c1.3.62 2.2 1.9 2.5 3.8" />
  </>,
);

export const IconInsights = make(
  "Insights",
  <>
    <path d="M3 16.75h14" />
    <path d="m3.5 13 3.75-4.25 3 2.75 6-6.25" />
    <circle cx="7.25" cy="8.75" r="1.1" fill="currentColor" stroke="none" />
    <circle cx="10.25" cy="11.5" r="1.1" fill="currentColor" stroke="none" />
    <circle cx="16.25" cy="5.25" r="1.1" fill="currentColor" stroke="none" />
  </>,
);

export const IconSliders = make(
  "Sliders",
  <>
    <path d="M3.5 6.5h6.75M14 6.5h2.5M3.5 13.5H6M9.75 13.5h6.75" />
    <circle cx="12.1" cy="6.5" r="1.85" />
    <circle cx="7.9" cy="13.5" r="1.85" />
  </>,
);

export const IconUpload = make(
  "Upload",
  <>
    <path d="M10 12.75v-9M6.25 7.5 10 3.75l3.75 3.75" />
    <path d="M3.75 12.5v2.25c0 .83.67 1.5 1.5 1.5h9.5c.83 0 1.5-.67 1.5-1.5V12.5" />
  </>,
);

export const IconGlobe = make(
  "Globe",
  <>
    <circle cx="10" cy="10" r="7.25" />
    <path d="M10 2.75c-2 2-3 4.4-3 7.25s1 5.25 3 7.25c2-2 3-4.4 3-7.25s-1-5.25-3-7.25Z" />
    <path d="M3.25 7.5h13.5M3.25 12.5h13.5" />
  </>,
);

/** Repository branch — used for code sources (GitHub). */
export const IconRepo = make(
  "Repo",
  <>
    <circle cx="6" cy="4.75" r="1.75" />
    <circle cx="6" cy="15.25" r="1.75" />
    <circle cx="14" cy="6.75" r="1.75" />
    <path d="M6 6.5v7M14 8.5v.75a3 3 0 0 1-3 3H8.25A2.25 2.25 0 0 0 6 14.5" />
  </>,
);

export const IconFile = make(
  "File",
  <>
    <path d="M5.25 2.75h5.9l3.6 3.6v9.9c0 .55-.45 1-1 1h-8.5c-.55 0-1-.45-1-1V3.75c0-.55.45-1 1-1Z" />
    <path d="M11 2.75V6.5h3.75M7 10.5h6M7 13.5h4" />
  </>,
);

export const IconLink = make(
  "Link",
  <>
    <path d="M8.5 11.5a3 3 0 0 0 4.24 0l2.5-2.5A3 3 0 0 0 11 4.76l-.75.75" />
    <path d="M11.5 8.5a3 3 0 0 0-4.24 0l-2.5 2.5A3 3 0 0 0 9 15.24l.75-.75" />
  </>,
);

export const IconExternal = make(
  "External",
  <>
    <path d="M8.25 4.75h-3a1 1 0 0 0-1 1v9a1 1 0 0 0 1 1h9a1 1 0 0 0 1-1v-3" />
    <path d="M11.25 3.75h5v5M16 4l-6.25 6.25" />
  </>,
);

export const IconTrash = make(
  "Trash",
  <>
    <path d="M3.75 5.75h12.5M8 5.75V4.5a1 1 0 0 1 1-1h2a1 1 0 0 1 1 1v1.25" />
    <path d="m5.5 5.75.72 9.8a1 1 0 0 0 1 .95h5.56a1 1 0 0 0 1-.95l.72-9.8M8.5 9v4.5M11.5 9v4.5" />
  </>,
);

export const IconRefresh = make(
  "Refresh",
  <>
    <path d="M15.6 7.6A6 6 0 0 0 4.55 7.2M4.4 12.4a6 6 0 0 0 11.05.4" />
    <path d="M15.75 3.75V7.6H11.9M4.25 16.25V12.4H8.1" />
  </>,
);

export const IconMore = make(
  "More",
  <>
    <circle cx="5" cy="10" r="1.3" />
    <circle cx="10" cy="10" r="1.3" />
    <circle cx="15" cy="10" r="1.3" />
  </>,
  { fill: true },
);

export const IconSun = make(
  "Sun",
  <>
    <circle cx="10" cy="10" r="3.25" />
    <path d="M10 2.75v1.5M10 15.75v1.5M2.75 10h1.5M15.75 10h1.5M4.87 4.87l1.06 1.06M14.07 14.07l1.06 1.06M4.87 15.13l1.06-1.06M14.07 5.93l1.06-1.06" />
  </>,
);
export const IconMoon = make("Moon", <path d="M15.75 12.6A6.5 6.5 0 0 1 7.4 4.25a6.5 6.5 0 1 0 8.35 8.35Z" />);
export const IconMonitor = make(
  "Monitor",
  <>
    <rect x="2.75" y="3.75" width="14.5" height="10" rx="1.5" />
    <path d="M7.5 16.75h5M10 13.75v3" />
  </>,
);

export const IconLogout = make(
  "Logout",
  <>
    <path d="M8 16.25H5.25a1 1 0 0 1-1-1V4.75a1 1 0 0 1 1-1H8" />
    <path d="M12.5 6.5 16 10l-3.5 3.5M16 10H8" />
  </>,
);

export const IconCopy = make(
  "Copy",
  <>
    <rect x="7" y="7" width="9.25" height="9.25" rx="1.5" />
    <path d="M13 4.75v-.5a1 1 0 0 0-1-1H4.75a1 1 0 0 0-1 1V12a1 1 0 0 0 1 1h.5" />
  </>,
);

export const IconThumbUp = make(
  "ThumbUp",
  <>
    <path d="M6.75 9.25 9.5 3.9a1.4 1.4 0 0 1 2.62.85l-.47 3.25h3.55a1.5 1.5 0 0 1 1.47 1.8l-1.08 5.3a1.5 1.5 0 0 1-1.47 1.2H6.75V9.25Z" />
    <path d="M3.75 9.25h3v7h-3z" />
  </>,
);
export const IconThumbDown = make(
  "ThumbDown",
  <g transform="rotate(180 10 10)">
    <path d="M6.75 9.25 9.5 3.9a1.4 1.4 0 0 1 2.62.85l-.47 3.25h3.55a1.5 1.5 0 0 1 1.47 1.8l-1.08 5.3a1.5 1.5 0 0 1-1.47 1.2H6.75V9.25Z" />
    <path d="M3.75 9.25h3v7h-3z" />
  </g>,
);

export const IconLock = make(
  "Lock",
  <>
    <rect x="4.5" y="8.75" width="11" height="7.75" rx="1.5" />
    <path d="M7 8.75v-2a3 3 0 0 1 6 0v2M10 12v1.5" />
  </>,
);

export const IconEye = make(
  "Eye",
  <>
    <path d="M2.75 10S5.5 4.75 10 4.75 17.25 10 17.25 10 14.5 15.25 10 15.25 2.75 10 2.75 10Z" />
    <circle cx="10" cy="10" r="2.25" />
  </>,
);

export const IconClock = make(
  "Clock",
  <>
    <circle cx="10" cy="10" r="7.25" />
    <path d="M10 6v4l2.75 1.75" />
  </>,
);

/** Trace step glyphs */
export const IconPlan = make(
  "Plan",
  <>
    <path d="M8.75 5.5h7.5M8.75 10h7.5M8.75 14.5h5" />
    <path d="m3.5 5.5 1.1 1.1 2-2.2" />
    <circle cx="4.75" cy="10" r="1.1" />
    <circle cx="4.75" cy="14.5" r="1.1" />
  </>,
);

export const IconRead = make(
  "Read",
  <>
    <path d="M10 5.5c-1.5-1.2-3.5-1.75-6.75-1.75v11.5c3.25 0 5.25.55 6.75 1.75 1.5-1.2 3.5-1.75 6.75-1.75V3.75C13.5 3.75 11.5 4.3 10 5.5Z" />
    <path d="M10 5.5V17" />
  </>,
);

export const IconReflect = make(
  "Reflect",
  <>
    <path d="M15.25 10a5.25 5.25 0 1 1-1.54-3.71" />
    <path d="M15.5 3.75v3h-3" />
    <circle cx="10" cy="10" r=".9" fill="currentColor" stroke="none" />
  </>,
);

/** Pen nib — composing the answer. */
export const IconAnswer = make(
  "Answer",
  <>
    <path d="m10 2.75 4.75 6.25L10 17.25 5.25 9 10 2.75Z" />
    <path d="M10 10.75v6.5" />
    <circle cx="10" cy="9.25" r="1.35" />
  </>,
);

export const IconFilter = make("Filter", <path d="M3.5 5h13M6 10h8M8.5 15h3" />);

export const IconBuilding = make(
  "Building",
  <>
    <path d="M4.25 16.5V5.25a1 1 0 0 1 1-1h5.5a1 1 0 0 1 1 1V16.5M11.75 8.5h3a1 1 0 0 1 1 1v7M2.75 16.5h14.5" />
    <path d="M6.75 7.25h.5M8.75 7.25h.5M6.75 10h.5M8.75 10h.5M6.75 12.75h.5M8.75 12.75h.5" />
  </>,
);

export const IconSidebar = make(
  "Sidebar",
  <>
    <rect x="2.75" y="3.75" width="14.5" height="12.5" rx="2" />
    <path d="M7.75 3.75v12.5" />
  </>,
);

export const IconInfo = make(
  "Info",
  <>
    <circle cx="10" cy="10" r="7.25" />
    <path d="M10 9.25v4.25M10 6.6v.05" />
  </>,
);

export const IconWarning = make(
  "Warning",
  <>
    <path d="M8.7 4.25a1.5 1.5 0 0 1 2.6 0l5.6 9.75a1.5 1.5 0 0 1-1.3 2.25H4.4a1.5 1.5 0 0 1-1.3-2.25l5.6-9.75Z" />
    <path d="M10 8v3.5M10 13.75v.05" />
  </>,
);

export const IconMail = make(
  "Mail",
  <>
    <rect x="2.75" y="4.5" width="14.5" height="11" rx="1.5" />
    <path d="m3.5 5.5 6.5 5 6.5-5" />
  </>,
);

export const IconKey = make(
  "Key",
  <>
    <circle cx="6.75" cy="13.25" r="3.25" />
    <path d="m9.1 10.9 7.15-7.15M13.5 6.5l2 2M11.75 8.25l1.5 1.5" />
  </>,
);

export const IconPencil = make(
  "Pencil",
  <>
    <path d="m12.75 4.25 3 3-8.25 8.25-3.75.75.75-3.75 8.25-8.25Z" />
    <path d="m11 6 3 3" />
  </>,
);

/** Two overlapping orgs — cross-organization scope. */
export const IconScope = make(
  "Scope",
  <>
    <circle cx="7.6" cy="10" r="4.85" />
    <circle cx="12.4" cy="10" r="4.85" />
  </>,
);

/** Quick mode — a single decisive stroke. */
export const IconQuick = make("Quick", <path d="M11.25 2.75 4.75 11.25H10l-1.25 6 6.5-8.5H10l1.25-6Z" />);

/** Deep mode — descending through strata. */
export const IconDeep = make("Deep", <path d="m4.75 4.5 5.25 3 5.25-3M4.75 9l5.25 3 5.25-3M4.75 13.5l5.25 3 5.25-3" />);

export const IconCommand = make(
  "Command",
  <path d="M7.25 7.25V5.5a1.75 1.75 0 1 0-1.75 1.75h9a1.75 1.75 0 1 0-1.75-1.75v9a1.75 1.75 0 1 0 1.75-1.75h-9a1.75 1.75 0 1 0 1.75 1.75v-7.5Z" />,
);

export const IconHash = make("Hash", <path d="M7.5 3.5 6 16.5M14 3.5l-1.5 13M3.75 7.5h13M3.25 12.5h13" />);

export function Spinner({ size = 16, className }: { size?: number; className?: string }) {
  return (
    <svg viewBox="0 0 20 20" width={size} height={size} className={className} aria-hidden="true">
      <circle cx="10" cy="10" r="7" fill="none" stroke="currentColor" strokeOpacity=".2" strokeWidth="2" />
      <path d="M17 10a7 7 0 0 0-7-7" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
        <animateTransform attributeName="transform" type="rotate" from="0 10 10" to="360 10 10" dur=".8s" repeatCount="indefinite" />
      </path>
    </svg>
  );
}

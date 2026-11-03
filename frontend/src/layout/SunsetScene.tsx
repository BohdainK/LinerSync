import { useId, useState } from "react";
import { useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";

// Mountains at sunset with a campfire, two chairs and a tent; at night in dark mode, under a moon.
// Pure decoration. The sky is a CSS gradient on the wrapper, so it always starts at the page colour,
// however the drawing is cropped. `compact` crops to the campfire and sun, for the mobile menu.

const SUN_X = 880;

type Colors = {
  sky: string;
  far: [fill: string, shade: string];
  mid: [fill: string, shade: string];
  hills: string;
  ground: string;
  logs: string;
  wood: [light: string, dark: string];
  tent: [front: string, side: string, door: string];
  stick: string;
  marshmallow: [white: string, toasted: string];
  fireGlow: number;
};

const DAY: Colors = {
  sky: "linear-gradient(to bottom, var(--background), #f4ddd7 45%, #e4a499 85%)",
  far: ["#8e9dab", "#6d7d8f"],
  mid: ["#56697a", "#3e5064"],
  hills: "#9a5a4e",
  ground: "#4a2820",
  logs: "#2b1a16",
  wood: ["#b07d52", "#7a5236"],
  tent: ["#b0362c", "#7d231d", "#4a1612"],
  stick: "#6b4a2e",
  marshmallow: ["#fbf3e6", "#e0b07a"],
  fireGlow: 0.22,
};
const NIGHT: Colors = {
  sky: "linear-gradient(to bottom, var(--background), #2a2638 45%, #3d3346 85%)",
  far: ["#3f4a5a", "#2e3847"],
  mid: ["#2b3543", "#1d2530"],
  hills: "#4e2e29",
  ground: "#24130f",
  logs: "#1a0f0c",
  wood: ["#7d5a3d", "#553a26"],
  tent: ["#8a2a23", "#5e1b16", "#2a0d0a"],
  stick: "#4f3622",
  marshmallow: ["#e8dfd0", "#b98a5a"],
  fireGlow: 0.35,
};

// The sun and moon sit on opposite sides of a hidden disk turning around a point below the horizon,
// so switching theme sets one behind the mountains as the other rises. The moon rides further out.
const DISK_Y = 260;
const SUN_Y = 108;
const MOON_Y = 60;
const MOON_LOCAL_Y = 2 * DISK_Y - MOON_Y;

const STARS: [number, number, number][] = [
  [90, 50, 1.2], [210, 28, 1], [470, 18, 1.4], [640, 36, 1], [1010, 22, 1.2], [1180, 40, 1], [1370, 20, 1.4],
];

type Point = [number, number];

// A ridge alternates valley, peak, valley, ... and starts and ends on a valley.
const FAR: Point[] = [
  [0, 98], [150, 64], [260, 94], [390, 46], [530, 90], [650, 66],
  [880, 104], [1060, 44], [1200, 90], [1310, 62], [1440, 84],
];
const MID: Point[] = [
  [0, 132], [90, 112], [200, 136], [300, 96], [420, 134], [600, 104], [760, 138],
  [960, 112], [1060, 140], [1180, 98], [1330, 134], [1400, 118], [1440, 126],
];

const ridge = (points: Point[]) => `M${points.map(([x, y]) => `${x} ${y}`).join(" L")} V200 H0Z`;

// A ridge, with each peak's face that points away from the sun (or moon) in shade. The shade is darkest at
// the slightly slanted crease below the peak, against the sunlit face, and fades towards the outer edge.
function Ridge({ points, fill, shade }: { points: Point[]; fill: string; shade: string }) {
  const id = useId();
  const faces = points.flatMap(([px, py], i) => {
    const leftOfSun = px < SUN_X;
    const away = i % 2 === 1 ? points[leftOfSun ? i - 1 : i + 1] : undefined;
    if (!away) return [];
    const crease = px + (leftOfSun ? 10 : -10);
    return [{ leftOfSun, d: `M${px} ${py} L${away[0]} ${away[1]} L${away[0]} 200 L${crease} 200Z` }];
  });
  return (
    <g>
      <defs>
        {[true, false].map((leftOfSun) => (
          <linearGradient key={String(leftOfSun)} id={`${id}${leftOfSun}`} x1={leftOfSun ? 1 : 0} x2={leftOfSun ? 0 : 1}>
            <stop offset="0" stopColor={shade} />
            <stop offset="0.45" stopColor={shade} stopOpacity="0.75" />
            <stop offset="1" stopColor={shade} stopOpacity="0" />
          </linearGradient>
        ))}
      </defs>
      <path fill={fill} d={ridge(points)} />
      {faces.map((f) => (
        <path key={f.d} fill={`url(#${id}${f.leftOfSun})`} d={f.d} />
      ))}
    </g>
  );
}

// A wooden Adirondack-style chair in profile, facing right, standing on (0, 0).
function Chair({ transform, colors }: { transform: string; colors: Colors }) {
  const [light, dark] = colors.wood;
  return (
    <g transform={transform} strokeLinecap="round">
      <line x1="-25" y1="0" x2="-21" y2="-17" stroke={dark} strokeWidth="4" />
      <polygon points="-30,-15 -24,-17 -35,-58 -42,-56" fill={light} />
      <line x1="-27" y1="-16" x2="-38.5" y2="-57" stroke={dark} strokeWidth="1" />
      <polygon points="-28,-15 3,-20 3,-24 -28,-20" fill={light} />
      <line x1="1" y1="0" x2="1" y2="-28" stroke={dark} strokeWidth="4" />
      <polygon points="-31,-30 7,-31 7,-27 -31,-26" fill={light} />
    </g>
  );
}

// A small A-frame tent seen from the front left, standing on (0, 0): the sunlit front and the shaded side.
function Tent({ transform, colors }: { transform: string; colors: Colors }) {
  const [front, side, door] = colors.tent;
  return (
    <g transform={transform}>
      <polygon points="18,-34 60,-31 70,0 36,0" fill={side} />
      <polygon points="0,0 36,0 18,-34" fill={front} />
      <polygon points="12,0 24,0 18,-19" fill={door} />
      <line x1="18" y1="-34" x2="18" y2="-40" stroke={colors.logs} strokeWidth="1.5" strokeLinecap="round" />
    </g>
  );
}

// A crooked toasting stick through `points`, pushed into the ground at the first point, with a marshmallow on its tip, browned on the side facing the fire.
function Marshmallow({ points, colors }: { points: Point[]; colors: Colors }) {
  const tip = points[points.length - 1] ?? [0, 0];
  return (
    <g>
      <polyline
        points={points.map((pt) => pt.join(",")).join(" ")}
        fill="none"
        stroke={colors.stick}
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
      <g transform={`translate(${tip[0]} ${tip[1]})`}>
        <rect x="-5" y="-5" width="11" height="10" rx="3" fill={colors.marshmallow[0]} />
        <rect x="-5" y="1" width="11" height="4" rx="2" fill={colors.marshmallow[1]} />
      </g>
    </g>
  );
}

export function SunsetScene({ className, compact = false }: { className?: string; compact?: boolean }) {
  const id = useId();
  const theme = useTheme();
  const night = theme === "dark";
  const colors = night ? NIGHT : DAY;
  // Each switch turns the disk another half turn the same way, so it keeps cycling forwards.
  const [disk, setDisk] = useState({ theme, degrees: night ? 180 : 0 });
  if (disk.theme !== theme) setDisk({ theme, degrees: disk.degrees + 180 });
  return (
    <div aria-hidden="true" className={cn("relative", className)}>
      {/* Both skies stay mounted and cross-fade, since a gradient itself can't be animated. */}
      {[DAY, NIGHT].map((c) => (
        <div
          key={c.sky}
          className="absolute inset-0 transition-opacity duration-1000 ease-in-out motion-reduce:transition-none"
          style={{ backgroundImage: c.sky, opacity: c === colors ? 1 : 0 }}
        />
      ))}
      <svg
        focusable="false"
        className="relative block h-full w-full motion-safe:[&_:is(path,polygon,rect,line,polyline,stop)]:transition-[fill,stroke,stop-color] motion-safe:[&_:is(path,polygon,rect,line,polyline,stop)]:duration-1000 motion-safe:[&_:is(path,polygon,rect,line,polyline,stop)]:ease-in-out"
        viewBox={compact ? "400 10 600 190" : "0 0 1440 200"}
        preserveAspectRatio={compact ? "xMidYMax meet" : "xMidYMax slice"}
      >
        <defs>
          <radialGradient id={`${id}sun`}>
            <stop offset="0.45" stopColor="#f2c14e" stopOpacity="0.45" />
            <stop offset="1" stopColor="#f2c14e" stopOpacity="0" />
          </radialGradient>
          <radialGradient id={`${id}moon`}>
            <stop offset="0.4" stopColor="#ece3cf" stopOpacity="0.25" />
            <stop offset="1" stopColor="#ece3cf" stopOpacity="0" />
          </radialGradient>
          {/* Drawn upside down on the disk; the half turn that brings the moon up rights it. */}
          <mask id={`${id}crescent`}>
            <circle cx={SUN_X} cy={MOON_LOCAL_Y} r="22" fill="white" />
            <circle cx={SUN_X - 10} cy={MOON_LOCAL_Y + 6} r="19" fill="black" />
          </mask>
          <radialGradient id={`${id}fire`}>
            <stop offset="0" stopColor="#f2c14e" stopOpacity={colors.fireGlow} />
            <stop offset="1" stopColor="#f2c14e" stopOpacity="0" />
          </radialGradient>
        </defs>

        <g
          className="transition-opacity duration-1000 ease-in-out motion-reduce:transition-none"
          style={{ opacity: night ? 0.7 : 0 }}
        >
          {STARS.map(([x, y, r]) => (
            <circle key={x} cx={x} cy={y} r={r} fill="#ece6e2" />
          ))}
        </g>

        {/* Each glow stays inside the top of the drawing: its radius is less than its centre's height. */}
        <g
          className="transition-transform duration-1000 ease-in-out motion-reduce:transition-none"
          style={{
            transform: `rotate(${disk.degrees}deg)`,
            transformBox: "view-box",
            transformOrigin: `${SUN_X}px ${DISK_Y}px`,
          }}
        >
          <circle cx={SUN_X} cy={SUN_Y} r="95" fill={`url(#${id}sun)`} />
          <circle cx={SUN_X} cy={SUN_Y} r="46" fill="#edb45a" />
          <circle cx={SUN_X} cy={MOON_LOCAL_Y} r="55" fill={`url(#${id}moon)`} />
          <circle cx={SUN_X} cy={MOON_LOCAL_Y} r="22" fill="#ece3cf" mask={`url(#${id}crescent)`} />
        </g>

        <Ridge points={FAR} fill={colors.far[0]} shade={colors.far[1]} />
        <Ridge points={MID} fill={colors.mid[0]} shade={colors.mid[1]} />
        <path
          fill={colors.hills}
          d="M0 165 C180 140 320 150 460 158 C600 166 700 146 860 150 C1020 154 1160 170 1300 156 C1370 149 1410 152 1440 155 V200 H0Z"
        />
        <path
          fill={colors.ground}
          d="M0 188 C200 176 420 172 600 174 C780 176 900 186 1100 182 C1250 179 1360 184 1440 186 V200 H0Z"
        />

        <circle cx="540" cy="148" r="64" fill={`url(#${id}fire)`} />
        <Tent transform="translate(1250 172)" colors={colors} />
        <Chair transform="translate(478 176) scale(1.2)" colors={colors} />
        <Chair transform="translate(604 176) scale(-1.2 1.2)" colors={colors} />

        {/* Campfire, drawn from the centre of its base */}
        <g transform="translate(540 175)">
          <rect x="-32" y="-8" width="64" height="9" rx="4" fill={colors.logs} transform="rotate(-14 0 -4)" />
          <rect x="-32" y="-8" width="64" height="9" rx="4" fill={colors.logs} transform="rotate(14 0 -4)" />
          <path
            fill="#e07b39"
            d="M-26 -12 C-30 -28 -14 -40 -3 -70 C4 -58 8 -50 7 -42 C10 -45 13 -47 15 -50 C23 -38 30 -27 27 -13 C24 0 -24 0 -26 -12Z"
          />
          <path
            fill="#ec9a45"
            d="M-19 -10 C-22 -22 -11 -32 -3 -53 C3 -44 6 -38 5 -32 C8 -34 10 -36 11 -38 C17 -29 22 -20 20 -11 C17 -1 -17 -1 -19 -10Z"
          />
          <path
            fill="#f2c14e"
            d="M-12 -9 C-14 -18 -7 -24 -2 -37 C3 -30 5 -27 4 -23 C6 -24 8 -26 9 -27 C12 -21 13 -15 11 -10 C9 -2 -10 -2 -12 -9Z"
          />
          <circle cx="14" cy="-104" r="2" fill="#f2c14e" />
          <circle cx="-6" cy="-116" r="1.5" fill="#f2c14e" />
        </g>

        <Marshmallow points={[[464, 186], [484, 152], [506, 123], [531, 103]]} colors={colors} />
        <Marshmallow points={[[618, 186], [598, 151], [576, 122], [551, 101]]} colors={colors} />
      </svg>
    </div>
  );
}

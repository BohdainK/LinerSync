/** The schedules the picker offers, and how they map to and from 5-field cron. Anything else is "custom". */
export type Frequency = "1h" | "2h" | "6h" | "12h" | "daily" | "weekly" | "twiceMonthly" | "monthly" | "custom";

/** `day` is the day of the month, kept to 1–28 so a monthly run never skips a short month. */
export type Schedule = { frequency: Frequency; hour: number; minute: number; weekday: number; day: number };

export const FREQUENCY_LABELS: Record<Frequency, string> = {
  "1h": "Every hour",
  "2h": "Every 2 hours",
  "6h": "Every 6 hours",
  "12h": "Every 12 hours",
  daily: "Every day",
  weekly: "Every week",
  // Plain cron can't say "every other week"; the 1st and 15th is the closest it gets.
  twiceMonthly: "Twice a month (1st and 15th)",
  monthly: "Every month",
  custom: "Custom (cron)",
};

/** Cron numbers weekdays from Sunday = 0. */
export const WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];

export const MONTH_DAYS = Array.from({ length: 28 }, (_, i) => i + 1);

const EVERY_HOURS: Partial<Record<string, Frequency>> = { "2": "2h", "6": "6h", "12": "12h" };

const DEFAULT_TIME = { hour: 3, minute: 0, weekday: 1, day: 1 };

function int(s: string | undefined, max: number): number | null {
  if (s === undefined || !/^\d{1,2}$/.test(s)) return null;
  const n = Number(s);
  return n <= max ? n : null;
}

export function parseCron(cron: string): Schedule {
  const trimmed = cron.trim().replace(/\s+/g, " ");
  if (trimmed === "0 * * * *") return { frequency: "1h", ...DEFAULT_TIME };
  const [m, h, dom, mon, dow] = trimmed.split(" ");
  const custom: Schedule = { frequency: "custom", ...DEFAULT_TIME };
  if (trimmed.split(" ").length !== 5 || mon !== "*") return custom;
  const everyHours = m === "0" && dow === "*" && h?.startsWith("*/") ? EVERY_HOURS[h.slice(2)] : undefined;
  if (everyHours && dom === "*") return { frequency: everyHours, ...DEFAULT_TIME };
  const minute = int(m, 59);
  const hour = int(h, 23);
  if (minute === null || hour === null) return custom;
  const at = { ...DEFAULT_TIME, hour, minute };
  if (dom === "1,15") return dow === "*" ? { ...at, frequency: "twiceMonthly" } : custom;
  if (dom !== "*") {
    const day = int(dom, 28);
    return day === null || day === 0 || dow !== "*" ? custom : { ...at, frequency: "monthly", day };
  }
  if (dow === "*") return { ...at, frequency: "daily" };
  const weekday = int(dow, 7);
  return weekday === null ? custom : { ...at, frequency: "weekly", weekday: weekday % 7 };
}

/** The cron for a preset; null for "custom", which has no single cron. */
export function toCron({ frequency, hour, minute, weekday, day }: Schedule): string | null {
  switch (frequency) {
    case "1h":
      return "0 * * * *";
    case "2h":
    case "6h":
    case "12h":
      return `0 */${frequency.slice(0, -1)} * * *`;
    case "daily":
      return `${minute} ${hour} * * *`;
    case "weekly":
      return `${minute} ${hour} * * ${weekday}`;
    case "twiceMonthly":
      return `${minute} ${hour} 1,15 * *`;
    case "monthly":
      return `${minute} ${hour} ${day} * *`;
    case "custom":
      return null;
  }
}

export function formatTime(hour: number, minute: number): string {
  return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
}

/** 1st, 2nd, 3rd, 4th … 21st, 22nd, 23rd, 24th … 28th. */
export function ordinal(n: number): string {
  const tens = n % 100;
  const suffix = tens >= 11 && tens <= 13 ? "th" : (["th", "st", "nd", "rd"][n % 10] ?? "th");
  return `${n}${suffix}`;
}

/** "Every 2 hours", "Every day at 03:00", "Every Monday at 07:30"; an unrecognised cron is shown as is. */
export function describeCron(cron: string): string {
  const s = parseCron(cron);
  switch (s.frequency) {
    case "daily":
      return `Every day at ${formatTime(s.hour, s.minute)}`;
    case "weekly":
      return `Every ${WEEKDAYS[s.weekday]} at ${formatTime(s.hour, s.minute)}`;
    case "twiceMonthly":
      return `Twice a month (1st and 15th) at ${formatTime(s.hour, s.minute)}`;
    case "monthly":
      return `Every month on the ${ordinal(s.day)} at ${formatTime(s.hour, s.minute)}`;
    case "custom":
      return cron;
    default:
      return FREQUENCY_LABELS[s.frequency];
  }
}

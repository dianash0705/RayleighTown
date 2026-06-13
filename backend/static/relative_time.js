const relativeTimeFormatter = new Intl.RelativeTimeFormat("en", { numeric: "auto" });

const RELATIVE_TIME_UNITS = [
  { unit: "year", ms: 365.25 * 24 * 60 * 60 * 1000 },
  { unit: "month", ms: (365.25 / 12) * 24 * 60 * 60 * 1000 },
  { unit: "week", ms: 7 * 24 * 60 * 60 * 1000 },
  { unit: "day", ms: 24 * 60 * 60 * 1000 },
  { unit: "hour", ms: 60 * 60 * 1000 },
  { unit: "minute", ms: 60 * 1000 },
  { unit: "second", ms: 1000 },
];

function formatRelativeTimestamp(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) {
    return String(value ?? "");
  }

  const date = new Date(number);
  if (Number.isNaN(date.getTime())) {
    return String(value);
  }

  const diffMs = date.getTime() - Date.now();
  const absMs = Math.abs(diffMs);

  for (const entry of RELATIVE_TIME_UNITS) {
    if (absMs >= entry.ms || entry.unit === "second") {
      const rounded = Math.round(diffMs / entry.ms);
      return relativeTimeFormatter.format(rounded, entry.unit);
    }
  }

  return relativeTimeFormatter.format(0, "second");
}

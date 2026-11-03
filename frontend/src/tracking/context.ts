import type { SessionContext, Via } from "../api";

/** The screen and local time a reading session starts on (D24). */
export function sessionContext(via: Via): SessionContext {
  const now = new Date();
  const coarse = window.matchMedia("(pointer: coarse)").matches;
  const shortSide = Math.min(screen.width, screen.height);
  return {
    via,
    device: !coarse ? "desktop" : shortSide >= 600 ? "tablet" : "phone",
    viewport: [window.innerWidth, window.innerHeight],
    screen: [screen.width, screen.height],
    dpr: Math.round(window.devicePixelRatio * 100) / 100,
    local_hour: now.getHours(),
    weekday: now.getDay() || 7,
    tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
    tz_offset_min: -now.getTimezoneOffset(),
  };
}

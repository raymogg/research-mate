import type { ReaderView, SessionContext, Via } from "../api";

export function deviceKind(): SessionContext["device"] {
  const coarse = window.matchMedia("(pointer: coarse)").matches;
  const shortSide = Math.min(screen.width, screen.height);
  return !coarse ? "desktop" : shortSide >= 600 ? "tablet" : "phone";
}

/** The screen and local time a reading session starts on (D24), and the view it opens in. */
export function sessionContext(via: Via, view: ReaderView): SessionContext {
  const now = new Date();
  return {
    via,
    view,
    device: deviceKind(),
    viewport: [window.innerWidth, window.innerHeight],
    screen: [screen.width, screen.height],
    dpr: Math.round(window.devicePixelRatio * 100) / 100,
    local_hour: now.getHours(),
    weekday: now.getDay() || 7,
    tz: Intl.DateTimeFormat().resolvedOptions().timeZone,
    tz_offset_min: -now.getTimezoneOffset(),
  };
}

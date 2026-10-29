"use client";

import { useEffect, useState } from "react";

export type ThemePref = "light" | "dark" | "system";

function apply(pref: ThemePref) {
  const dark = pref === "dark" || (pref === "system" && matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.dataset.theme = dark ? "dark" : "light";
}

export function useTheme() {
  const [pref, setPref] = useState<ThemePref>("system");

  useEffect(() => {
    try {
      setPref((localStorage.getItem("strata-theme") as ThemePref) || "system");
    } catch {}
  }, []);

  useEffect(() => {
    if (pref !== "system") return;
    const mq = matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => apply("system");
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [pref]);

  const set = (next: ThemePref) => {
    setPref(next);
    try {
      localStorage.setItem("strata-theme", next);
    } catch {}
    apply(next);
  };

  return { pref, set };
}

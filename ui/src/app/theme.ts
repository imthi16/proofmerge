import { useEffect, useState } from "react";

export type Theme = "system" | "light" | "dark";
const KEY = "pm-theme";

// Browser storage is a per-viewer convenience; it may be missing or throw.
function load(): Theme {
  try {
    const value = window.localStorage.getItem(KEY);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

export function useTheme(): [Theme, (theme: Theme) => void] {
  const [theme, setTheme] = useState<Theme>(load);
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    try {
      window.localStorage.setItem(KEY, theme);
    } catch {
      /* private window */
    }
  }, [theme]);
  return [theme, setTheme];
}

import { useCallback, useEffect, useState } from "react";

export type Theme = "light" | "dark";

const KEY = "hive-theme";

function readTheme(): Theme {
  const stored = localStorage.getItem(KEY);
  return stored === "light" ? "light" : "dark";
}

export function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem(KEY, theme);
}

export function useTheme() {
  const [theme, setThemeState] = useState<Theme>(readTheme);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const setTheme = useCallback((next: Theme) => setThemeState(next), []);
  const toggle = useCallback(() => setThemeState((t) => (t === "dark" ? "light" : "dark")), []);
  return { theme, setTheme, toggle };
}

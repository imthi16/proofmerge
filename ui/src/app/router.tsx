import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { AnchorHTMLAttributes, ReactNode } from "react";

// A small history-API router: the app has seven routes and no nested layouts, so a
// dependency is not worth it. Run ids may contain "/", so they travel encoded.
interface Location {
  path: string;
  query: URLSearchParams;
}

interface RouterValue extends Location {
  navigate: (to: string, options?: { replace?: boolean }) => void;
}

const RouterContext = createContext<RouterValue | null>(null);

function current(): Location {
  return { path: window.location.pathname, query: new URLSearchParams(window.location.search) };
}

export function Router({ children }: { children: ReactNode }) {
  const [location, setLocation] = useState<Location>(current);
  useEffect(() => {
    const onPop = () => setLocation(current());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  const navigate = useCallback((to: string, options?: { replace?: boolean }) => {
    const here = window.location.pathname + window.location.search;
    if (to === here) return;
    if (options?.replace) window.history.replaceState(null, "", to);
    else window.history.pushState(null, "", to);
    setLocation(current());
    if (!options?.replace) window.scrollTo({ top: 0 });
  }, []);
  const value = useMemo(() => ({ ...location, navigate }), [location, navigate]);
  return <RouterContext.Provider value={value}>{children}</RouterContext.Provider>;
}

export function useRouter(): RouterValue {
  const value = useContext(RouterContext);
  if (!value) throw new Error("useRouter needs <Router>");
  return value;
}

/** Match "/runs/:id" style patterns; parameters are decoded. */
export function match(pattern: string, path: string): Record<string, string> | null {
  const want = pattern.split("/").filter(Boolean);
  const got = path.split("/").filter(Boolean);
  if (want.length !== got.length) return null;
  const params: Record<string, string> = {};
  for (let i = 0; i < want.length; i += 1) {
    const w = want[i]!;
    const g = got[i]!;
    if (w.startsWith(":")) {
      try {
        params[w.slice(1)] = decodeURIComponent(g);
      } catch {
        return null;
      }
    } else if (w !== g) return null;
  }
  return params;
}

type LinkProps = AnchorHTMLAttributes<HTMLAnchorElement> & { to: string };

/** An in-app link: a real <a href>, so middle-click and copy-link work. */
export function Link({ to, onClick, children, ...rest }: LinkProps) {
  const { navigate } = useRouter();
  return (
    <a
      {...rest}
      href={to}
      onClick={(event) => {
        onClick?.(event);
        if (
          event.defaultPrevented ||
          event.button !== 0 ||
          event.metaKey ||
          event.ctrlKey ||
          event.shiftKey ||
          event.altKey
        )
          return;
        event.preventDefault();
        navigate(to);
      }}
    >
      {children}
    </a>
  );
}

export const routes = {
  overview: "/",
  pullRequests: (pr?: number) => (pr ? `/pull-requests?pr=${pr}` : "/pull-requests"),
  verification: (jobId: string) => `/verifications/${encodeURIComponent(jobId)}`,
  runs: (query?: URLSearchParams) => (query && query.toString() ? `/runs?${query}` : "/runs"),
  run: (id: string) => `/runs/${encodeURIComponent(id)}`,
  benchmarks: "/benchmarks",
  diagnostics: "/diagnostics",
};

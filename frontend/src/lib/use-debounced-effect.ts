import { useEffect, useRef } from "react";

/**
 * Runs `effect` `delayMs` after the last change to `deps`, skipping the very
 * first render (so auto-save effects don't fire immediately on page load
 * before the user has changed anything).
 */
export function useDebouncedEffect(
  effect: () => void,
  deps: React.DependencyList,
  delayMs: number,
): void {
  const isFirstRun = useRef(true);

  useEffect(() => {
    if (isFirstRun.current) {
      isFirstRun.current = false;
      return;
    }
    const timeout = setTimeout(effect, delayMs);
    return () => clearTimeout(timeout);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}

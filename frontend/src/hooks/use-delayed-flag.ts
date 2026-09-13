import { useEffect, useRef, useState } from "react";

/**
 * Returns `false` until `active` has been continuously `true` for `delayMs`,
 * then tracks `active` directly. Flips back to `false` immediately when
 * `active` goes `false`. Use to gate skeleton/spinner rendering so brief
 * loads don't flash a skeleton.
 */
export function useDelayedFlag(active: boolean, delayMs = 300): boolean {
  const [flag, setFlag] = useState(false);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (active) {
      timeoutRef.current = setTimeout(() => setFlag(true), delayMs);
    } else {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
      setFlag(false);
    }

    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
        timeoutRef.current = null;
      }
    };
  }, [active, delayMs]);

  return flag;
}

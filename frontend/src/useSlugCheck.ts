import { useEffect, useState } from "react";
import { slugAvailable } from "./api/client";

export type SlugState = "idle" | "bad" | "checking" | "free" | "taken";

const SLUG_RE = /^[a-z0-9][a-z0-9-]*$/;

/** Debounced live slug validation: format + availability. */
export function useSlugCheck(slug: string, excludeId?: number): SlugState {
  const [state, setState] = useState<SlugState>("idle");

  useEffect(() => {
    const s = slug.trim();
    if (!s) {
      setState("idle");
      return;
    }
    if (!SLUG_RE.test(s)) {
      setState("bad");
      return;
    }
    setState("checking");
    const id = setTimeout(() => {
      slugAvailable(s, excludeId)
        .then((ok) => setState(ok ? "free" : "taken"))
        .catch(() => setState("idle"));
    }, 350);
    return () => clearTimeout(id);
  }, [slug, excludeId]);

  return state;
}

export const slugInvalid = (s: SlugState) => s === "bad" || s === "taken" || s === "checking";

const INSTALLED = Symbol.for("supoclip.domTranslationGuard");

export type SkippedDomOperation = "removeChild" | "insertBefore";

/**
 * Browser page translation (Chrome, Safari) swaps the text nodes React rendered for its own. When
 * React later removes or inserts around a node the translator already replaced, the DOM throws
 * NotFoundError and the page crashes. Skip those stale operations instead of throwing
 * (facebook/react#11538). `onSkip` hears about each one, so a skip that hides a real bug is seen.
 */
export function installDomTranslationGuard(onSkip?: (operation: SkippedDomOperation, error: Error) => void) {
  if (typeof window === "undefined" || typeof Node === "undefined") return;
  const proto = Node.prototype as typeof Node.prototype & { [INSTALLED]?: true };
  if (proto[INSTALLED]) return;
  proto[INSTALLED] = true;

  const skip = (operation: SkippedDomOperation) => {
    if (!onSkip) return;
    // Made here so its stack shows who asked; reported after the current React commit.
    const error = new Error(`Skipped ${operation}: the node is no longer a child of this parent`);
    setTimeout(() => onSkip(operation, error));
  };

  const removeChild = proto.removeChild;
  proto.removeChild = function <T extends Node>(this: Node, child: T): T {
    if (child.parentNode !== this) {
      skip("removeChild");
      return child;
    }
    return removeChild.call(this, child) as T;
  };

  const insertBefore = proto.insertBefore;
  proto.insertBefore = function <T extends Node>(this: Node, node: T, reference: Node | null): T {
    if (reference && reference.parentNode !== this) {
      skip("insertBefore");
      return node;
    }
    return insertBefore.call(this, node, reference) as T;
  };
}

/** Chrome and Edge mark <html> once they translate a page; Google's translator wraps text in <font>. */
export function pageLooksTranslated(doc: Document = document) {
  const root = doc.documentElement;
  return (
    root.classList.contains("translated-ltr") ||
    root.classList.contains("translated-rtl") ||
    doc.querySelector('font[style*="vertical-align"]') !== null
  );
}

/**
 * Reports skipped DOM operations at most once per page load for each cause. Skips on a translated
 * page are the known case and only a sample is kept; any other skip may hide a real bug, so it is
 * always reported.
 */
export function reportSkippedDomOperations(
  capture: (error: Error, properties: Record<string, unknown>) => void,
  { sampleRate = 0.1, random = Math.random }: { sampleRate?: number; random?: () => number } = {},
) {
  const seen = new Set<boolean>();
  return (operation: SkippedDomOperation, error: Error) => {
    const translated = pageLooksTranslated();
    if (seen.has(translated)) return;
    seen.add(translated);
    if (translated && random() >= sampleRate) return;
    capture(error, {
      dom_guard_operation: operation,
      page_translated: translated,
      sample_rate: translated ? sampleRate : 1,
    });
  };
}

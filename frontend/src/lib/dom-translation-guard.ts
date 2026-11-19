const INSTALLED = Symbol.for("supoclip.domTranslationGuard");

/**
 * Browser page translation (Chrome, Safari) swaps the text nodes React rendered for its own. When
 * React later removes or inserts around a node the translator already replaced, the DOM throws
 * NotFoundError and the page crashes. Skip those stale operations instead of throwing
 * (facebook/react#11538).
 */
export function installDomTranslationGuard() {
  if (typeof window === "undefined" || typeof Node === "undefined") return;
  const proto = Node.prototype as typeof Node.prototype & { [INSTALLED]?: true };
  if (proto[INSTALLED]) return;
  proto[INSTALLED] = true;

  const removeChild = proto.removeChild;
  proto.removeChild = function <T extends Node>(this: Node, child: T): T {
    if (child.parentNode !== this) return child;
    return removeChild.call(this, child) as T;
  };

  const insertBefore = proto.insertBefore;
  proto.insertBefore = function <T extends Node>(this: Node, node: T, reference: Node | null): T {
    if (reference && reference.parentNode !== this) return node;
    return insertBefore.call(this, node, reference) as T;
  };
}

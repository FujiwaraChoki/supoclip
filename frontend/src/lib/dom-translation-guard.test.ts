import { describe, expect, it } from "vitest";

import { installDomTranslationGuard } from "./dom-translation-guard";

describe("installDomTranslationGuard", () => {
  installDomTranslationGuard();

  it("ignores removing a node a translator already moved out", () => {
    const parent = document.createElement("div");
    const text = document.createTextNode("Hello");
    parent.appendChild(text);
    document.createElement("font").appendChild(text);

    expect(parent.removeChild(text)).toBe(text);
    expect(parent.childNodes).toHaveLength(0);
  });

  it("ignores inserting before a node that is no longer a child", () => {
    const parent = document.createElement("div");
    const stale = document.createTextNode("Hello");
    const node = document.createElement("span");

    expect(parent.insertBefore(node, stale)).toBe(node);
    expect(node.parentNode).toBeNull();
  });

  it("still removes and inserts real children", () => {
    const parent = document.createElement("div");
    const first = parent.appendChild(document.createElement("b"));
    const second = document.createElement("i");

    parent.insertBefore(second, first);
    expect(Array.from(parent.childNodes)).toEqual([second, first]);
    parent.insertBefore(document.createElement("u"), null);
    expect(parent.childNodes).toHaveLength(3);
    expect(parent.removeChild(first)).toBe(first);
    expect(first.parentNode).toBeNull();
  });

  it("installs only once", () => {
    const removeChild = Node.prototype.removeChild;
    installDomTranslationGuard();
    expect(Node.prototype.removeChild).toBe(removeChild);
  });
});

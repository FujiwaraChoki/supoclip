import { afterEach, describe, expect, it, vi } from "vitest";

import {
  installDomTranslationGuard,
  pageLooksTranslated,
  reportSkippedDomOperations,
} from "./dom-translation-guard";

const onSkip = vi.fn();

describe("installDomTranslationGuard", () => {
  installDomTranslationGuard(onSkip);
  const settled = () => new Promise((resolve) => setTimeout(resolve));
  afterEach(async () => {
    await settled();
    onSkip.mockReset();
  });

  it("ignores removing a node a translator already moved out", () => {
    const parent = document.createElement("div");
    const text = document.createTextNode("Hello");
    parent.appendChild(text);
    document.createElement("font").appendChild(text);

    expect(parent.removeChild(text)).toBe(text);
    expect(parent.childNodes).toHaveLength(0);
  });

  it("tells the reporter about a skip once the current work is done", async () => {
    const parent = document.createElement("div");
    parent.removeChild(document.createTextNode("stale"));
    expect(onSkip).not.toHaveBeenCalled();

    await settled();
    expect(onSkip).toHaveBeenCalledOnce();
    const [operation, error] = onSkip.mock.calls[0];
    expect(operation).toBe("removeChild");
    expect(error).toBeInstanceOf(Error);
    expect(error.stack).toContain("dom-translation-guard.test");
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

  it("still throws for operations it does not guard", () => {
    const parent = document.createElement("div");
    expect(() => parent.replaceChild(document.createElement("b"), document.createTextNode("x"))).toThrow();
  });

  it("installs only once", () => {
    const removeChild = Node.prototype.removeChild;
    installDomTranslationGuard();
    expect(Node.prototype.removeChild).toBe(removeChild);
  });
});

describe("reportSkippedDomOperations", () => {
  afterEach(() => {
    document.documentElement.className = "";
    document.body.innerHTML = "";
  });

  it("always reports a skip on a page nobody translated, once per page load", () => {
    const capture = vi.fn();
    const report = reportSkippedDomOperations(capture, { random: () => 0.99 });
    const error = new Error("skipped");

    report("insertBefore", error);
    report("removeChild", new Error("again"));

    expect(capture).toHaveBeenCalledOnce();
    expect(capture).toHaveBeenCalledWith(error, {
      dom_guard_operation: "insertBefore",
      page_translated: false,
      sample_rate: 1,
    });
  });

  it("samples skips on a translated page", () => {
    document.documentElement.classList.add("translated-ltr");
    const kept = vi.fn();
    reportSkippedDomOperations(kept, { random: () => 0.05 })("removeChild", new Error("x"));
    expect(kept).toHaveBeenCalledWith(expect.any(Error), {
      dom_guard_operation: "removeChild",
      page_translated: true,
      sample_rate: 0.1,
    });

    const dropped = vi.fn();
    reportSkippedDomOperations(dropped, { random: () => 0.5 })("removeChild", new Error("x"));
    expect(dropped).not.toHaveBeenCalled();
  });

  it("still reports an untranslated skip after a translated one", () => {
    const capture = vi.fn();
    const report = reportSkippedDomOperations(capture, { random: () => 0 });
    document.documentElement.classList.add("translated-rtl");
    report("removeChild", new Error("translated"));
    document.documentElement.classList.remove("translated-rtl");
    report("removeChild", new Error("real bug"));
    expect(capture.mock.calls.map(([error]) => error.message)).toEqual(["translated", "real bug"]);
  });

  it("recognizes Google's translated text wrappers", () => {
    expect(pageLooksTranslated()).toBe(false);
    document.body.innerHTML = '<p><font style="vertical-align: inherit;"><font style="vertical-align: inherit;">Hallo</font></font></p>';
    expect(pageLooksTranslated()).toBe(true);
  });
});

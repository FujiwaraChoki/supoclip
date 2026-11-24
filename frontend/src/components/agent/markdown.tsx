import { Fragment, type ReactNode } from "react";
import Link from "next/link";

/**
 * The little Markdown the agent writes: paragraphs, bullet and numbered lists,
 * **bold**, *italic*, `code` and links. Built as React nodes, never as HTML.
 */
export function AgentMarkdown({ text }: { text: string }) {
  const blocks = text.replace(/\r\n/g, "\n").trim().split(/\n{2,}/);
  return (
    <div className="space-y-2.5 text-[0.9375rem] leading-relaxed">
      {blocks.map((block, index) => <Block key={index} block={block} />)}
    </div>
  );
}

const BULLET = /^\s*[-*•]\s+/;
const NUMBERED = /^\s*\d+[.)]\s+/;

/** One paragraph-separated block; runs of bullet or numbered lines inside it become lists. */
function Block({ block }: { block: string }) {
  const runs: { kind: "text" | "ul" | "ol"; lines: string[] }[] = [];
  for (const line of block.split("\n")) {
    const kind = BULLET.test(line) ? "ul" : NUMBERED.test(line) ? "ol" : "text";
    const last = runs[runs.length - 1];
    if (last && last.kind === kind) last.lines.push(line);
    else runs.push({ kind, lines: [line] });
  }
  return (
    <>
      {runs.map((run, index) => {
        if (run.kind === "ul") {
          return (
            <ul key={index} className="space-y-1.5 pl-1">
              {run.lines.map((line, item) => (
                <li key={item} className="flex gap-2.5">
                  <span className="mt-[0.6rem] size-1 shrink-0 rounded-full bg-foreground/40" aria-hidden />
                  <span>{inline(line.replace(BULLET, ""))}</span>
                </li>
              ))}
            </ul>
          );
        }
        if (run.kind === "ol") {
          return (
            <ol key={index} className="space-y-1.5 pl-1">
              {run.lines.map((line, item) => (
                <li key={item} className="flex gap-2.5">
                  <span className="w-4 shrink-0 text-right tabular-nums text-muted-foreground">{item + 1}.</span>
                  <span>{inline(line.replace(NUMBERED, ""))}</span>
                </li>
              ))}
            </ol>
          );
        }
        const heading = run.lines.length === 1 ? run.lines[0].match(/^#{1,4}\s+(.*)$/) : null;
        if (heading) return <p key={index} className="font-semibold">{inline(heading[1])}</p>;
        return (
          <p key={index}>
            {run.lines.map((line, at) => <Fragment key={at}>{at > 0 && <br />}{inline(line)}</Fragment>)}
          </p>
        );
      })}
    </>
  );
}

const TOKEN = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)\s]+\)|\*[^*\s][^*]*\*|_[^_\s][^_]*_)/g;

function inline(text: string): ReactNode[] {
  const parts = text.split(TOKEN).filter((part) => part !== "");
  return parts.map((part, index) => {
    if (part.startsWith("**") && part.endsWith("**")) return <strong key={index} className="font-semibold">{part.slice(2, -2)}</strong>;
    if (part.startsWith("`") && part.endsWith("`")) return <code key={index} className="rounded bg-muted px-1 py-0.5 text-[0.85em]">{part.slice(1, -1)}</code>;
    const link = part.match(/^\[([^\]]+)\]\(([^)\s]+)\)$/);
    if (link) {
      const [, label, href] = link;
      if (href.startsWith("/")) return <Link key={index} href={href} className="font-medium underline underline-offset-2">{label}</Link>;
      if (/^https?:\/\//.test(href)) return <a key={index} href={href} target="_blank" rel="noreferrer" className="font-medium underline underline-offset-2">{label}</a>;
      return <Fragment key={index}>{label}</Fragment>;
    }
    if ((part.startsWith("*") && part.endsWith("*")) || (part.startsWith("_") && part.endsWith("_"))) {
      return <em key={index}>{part.slice(1, -1)}</em>;
    }
    return <Fragment key={index}>{part}</Fragment>;
  });
}

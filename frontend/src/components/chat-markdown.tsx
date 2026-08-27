// Renders an assistant/copilot reply's markdown (headings, bold, lists,
// links, ...) instead of showing the raw "**bold**"/"## Heading" characters
// literally. Shared by copilot-dock.tsx and chat-window.tsx -- both surfaces
// hit the same bug: an LLM defaults to markdown-formatted prose unless told
// otherwise, and neither bubble was parsing it.
//
// react-markdown never touches innerHTML (it builds React elements), so this
// stays safe against a message containing literal HTML/script tags without
// needing a sanitizer on top.

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "@/lib/utils";

// Sizing/max-width is the caller's concern (bubble vs. plain flex row) --
// this component only owns markdown-element typography.
export function ChatMarkdown({ text, className }: { text: string; className?: string }) {
  return (
    <div
      className={cn(
        "space-y-2 text-sm leading-relaxed text-foreground/90 [&_a]:text-primary [&_a]:underline [&_code]:rounded [&_code]:bg-muted/50 [&_code]:px-1 [&_code]:py-0.5 [&_code]:text-[0.85em] [&_h1]:text-base [&_h1]:font-semibold [&_h2]:text-sm [&_h2]:font-semibold [&_h3]:text-sm [&_h3]:font-medium [&_li]:ml-4 [&_ol]:list-decimal [&_ol]:space-y-0.5 [&_p]:leading-relaxed [&_strong]:font-semibold [&_ul]:list-disc [&_ul]:space-y-0.5",
        className,
      )}
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  );
}

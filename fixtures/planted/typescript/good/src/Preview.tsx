import React from "react";
import DOMPurify from "dompurify";

export function Preview({ markdown, html }: { markdown: string; html: string }) {
  return (
    <section>
      <div dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(html) }} />
      <pre>{markdown}</pre>
    </section>
  );
}

export function mount(el: HTMLElement, html: string): void {
  el.textContent = html;
}

export function reset(el: HTMLElement): void {
  el.innerHTML = "<p>Loading…</p>";
}

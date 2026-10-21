import React from "react";

export function Preview({ markdown, html }: { markdown: string; html: string }) {
  return (
    <section>
      <div
        dangerouslySetInnerHTML={{ __html: html }} // expect: arbiter/typescript.dangerously-set-inner-html
      />
      <pre>{markdown}</pre>
    </section>
  );
}

export function mount(el: HTMLElement, html: string): void {
  el.innerHTML = html; // expect: arbiter/typescript.inner-html-assigned
}

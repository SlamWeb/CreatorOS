export type ChatLink = { url: string; label: string; source_tool: string };

// Host receipts authorize navigation; Markdown is model output, not a router.
export function chatHref(href: string | undefined, links: ChatLink[], origin: string):
  { kind: "internal" | "external"; href: string } | null {
  if (!href || /[\s\\]/.test(href) || href.startsWith("//")) return null;
  try {
    const url = new URL(href, origin);
    if (url.username || url.password || !["http:", "https:"].includes(url.protocol)) return null;
    const internal = url.origin === origin || /^\/(?:series|runs)(?:\/|$)/.test(url.pathname);
    if (internal || !/^https?:\/\//.test(href)) {
      const path = `${url.pathname}${url.search}${url.hash}`;
      const explicit = href.startsWith("/") || /^https?:\/\//.test(href);
      return url.origin === origin && explicit && links.some(link => link.url === path)
        ? { kind: "internal", href: path } : null;
    }
    return { kind: "external", href };
  } catch { return null; }
}

export type ChatLink = { url: string; label: string; source_tool: string };

// Host receipts authorize navigation; Markdown is model output, not a router.
export function chatHref(href: string | undefined, links: ChatLink[], origin: string):
  { kind: "internal" | "external"; href: string } | null {
  if (!href || /[\s\\]/.test(href) || href.startsWith("//")) return null;
  try {
    const url = new URL(href, origin);
    if (url.username || url.password || !["http:", "https:"].includes(url.protocol)) return null;
    const pathname = decodeURIComponent(url.pathname);
    if (/[\\\s]/.test(pathname)) return null;
    const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
    const managed = /^\/(?:api\/)?(?:series\/series-[A-Za-z0-9_-]+|runs\/[a-f0-9-]{36})(?:\/|$)/i.test(pathname)
      || links.some(link => pathname === new URL(link.url, origin).pathname);
    const internal = url.origin === origin || loopback || managed;
    if (internal || !/^https?:\/\//.test(href)) {
      const path = `${url.pathname}${url.search}${url.hash}`;
      const explicit = href.startsWith("/") || /^https?:\/\//.test(href);
      return url.origin === origin && explicit && links.some(link => link.url === path)
        ? { kind: "internal", href: path } : null;
    }
    return { kind: "external", href };
  } catch { return null; }
}

export type ChatLink = { url: string; label: string; source_tool: string;
  kind?: string; title?: string; version?: number | string; created_at?: string; run_id?: string };

const skillTaskDownloadPath = /^\/api\/agent\/sessions\/([a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12})\/skill-tasks\/[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}\/download$/i;

export function isChatDownload(href: string, sessionId: string, origin: string): boolean {
  const url = new URL(href, origin);
  if (url.origin !== origin || url.hash) return false;
  if (/^\/api\/creators\/[A-Za-z0-9_-]+\/collections\/[A-Za-z0-9_-]+\/exports\/[a-f0-9]{64}\/download$/.test(url.pathname)) {
    return !url.search;
  }
  if (skillTaskDownloadPath.exec(url.pathname)?.[1] !== sessionId) return false;
  const entries = [...url.searchParams.entries()];
  if (entries.length !== 1 || entries[0][0] !== "path") return false;
  const path = entries[0][1];
  return path.startsWith("out/") && !/[\\:\u0000-\u001f\u007f]/.test(path)
    && path.split("/").every(segment => segment !== "" && segment !== "." && segment !== "..");
}

// Resource identity comes from the host receipt, never the model's wording.
export function chatLinkLabel(link: ChatLink): string {
  if (!link.title) return link.label;
  const version = link.version == null ? "" : ` · 第 ${link.version} 版`;
  const run = chatLinkKind(link) === "production" && typeof link.run_id === "string"
    && /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i.test(link.run_id)
    ? ` · ${link.run_id.slice(0, 6)}` : "";
  return `${link.title}${version}${run}`;
}

export function uniqueChatLinks(links: ChatLink[]): ChatLink[] {
  const byUrl = new Map<string, ChatLink>();
  for (const link of links) {
    const previous = byUrl.get(link.url);
    if (!previous || (!previous.title && !!link.title)) byUrl.set(link.url, link);
  }
  return [...byUrl.values()];
}

export function chatLinkKind(link: ChatLink): string | null {
  if (link.kind) return link.kind;
  // Historical receipt URLs already passed the host allowlist. Inferring a
  // display category does not grant navigation to any additional destination.
  if (/^\/runs\/[a-f0-9-]{36}(?:[?#]|$)/i.test(link.url)) return "production";
  if (/^\/skills\?/.test(link.url)) {
    const query = new URLSearchParams(link.url.split("?")[1]);
    return query.has("iteration") ? "iteration" : "skill";
  }
  if (/^\/series\/series-[\w-]+(?:[?#]|$)/.test(link.url)) {
    const query = new URLSearchParams(link.url.split("?")[1] ?? "");
    return query.has("research") ? "research" : "series";
  }
  return null;
}

// Host receipts authorize navigation; Markdown is model output, not a router.
export function chatHref(href: string | undefined, links: ChatLink[], origin: string):
  { kind: "internal" | "external"; href: string } | null {
  if (!href || /[\s\\]/.test(href) || href.startsWith("//")) return null;
  try {
    const url = new URL(href, origin);
    if (url.username || url.password || !["http:", "https:"].includes(url.protocol)) return null;
    const pathname = decodeURIComponent(url.pathname);
    if (/[\\\s]/.test(pathname)) return null;
    const extraction = pathname === "/skills" && url.searchParams.has("extraction");
    if (extraction && (!/^[a-f0-9]{64}$/.test(url.searchParams.get("extraction") ?? "")
      || [...url.searchParams.keys()].length !== 1 || url.hash)) return null;
    const loopback = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
    const managed = /^\/(?:api\/)?(?:series\/series-[A-Za-z0-9_-]+|runs\/[a-f0-9-]{36})(?:\/|$)/i.test(pathname)
      || skillTaskDownloadPath.test(pathname)
      || extraction || links.some(link => pathname === new URL(link.url, origin).pathname);
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

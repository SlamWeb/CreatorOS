import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { request } from "../api/client";
import type { CreatorView, PageResponse } from "../api/types";
import { BrandMark } from "../components/BrandMark";
import { Button } from "../components/ui/button";
import "./home.css";

export function HomePage() {
  const accounts = useQuery({ queryKey: ["home-accounts"],
    queryFn: () => request<PageResponse<CreatorView>>("/api/creators?limit=4"),
    retry: false, staleTime: 60_000, refetchOnWindowFocus: false });

  return <div className="home-page">
    <a className="home-skip" href="#home-main">跳到正文</a>
    <header className="home-nav">
      <Link to="/" className="home-brand" aria-label="CreatorOS 首页"><BrandMark /><span>CreatorOS</span></Link>
      <nav aria-label="首页导航">
        <Link to="/skills">Skill 库</Link>
        <a href="https://github.com/SlamWeb/CreatorOS" target="_blank" rel="noreferrer">GitHub</a>
      </nav>
    </header>

    <main id="home-main">
      <section className="home-hero" aria-labelledby="home-title">
        <h1 id="home-title"><span>从一个好作品，</span><span>到一百个。</span></h1>
        <p>把这一次的创作经验，变成下一次的起点。</p>
        <div className="home-actions">
          <Button asChild size="lg"><Link to="/workspace">进入工作台</Link></Button>
        </div>
      </section>

      <section className="home-workspaces" aria-labelledby="home-workspaces-title">
        <div className="home-section-heading"><h2 id="home-workspaces-title">你的账号</h2></div>
        <div className="home-account-list" aria-busy={accounts.isPending}>
          {accounts.isPending ? <p className="home-account-state" role="status">正在读取你的账号…</p>
            : accounts.isError ? <div className="home-account-state" role="status"><p>暂时无法读取账号，你仍可以进入工作台。</p><Button type="button" variant="secondary" onClick={() => void accounts.refetch()}>重新读取</Button></div>
            : accounts.data?.items.length ? accounts.data.items.map(account => <Link className="home-account" key={account.id}
              to={`/?${new URLSearchParams({ creator: account.id })}`}>
              <span className="home-account-avatar" aria-hidden="true">{account.display_name.slice(0, 1)}</span>
              <span className="home-account-copy"><strong>{account.display_name}</strong><span>{account.series.length ? account.series.slice(0, 2).map(series => series.name).join(" / ") : "添加栏目，开始创作"}</span></span>
            </Link>) : <div className="home-account-state"><p>你的第一个创作账号，从这里开始。</p><Button asChild variant="secondary"><Link to="/workspace">创建账号</Link></Button></div>}
        </div>
      </section>
    </main>

  </div>;
}

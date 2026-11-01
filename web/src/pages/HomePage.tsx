import { useQuery } from "@tanstack/react-query";
import { ArrowRight, ArrowUpRight, CodeXml } from "lucide-react";
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
        <Link to="/workspace">工作台</Link>
        <Link to="/skills">Skill 库</Link>
        <a href="https://github.com/SlamWeb/CreatorOS" target="_blank" rel="noreferrer">GitHub <ArrowUpRight size={14} aria-hidden="true" /></a>
      </nav>
      <Button asChild className="home-nav-enter"><Link to="/workspace">进入工作台 <ArrowUpRight size={15} aria-hidden="true" /></Link></Button>
    </header>

    <main id="home-main">
      <section className="home-hero" aria-labelledby="home-title">
        <h1 id="home-title"><span>把灵感，</span><span>做成 <span className="home-title-mark"><BrandMark /></span><em>好作品。</em></span></h1>
        <p>从参考作品到 Skill，从选题到图文与视频。<br />让每个账号，都有自己的创作工作台。</p>
        <div className="home-actions">
          <Button asChild size="lg"><Link to="/workspace">进入工作台 <ArrowRight size={19} aria-hidden="true" /></Link></Button>
          <Button asChild size="lg" variant="secondary"><Link to="/skills">打开 Skill 库 <ArrowUpRight size={18} aria-hidden="true" /></Link></Button>
        </div>
      </section>

      <section className="home-workspaces" aria-labelledby="home-workspaces-title">
        <div className="home-section-heading"><div><h2 id="home-workspaces-title">回到你的创作。</h2><p>选一个账号，从上次的灵感继续。</p></div>
          <Link className="home-inline-link" to="/workspace">查看工作台 <ArrowUpRight size={17} aria-hidden="true" /></Link>
        </div>
        <div className="home-account-list" aria-busy={accounts.isPending}>
          {accounts.isPending ? <p className="home-account-state" role="status">正在读取你的账号…</p>
            : accounts.isError ? <div className="home-account-state" role="status"><p>暂时无法读取账号，你仍可以进入工作台。</p><Button type="button" variant="secondary" onClick={() => void accounts.refetch()}>重新读取</Button></div>
            : accounts.data?.items.length ? accounts.data.items.map(account => <Link className="home-account" key={account.id}
              to={`/?${new URLSearchParams({ creator: account.id })}`}>
              <span className="home-account-avatar" aria-hidden="true">{account.display_name.slice(0, 1)}</span>
              <span className="home-account-copy"><strong>{account.display_name}</strong><span>{account.series.length ? account.series.slice(0, 2).map(series => series.name).join(" / ") : "添加栏目，开始创作"}</span></span>
              <ArrowUpRight size={20} aria-hidden="true" />
            </Link>) : <div className="home-account-state"><p>你的第一个创作账号，从这里开始。</p><Button asChild variant="secondary"><Link to="/workspace">创建账号 <ArrowRight size={16} aria-hidden="true" /></Link></Button></div>}
        </div>
      </section>
    </main>

    <footer className="home-footer"><span>CreatorOS · 为自己的创作而造</span><a href="https://github.com/SlamWeb/CreatorOS" target="_blank" rel="noreferrer"><CodeXml size={16} aria-hidden="true" /> 在 GitHub 查看项目 <ArrowUpRight size={14} aria-hidden="true" /></a></footer>
  </div>;
}

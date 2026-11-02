import { useEffect, useRef, type PointerEvent } from "react";
import { Link } from "react-router-dom";
import { BrandMark } from "../components/BrandMark";
import { Button } from "../components/ui/button";
import { HomeHeroIcons } from "../components/HomeHeroIcons";
import "./home.css";

export function HomePage() {
  const actionRef = useRef<HTMLDivElement>(null);
  function resetMagnet() {
    actionRef.current?.style.removeProperty("--magnet-x");
    actionRef.current?.style.removeProperty("--magnet-y");
  }
  function moveMagnet(event: PointerEvent<HTMLDivElement>) {
    if (event.pointerType !== "mouse" || !matchMedia("(hover: hover) and (pointer: fine)").matches
      || matchMedia("(prefers-reduced-motion: reduce)").matches
      || event.currentTarget.querySelector(":focus-visible")) return;
    const box = event.currentTarget.getBoundingClientRect();
    const x = Math.max(-5, Math.min(5, (event.clientX - box.left - box.width / 2) * .1));
    const y = Math.max(-3, Math.min(3, (event.clientY - box.top - box.height / 2) * .1));
    event.currentTarget.style.setProperty("--magnet-x", `${x}px`);
    event.currentTarget.style.setProperty("--magnet-y", `${y}px`);
  }
  useEffect(() => {
    const reduced = matchMedia("(prefers-reduced-motion: reduce)");
    document.addEventListener("visibilitychange", resetMagnet);
    reduced.addEventListener("change", resetMagnet);
    return () => {
      document.removeEventListener("visibilitychange", resetMagnet);
      reduced.removeEventListener("change", resetMagnet);
    };
  }, []);

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
        <h1 id="home-title" aria-label="每个好作品 都不止于一次">
          <span>每个好作品<HomeHeroIcons kind="clapper" /></span>
          <span>都不止于一次<HomeHeroIcons kind="puzzle" /></span>
        </h1>
        <p>把这一次的创作经验，变成下一次的起点。</p>
        <div className="home-actions">
          <div className="home-action-hit" ref={actionRef} onPointerMove={moveMagnet}
            onPointerLeave={resetMagnet} onPointerCancel={resetMagnet} onPointerDown={resetMagnet}
            onFocus={resetMagnet} onBlur={resetMagnet}>
            <Button asChild size="lg"><Link className="home-cta" to="/workspace">进入工作台</Link></Button>
          </div>
        </div>
      </section>

    </main>

  </div>;
}

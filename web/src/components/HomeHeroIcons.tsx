import { useEffect, useRef, useState, type PointerEvent } from "react";
import clapperboard from "../assets/hero-interactions/clapperboard.png";
import puzzle from "../assets/hero-interactions/puzzle.png";

export function HomeHeroIcons({ kind }: { kind: "clapper" | "puzzle" }) {
  const armRef = useRef<HTMLSpanElement>(null);
  const [joined, setJoined] = useState(false);

  function clap() {
    if (document.hidden || matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const arm = armRef.current;
    if (!arm) return;
    arm.getAnimations().forEach(animation => animation.cancel());
    arm.animate([
      { transform: "rotate(0deg)", easing: "cubic-bezier(.4,0,.8,1)" },
      { transform: "rotate(18deg)", offset: .38, easing: "linear" },
      { transform: "rotate(18deg)", offset: .5, easing: "cubic-bezier(.16,1,.3,1)" },
      { transform: "rotate(0deg)" },
    ], { duration: 650, easing: "linear" });
  }

  function enter(event: PointerEvent<HTMLButtonElement>) {
    if (event.pointerType !== "mouse" || !matchMedia("(hover: hover) and (pointer: fine)").matches) return;
    if (kind === "clapper") clap();
    else setJoined(true);
  }

  useEffect(() => {
    const preference = matchMedia("(prefers-reduced-motion: reduce)");
    const arm = armRef.current;
    const reset = () => {
      arm?.getAnimations().forEach(animation => animation.cancel());
      setJoined(false);
    };
    const visibility = () => { if (document.hidden) reset(); };
    document.addEventListener("visibilitychange", visibility);
    preference.addEventListener("change", reset);
    return () => {
      arm?.getAnimations().forEach(animation => animation.cancel());
      document.removeEventListener("visibilitychange", visibility);
      preference.removeEventListener("change", reset);
    };
  }, []);

  return <button className={`home-icon-mark ${kind === "clapper" ? "home-icon-work" : "home-icon-reuse"}`}
    type="button" aria-label={kind === "clapper" ? "打一下板" : "拼接积木"}
    data-joined={joined} onPointerEnter={enter} onPointerLeave={() => setJoined(false)}
    onBlur={() => setJoined(false)} onClick={() => kind === "clapper" ? clap() : setJoined(true)}>
    {kind === "clapper" ? <span className="clapper-scene" aria-hidden="true">
      <img className="clapper-body" src={clapperboard} alt="" draggable={false} />
      <span className="clapper-hinge" />
      <span className="clapper-arm" ref={armRef}><img src={clapperboard} alt="" draggable={false} /></span>
    </span> : <span className="puzzle-scene" aria-hidden="true">
      <img className="puzzle-base" src={puzzle} alt="" draggable={false} />
      <img className="puzzle-arrival" src={puzzle} alt="" draggable={false} />
    </span>}
  </button>;
}

import { useRef, useState } from "react";
import { apiUrl } from "../api/client";
import type { BgmTrack } from "../api/bgm";
import "./bgm-pool.css";

export function bgmDuration(seconds: number) {
  return Math.floor(seconds / 60) + ":" + String(Math.floor(seconds % 60)).padStart(2, "0");
}

export function BgmPreview({ item }: { item: BgmTrack }) {
  const audio = useRef<HTMLAudioElement>(null);
  const [failed, setFailed] = useState(false);
  return <div className="bgm-preview">
    <audio ref={audio} controls preload="none" aria-label={`试听 ${item.name}`} src={apiUrl(item.url)} onError={() => setFailed(true)} />
    {failed && <p role="alert" className="form-error">音频暂时无法播放。<button className="text-link" type="button" onClick={() => {
      setFailed(false);
      audio.current?.load();
    }}>重新加载</button></p>}
  </div>;
}

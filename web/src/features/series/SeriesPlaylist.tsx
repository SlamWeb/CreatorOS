import { useId, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { bgmApi, bgmListKey, bgmPlaylistKey, refreshBgm } from "../../api/bgm";
import { BgmPreview, bgmDuration } from "../../components/BgmPreview";

export function SeriesPlaylist({ seriesId, name }: { seriesId: string; name: string }) {
  const client = useQueryClient();
  const [trackId, setTrackId] = useState("");
  const picker = useId();
  const playlist = useQuery({ queryKey: bgmPlaylistKey(seriesId), queryFn: ({ signal }) => bgmApi.playlist(seriesId, signal), retry: false });
  const tracks = useQuery({ queryKey: bgmListKey(), queryFn: ({ signal }) => bgmApi.list("", false, signal), retry: false });
  const change = useMutation({
    retry: false,
    mutationFn: async ({ trackId: id, add }: { trackId: string; add: boolean }) => {
      if (add) await bgmApi.add(seriesId, id);
      else await bgmApi.remove(seriesId, id);
    },
    onSuccess: (_, { add }) => { if (add) setTrackId(""); },
    onSettled: () => refreshBgm(client),
  });
  const inPlaylist = new Set(playlist.data?.map(item => item.id));
  const choices = tracks.data?.items.filter(item => !item.archived && !inPlaylist.has(item.id)) ?? [];
  const selected = choices.find(item => item.id === trackId);
  return <section className="series-playlist" aria-label="栏目歌单">
    <header className="bgm-head"><div><h1>{name}</h1><p>歌单中的音乐会优先显示在视频制作表单。</p></div>
      <Link to="/bgm" className="button button-secondary">打开 BGM 池</Link>
    </header>
    {playlist.isError && <p role="alert" className="form-error">{playlist.error.message} <button type="button" className="text-link" onClick={() => void playlist.refetch()}>重读歌单</button></p>}
    {tracks.isError && <p role="alert" className="form-error">{tracks.error.message} <button type="button" className="text-link" onClick={() => void tracks.refetch()}>重读音乐</button></p>}
    {playlist.isPending || tracks.isPending ? <p role="status">正在读取音乐…</p> :
      <form className="bgm-picker" onSubmit={event => {
        event.preventDefault();
        if (selected && !change.isPending) change.mutate({ trackId: selected.id, add: true });
      }}>
        <label htmlFor={picker}>从 BGM 池添加</label><div>
          <select id={picker} value={selected?.id ?? ""} disabled={change.isPending || playlist.isError || tracks.isError || !choices.length} onChange={event => setTrackId(event.target.value)}>
            <option value="">{choices.length ? "选择一首音乐" : "暂无可添加的音乐"}</option>
            {choices.map(item => <option key={item.id} value={item.id}>{item.name} · {bgmDuration(item.duration_seconds)}</option>)}
          </select>
          <button className="button button-primary" disabled={!selected || change.isPending}>添加到歌单</button>
        </div>
      </form>}
    {change.isError && <p className="form-error" role="alert">{change.error.message}</p>}
    {change.isSuccess && <p role="status">{change.variables.add ? "音乐已加入歌单。" : "已从歌单移除。"}</p>}
    {playlist.isSuccess && (playlist.data.length ? <div className="bgm-list">
      {playlist.data.map(item => <article key={item.id} className="bgm-row">
        <div className="bgm-row-title"><h2>{item.name}</h2><span>{bgmDuration(item.duration_seconds)}</span>{item.archived && <span>已归档 · 暂不可用于新制作</span>}</div>
        <BgmPreview item={item} />
        <div className="bgm-row-actions"><button type="button" className="button button-quiet" aria-label={`移出歌单 ${item.name}`} disabled={change.isPending} onClick={() => change.mutate({ trackId: item.id, add: false })}>移出歌单</button></div>
      </article>)}
    </div> : <p className="bgm-empty">歌单还是空的。从 BGM 池添加常用音乐。</p>)}
  </section>;
}

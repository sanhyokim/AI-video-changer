"""M6: 品質チェックの HTML レポート。

ブラウザで開くと、出力の動画、指標のグラフ、問題区間の一覧、目視のチェックリストが並ぶ。
区間の「この時刻へ」を押すと、上の動画がその時刻に移動する。外部のファイルやネットには依存しない。
"""

from __future__ import annotations

import html
import math
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .project import Project
from .qc import REASONS, VISUAL_CHECKS

SERIES_LIGHT = ["#2a78d6", "#eb6834", "#1baf7a"]
SERIES_DARK = ["#3987e5", "#d95926", "#199e70"]

METRIC_CHARTS = [
    ("sim_set", "顔素材との似ている度合い", "qc_min_sim_faceset", "下限", 0.0, 1.0,
     "高いほど AI 人物に似ている。線より下は別人に見える可能性"),
    ("sim_orig", "元の人物との似ている度合い", "qc_max_sim_original", "上限", 0.0, 1.0,
     "低いほどよく置き換わっている。線より上は元の顔が残っている可能性"),
    ("flicker", "ちらつき（隣のフレームとの差）", "qc_max_flicker", "上限", 0.0, None,
     "低いほど安定。線より上は顔が揺れている可能性"),
]


def _e(x: Any) -> str:
    return html.escape("" if x is None else str(x))


def _t(sec: float) -> str:
    m, s = divmod(max(0.0, sec), 60)
    return f"{int(m):02d}:{s:06.3f}"


def _rel_url(target: Path, base: Path) -> str:
    rel = os.path.relpath(target, base).replace(os.sep, "/")
    return quote(rel, safe="/.-_~")


def _chart_svg(key: str, title: str, thr: float, thr_kind: str, lo: float, hi: float | None, note: str,
               metrics: dict[str, Any]) -> str:
    frames = metrics["frames"]
    if not frames:
        return ""
    vals = [v for ser in metrics["series"].values() for v in ser[key] if v is not None]
    if hi is None:
        hi = max([0.3, thr * 1.5] + vals) if vals else 0.3
        hi = math.ceil(hi * 10 - 1e-9) / 10
    W, H, L, R, T, B = 900, 190, 44, 12, 14, 26
    x0, x1 = frames[0], frames[-1] + metrics["step"]

    def X(n: float) -> float:
        return L + (n - x0) / max(1, x1 - x0) * (W - L - R)

    def Y(v: float) -> float:
        v = min(max(v, lo), hi)
        return T + (1 - (v - lo) / (hi - lo)) * (H - T - B)

    parts = [f'<svg viewBox="0 0 {W} {H}" class="chart" data-metric="{key}" role="img" '
             f'aria-label="{_e(title)}">']
    for iv in metrics["intervals"]:
        xa, xb = X(iv["start_frame"]), X(iv["end_frame"] + 1)
        parts.append(f'<rect class="band" x="{xa:.1f}" y="{T}" width="{max(1.5, xb - xa):.1f}" '
                     f'height="{H - T - B}"/>')
    for g in range(0, 5):
        v = lo + (hi - lo) * g / 4
        y = Y(v)
        parts.append(f'<line class="grid" x1="{L}" x2="{W - R}" y1="{y:.1f}" y2="{y:.1f}"/>'
                     f'<text class="tick" x="{L - 6}" y="{y + 4:.1f}" text-anchor="end">{v:.2f}</text>')
    for g in range(0, 6):
        n = x0 + (x1 - x0) * g / 5
        parts.append(f'<text class="tick" x="{X(n):.1f}" y="{H - 8}" text-anchor="middle">{int(n)}</text>')
    yt = Y(thr)
    parts.append(f'<line class="thr" x1="{L}" x2="{W - R}" y1="{yt:.1f}" y2="{yt:.1f}"/>'
                 f'<text class="thrlabel" x="{W - R - 4}" y="{yt - 5:.1f}" text-anchor="end">'
                 f'基準の{thr_kind} {thr:g}</text>')
    for k, (pid, ser) in enumerate(metrics["series"].items()):
        d, pen = [], False
        for n, v in zip(frames, ser[key]):
            if v is None:
                pen = False
                continue
            d.append(f"{'L' if pen else 'M'}{X(n):.1f},{Y(v):.1f}")
            pen = True
        parts.append(f'<path class="line s{k}" d="{" ".join(d)}"/>')
    parts.append(f'<line class="cross" x1="0" x2="0" y1="{T}" y2="{H - B}" visibility="hidden"/>')
    parts.append(f'<rect class="hit" x="{L}" y="{T}" width="{W - L - R}" height="{H - T - B}" '
                 f'data-x0="{x0}" data-x1="{x1}" data-l="{L}" data-w="{W - L - R}"/>')
    parts.append("</svg>")
    return (f'<figure class="fig"><figcaption><b>{_e(title)}</b><span class="muted">　{_e(note)}</span>'
            f'</figcaption>{"".join(parts)}</figure>')


def write_report(project: Project, metrics: dict[str, Any], render: dict[str, Any], clip: dict[str, Any],
                 out_dir: Path, thumbs: dict[int, str]) -> Path:
    fps = metrics["fps"]
    names = metrics.get("person_names", {})
    thr = metrics["thresholds"]
    ratio = metrics["ratio"]
    passed = metrics["pass"]
    if passed is None:
        badge = '<span class="badge warn">⚠ 判定できません（置き換える顔が見つかりません）</span>'
    elif passed:
        badge = '<span class="badge good">✓ 自動チェックは合格の目安内</span>'
    else:
        badge = '<span class="badge bad">⚠ 自動チェックで要確認</span>'
    video_url = _rel_url(project.abs(render["file"]), out_dir)
    clip_url = _rel_url(project.abs(clip["file"]), out_dir)

    legend = ""
    if len(metrics["persons"]) >= 2:
        legend = '<div class="legend">' + "".join(
            f'<span><i class="sw s{k}"></i>{_e(names.get(pid, pid))}</span>'
            for k, pid in enumerate(metrics["persons"])) + "</div>"
    charts = "".join(_chart_svg(k, t, float(thr[tk]), kind, lo, hi, note, metrics)
                     for k, t, tk, kind, lo, hi, note in METRIC_CHARTS)

    rows = []
    for i, iv in enumerate(metrics["intervals"]):
        reasons = "、".join(f"{REASONS.get(r, r)}（{c}）" for r, c in
                           sorted(iv["reasons"].items(), key=lambda x: -x[1]))
        th = thumbs.get(iv["worst_frame"])
        img = f'<img src="{_e(th)}" alt="フレーム {iv["worst_frame"]} の元と出力" loading="lazy">' if th else ""
        rows.append(
            f'<tr><td>{i + 1}</td>'
            f'<td><button class="seek" data-t="{iv["start_frame"] / fps:.4f}">この時刻へ</button></td>'
            f'<td>{_t(iv["start_time"])}〜{_t(iv["end_time"])}<br><span class="muted">'
            f'クリップのフレーム {iv["start_frame"]}〜{iv["end_frame"]}</span></td>'
            f'<td>{iv["source_start_frame"]}〜{iv["source_end_frame"]}<br><span class="muted">'
            f'元の動画 {_t(iv["source_start_time"])}</span></td>'
            f'<td>{_e(reasons)}</td><td class="thumb">{img}<br><span class="muted">左：元　右：出力（フレーム '
            f'{iv["worst_frame"]}）</span></td></tr>')
    table = ("<p>基準を外れた区間はありませんでした。</p>" if not rows else
             '<table><thead><tr><th>#</th><th></th><th>クリップ内の時刻</th><th>元の動画のフレーム</th>'
             '<th>理由（フレーム数）</th><th>いちばん悪いフレーム</th></tr></thead><tbody>'
             + "".join(rows) + "</tbody></table>")

    re_rows = "".join(
        f'<tr><td>{_e(names.get(e["person_id"], e["person_id"]))}</td><td>{e["gap_start"]}〜{e["gap_end"]}</td>'
        f'<td>{e["before"]:.3f}</td><td>{e["after"]:.3f}</td><td>{e["delta"]:.3f}</td>'
        f'<td>{"⚠ 基準超え" if e["flagged"] else "✓"}</td></tr>' for e in metrics["reappear_events"])
    reappear = ("<p>顔が消えて再び現れる場面はありませんでした。</p>" if not re_rows else
                '<table><thead><tr><th>人物</th><th>顔が消えていたフレーム</th><th>前の平均</th><th>後の平均</th>'
                f'<th>差</th><th>判定</th></tr></thead><tbody>{re_rows}</tbody></table>')

    checks = "".join(f'<li><label><input type="checkbox" data-key="{k}"> {_e(t)}</label></li>'
                     for k, t in VISUAL_CHECKS)
    thr_rows = "".join(f"<tr><td>{_e(k)}</td><td>{_e(v)}</td></tr>" for k, v in thr.items())
    notes = "".join(f'<p class="badge warn">⚠ {_e(n)}</p>' for n in metrics.get("notes", []))
    assign = "、".join(f'{_e(names.get(a["person_id"], a["person_id"]))}'
                      + (f'（基準フレーム {a["reference_frame"]}、位置 {a.get("reference_face_position", 0)}）'
                         if a.get("reference_frame") is not None else "（自動）") for a in metrics["assignments"])
    data = {"fps": fps, "frames": metrics["frames"], "series": metrics["series"],
            "names": [names.get(p, p) for p in metrics["persons"]], "persons": metrics["persons"],
            "id": metrics["id"]}
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    ratio_txt = "—" if ratio is None else f"{ratio * 100:.2f}%"
    doc = f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>品質チェック {_e(metrics['id'])}</title>
<style>
:root {{ color-scheme: light; --surface:#fcfcfb; --panel:#f3f2ef; --text:#0b0b0b; --text2:#52514e; --muted:#77756f;
  --grid:#e4e2dd; --s0:{SERIES_LIGHT[0]}; --s1:{SERIES_LIGHT[1]}; --s2:{SERIES_LIGHT[2]};
  --good:#0ca30c; --warn:#b07800; --bad:#d03b3b; --band:rgba(208,59,59,.13); --border:#dcdad4; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{ color-scheme: dark; --surface:#1a1a19;
  --panel:#242422; --text:#f0efec; --text2:#c3c2b7; --muted:#9a998f; --grid:#33332f; --s0:{SERIES_DARK[0]};
  --s1:{SERIES_DARK[1]}; --s2:{SERIES_DARK[2]}; --good:#0ca30c; --warn:#fab219; --bad:#e66767;
  --band:rgba(230,103,103,.18); --border:#3a3a36; }} }}
:root[data-theme="dark"] {{ color-scheme: dark; --surface:#1a1a19; --panel:#242422; --text:#f0efec; --text2:#c3c2b7;
  --muted:#9a998f; --grid:#33332f; --s0:{SERIES_DARK[0]}; --s1:{SERIES_DARK[1]}; --s2:{SERIES_DARK[2]};
  --good:#0ca30c; --warn:#fab219; --bad:#e66767; --band:rgba(230,103,103,.18); --border:#3a3a36; }}
body {{ background:var(--surface); color:var(--text); font-family: system-ui, "Yu Gothic UI", "Meiryo", sans-serif;
  margin:0; padding:16px; line-height:1.5; }}
main {{ max-width:1100px; margin:0 auto; }}
h1 {{ font-size:22px; margin:0 0 4px; }} h2 {{ font-size:17px; margin:28px 0 8px; }}
.muted {{ color:var(--muted); font-size:12px; }}
.badge {{ display:inline-block; padding:4px 10px; border-radius:4px; background:var(--panel); font-weight:600; }}
.badge.good {{ color:var(--good); }} .badge.bad {{ color:var(--bad); }} .badge.warn {{ color:var(--warn); }}
.tiles {{ display:flex; gap:12px; flex-wrap:wrap; margin:12px 0; }}
.tile {{ background:var(--panel); padding:10px 14px; border-radius:6px; min-width:150px; }}
.tile b {{ display:block; font-size:22px; }}
video {{ width:100%; max-height:60vh; background:#000; border-radius:4px; }}
.controls button, button.seek {{ background:var(--panel); color:var(--text); border:1px solid var(--border);
  padding:4px 10px; border-radius:4px; cursor:pointer; white-space:nowrap; }}
.fig {{ margin:10px 0 18px; }} .fig figcaption {{ margin-bottom:4px; }}
svg.chart {{ width:100%; height:auto; display:block; }}
.chart .grid {{ stroke:var(--grid); stroke-width:1; }} .chart .tick {{ fill:var(--muted); font-size:11px; }}
.chart .thr {{ stroke:var(--text2); stroke-width:1.5; stroke-dasharray:6 4; }}
.chart .thrlabel {{ fill:var(--text2); font-size:11px; }}
.chart .band {{ fill:var(--band); }} .chart .line {{ fill:none; stroke-width:2; stroke-linejoin:round; }}
.chart .s0, .sw.s0 {{ stroke:var(--s0); background:var(--s0); }} .chart .s1, .sw.s1 {{ stroke:var(--s1); background:var(--s1); }}
.chart .s2, .sw.s2 {{ stroke:var(--s2); background:var(--s2); }}
.chart .cross {{ stroke:var(--text2); stroke-width:1; }} .chart .hit {{ fill:transparent; cursor:crosshair; }}
.legend {{ display:flex; gap:16px; font-size:13px; }} .sw {{ display:inline-block; width:14px; height:3px; margin-right:6px;
  vertical-align:middle; }}
#tip {{ position:fixed; pointer-events:none; background:var(--panel); color:var(--text); border:1px solid var(--border);
  padding:6px 8px; border-radius:4px; font-size:12px; display:none; z-index:10; }}
table {{ border-collapse:collapse; width:100%; font-size:13px; }}
th, td {{ border-bottom:1px solid var(--border); padding:6px; text-align:left; vertical-align:top; }}
td.thumb img {{ max-width:320px; width:100%; border-radius:3px; }}
ul.checks {{ list-style:none; padding:0; }} ul.checks li {{ margin:6px 0; }}
details {{ margin-top:16px; }}
@media (max-width: 640px) {{ td.thumb img {{ max-width:160px; }} th:nth-child(4), td:nth-child(4) {{ display:none; }} }}
</style></head>
<body><main>
<h1>品質チェック</h1>
<div class="muted">{_e(metrics['id'])}　出力：{_e(render.get('label') or render['id'])}（{_e(render['file'])}）
　クリップ：{_e(clip['file'])}　置き換える人物：{assign}</div>
<p>{badge}</p>
<div class="tiles">
<div class="tile"><span class="muted">問題のあるフレームの割合</span><b>{ratio_txt}</b>
<span class="muted">合格の目安 {float(thr['qc_pass_ratio']) * 100:g}% 以内（仕様 12.2）</span></div>
<div class="tile"><span class="muted">問題区間</span><b>{len(metrics['intervals'])}</b></div>
<div class="tile"><span class="muted">問題のフレーム / 置き換える顔があるフレーム</span>
<b>{metrics['problem_frames']} / {metrics['target_frames']}</b></div>
</div>
{notes}
<p class="muted">自動チェックは目安です。合格には、12.1 の 6 項目を等倍と 0.5 倍の再生で見て、違和感がないことも必要です（仕様 12.2）。
基準値は初期値（推測）を含みます。よい出力で決め直してください。</p>

<h2>出力の動画</h2>
<video id="v" src="{video_url}" controls preload="metadata"></video>
<div class="controls"><button data-rate="1">等倍</button> <button data-rate="0.5">0.5 倍</button>
<span class="muted" id="pos"></span>　<a href="{clip_url}">元のクリップを開く</a></div>

<h2>指標の推移</h2>
<p class="muted">横軸はクリップのフレーム番号。赤い帯は問題区間。グラフをクリックするとその時刻へ移動します。</p>
{legend}{charts}

<h2>問題区間（VisoMaster Fusion で直すときの時刻）</h2>
{table}

<h2>顔が消えて再び現れた場面</h2>
{reappear}

<h2>目視のチェックリスト（仕様 12.1）</h2>
<p class="muted">等倍と 0.5 倍で見て確認します。チェックはこのブラウザにだけ保存されます。結果はツールの品質チェック画面でも記録できます。</p>
<ul class="checks">{checks}</ul>

<details><summary>使った基準値</summary><table><tbody>{thr_rows}</tbody></table></details>
</main>
<div id="tip"></div>
<script id="data" type="application/json">{data_json}</script>
<script>
(function() {{
  const D = JSON.parse(document.getElementById('data').textContent);
  const v = document.getElementById('v');
  const tip = document.getElementById('tip');
  const pos = document.getElementById('pos');
  const labels = {{sim_set: '顔素材', sim_orig: '元の人物', flicker: 'ちらつき'}};
  const esc = s => String(s).replace(/[&<>"']/g, c => ({{'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}})[c]);
  function seek(t) {{ v.currentTime = Math.max(0, t + 0.0001); v.scrollIntoView({{behavior: 'smooth', block: 'center'}}); }}
  document.querySelectorAll('button.seek').forEach(b => b.addEventListener('click', () => seek(parseFloat(b.dataset.t))));
  document.querySelectorAll('[data-rate]').forEach(b => b.addEventListener('click', () => {{ v.playbackRate = parseFloat(b.dataset.rate); v.play(); }}));
  v.addEventListener('timeupdate', () => {{ pos.textContent = 'フレーム ' + Math.floor(v.currentTime * D.fps + 1e-6); }});
  function nearest(f) {{ let lo = 0, hi = D.frames.length - 1;
    while (lo < hi) {{ const m = (lo + hi) >> 1; if (D.frames[m] < f) lo = m + 1; else hi = m; }} return lo; }}
  document.querySelectorAll('svg.chart').forEach(svg => {{
    const hit = svg.querySelector('.hit'), cross = svg.querySelector('.cross'), key = svg.dataset.metric;
    const x0 = +hit.dataset.x0, x1 = +hit.dataset.x1, L = +hit.dataset.l, W = +hit.dataset.w;
    function frameAt(ev) {{ const r = svg.getBoundingClientRect(); const sx = (ev.clientX - r.left) * (svg.viewBox.baseVal.width / r.width);
      return x0 + (sx - L) / W * (x1 - x0); }}
    hit.addEventListener('mousemove', ev => {{
      const i = nearest(frameAt(ev)); const f = D.frames[i];
      const x = L + (f - x0) / (x1 - x0) * W; cross.setAttribute('x1', x); cross.setAttribute('x2', x); cross.setAttribute('visibility', 'visible');
      let h = 'フレーム ' + f + '（' + (f / D.fps).toFixed(2) + ' 秒）';
      D.persons.forEach((p, k) => {{ const val = D.series[p][key][i];
        h += '<br>' + (D.names.length > 1 ? esc(D.names[k]) + '：' : labels[key] + '：') + (val === null ? '顔なし' : val.toFixed(3)); }});
      tip.innerHTML = h; tip.style.display = 'block'; tip.style.left = (ev.clientX + 14) + 'px'; tip.style.top = (ev.clientY + 10) + 'px';
    }});
    hit.addEventListener('mouseleave', () => {{ tip.style.display = 'none'; cross.setAttribute('visibility', 'hidden'); }});
    hit.addEventListener('click', ev => seek(D.frames[nearest(frameAt(ev))] / D.fps));
  }});
  const store = 'vfc-qc-' + D.id;
  let saved = {{}};
  try {{ saved = JSON.parse(localStorage.getItem(store) || '{{}}'); }} catch (e) {{ saved = {{}}; }}
  document.querySelectorAll('ul.checks input').forEach(cb => {{
    cb.checked = !!saved[cb.dataset.key];
    cb.addEventListener('change', () => {{ saved[cb.dataset.key] = cb.checked;
      try {{ localStorage.setItem(store, JSON.stringify(saved)); }} catch (e) {{}} }});
  }});
}})();
</script>
</body></html>
"""
    path = out_dir / "report.html"
    path.write_text(doc, encoding="utf-8")
    return path

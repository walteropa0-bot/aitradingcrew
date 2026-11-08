"""All CSS of the dashboard (dark Web3 look, neon green = profit, neon red = loss)."""

from urllib.parse import quote

_SVG = ("<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='black' stroke-width='{w}' "
        "stroke-linecap='round' stroke-linejoin='round'>{body}</svg>")
_ICONS = {
    "radar": ("<circle cx='12' cy='12' r='9'/><circle cx='12' cy='12' r='4.5'/><path d='M12 12l6.2-6.2'/>", 1.9),
    "chart": ("<rect x='3.5' y='9' width='3.6' height='7' rx='1'/><path d='M5.3 5.5V9M5.3 16v3'/><rect x='10.2' y='5.5' width='3.6' height='9' rx='1'/>"
              "<path d='M12 3v2.5M12 14.5V19'/><rect x='16.9' y='10' width='3.6' height='6' rx='1'/><path d='M18.7 7v3M18.7 16v3.5'/>", 1.9),
    "shield": ("<path d='M12 3l7.5 3v5.2c0 4.6-3.1 8.1-7.5 9.8-4.4-1.7-7.5-5.2-7.5-9.8V6L12 3z'/><path d='M8.8 12.2l2.2 2.2 4.2-4.4'/>", 1.9),
    "logo": ("<path d='M3 17l5.2-6.3 4 3.2L21 5.5'/><path d='M15.5 5.5H21V11'/>", 2.4),
}
# The HTML sanitiser strips inline <svg>, so icons are CSS masks (data-URI SVG) coloured via background.
ICON_CSS = ".ico{display:block;width:21px;height:21px;background:var(--c,var(--muted));-webkit-mask:var(--m) center/contain no-repeat;mask:var(--m) center/contain no-repeat}" + "".join(
    f'.ico.{name}{{--m:url("data:image/svg+xml,{quote(_SVG.format(w=w, body=body), safe="")}")}}' for name, (body, w) in _ICONS.items()
) + ".ico.logo{width:18px;height:18px;background:#00f5a0}"

CSS = ICON_CSS + r"""
:root{
  --bg:#05060a; --bg2:#090b11; --card:#0d1017; --card2:#121723; --line:rgba(255,255,255,.075); --line2:rgba(255,255,255,.12);
  --text:#eef1f6; --muted:#8d95a9; --dim:#586075;
  --green:#00f5a0; --green2:#00c47f; --red:#ff3b5c; --red2:#d4163b; --blue:#3da9ff; --amber:#ffb43b;
  --g-glow:rgba(0,245,160,.16); --r-glow:rgba(255,59,92,.16);
  --radius:22px; --font:-apple-system,BlinkMacSystemFont,"SF Pro Display","SF Pro Text","Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  --mono:ui-monospace,SFMono-Regular,"SF Mono",Menlo,Consolas,monospace;
}
html,body,.stApp,[data-testid="stAppViewContainer"],[data-testid="stMain"]{background:var(--bg)!important;color:var(--text);font-family:var(--font);
  -webkit-font-smoothing:antialiased;-webkit-tap-highlight-color:transparent}
header[data-testid="stHeader"],footer,#MainMenu,[data-testid="stToolbar"],[data-testid="stDecoration"],[data-testid="stStatusWidget"],
[data-testid="stDeployButton"],.stDeployButton{display:none!important}
.block-container{max-width:520px!important;padding:max(14px,env(safe-area-inset-top)) 14px calc(env(safe-area-inset-bottom) + 70px)!important}
[data-testid="stVerticalBlock"]{gap:.7rem!important}
div[data-stale="true"]{opacity:1!important}
[data-testid="stElementContainer"]{width:100%}
.stApp::before{content:"";position:fixed;inset:0;pointer-events:none;z-index:0;
  background:radial-gradient(70% 40% at 12% -5%,rgba(61,169,255,.07),transparent 70%),radial-gradient(60% 35% at 100% 0%,rgba(0,245,160,.06),transparent 70%)}
.tc *{box-sizing:border-box}
.tc{font-family:var(--font)}
.num{font-variant-numeric:tabular-nums;font-feature-settings:"tnum" 1}
.pos{color:var(--green)} .neg{color:var(--red)} .mut{color:var(--muted)} .amb{color:var(--amber)} .blu{color:var(--blue)}

/* ───── header ───── */
.hdr{display:flex;align-items:center;justify-content:space-between;padding:4px 2px 2px}
.logo{display:flex;align-items:center;gap:10px;font-weight:800;letter-spacing:.14em;font-size:15px}
.logo b{font-weight:800;background:linear-gradient(90deg,#fff,#9ff7d6 60%,var(--green));-webkit-background-clip:text;background-clip:text;color:transparent}
.logo-mark{width:30px;height:30px;border-radius:10px;display:grid;place-items:center;background:linear-gradient(145deg,#0f1a17,#08120f);
  border:1px solid rgba(0,245,160,.35);box-shadow:0 0 18px rgba(0,245,160,.25),inset 0 0 12px rgba(0,245,160,.12)}
.badges{display:flex;gap:6px;align-items:center}
.badge{font-size:10.5px;font-weight:800;letter-spacing:.12em;padding:5px 9px;border-radius:99px;border:1px solid var(--line2);color:var(--muted);background:rgba(255,255,255,.03)}
.badge.paper{color:var(--amber);border-color:rgba(255,180,59,.4);background:rgba(255,180,59,.08)}
.badge.live{color:#fff;border-color:rgba(255,59,92,.7);background:rgba(255,59,92,.18);box-shadow:0 0 14px rgba(255,59,92,.4)}
.badge.run{color:var(--green);border-color:rgba(0,245,160,.45);background:rgba(0,245,160,.09)}
.badge.pause{color:var(--amber);border-color:rgba(255,180,59,.4);background:rgba(255,180,59,.08)}
.badge.halt{color:var(--red);border-color:rgba(255,59,92,.5);background:rgba(255,59,92,.1)}

/* ───── cards ───── */
.card{position:relative;background:linear-gradient(180deg,rgba(255,255,255,.04),rgba(255,255,255,.012));border:1px solid var(--line);
  border-radius:var(--radius);padding:16px 18px;overflow:hidden}
.card h4{margin:0 0 10px;font-size:11px;font-weight:800;letter-spacing:.16em;color:var(--muted);text-transform:uppercase;display:flex;align-items:center;justify-content:space-between}
.card h4 small{font-size:10.5px;letter-spacing:.04em;font-weight:600;color:var(--dim);text-transform:none}

/* ───── hero ───── */
.hero{padding:20px 20px 18px;border-radius:26px;background:radial-gradient(120% 100% at 50% -20%,var(--glow,rgba(255,255,255,.05)),transparent 62%),linear-gradient(180deg,#0d1118,#090c12);
  border:1px solid var(--line)}
.hero.up{--glow:var(--g-glow);border-color:rgba(0,245,160,.18)} .hero.down{--glow:var(--r-glow);border-color:rgba(255,59,92,.2)}
.hero .lbl{font-size:11px;font-weight:700;letter-spacing:.16em;color:var(--muted);text-transform:uppercase}
.hero .big{font-size:clamp(36px,11vw,50px);font-weight:800;letter-spacing:-.02em;line-height:1.08;margin:6px 0 10px}
.hero .big small{font-size:.5em;font-weight:700;color:var(--muted);margin-left:4px}
.chip{display:inline-flex;align-items:center;gap:6px;padding:7px 12px;border-radius:99px;font-weight:700;font-size:14px;border:1px solid transparent}
.chip.up{color:var(--green);background:rgba(0,245,160,.1);border-color:rgba(0,245,160,.25)}
.chip.down{color:var(--red);background:rgba(255,59,92,.1);border-color:rgba(255,59,92,.28)}
.chip.flat{color:var(--muted);background:rgba(255,255,255,.05);border-color:var(--line)}
.hero .sub{display:flex;gap:16px;margin-top:14px;font-size:12.5px;color:var(--muted);flex-wrap:wrap}
.hero .sub b{color:var(--text);font-weight:700}

/* ───── banners ───── */
.banner{display:flex;gap:10px;align-items:flex-start;padding:12px 14px;border-radius:16px;font-size:13px;line-height:1.35;border:1px solid var(--line2);background:rgba(255,255,255,.04)}
.banner.warn{border-color:rgba(255,180,59,.4);background:rgba(255,180,59,.08);color:#ffd9a0}
.banner.bad{border-color:rgba(255,59,92,.5);background:rgba(255,59,92,.1);color:#ffc2cc}
.banner.ok{border-color:rgba(0,245,160,.35);background:rgba(0,245,160,.07);color:#b8ffe4}
.banner b{color:#fff}

/* ───── buttons (real Streamlit buttons, styled via their key) ───── */
.stButton button{border-radius:20px!important;font-weight:800!important;letter-spacing:.06em!important;min-height:62px!important;font-size:15.5px!important;
  border:1px solid var(--line2)!important;transition:transform .12s ease,filter .2s ease!important;width:100%}
.stButton button:active{transform:scale(.975)}
.stButton button p{font-weight:800!important;font-size:15.5px!important;letter-spacing:.06em}
.st-key-btn_start button,.st-key-btn_login button{background:linear-gradient(135deg,#00ffa8,#00c0c0)!important;color:#00160d!important;border:0!important;box-shadow:0 10px 32px rgba(0,245,160,.28),inset 0 1px 0 rgba(255,255,255,.4)!important}
.st-key-btn_start button p,.st-key-btn_login button p{color:#00160d!important}
.st-key-btn_pause button{background:linear-gradient(135deg,#1a2030,#131826)!important;color:var(--amber)!important;border:1px solid rgba(255,180,59,.45)!important}
.st-key-btn_pause button p{color:var(--amber)!important}
.st-key-btn_panic button,.st-key-btn_panic_yes button{background:linear-gradient(135deg,#ff2a52,#d4163b)!important;color:#fff!important;border:0!important;animation:panic 1.9s ease-in-out infinite}
.st-key-btn_panic button p,.st-key-btn_panic_yes button p{color:#fff!important}
.st-key-btn_ghost button,.st-key-btn_ghost_reset button,.st-key-btn_ghost_logout button{background:rgba(255,255,255,.04)!important;color:var(--muted)!important;min-height:46px!important;font-size:13px!important}
.st-key-btn_ghost button p,.st-key-btn_ghost_reset button p,.st-key-btn_ghost_logout button p{color:var(--muted)!important;font-size:13px!important}
.st-key-btn_start_yes button{background:linear-gradient(135deg,#00ffa8,#00c0c0)!important;color:#00160d!important;border:0!important}
.st-key-btn_start_yes button p{color:#00160d!important}
@keyframes panic{0%,100%{box-shadow:0 0 0 0 rgba(255,42,82,.55),0 10px 30px rgba(255,42,82,.28)}55%{box-shadow:0 0 0 14px rgba(255,42,82,0),0 10px 38px rgba(255,42,82,.5)}}
.ctrl-note{text-align:center;font-size:12px;color:var(--muted);margin-top:-2px}
.confirm{border:1px solid rgba(255,59,92,.5);background:linear-gradient(180deg,rgba(255,59,92,.12),rgba(255,59,92,.04));border-radius:20px;padding:16px;text-align:center;font-size:14px;line-height:1.4}
.confirm.go{border-color:rgba(0,245,160,.45);background:linear-gradient(180deg,rgba(0,245,160,.1),rgba(0,245,160,.03))}
.confirm b{font-size:16px}

/* ───── agent tracker ───── */
.agent{display:flex;gap:14px;align-items:flex-start;padding:14px 16px;border-radius:20px;background:linear-gradient(180deg,rgba(255,255,255,.04),rgba(255,255,255,.012));border:1px solid var(--line);position:relative}
.agent .ic{width:42px;height:42px;border-radius:14px;display:grid;place-items:center;flex:0 0 auto;background:rgba(255,255,255,.04);border:1px solid var(--line);position:relative}
.agent .dot{position:absolute;top:-3px;right:-3px;width:11px;height:11px;border-radius:50%;background:var(--c,var(--dim));border:2px solid var(--bg2)}
.agent .body{min-width:0;flex:1}
.agent .nm{font-weight:800;font-size:14px;display:flex;justify-content:space-between;gap:8px;align-items:baseline}
.agent .nm small{font-weight:600;font-size:11px;color:var(--dim);white-space:nowrap}
.agent .tx{font-size:13.5px;color:var(--text);margin-top:4px;line-height:1.35;word-break:break-word}
.agent .tx.typing{display:inline-block;overflow:hidden;white-space:nowrap;max-width:100%;animation:typing 1.1s steps(36,end) 1,caret .7s step-end 2;border-right:2px solid var(--c)}
.agent .dt{font-size:11.5px;color:var(--muted);margin-top:3px;line-height:1.3}
.agent.working{--c:var(--blue);border-color:rgba(61,169,255,.35);background:linear-gradient(180deg,rgba(61,169,255,.09),rgba(61,169,255,.02))}
.agent.working .dot{animation:pulse-b 1.3s infinite}
.agent.working .ic{border-color:rgba(61,169,255,.45);box-shadow:0 0 22px rgba(61,169,255,.22)}
.agent.ok{--c:var(--green)} .agent.warn{--c:var(--amber)} .agent.error{--c:var(--red)} .agent.idle{--c:var(--dim)}
.agent.ok .dot{box-shadow:0 0 10px rgba(0,245,160,.7)} .agent.warn .dot{animation:pulse-a 1.6s infinite} .agent.error .dot{animation:pulse-r 1.1s infinite}
@keyframes pulse-b{0%{box-shadow:0 0 0 0 rgba(61,169,255,.8)}70%{box-shadow:0 0 0 11px rgba(61,169,255,0)}100%{box-shadow:0 0 0 0 rgba(61,169,255,0)}}
@keyframes pulse-a{0%{box-shadow:0 0 0 0 rgba(255,180,59,.7)}70%{box-shadow:0 0 0 9px rgba(255,180,59,0)}100%{box-shadow:0 0 0 0 rgba(255,180,59,0)}}
@keyframes pulse-r{0%{box-shadow:0 0 0 0 rgba(255,59,92,.8)}70%{box-shadow:0 0 0 10px rgba(255,59,92,0)}100%{box-shadow:0 0 0 0 rgba(255,59,92,0)}}
@keyframes typing{from{max-width:0}to{max-width:100%}}
@keyframes caret{50%{border-color:transparent}}

/* ───── markets ───── */
.mk{padding:15px 16px}
.mk .row{display:flex;justify-content:space-between;align-items:flex-start;gap:10px}
.mk .sym{font-weight:800;font-size:17px;letter-spacing:.02em} .mk .sym small{color:var(--dim);font-weight:700;font-size:11px;margin-left:6px;letter-spacing:.1em}
.mk .px{font-size:19px;font-weight:800;text-align:right} .mk .ch{font-size:12px;font-weight:700;text-align:right}
.tags{display:flex;gap:6px;flex-wrap:wrap;margin:10px 0 2px}
.tag{font-size:11px;font-weight:700;padding:4px 9px;border-radius:99px;background:rgba(255,255,255,.05);border:1px solid var(--line);color:var(--muted)}
.tag.up{color:var(--green);background:rgba(0,245,160,.08);border-color:rgba(0,245,160,.25)} .tag.down{color:var(--red);background:rgba(255,59,92,.08);border-color:rgba(255,59,92,.25)}
.tag.buy{color:#00160d;background:linear-gradient(135deg,#00ffa8,#00c0c0);border-color:transparent;box-shadow:0 0 16px rgba(0,245,160,.45);animation:glow 1.6s ease-in-out infinite}
.tag.held{color:var(--blue);background:rgba(61,169,255,.1);border-color:rgba(61,169,255,.3)}
@keyframes glow{50%{box-shadow:0 0 26px rgba(0,245,160,.8)}}
.gauge{margin:12px 0 4px;position:relative;height:8px;border-radius:99px;background:linear-gradient(90deg,rgba(61,169,255,.55) 0 30%,rgba(255,255,255,.1) 30% 70%,rgba(255,59,92,.55) 70% 100%)}
.gauge i{position:absolute;top:-4px;width:4px;height:16px;border-radius:3px;background:#fff;box-shadow:0 0 10px rgba(255,255,255,.8);transform:translateX(-2px)}
.gl{display:flex;justify-content:space-between;font-size:10.5px;color:var(--dim);margin-bottom:8px}
.checks{display:flex;flex-wrap:wrap;gap:5px 6px;margin-top:8px}
.ck{font-size:10.8px;font-weight:600;padding:3px 8px;border-radius:8px;border:1px solid var(--line);color:var(--dim)}
.ck.ok{color:#9fffd6;border-color:rgba(0,245,160,.3);background:rgba(0,245,160,.06)} .ck.no{color:#8d95a9}
.sig{font-size:12px;color:var(--muted);margin-top:8px;line-height:1.35}

/* ───── positions / feed / stats ───── */
.empty{text-align:center;color:var(--dim);font-size:13px;padding:14px 4px}
.pos-i{padding:13px 0;border-top:1px solid var(--line)} .pos-i:first-of-type{border-top:0;padding-top:2px}
.pos-i .t{display:flex;justify-content:space-between;align-items:baseline;font-weight:800;font-size:15px}
.pos-i .s{display:flex;justify-content:space-between;font-size:12px;color:var(--muted);margin-top:4px;gap:8px}
.bar{height:6px;border-radius:99px;background:rgba(255,255,255,.07);margin-top:9px;position:relative;overflow:hidden}
.bar i{position:absolute;left:0;top:0;bottom:0;border-radius:99px;background:linear-gradient(90deg,var(--red),var(--amber),var(--green))}
.feed-i{display:flex;gap:12px;align-items:center;padding:11px 0;border-top:1px solid var(--line)} .feed-i:first-of-type{border-top:0;padding-top:2px}
.feed-i.new{animation:slidein .7s cubic-bezier(.2,.9,.3,1)}
@keyframes slidein{from{opacity:0;transform:translateY(-14px) scale(.98);background:rgba(0,245,160,.14)}to{opacity:1;transform:none;background:transparent}}
.fi{width:36px;height:36px;border-radius:12px;display:grid;place-items:center;font-weight:900;font-size:14px;flex:0 0 auto}
.fi.buy{color:var(--green);background:rgba(0,245,160,.1);border:1px solid rgba(0,245,160,.3)} .fi.sell{color:var(--red);background:rgba(255,59,92,.1);border:1px solid rgba(255,59,92,.3)}
.feed-i .m{flex:1;min-width:0} .feed-i .m b{font-size:14px} .feed-i .m div{font-size:11.8px;color:var(--muted);margin-top:2px}.feed-i .m div+div{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.feed-i .r{text-align:right;font-weight:800;font-size:14px;white-space:nowrap} .feed-i .r small{display:block;font-weight:600;font-size:11px;color:var(--dim)}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}
.kpi{background:rgba(255,255,255,.035);border:1px solid var(--line);border-radius:16px;padding:11px 12px}
.kpi small{display:block;font-size:10.5px;color:var(--muted);font-weight:700;letter-spacing:.04em}
.kpi b{display:block;font-size:17px;font-weight:800;margin-top:3px}
.log{font-family:var(--mono);font-size:11.3px;line-height:1.55;color:var(--muted)}
.log div{display:flex;gap:8px;padding:2px 0;border-top:1px dashed rgba(255,255,255,.05)} .log div:first-child{border-top:0}
.log time{color:var(--dim);flex:0 0 auto} .log em{font-style:normal;flex:0 0 auto;font-weight:700}
.log .ok{color:#8dffd0} .log .warn{color:var(--amber)} .log .bad{color:#ff8da1} .log span{min-width:0;word-break:break-word}
.sysrow{display:flex;justify-content:space-between;font-size:12.5px;padding:7px 0;border-top:1px solid var(--line);color:var(--muted)} .sysrow:first-of-type{border-top:0}
.sysrow b{color:var(--text);font-weight:600;text-align:right}
.foot{text-align:center;font-size:11px;color:var(--dim);padding:6px 0 0;line-height:1.5}

/* ───── segmented control + plotly ───── */
[data-testid="stSegmentedControl"]{width:100%} [data-testid="stSegmentedControl"] button{font-weight:700!important;font-size:12px!important}
.js-plotly-plot,.plotly{border-radius:16px}
[data-testid="stPlotlyChart"]{margin:-6px -6px 0}
.st-key-chartcard{background:linear-gradient(180deg,rgba(255,255,255,.04),rgba(255,255,255,.012));border:1px solid var(--line);border-radius:var(--radius);padding:16px 16px 8px}
.st-key-chartcard [data-testid="stVerticalBlock"]{gap:.35rem!important}
.loginwrap{margin-top:12vh}
.login{max-width:380px;margin:0 auto;text-align:center}
.login h1{font-size:22px;letter-spacing:.14em;margin:16px 0 4px}
.login p{color:var(--muted);font-size:13.5px}
[data-testid="stTextInput"] input{background:var(--card2)!important;border-radius:16px!important;color:var(--text)!important;min-height:52px;border:1px solid var(--line2)!important;font-size:16px!important}
@media (max-width:380px){.grid{grid-template-columns:repeat(2,1fr)}.hero .big{font-size:36px}.logo{letter-spacing:.07em;font-size:13.5px;gap:8px}.badge{padding:4px 7px;letter-spacing:.08em}}
@media (prefers-reduced-motion:reduce){*{animation-duration:.01ms!important;animation-iteration-count:1!important}}
"""

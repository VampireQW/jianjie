"""随简截本机接收服务提供的安卓选图页；无外部资源或运行时。"""

HTML = r'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<title>简捷互传</title>
<link rel="icon" href="/android/icon-192.png" type="image/png">
<link rel="apple-touch-icon" href="/android/icon-192.png">
<link rel="manifest" href="/android/manifest.webmanifest">
<link rel="stylesheet" href="/android/style.css"><script defer src="/android/app.js"></script>
</head><body><main>
<header><span class="brand">简捷互传</span><span class="badge">局域网互传</span></header>
<h1>跨设备，随手互传</h1><p class="intro">Mac、Windows 与手机，图片、文字和文件双向收发。</p>
<section class="connection" aria-label="目标电脑"><span>接收电脑</span><strong id="computer"></strong></section>
<details id="home-guide" class="home-guide"><summary><span>记住连接 / 添加桌面入口</span><span id="pair-state">正在连接</span></summary>
<ol><li>Mac / Windows：用浏览器打开配对链接，收藏此页即可再次使用。iPhone 请用 Safari；安卓请用 Chrome 或常用浏览器打开此页。微信内打开时，可复制下方配对链接到浏览器。</li><li>iPhone 点 Safari 分享按钮 →“添加到主屏幕”。安卓点浏览器右上角“⋮”，选择“添加到主屏幕”或“创建快捷方式”，命名为“简捷互传”。具体名称以浏览器菜单为准。</li><li>以后截屏后点桌面图标，选择刚截的图片并发送。手机与电脑仍需连接同一局域网。</li></ol>
<p>正常浏览器会记住此电脑。修改电脑账号密码后重新扫码；更换电脑地址或端口后重新创建桌面入口。</p>
<label for="pair-link">配对链接（换浏览器时使用）</label><input id="pair-link" readonly aria-label="配对链接">
<div class="entry-actions"><button id="copy-pair" type="button" disabled>复制配对链接</button><button id="forget" type="button" disabled>取消此浏览器配对</button></div>
</details>
<label class="content-label" for="text">发送内容 · 支持拖入文件</label><textarea id="text" placeholder="输入或粘贴文字，也可以选择图片、文件" rows="3"></textarea>
<section class="image-area" aria-label="待发送图片">
  <div id="empty"><svg viewBox="0 0 48 48" aria-hidden="true"><rect x="6" y="6" width="36" height="36" rx="4"/><circle cx="18" cy="17" r="3"/><path d="m8 35 11-11 8 8 6-6 8 9"/></svg><p>选择要发送的图片</p><span>最多 9 张 · JPEG / PNG · 单张最大 32 MiB</span></div>
  <img id="preview" alt="待发送图片预览" hidden>
</section>
<div class="file-info"><span id="filename">尚未选择图片或文件</span><span id="filesize"></span></div>
<ol id="selection-list" class="selection-list" aria-label="已选图片或文件" hidden></ol>
<input id="any-file" type="file" multiple hidden>
<input id="file" type="file" accept="image/jpeg,image/png,.jpg,.jpeg,.png" multiple hidden>
<div class="compose-actions"><button id="choose" type="button">选择图片（最多 9 张）</button><button id="choose-file" type="button">选择文件</button><button id="remove-image" type="button" hidden>移除</button></div>
<div class="status-box" role="status" aria-live="polite"><p id="status">正在确认连接…</p><progress id="progress" max="100" value="0" hidden aria-label="文件上传进度"></progress></div>
<footer><button id="send" type="button" disabled>发送到电脑</button><button id="cancel-send" type="button" hidden>取消发送</button><p>仅在你的局域网内传输</p></footer>
<section class="recent" aria-label="最近互传"><div class="recent-heading"><h2>最近互传</h2><span>最近 9 条 · 30 分钟</span></div><p id="receive-status" role="status">正在获取记录…</p><div id="records"></div></section>
<details><summary>连接或发送遇到问题？</summary><p>两台设备需处于可互通的同一局域网，电脑上的简截需保持“简捷互传已开启”。微信内无法选择图片时，请用浏览器打开，或展开上方“添加到手机桌面”复制配对链接。</p><p>修改了用户名或密码，请重新扫描电脑上的二维码。清理浏览器网站数据也会清除配对。一次最多选择 9 张 JPEG / PNG，高像素照片按原分辨率发送，单张最大 32 MiB。图片和文字可在支持粘贴的应用中使用；文件可在资源管理器中粘贴保存，也可在电脑的简捷互传记录中点“保存”。文件最大 2 GiB；临时记录保留 30 分钟，请及时保存。</p></details>
<noscript><p>请用支持 JavaScript 的手机浏览器打开此页面。</p></noscript>

<dialog id="image-copy-dialog"><div class="copy-heading"><strong>复制图片</strong><button id="close-copy" type="button" aria-label="关闭图片" title="关闭"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18"/></svg></button></div><p id="copy-tip">长按下方原图，在手机菜单中选择“复制”。</p><img id="copy-original" alt="长按复制原图"></dialog>
</main></body></html>'''

CSS = r''':root {
--bg:#f7f8f8;--surface:#ffffff;--surface-alt:#f1f2f4;--hover:#ebedf0;
--border:#d8dee3;--control:#808790;--text:#1a1d23;--muted:#4e5560;
--accent:#1d4ed8;--on-accent:#ffffff;--soft:#eef2ff;--error:#b42318;--success:#046c4e;
--space-1:4px;--space-2:8px;--space-3:12px;--space-4:16px;--space-5:24px;--space-6:32px;
--radius:6px;--font:"PingFang SC","Microsoft YaHei UI","Microsoft YaHei","Noto Sans CJK SC",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
}
@media(prefers-color-scheme:dark){:root{
--bg:#0b0d10;--surface:#121519;--surface-alt:#0e1116;--hover:#1a1e23;
--border:#262b31;--control:#626a78;--text:#f2f4f7;--muted:#a8b0ba;
--accent:#7ba2ff;--on-accent:#0b0d10;--soft:#1a1e23;--error:#ff8a8a;--success:#5fd39b;
}}
*{box-sizing:border-box}html{color-scheme:light dark}body{margin:0;background:var(--bg);color:var(--text);font-family:var(--font);font-size:14px;line-height:1.7;letter-spacing:.02em}
main{max-width:760px;margin:auto;padding:var(--space-5) var(--space-5) 0}
header{display:flex;align-items:center;justify-content:space-between;gap:var(--space-3);margin-bottom:var(--space-6)}
.brand{font-size:18px;font-weight:650}.badge{font-size:12px;color:var(--accent);background:var(--soft);border-radius:var(--radius);padding:var(--space-1) var(--space-2)}
h1{font-size:24px;line-height:1.7;font-weight:650;margin:0}.intro{margin:var(--space-1) 0 var(--space-5);color:var(--muted)}
.connection{display:flex;justify-content:space-between;align-items:center;gap:var(--space-3);padding:var(--space-3) var(--space-4);background:var(--surface-alt);border-radius:var(--radius);margin-bottom:var(--space-4)}
.connection span{font-size:12px;color:var(--muted)}.connection strong{font-weight:550;font-variant-numeric:tabular-nums;overflow-wrap:anywhere}
.image-area{background:var(--surface);border:1px dashed var(--control);border-radius:var(--radius);display:flex;align-items:center;justify-content:center;min-height:224px;height:28vh;max-height:320px;overflow:hidden;text-align:center}
.image-area svg{width:48px;height:48px;fill:none;stroke:var(--muted);stroke-width:2;stroke-linecap:round;stroke-linejoin:round}.image-area p{margin:var(--space-2) 0 var(--space-1)}.image-area span{font-size:12px;color:var(--muted)}
#preview{width:100%;height:100%;object-fit:contain}.file-info{display:flex;justify-content:space-between;gap:var(--space-3);font-size:12px;color:var(--muted);margin:var(--space-2) 0 var(--space-4)}
#filename{overflow-wrap:anywhere;min-width:0}#filesize{flex-shrink:0;font-variant-numeric:tabular-nums}
button{width:100%;min-height:48px;border:1px solid var(--control);border-radius:var(--radius);background:var(--surface);color:var(--text);font:500 14px/1.7 var(--font);letter-spacing:.02em;padding:var(--space-3) var(--space-4);cursor:pointer;touch-action:manipulation}
button:hover:enabled{background:var(--hover)}button:active:enabled{opacity:.8}button:disabled{cursor:default;color:var(--muted);background:var(--surface-alt)}
button:focus-visible,summary:focus-visible{outline:2px solid var(--accent);outline-offset:3px}
.status-box{min-height:24px;margin:var(--space-3) 0}#status{margin:0;color:var(--muted)}#status[data-tone=error]{color:var(--error)}#status[data-tone=success]{color:var(--success)}
progress{width:100%;height:8px;margin-top:var(--space-2);accent-color:var(--accent)}details{color:var(--muted);font-size:12px;margin-bottom:var(--space-5)}summary{min-height:44px;display:list-item;cursor:pointer;padding:var(--space-3) 0}details p{margin:var(--space-2) 0}
.home-guide{margin-top:calc(-1 * var(--space-2));margin-bottom:var(--space-4)}.home-guide summary{display:flex;justify-content:space-between;gap:var(--space-3);color:var(--accent)}.home-guide summary::before{content:"+"}.home-guide[open] summary::before{content:"−"}.home-guide summary span:first-child{flex:1}#pair-state{color:var(--muted)}.home-guide ol{padding-left:var(--space-5);margin:var(--space-2) 0}.home-guide li+li{margin-top:var(--space-2)}.home-guide input{display:block;width:100%;min-height:44px;margin:var(--space-2) 0;border:1px solid var(--control);border-radius:var(--radius);padding:var(--space-2) var(--space-3);background:var(--surface);color:var(--text);font:12px/1.7 var(--font);letter-spacing:.02em}.home-guide input:focus-visible{outline:2px solid var(--accent);outline-offset:3px}.entry-actions{display:flex;gap:var(--space-2)}.entry-actions button{font-size:12px;padding:var(--space-2)}
footer{position:static;background:var(--bg);padding:var(--space-3) 0 max(var(--space-4),env(safe-area-inset-bottom));border-top:1px solid var(--border)}footer p{font-size:12px;color:var(--muted);margin:var(--space-2) 0 0;text-align:center}
.home-guide[open]~footer{position:static}
#send{background:var(--accent);border-color:var(--accent);color:var(--on-accent)}#send:enabled:hover{opacity:.9}#send:disabled{background:var(--soft);border-color:var(--soft);color:var(--muted)}
[hidden]{display:none!important}@media(max-width:360px){main{padding:var(--space-4) var(--space-4) 0}h1{font-size:22px}}
@media(prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important}}

textarea{width:100%;resize:vertical;min-height:104px;max-height:224px;border:1px solid var(--control);border-radius:var(--radius);background:var(--surface);color:var(--text);padding:12px;font:inherit;line-height:1.7}
textarea:focus{outline:2px solid var(--accent);outline-offset:2px}textarea::placeholder{color:var(--muted)}
.content-label{display:block;margin:12px 0 8px;font-weight:600}.image-area:has(#preview[hidden]){display:none}.image-area{height:144px;min-height:144px;margin-top:12px}.file-info:has(#filesize:empty){display:none}
.compose-actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}.compose-actions button{flex:1}.recent{margin:24px 0 16px}.recent-heading{display:flex;justify-content:space-between;align-items:center;gap:8px}.recent h2{font-size:16px;margin:0}.recent-heading span,#receive-status{color:var(--muted);font-size:12px}
.record{background:var(--surface);border:1px solid var(--border);padding:12px;border-radius:var(--radius);margin-top:8px}.record-meta{display:flex;justify-content:space-between;gap:8px;color:var(--muted);font-size:12px}.record textarea{min-height:68px;max-height:120px;margin:8px 0;resize:none;font-size:14px}.record img{display:block;width:100%;height:112px;object-fit:contain;margin:8px 0;background:var(--surface-alt);border-radius:var(--radius)}.record button{width:100%;margin-top:8px}.record button:disabled{cursor:default}.record p{margin:8px 0;font-size:12px;color:var(--muted)}[hidden]{display:none!important}
@media(max-width:360px){main{padding:16px 16px 0}h1{font-size:22px}.recent-heading span{font-size:11px}}
'''

CSS += r'''
.record-actions{display:flex;gap:var(--space-2)}.record-actions button{flex:1;min-width:0}
.selection-list{list-style:none;padding:0;margin:var(--space-3) 0;border:1px solid var(--border);border-radius:var(--radius);overflow:hidden}
.selection-list button{width:auto;min-height:44px}
.selection-list li{display:flex;align-items:center;gap:var(--space-2);padding:var(--space-1) var(--space-2);background:var(--surface)}.selection-list li+li{border-top:1px solid var(--border)}
.selection-list .selection-name{flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:left;font-size:12px}
.selection-list button.selection-name{padding:var(--space-2);background:transparent;border-color:transparent}.selection-list button[aria-pressed=true]{color:var(--accent);background:var(--soft)}
.selection-size{flex:none;color:var(--muted);font-size:12px}.selection-remove{flex:none;min-width:44px;padding:var(--space-2);font-size:12px}
.record strong{display:block;overflow-wrap:anywhere;margin-top:var(--space-2)}
dialog{width:calc(100% - 32px);max-width:760px;max-height:85vh;padding:var(--space-4);background:var(--surface);color:var(--text);border:1px solid var(--control);border-radius:var(--radius);overflow:auto}
dialog::backdrop{background:var(--bg);opacity:.9}.copy-heading{display:flex;align-items:center;justify-content:space-between;gap:var(--space-3)}dialog p{font-size:14px;line-height:1.7}dialog img{display:block;width:100%;height:auto;object-fit:contain}
#close-copy{display:grid;place-items:center;flex:0 0 44px;width:44px;height:44px;min-width:0;min-height:44px;padding:0;border:0;background:transparent;color:var(--muted)}#close-copy svg{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round}#close-copy:hover:enabled{background:var(--hover);color:var(--text)}
'''

JS = r'''(() => {
"use strict";
const el = id => document.getElementById(id);
const file = el("file"), choose = el("choose"), send = el("send"), preview = el("preview"), status = el("status"), progress = el("progress");
const MAX_FILE_BYTES = 2 * 1024 * 1024 * 1024;
const MAX_IMAGES = 9;
let activeUpload = null;
let selectedFiles = [];
let selectedKind = "image";
let token = "", selected = null, previewUrl = "", previewFile = null, busy = false, selecting = false, paired = false, generation = 0;
const storageKey = "jianjie-phone-token";
const isWeChat = /MicroMessenger/i.test(navigator.userAgent);
el("computer").textContent = location.host;
function message(text, tone = "normal") { status.textContent = text; status.dataset.tone = tone; }
function controls() { const locked=busy||selecting; choose.disabled = locked || !paired; el("choose-file").disabled = locked || !paired; send.disabled = locked || !paired || (!selected && !el("text").value.trim()); el("copy-pair").disabled = locked || !paired; el("forget").disabled = locked || !token; el("text").disabled = locked; el("remove-image").disabled = locked; for(const button of el("selection-list").querySelectorAll("button")) button.disabled=locked; }
function cancelPair(force = false) { const rejected = token; token = ""; paired = false; for(const storage of ["localStorage","sessionStorage"]) { try { if(force || window[storage].getItem(storageKey) === rejected) window[storage].removeItem(storageKey); } catch(_) {} } el("pair-link").value = ""; el("pair-state").textContent = "未配对"; history.replaceState(null, "", location.pathname); controls(); }
function rememberPair() { let saved = false; try { localStorage.setItem(storageKey, token); saved = localStorage.getItem(storageKey) === token; } catch(_) {} try { sessionStorage.setItem(storageKey, token); } catch(_) {} el("pair-state").textContent = saved ? "已记住此电脑" : "仅本次配对"; el("pair-link").value = location.origin + "/android#token=" + token; if(saved && !isWeChat) history.replaceState(null, "", location.pathname); return saved; }
function clearPreview() { preview.onload=null; preview.onerror=null; preview.hidden=true; preview.removeAttribute("src"); if(previewUrl) URL.revokeObjectURL(previewUrl); previewUrl=""; previewFile=null; el("empty").hidden=false; }
function clearSelection() { selected = null; selectedFiles = []; clearPreview(); el("selection-list").replaceChildren(); el("selection-list").hidden=true; el("filename").textContent = "尚未选择图片或文件"; el("filesize").textContent = ""; choose.textContent = "选择图片（最多 9 张）"; el("text").hidden = false; el("remove-image").hidden = true; send.textContent="发送到电脑"; controls(); }
function selectionLabel(count=selectedFiles.length, kind=selectedKind) { return count+(kind==="image" ? " 张图片" : " 个文件"); }
function renderSelection() {
  selected=selectedFiles[0]||null;
  const list=el("selection-list"); list.replaceChildren(); list.hidden=selectedFiles.length<2;
  if(!selected) return;
  el("filename").textContent=selectedFiles.length>1 ? "已选择 "+selectionLabel() : selected.name;
  el("filesize").textContent=(selectedFiles.reduce((n,f)=>n+f.size,0)/1048576).toFixed(2)+" MiB";
  el("text").hidden=true; el("remove-image").hidden=false;
  el("remove-image").textContent=selectedFiles.length>1 ? "清空已选" : "移除";
  choose.textContent=selectedKind==="image" ? "更换图片（最多 9 张）" : "选择图片（最多 9 张）";
  selectedFiles.forEach((item,index)=>{
    const row=node("li"), name=node(selectedKind==="image" ? "button" : "span",(index+1)+". "+item.name,"selection-name");
    name.title=item.name;
    if(selectedKind==="image") { name.type="button"; name.setAttribute("aria-pressed",String(item===previewFile)); name.onclick=()=>{if(!busy&&!selecting)showPreview(item);}; }
    const remove=node("button","移除","selection-remove"); remove.type="button"; remove.setAttribute("aria-label","移除 "+item.name); remove.onclick=()=>removeSelected(index);
    row.append(name,node("span",(item.size/1048576).toFixed(2)+" MiB","selection-size"),remove); list.append(row);
  });
  controls();
}
function showPreview(item) {
  clearPreview(); previewFile=item;
  const url=previewUrl=URL.createObjectURL(item);
  preview.onload=()=>{if(previewUrl===url){preview.hidden=false;el("empty").hidden=true;}};
  preview.onerror=()=>{if(previewUrl===url){preview.hidden=true;message("“"+item.name+"”无法预览，可以尝试发送，或移除后重新选择。","error");}};
  preview.src=url; renderSelection();
}
function removeSelected(index) {
  if(busy||selecting) return;
  ++generation;
  const removed=selectedFiles.splice(index,1)[0];
  if(!selectedFiles.length){clearSelection();message("请输入文字，或选择图片、文件。");return;}
  if(selectedKind==="image"&&removed===previewFile) showPreview(selectedFiles[0]); else renderSelection();
  send.textContent="发送 "+selectionLabel(); message("已选择 "+selectionLabel()+"，将按顺序发送。");
}
try {
  const fresh = new URLSearchParams(location.hash.slice(1)).get("token");
  if(fresh && /^[a-f0-9]{64}$/.test(fresh)) token = fresh;
  else { try { token = localStorage.getItem(storageKey) || ""; } catch(_) {} if(!token) { try { token = sessionStorage.getItem(storageKey) || ""; } catch(_) {} } }
} catch(_) {}
el("forget").addEventListener("click", () => { ++generation; cancelPair(true); clearSelection(); el("text").value = ""; clearRecords(); message("已取消此浏览器配对。再次使用时，请扫描电脑上的二维码。", "normal"); });
el("copy-pair").addEventListener("click", async () => {
  const input = el("pair-link"), button = el("copy-pair"); let copied = false;
  try { if(navigator.clipboard) { await navigator.clipboard.writeText(input.value); copied = true; } } catch(_) {}
  if(!copied) { try { input.focus(); input.select(); input.setSelectionRange(0, input.value.length); copied = document.execCommand("copy"); } catch(_) {} }
  button.textContent = copied ? "已复制配对链接" : "请长按上方链接复制";
  setTimeout(() => { button.textContent = "复制配对链接"; }, 2500);
});
choose.addEventListener("click", () => { file.value = ""; file.click(); });
file.addEventListener("change", () => selectFiles(Array.from(file.files), false));
el("choose-file").addEventListener("click",()=>{el("any-file").value="";el("any-file").click();});
el("any-file").addEventListener("change",()=>selectFiles(Array.from(el("any-file").files)));
async function selectFiles(files, generic=true) {
  if(!files.length || busy || selecting || !paired) return;
  if(files.length > (generic ? 5 : MAX_IMAGES)) { message(generic ? "一次最多选择 5 个文件。" : "一次最多选择 9 张图片。", "error"); return; }
  if(files.some(f => !f.size || f.size > (generic ? MAX_FILE_BYTES : 32*1024*1024))) { message(generic ? "请选择非空文件，每个最大 2 GiB。" : "请选择非空图片，单张最大 32 MiB。", "error"); return; }
  const current=++generation; selecting=true; controls();
  message(generic ? "正在准备文件…" : "正在读取所选图片…");
  try {
    if(!generic) for(const next of files) {
      const bytes = new Uint8Array(await next.slice(0, 16).arrayBuffer());
      if(current !== generation) return;
      const jpeg = bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255;
      const png = [137,80,78,71,13,10,26,10].every((v,i) => bytes[i] === v);
      if(!jpeg && !png) throw new Error("“"+next.name+"”不是 JPEG 或 PNG 图片，请转换后再选择。");
    }
    if(current !== generation) return;
    clearSelection(); selectedKind=generic ? "file" : "image"; selectedFiles=files.slice(); selected=selectedFiles[0]; el("text").value="";
    if(generic) renderSelection(); else showPreview(selected);
    send.textContent="发送 "+selectionLabel(); message("已选择 "+selectionLabel()+"，将按顺序发送。");
  } catch(error) { if(current===generation) message(error.message||"内容读取失败，请重新选择。", "error"); }
  finally { if(current===generation){selecting=false;controls();} }
}
document.querySelector("main").addEventListener("dragover", event => {
  if(event.dataTransfer && Array.from(event.dataTransfer.types).includes("Files")) event.preventDefault();
});
document.querySelector("main").addEventListener("drop", event => {
  if(!event.dataTransfer || !event.dataTransfer.files.length) return;
  event.preventDefault();
  if(!paired) { message("请先打开电脑提供的配对链接。", "error"); return; }
  if(busy) { message("正在发送，请完成或取消后再拖入文件。"); return; }
  const files=Array.from(event.dataTransfer.files);
  const imagesOnly=files.every(item=>/^image\/(jpeg|png)$/i.test(item.type)||/\.(jpe?g|png)$/i.test(item.name));
  selectFiles(files, !imagesOnly);
});
el("cancel-send").onclick = () => { if(activeUpload) activeUpload.abort(); };
send.addEventListener("click", () => {
  if((!selected && !el("text").value.trim()) || busy || selecting || !paired) return;
  if(!selected && new TextEncoder().encode(el("text").value).length > 65536){ message("文字最多 64 KiB，请分段发送。", "error"); return; }
  const uploads = selectedFiles.length ? selectedFiles.slice() : [selected];
  const batchKind=selectedKind, totalBytes=uploads.reduce((n,item)=>n+(item ? item.size : 0),0);
  let completed = 0, completedBytes=0;
  busy = true; controls(); progress.hidden = false; progress.value = 0;
  send.textContent = "正在发送…"; message("正在发送，请保持此页面打开。");
  function uploadNext(index) {
  const upload = uploads[index], isImage = !!upload, isFile = isImage && batchKind === "file";
  const xhr = new XMLHttpRequest(); activeUpload = xhr;
  // 文件不设总时长限制；60 秒无上传进度或确认才终止，允许慢速局域网。
  xhr.timeout = isFile ? 0 : 300000;
  el("cancel-send").hidden = false;
  let idleTimer = null, stalled = false, finished = false, started = performance.now();
  const watch = () => { clearTimeout(idleTimer); idleTimer = setTimeout(() => { stalled = true; xhr.abort(); }, 60000); };
  watch();
  xhr.upload.addEventListener("progress", event => {
    watch();
    if(isImage && event.lengthComputable) {
      const percent=Math.min(100,event.loaded/event.total*100);
      progress.value = totalBytes ? (completedBytes+Math.min(upload.size,event.loaded))/totalBytes*100 : percent;
      const elapsed = Math.max((performance.now()-started)/1000, .1);
      const speed = event.loaded/elapsed, remaining = Math.max(0, Math.ceil((event.total-event.loaded)/Math.max(speed,1)));
      const rate = speed >= 1048576 ? (speed/1048576).toFixed(1)+" MiB/s" : (speed/1024).toFixed(0)+" KiB/s";
      const eta = remaining >= 60 ? Math.ceil(remaining/60)+" 分钟" : remaining+" 秒";
      const prefix=uploads.length>1 ? (isFile ? "文件 " : "图片 ")+(index+1)+"/"+uploads.length+" · " : "";
      message(prefix+(percent >= 100 ? "已上传，正在等待电脑确认…" :
        "正在发送 "+Math.round(percent)+"% · "+rate+" · 约剩 "+eta));
    }
  });
  xhr.upload.addEventListener("load", watch);
  const finish = (text, tone) => {
    if(finished) return; finished = true; clearTimeout(idleTimer); activeUpload = null;
    if(tone === "success") {
      ++completed;
      completedBytes+=upload ? upload.size : 0;
      if(index+1 < uploads.length) { uploadNext(index+1); return; }
      if(uploads.length>1) text="已发送 "+selectionLabel(completed,batchKind)+"，请在电脑查看接收记录。";
    } else if(uploads.length>1) text="已发送 "+completed+"/"+uploads.length+(batchKind==="image" ? " 张图片。" : " 个文件。")+text;
    busy = false; progress.hidden = true; el("cancel-send").hidden = true;
    if(tone!=="success"&&completed&&upload){
      selectedFiles=uploads.slice(index);
      if(batchKind==="image"&&!selectedFiles.includes(previewFile)) showPreview(selectedFiles[0]); else renderSelection();
    }
    send.textContent = tone === "success" ? "再次发送" : (completed&&upload ? "继续发送剩余 "+selectionLabel() : "重新发送"); message(text, tone); controls();
  };
  xhr.onload = () => {
    let response = {}; try { response = JSON.parse(xhr.responseText); } catch(_) {}
    if(xhr.status === 200) { poll(); finish(response.message || "电脑已接收。", "success"); }
    else { if(xhr.status === 401) cancelPair(); finish(response.message || "发送失败，请检查电脑互传是否开启。", "error"); }
  };
  xhr.onerror = () => finish("连接中断，请确认局域网可互通、简捷互传已开启后重试。", "error");
  xhr.ontimeout = () => finish("未收到电脑确认，请先检查电脑是否已经收到，再重试。", "error");
  xhr.onabort = () => finish(stalled ? "60 秒没有传输进度或确认，请检查网络后重试。" : "已取消发送；若上传已完成，请检查电脑接收记录。", "error");
  xhr.open("POST", isImage ? (isFile ? "/exchange/file" : "/android/image") : "/exchange/text");
  xhr.setRequestHeader("Authorization", "Bearer " + token);
  if(isFile) xhr.setRequestHeader("X-File-Name",encodeURIComponent(upload.name));
  xhr.setRequestHeader("Content-Type", isImage ? (upload.type || "application/octet-stream") : "application/json");
  try { xhr.send(isImage ? upload : JSON.stringify({text:el("text").value})); }
  catch(error) { finish(error.message || "发送失败，请重新选择文件。", "error"); }
  }
  uploadNext(0);
});
window.addEventListener("beforeunload", event => { if(busy) { event.preventDefault(); event.returnValue = ""; } });
async function connect() {
  controls();
  if(!token) { el("pair-state").textContent = "未配对"; message("请打开电脑“简捷互传 → 浏览器连接”中提供的配对链接，或用手机扫码。", "error"); return; }
  const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 8000);
  try {
    const response = await fetch("/android/status", { headers: { Authorization: "Bearer " + token }, cache: "no-store", signal: controller.signal });
    const result = await response.json();
    if(response.status === 401) cancelPair();
    if(!response.ok) throw new Error(result.message);
    paired = true; poll(); const saved = rememberPair(); controls(); message(saved ? "电脑已连接，可以发送图片、文字或文件。" : "电脑已连接，但浏览器未能保存配对。请用普通浏览模式，或保留配对链接。", "normal");
  } catch(error) { message(error.name === "AbortError" || error.message === "Failed to fetch" ? "无法连接电脑，请检查局域网连接和互传开关，然后刷新页面。" : (error.message || "连接失败，请重新扫码。"), "error"); }
  finally { clearTimeout(timeout); }
}

let revision = -1, timer = null, polling = false, recordUrls = [], recordPreviewChain=Promise.resolve();
el("text").addEventListener("input", controls);
el("remove-image").addEventListener("click", () => { if(!busy) { ++generation; clearSelection(); message("请输入文字，或选择另一张图片。"); } });
function clearRecords(){ revision = -1; el("records").replaceChildren(); for(const url of recordUrls) URL.revokeObjectURL(url); recordUrls = []; clearTimeout(timer); }
async function api(path, options={}) {
  const controller = new AbortController(), limit = setTimeout(() => controller.abort(), 12000);
  const requestToken = token;
  try {
    const response = await fetch(path, {...options,headers:{...options.headers,Authorization:"Bearer "+requestToken},cache:"no-store",signal:controller.signal});
    if(response.status===401 && token===requestToken){ cancelPair(); clearRecords(); }
    if(!response.ok) { let data={}; try{data=await response.json();}catch(_){} throw new Error(data.message || "暂时无法连接电脑"); }
    if(token !== requestToken) throw new Error("配对已更改");
    return response;
  } finally { clearTimeout(limit); }
}
async function ack(id,state){ await api("/exchange/ack",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({id,state})}); }
function node(tag,text,className){ const n=document.createElement(tag); if(text!==undefined)n.textContent=text; if(className)n.className=className; return n; }
function loadRecordPreview(card,img,id){
  // One preview request at a time leaves a receiver slot for the serial upload.
  recordPreviewChain=recordPreviewChain.then(async()=>{
    if(!card.isConnected)return;
    const response=await api("/exchange/image/"+id+"/preview"),blob=await response.blob();
    if(!card.isConnected)return;
    const url=URL.createObjectURL(blob);recordUrls.push(url);img.src=url;
  }).catch(()=>{if(card.isConnected)img.replaceWith(node("p","预览暂不可用，可尝试下载原图。"));});
}
function render(items){
  for(const url of recordUrls) URL.revokeObjectURL(url); recordUrls=[];
  const list=el("records"); list.replaceChildren();
  if(!items.length){ el("receive-status").textContent="还没有互传记录，电脑发送后会显示在这里。"; return; }
  el("receive-status").textContent="页面打开时自动接收；切到后台后暂停检查。";
  for(const item of items){
    const card=node("article",undefined,"record"), meta=node("div",undefined,"record-meta");
    meta.append(node("span",item.sender==="desktop"?"电脑 → 本设备":"本设备 → 电脑"),node("span",new Date(item.created*1000).toLocaleTimeString([], {hour:"2-digit",minute:"2-digit"})));
    card.append(meta);
    if(item.kind==="text"){
      const content=document.createElement("textarea");content.readOnly=true;content.value=item.text;content.setAttribute("aria-label","互传文字");card.append(content);
      const copy=node("button","复制文字");copy.type="button";
      copy.onclick=async()=>{let copied=false;try{if(navigator.clipboard){await navigator.clipboard.writeText(item.text);copied=true;}}catch(_){}
        if(!copied){try{content.focus();content.select();content.setSelectionRange(0,content.value.length);copied=document.execCommand("copy");}catch(_){} }
        copy.textContent=copied?"文字已复制":"请长按上方文字复制"; };
      card.append(copy);
    } else if(item.kind==="image") {
      const img=document.createElement("img");img.alt="互传图片预览";card.append(img);
      loadRecordPreview(card,img,item.id);
      card.append(node("p",`${item.width} × ${item.height} · ${(item.size/1048576).toFixed(2)} MiB · 原图`));
      const download=node("button","下载原图");download.type="button";
      download.onclick=async()=>{download.disabled=true;download.textContent="正在下载…";try{
        const response=await api("/exchange/image/"+item.id), blob=await response.blob(), url=URL.createObjectURL(blob);
        const link=document.createElement("a");link.href=url;link.download=item.name;link.textContent="保存原图";link.rel="noopener";document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);
        download.textContent="原图已下载，可在浏览器中保存"; if(item.sender==="desktop")ack(item.id,"downloaded").catch(()=>{});
      }catch(error){download.textContent="重试下载原图";message(error.message,"error");}finally{download.disabled=false;}};
      const copy=node("button","复制图片"); copy.type="button";
      copy.onclick=()=>copyImage(item,copy);
      const actions=node("div",undefined,"record-actions");actions.append(copy,download);card.append(actions);
    }
    if(item.kind==="file") {
      card.append(node("strong",item.name),node("p",`${(item.size/1048576).toFixed(2)} MiB · 文件`));
      const download=node("button","下载文件");download.type="button";
      download.onclick=async()=>{download.disabled=true;download.textContent="正在准备下载…";try{
        const response=await api("/exchange/file/"+item.id+"/download-link"), data=await response.json();
        const url=new URL(data.url,location.origin);
        if(url.origin!==location.origin || !url.pathname.startsWith("/exchange/download/"))throw new Error("下载地址无效");
        // 原生下载直接落盘，不在 JavaScript 中把大文件转为 Blob。
        const link=document.createElement("a");link.href=url.href;link.download=item.name;link.rel="noopener";link.referrerPolicy="no-referrer";
        document.body.append(link);link.click();link.remove();
        download.textContent="再次下载文件";message("已开始下载，请查看浏览器下载列表。");
      }catch(error){download.textContent="重试下载文件";message(error.message,"error");}finally{download.disabled=false;}};
      card.append(download);
    }
    card.append(node("p",item.status.replace(/手机/g,"接收端")));list.append(card);
    if(item.kind==="text" && item.sender==="desktop" && item.status==="待手机获取") ack(item.id,"received").catch(()=>{});
  }
}
let originalUrl="";
function closeCopy(){el("image-copy-dialog").close();el("copy-original").removeAttribute("src");if(originalUrl)URL.revokeObjectURL(originalUrl);originalUrl="";}
el("close-copy").onclick=closeCopy;
el("image-copy-dialog").addEventListener("cancel",event=>{event.preventDefault();closeCopy();});
async function showOriginal(item){
  const dialog=el("image-copy-dialog");el("copy-tip").textContent="此浏览器不允许网页直接写入图片剪贴板。长按原图，选择“复制”；若菜单不提供复制，可下载保存。";
  dialog.showModal();
  try{const response=await api("/exchange/image/"+item.id),blob=await response.blob();if(!dialog.open)return;
    if(originalUrl)URL.revokeObjectURL(originalUrl);originalUrl=URL.createObjectURL(blob);el("copy-original").src=originalUrl;
  }catch(error){el("copy-tip").textContent=error.message;}
}
async function pngBlob(item){
  const response=await api("/exchange/image/"+item.id),blob=await response.blob();
  if(blob.type==="image/png")return blob;
  const bitmap=await createImageBitmap(blob);
  try{const canvas=document.createElement("canvas");canvas.width=bitmap.width;canvas.height=bitmap.height;
    canvas.getContext("2d").drawImage(bitmap,0,0);
    return await new Promise((resolve,reject)=>canvas.toBlob(b=>b?resolve(b):reject(new Error("图片转换失败")),"image/png"));
  }finally{bitmap.close();}
}
function copyImage(item,button){
  if(!navigator.clipboard?.write || !window.ClipboardItem){showOriginal(item);return;}
  button.disabled=true;button.textContent="正在复制…";
  const data=pngBlob(item); data.catch(()=>{});
  let operation;
  try{operation=navigator.clipboard.write([new ClipboardItem({"image/png":data})]);}
  catch(_){button.disabled=false;button.textContent="复制图片";showOriginal(item);return;}
  operation.then(()=>{button.textContent="图片已复制";}).catch(()=>{button.textContent="长按原图复制";showOriginal(item);}).finally(()=>{button.disabled=false;});
}
async function poll(){
  clearTimeout(timer);
  if(!paired || !token || document.hidden || polling)return;
  polling=true;
  try{const response=await api("/exchange/records?after="+Math.max(0,revision)),data=await response.json();
    if(data.changed || revision<0){revision=data.revision;render(data.items||[]);}
  }catch(error){el("receive-status").textContent=error.name==="AbortError"?"连接超时，将自动重试。":error.message;}
  finally{polling=false;if(paired&&!document.hidden)timer=setTimeout(poll,2500);}
}
document.addEventListener("visibilitychange",()=>{clearTimeout(timer);if(!document.hidden)poll();});
window.addEventListener("pagehide",()=>clearTimeout(timer));

connect();
})();'''

# HOME_ASSETS_START
from base64 import b64decode as _icon_decode
MANIFEST = '{"name":"简捷互传","short_name":"简捷互传","start_url":"/android","scope":"/android","display":"browser","theme_color":"#1d4ed8","background_color":"#f7f8f8","icons":[{"src":"/android/icon-192.png","sizes":"192x192","type":"image/png","purpose":"any"},{"src":"/android/icon-512.png","sizes":"512x512","type":"image/png","purpose":"any"}]}'.encode("utf-8")
ICON_192 = _icon_decode(b'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')
ICON_512 = _icon_decode(b'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PIQbi1IlfiWZ05U0AEOjvv9F1SV+n+ETQAb5lWROIemXJj3bixpIvJsl3rGpHgEACEoAAIISAICgBAAgKAEACEoAAIISAICgBAAgqCSPnYLqaSrkXntC1xtfNWvFi9v7D2tpqvO3KNOl3I7dU+t+PvHP9+77/gOj076sTZYIABly/Es6PvOB/qULk9wNO5uaCrn5c1rmz2l540kzNz4xedkXdzz4aJLbKUM11Pn7KxrIylNnfetTCxtp9X+GpQtbv/WphStPnZX2IPArAkAmnHZ85+cuntvc1OC3i2luyn/u4rmnHe8BuWSCAJC+7q6mz148N8i9wgr53Gcvntvd1ZT2ICAAZMC7zu7t6w60IPZ1N73r7N60pwABIG35fO7807vTnqLWzj+9Ox/jEw9ZJgCkbMnc1v6+cFej9fc1L5nbsKe7qRcCQMqWzA+6Dob9wckOASBlbS1BD4WE/cHJDgEgZUOjQZ9lH/YHJzsEgJRt2HIg7RHSEfYHJzsEgJQNDk+v2xDu7gjrNowPDvsEQMoEgPT9w3eG0h6h1gL+yGSQAJC+b9+774GHA30IeODh8W/fuy/tKUAAyIBSOffBq7cPDBbTHqQWBgaLH7x6e6mc9hwgAGTEwGDxvI9u3fjEZNqDVNfGJybP++jWIKkj+wSArNi6Y+oNH97yhZsGR8cb8LEpo+OlL9w0+IYPb9m6YyrtWeBXwn0FnywbP1D67A27V92y55QVnSuWtR8xu7mlub6/LTVVLO/cU1y3YeKH68b2TzRg2KhrAkDm7J8orVk7smbtSNqDQINzCAggKAEACEoAAIISAICgBAAgKAEACEoAAIISAICgBAAgKAEACEoAAIISAICgBAAgKAEACEoAAIISAICgBAAgKAEACEoAAIISAICgBAAgKAEACEoAAIISAICgBAAgKAEACEoAAIISAICgBAAgKAEACEoAAIISAICgBAAgKAEACEoAAIISAICgBAAgKAEACEoAAIISAICgBAAgKAEACEoAAIISAICgBAAgKAEACEoAAIISAICgBAAgKAEACEoAAIISAICgBAAgKAEACEoAAIISAICgBAAgKAEACEoAAIISAICgBAAgKAEACEoAAIISAICgBAAgKAEACEoAAIISAICgBAAgKAEACEoAAIISAICgBAAgqOa0BwBCeOsZ3W945cyJydLmgan7frr/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')
# HOME_ASSETS_END

ASSETS = {
    "/android": (HTML.encode("utf-8"), "text/html; charset=utf-8"),
    "/android/": (HTML.encode("utf-8"), "text/html; charset=utf-8"),
    "/android/style.css": (CSS.encode("utf-8"), "text/css; charset=utf-8"),
    "/android/app.js": (JS.encode("utf-8"), "application/javascript; charset=utf-8"),
    "/android/manifest.webmanifest": (MANIFEST, "application/manifest+json; charset=utf-8"),
    "/android/icon-192.png": (ICON_192, "image/png"),
    "/android/icon-512.png": (ICON_512, "image/png"),
}

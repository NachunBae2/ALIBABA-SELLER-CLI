// Aside repl script for the OneTalk web inbox (onetalk.alibaba.com/message/weblitePWA.htm), for the
// commands with no API path yet: inspect, draft, ui probe.
// Sends, templates, conversations, read state and tags go through the HTTP API instead (onetalk_cli.mjs).
// Sent on stdin by lib/onetalk.mjs (never written to disk). __PARAMS__ =
//   {action, mode: "dry-run"|"open"|"send", ids: [inquiry ids], match: {aliIds, cids, times, country} | null,
//    draftButton, shot}
// The conversation row is found by matchRow(): sender ali id = row data-ali-id, else an inquiry id inside
// data-cid, else any id in any row attribute, else data-modify-time near the inquiry's last reply time
// (+ country when the row shows one). Two or more candidates at one level = refused. If the target is
// not on screen the list is scrolled (wheel only, no conversation opened). Ids and attribute values
// stay in this script's RAM: result.found carries only the method name and counts.
// Every interaction goes through act(): "hover" always runs; "open" (close the warning popup,
// open a conversation, open a panel or menu) runs in open/send mode. Anything not run is
// listed in result.plan. The result carries element positions, labels and counts only: no buyer
// name, conversation text or typed text.
const P = __PARAMS__;
const URL_ONETALK = "https://onetalk.alibaba.com/message/weblitePWA.htm";
const result = { action: P.action, mode: P.mode, status: "ok", plan: [], done: [], found: {} };
const RUNS = { hover: ["dry-run", "open", "send"], scroll: ["dry-run", "open", "send"], open: ["open", "send"] };
const MAX_SCROLLS = 40;
const NEAR_MS = 120000;

async function act(kind, what, fn) {
  if (!RUNS[kind].includes(P.mode)) { result.plan.push({ kind, what }); return false; }
  await fn();
  result.done.push({ kind, what });
  return true;
}
const click = (kind, what, at) => act(kind, what, () => page.mouse.click(at.x, at.y));

// Icon roles by tooltip / aria / title / class / svg name. Positions are never hard-coded.
const ROLES = {
  card: /exchange name ?card|business ?card|name ?card|名片/i,
  file: /\bfile\b|attach|upload|document|文件|附件/i,
  product: /\bproduct|商品|产品/i,
  template: /tmpl|template|quick ?repl|phrase|模板|快捷/i,
  more: /\bmore\b|更多|ellipsis/i,
  image: /image|photo|picture|图片/i,
  emoji: /emoji|表情/i,
};

const readUi = (arg) => page.evaluate(function readUi(a) {
  const vis = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== "hidden"; };
  const at = (el) => { const r = el.getBoundingClientRect(); return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) }; };
  const short = (s) => (s || "").replace(/\s+/g, " ").trim().slice(0, 40);
  const out = { popup: null, rows: [], rowAttrNames: [], input: null, send: null, toolbar: [], aiButtons: [], tooltips: [], menuItems: [], dialogButtons: [] };
  const warn = [...document.querySelectorAll("body *")].find((el) => vis(el) && el.children.length < 4 && /avoid off-platform communication/i.test(el.textContent || ""));
  if (warn) {
    let box = warn;
    while (box.parentElement && box.parentElement !== document.body && box.getBoundingClientRect().width < 280) box = box.parentElement;
    const r = box.getBoundingClientRect();
    const x = [...box.querySelectorAll("[class*=close], [aria-label*=lose], button, i, svg")].filter(vis)
      .map((el) => ({ el, r: el.getBoundingClientRect() })).filter((o) => o.r.top < r.top + 60 && o.r.left > r.right - 80)
      .sort((p, q) => q.r.left - p.r.left || p.r.top - q.r.top)[0];
    out.popup = { closeX: x ? at(x.el) : { x: Math.round(r.right - 16), y: Math.round(r.top + 16) }, byElement: !!x };
  }
  const rowEls = [...document.querySelectorAll("[class*=session-item], [class*=conversation-item], [class*=contact-item], [class*=session] li, [role=listitem]")].filter(vis);
  const names = new Set();
  for (const el of rowEls) {
    const attrs = [el, ...el.querySelectorAll("*")].flatMap((n) => [...n.attributes]);
    attrs.forEach((x) => names.add(x.name));
    const vals = (re) => attrs.filter((x) => re.test(x.name)).map((x) => x.value).filter(Boolean);
    out.rows.push({ ...at(el), active: /active|selected|current/i.test(el.className),
      attrs: { ali: vals(/^data-ali-id$/), cid: vals(/^data-cid$/), mod: vals(/^data-modify-time$/), name: vals(/^data-name$/), hint: vals(/^(alt|title|aria-label)$/), all: attrs.map((x) => x.value) } });
  }
  out.rowAttrNames = [...names].sort();
  const input = [...document.querySelectorAll("textarea, [contenteditable=true], input[type=text]")].filter(vis)
    .find((el) => /smart assistant|enter/i.test(el.getAttribute("placeholder") || el.getAttribute("data-placeholder") || el.textContent || "")) ||
    [...document.querySelectorAll("textarea, [contenteditable=true]")].filter(vis).pop();
  if (input) out.input = { ...at(input), placeholder: short(input.getAttribute("placeholder") || input.getAttribute("data-placeholder")), length: (input.value || input.textContent || "").length };
  const buttons = [...document.querySelectorAll("button, [role=button], a, span, div")].filter((el) => vis(el) && el.children.length <= 2);
  const send = buttons.find((el) => /^send$/i.test(short(el.textContent)));
  if (send) out.send = at(send);
  for (const el of buttons) {
    const t = short(el.textContent);
    if (/^(copilot|draft summary|content enhancer)$/i.test(t)) out.aiButtons.push({ ...at(el), text: t });
  }
  for (const el of [...document.querySelectorAll(".send-tool-icon, [class*=send-tool] [class*=icon], [class*=toolbar] [class*=icon]")].filter(vis)) {
    const use = el.querySelector("use");
    out.toolbar.push({ ...at(el), cls: short(String(el.className && el.className.baseVal || el.className)), label: short(el.getAttribute("aria-label")),
      title: short(el.getAttribute("title") || el.getAttribute("data-title") || el.getAttribute("data-tip")),
      svg: short(use && (use.getAttribute("xlink:href") || use.getAttribute("href"))) });
  }
  const pops = [...document.querySelectorAll("[role=tooltip], [class*=tooltip], [class*=balloon], [class*=popover], [class*=dropdown], [class*=overlay]")].filter(vis);
  out.tooltips = pops.map((el) => short(el.textContent)).filter(Boolean);
  // hover menus of the send-box icons: their leaf items ("Insert file", "Select Product", "Send business card" …)
  out.popItems = [];
  for (const box of pops) for (const el of box.querySelectorAll("*")) {
    const t = (el.textContent || "").replace(/\s+/g, " ").trim();
    if (!vis(el) || !t || t.length > 60 || [...el.children].some((c) => (c.textContent || "").trim() === t)) continue;
    if (!out.popItems.some((o) => o.text === t)) out.popItems.push({ ...at(el), text: t });
  }
  out.menuItems = [...document.querySelectorAll("[role=menuitem], [class*=menu-item], [class*=dropdown] li, [class*=popover] li")].filter(vis).map((el) => ({ ...at(el), text: short(el.textContent) }));
  out.dialogButtons = [...document.querySelectorAll("[role=dialog] button, [class*=dialog] button, [class*=modal] button")].filter(vis).map((el) => ({ ...at(el), text: short(el.textContent) }));
  return out;
}, arg);

// -> {row, method} | {method: null, candidates}. Pure: values in, one row or nothing out.
function matchRow(rows, ids, m) {
  const has = (vals, keys) => vals.some((v) => keys.some((k) => k.length >= 5 && (v === k || v.includes(k))));
  const ms = (v) => { const n = Number(v); return n > 0 && n < 1e12 ? n * 1000 : n; };
  const country = ((m && m.country) || []).map((c) => c.toLowerCase());
  const nm = (v) => String(v || "").replace(/\s+/g, " ").trim().toLowerCase();
  const names = ((m && m.names) || []).map(nm).filter((v) => v.length >= 2);
  const countryOk = (r) => !country.length || !r.attrs.hint.some((h) => /^[A-Za-z]{2}$/.test(h)) || r.attrs.hint.some((h) => country.includes(h.toLowerCase()));
  const levels = [
    ["ali-id", (r) => m && has(r.attrs.ali, m.aliIds || [])],
    ["cid", (r) => m && has(r.attrs.cid, m.cids || [])],
    ["any-attribute-id", (r) => has(r.attrs.all, ids)],
    ["name" + (country.length ? "+country" : ""), (r) => names.length > 0 && (r.attrs.name || []).some((v) => names.includes(nm(v))) && countryOk(r)],
    ["modify-time" + (country.length ? "+country" : ""), (r) => m && (m.times || []).length > 0 &&
      r.attrs.mod.some((v) => m.times.some((t) => Math.abs(ms(v) - ms(t)) <= NEAR_MS)) && countryOk(r)],
  ];
  for (const [method, test] of levels) {
    const hit = rows.filter(test);
    if (hit.length === 1) return { row: hit[0], method };
    if (hit.length > 1) return { method: null, ambiguous: method, candidates: hit.length };
  }
  return { method: null, candidates: 0 };
}

async function locateRow(ui) {
  let got = matchRow(ui.rows, P.ids || [], P.match);
  let scrolls = 0, stale = 0;
  // The list loads more rows lazily: wheel on the last row + push the list box's scrollTop, and only stop
  // after 3 scrolls in a row that load nothing new.
  while (!got.row && !got.ambiguous && ui.rows.length && scrolls < MAX_SCROLLS && stale < 3) {
    const last = ui.rows[ui.rows.length - 1];
    await act("scroll", "scroll conversation list", async () => {
      await page.mouse.move(last.x, last.y); await page.mouse.wheel(0, 1200);
      await page.evaluate(function scrollList() {
        // start from a row element (the last row's center can be below the viewport)
        let el = document.querySelector("[data-cid], [class*=contact-item], [class*=session-item], [class*=conversation-item]");
        while (el && el !== document.body && !(el.scrollHeight > el.clientHeight + 4 && /(auto|scroll)/.test(getComputedStyle(el).overflowY))) el = el.parentElement;
        if (el && el !== document.body) el.scrollTop = el.scrollHeight;
      });
    });
    scrolls += 1;
    await sleep(1500 + stale * 1500);
    const next = await readUi({});
    const grew = next.rows.length !== ui.rows.length || next.rows.some((r, i) => (r.attrs.all.join() !== (ui.rows[i] && ui.rows[i].attrs.all.join())));
    ui = next;
    got = matchRow(ui.rows, P.ids || [], P.match);
    stale = grew ? 0 : stale + 1;
  }
  return { ui, got, scrolls };
}

const roleOf = (icon) => {
  const hay = [icon.label, icon.title, icon.tooltip, icon.cls, icon.svg, ...(icon.menu || []).map((m) => m.text)].filter(Boolean).join(" ");
  return Object.keys(ROLES).find((k) => ROLES[k].test(hay)) || null;
};
const pick = (items, re) => items.find((x) => re.test(x.text || ""));

// Chat pane = the largest scrollable box above the message input. -> {n, tail} | null (values stay here).
const readChat = (probe = false) => page.evaluate(function readChat(probe) {
  const vis = (el) => el.getBoundingClientRect().width > 0 && el.getBoundingClientRect().height > 0;
  const input = document.querySelector("textarea.send-textarea") || [...document.querySelectorAll("textarea, [contenteditable=true]")].filter(vis).pop();
  if (!input) return null;
  const ir = input.getBoundingClientRect();
  const mid = ir.left + ir.width / 2;
  // The message pane: the biggest scroll box (or message-list box, even when too short to scroll) above the input.
  const cands = [...document.querySelectorAll("div, ul, section")].filter((el) => {
    const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    if (r.width < 200 || r.height < 120 || r.left > mid || r.right < mid || r.bottom > ir.top + 40 || el.contains(input)) return false;
    return /(auto|scroll)/.test(cs.overflowY) || /message|msg|chat/i.test(String(el.className || ""));
  });
  const area = (el) => { const r = el.getBoundingClientRect(); return r.width * r.height; };
  // the open conversation's pane; earlier conversations stay mounted as hidden boxes the size rule can pick
  const hit = document.elementFromPoint(mid, Math.max(0, ir.top - 120));
  const flow = hit && hit.closest(".message-flow-container");
  const box = flow || cands.sort((a, b) => (b.scrollHeight > b.clientHeight + 4) - (a.scrollHeight > a.clientHeight + 4) || area(b) - area(a))[0];
  if (!box) return probe ? { none: true, cands: cands.length, input: String(input.className || "").slice(0, 40) } : null;
  box.scrollTop = box.scrollHeight;
  const text = (box.innerText || "").replace(/\s+/g, " ");
  const reversed = [box, ...box.querySelectorAll("*")].slice(0, 40).some((el) => /reverse/.test(getComputedStyle(el).flexDirection));
  const media = [...box.querySelectorAll("img, a[href], [class*=card], [class*=file], [class*=product]")].length;
  return { n: box.querySelectorAll("*").length, media, tail: reversed ? text.slice(0, 1500) : text.slice(-1500), reversed, cls: String(box.className || "").slice(0, 60) };
}, probe);
// Product links on screen outside the chat pane (the customer panel's visited/inquired products).
const readProducts = () => page.evaluate(function readProducts() {
  const out = [];
  for (const a of document.querySelectorAll("a[href]")) {
    const m = /(?:product-detail\/[^"?#]*?_|\/product\/|productId=)(\d{8,})/.exec(a.href);
    const r = a.getBoundingClientRect();
    if (!m || r.width === 0) continue;
    const title = (a.innerText || a.getAttribute("title") || "").replace(/\s+/g, " ").trim().slice(0, 80);
    if (!out.some((o) => o.id === m[1])) out.push({ id: m[1], title, x: Math.round(r.left) });
  }
  return out;
});
const mask = (t) => String(t || "").replace(/[\w.+-]+@[\w-]+\.[\w.]+/g, "[email]").replace(/\+?\d[\d\s().-]{7,}\d/g, "[number]");

async function probeToolbar(ui) {
  for (const icon of ui.toolbar) {
    if (await act("hover", "hover toolbar icon", () => page.mouse.move(icon.x, icon.y))) {
      await sleep(400);
      const seen = await readUi({});
      icon.tooltip = seen.tooltips[0] || "";
      icon.menu = seen.popItems;
    }
    icon.role = roleOf(icon);
  }
  return ui.toolbar;
}

const page = await openTab(URL_ONETALK);
try {
  await sleep(4000);
  let ui = await readUi({});
  result.found.popup = !!ui.popup;
  if (ui.popup) {
    if (await click("open", "close off-platform warning popup", ui.popup.closeX)) { await sleep(600); ui = await readUi({}); }
  }
  result.found.rows = ui.rows.length;
  if ((P.ids && P.ids.length) || P.match) {
    const loc = await locateRow(ui);
    ui = loc.ui;
    result.found.scrolls = loc.scrolls;
    result.found.conversation = !!loc.got.row;
    result.found.match = loc.got.method;
    result.found.matchKeys = P.match ? { aliIds: (P.match.aliIds || []).length, cids: (P.match.cids || []).length, times: (P.match.times || []).length, country: (P.match.country || []).length > 0, names: (P.match.names || []).length } : null;
    if (loc.got.ambiguous) { result.status = "conversation_ambiguous"; result.found.ambiguous = loc.got.ambiguous; result.found.candidates = loc.got.candidates; }
    else if (!loc.got.row) {
      result.status = "conversation_not_located";
      result.found.rowAttrNames = ui.rowAttrNames;
      // value shapes only (digits -> 9, letters -> a): shows whether row ids and lookup keys are the same kind of id
      const shape = (v) => String(v).replace(/\d/g, "9").replace(/[a-z]/gi, "a").slice(0, 40);
      const uniq = (xs) => [...new Set(xs.map(shape))].slice(0, 6);
      result.found.shapes = { rowAli: uniq(ui.rows.flatMap((r) => r.attrs.ali)), rowCid: uniq(ui.rows.flatMap((r) => r.attrs.cid)),
        keyAli: uniq((P.match && P.match.aliIds) || []), keyCid: uniq((P.match && P.match.cids) || []), ids: uniq(P.ids || []) };
    }
    else if (await act("open", "open conversation", async () => {
      // the matched row can sit below the visible part of the list: scroll it into view, then click its new center
      const r = loc.got.row;
      const key = r.attrs.cid[0] ? ["data-cid", r.attrs.cid[0]] : r.attrs.ali[0] ? ["data-ali-id", r.attrs.ali[0]] : null;
      const at = (key && await page.evaluate(function revealRow(k) {
        const el = [...document.querySelectorAll("[" + k[0] + "]")].find((e) => e.getAttribute(k[0]) === k[1]);
        if (!el) return null;
        el.scrollIntoView({ block: "center" });
        const b = el.getBoundingClientRect();
        return { x: Math.round(b.left + b.width / 2), y: Math.round(b.top + b.height / 2) };
      }, key)) || r;
      await sleep(400);
      await page.mouse.click(at.x, at.y);
    })) {
      for (let i = 0; i < 5; i++) { await sleep(2000); ui = await readUi({}); if (ui.input) break; }
      if (!ui.input) result.found.editors = await page.evaluate(function listEditors() {
        return { iframes: document.querySelectorAll("iframe").length,
          editors: [...document.querySelectorAll("textarea, [contenteditable], input, [role=textbox]")].map((e) => {
            const r = e.getBoundingClientRect();
            return { tag: e.tagName, ce: e.getAttribute("contenteditable"), type: e.getAttribute("type"), role: e.getAttribute("role"), w: Math.round(r.width), h: Math.round(r.height), cls: String(e.className || "").slice(0, 50), ph: (e.getAttribute("placeholder") || e.getAttribute("data-placeholder") || "").slice(0, 40) };
          }).slice(0, 15),
          // UI labels around the send box (e.g. an AI-reception take-over button): button texts only
          sendBox: [...document.querySelectorAll(".message-box button, .message-box [role=button], [class*=send-box] button, [class*=send-box] span, [class*=reception] button, [class*=takeover] *, [class*=take-over] *")]
            .filter((e) => e.getBoundingClientRect().width > 0 && e.children.length <= 2).map((e) => (e.innerText || "").trim().slice(0, 40)).filter(Boolean).slice(0, 25) };
      });
      // --shot f.png: picture of the lower right (send box) area only, to see what replaced the input
      if (!ui.input && P.shot) await page.screenshot({ path: "./artifacts/onetalk-sendbox.png", clip: { x: 440, y: 560, width: 1000, height: 340 } }).catch(() => {});
    }
  }
  result.found.input = !!ui.input;
  result.found.send = !!ui.send;
  result.found.aiButtons = ui.aiButtons.map((b) => b.text);
  if (result.status === "ok") {
    const a = P.action;
    if (a === "ui-probe") {
      result.found.toolbar = (await probeToolbar(ui)).map(({ x, y, cls, label, title, svg, tooltip, role, menu }) => ({ x, y, cls, label, title, svg, tooltip, role, menu: (menu || []).map((m) => m.text) }));
      const more = ui.toolbar.find((i) => i.role === "more");
      if (more && (await click("open", "open more menu", more))) {
        await sleep(500);
        result.found.moreMenu = (await readUi({})).menuItems.map((m) => m.text);
        await act("open", "close more menu", () => page.keyboard.press("Escape"));
      }
      result.found.rowAttrNames = ui.rowAttrNames;
      // file inputs and the send-box icon each sits in (a local file is sent through one of them)
      result.found.fileInputs = await page.evaluate(function fileInputs() {
        return [...document.querySelectorAll("input[type=file]")].map((el) => {
          const icon = el.closest(".send-tool-icon, [class*=icon], label, button") || el.parentElement;
          const r = icon.getBoundingClientRect();
          return { accept: el.getAttribute("accept") || "", multiple: el.multiple, iconCls: String(icon.className || "").slice(0, 40), x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) };
        });
      });
      // icons with no hover menu: click once (open mode, nothing is sent), read what opens, close it
      for (const icon of ui.toolbar.filter((i) => i.cls === "send-tool-icon" && !(i.menu || []).length && !ui.aiButtons.some((b) => Math.abs(b.y - i.y) < 15))) {
        const hasFile = result.found.fileInputs.some((f) => Math.abs(f.x - icon.x) < 12 && Math.abs(f.y - icon.y) < 12);
        if (hasFile) { icon.clicked = "file input (not clicked)"; continue; }
        const leafTexts = () => page.evaluate(function leafTexts() {
          return [...document.querySelectorAll("body *")].filter((el) => !el.children.length && el.getBoundingClientRect().width > 0)
            .map((el) => (el.textContent || el.getAttribute("title") || el.getAttribute("placeholder") || "").trim()).filter((t) => t && t.length < 50);
        });
        const before = new Set(await leafTexts());
        if (await click("open", "click toolbar icon", icon)) {
          await sleep(1200);
          const seen = await readUi({});
          const appeared = [...new Set((await leafTexts()).filter((t) => !before.has(t)))].slice(0, 25);
          icon.clicked = { appeared, popItems: seen.popItems.map((m) => m.text).slice(0, 15), dialogButtons: seen.dialogButtons.map((b) => b.text).slice(0, 10) };
          await act("open", "close panel", () => page.keyboard.press("Escape"));
          await sleep(400);
        }
      }
      result.found.clicked = ui.toolbar.filter((i) => i.clicked).map((i) => ({ x: i.x, y: i.y, clicked: i.clicked }));
      // inner class names of each send-box icon (icon font names say what the icon is) and card-like controls on the page
      result.found.iconInner = await page.evaluate(function iconInner() {
        return [...document.querySelectorAll(".send-tool-icon")].map((el) => {
          const r = el.getBoundingClientRect();
          const cls = [...el.querySelectorAll("*")].map((c) => String(c.className && c.className.baseVal != null ? c.className.baseVal : c.className || "")).filter(Boolean);
          return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2), inner: [...new Set(cls)].join(" ").slice(0, 160) };
        });
      });
      // --shot: picture of the send box only (icons + input, no chat text) under Aside's ./artifacts
      if (P.shot && ui.input) await page.screenshot({ path: "./artifacts/onetalk-toolbar.png", clip: { x: 440, y: Math.max(0, ui.input.y - 110), width: 1000, height: 150 } }).catch(() => {});
      // the product picker: hover the product icon, click "Select Product" (opens a picker, sends nothing), read it, close it
      const prod = ui.toolbar.find((i) => i.role === "product" && (i.menu || []).some((m) => /^select product$/i.test(m.text)));
      if (prod && (await act("hover", "hover product icon", () => page.mouse.move(prod.x, prod.y)))) {
        await sleep(500);
        const sel = (await readUi({})).popItems.find((m) => /^select product$/i.test(m.text));
        if (sel && (await click("open", "open product picker", sel))) {
          await sleep(3500);
          result.found.productPicker = await page.evaluate(function picker() {
            const vis = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
            const area = (el) => { const r = el.getBoundingClientRect(); return r.width * r.height; };
            const box = [...document.querySelectorAll("[role=dialog], [class*=dialog], [class*=modal], [class*=drawer]")].filter(vis).sort((a, b) => area(b) - area(a))[0];
            if (!box) return null;
            const r = box.getBoundingClientRect();
            return { cls: String(box.className || "").slice(0, 60), w: Math.round(r.width), h: Math.round(r.height),
              inputs: [...box.querySelectorAll("input, textarea")].filter(vis).map((e) => ({ type: e.type, ph: (e.placeholder || "").slice(0, 50) })),
              buttons: [...box.querySelectorAll("button, [role=button]")].filter(vis).map((e) => (e.innerText || "").trim().slice(0, 30)).filter(Boolean).slice(0, 15),
              checkboxes: box.querySelectorAll("input[type=checkbox], [class*=checkbox]").length,
              items: [...box.querySelectorAll("li, tr, [class*=item]")].filter(vis).length,
              idsShown: /\d{11,}/.test(box.innerText || ""),
              iframes: [...box.querySelectorAll("iframe")].map((f) => { try { return new URL(f.src).host + new URL(f.src).pathname; } catch (e) { return "?"; } }),
              texts: [...box.querySelectorAll("*")].filter((el) => !el.children.length && vis(el)).map((el) => (el.textContent || el.getAttribute("placeholder") || "").trim()).filter((t) => t && t.length < 40 && !/\d{6,}/.test(t)).slice(0, 30) };
          });
          await act("open", "close product picker", () => page.keyboard.press("Escape"));
          await sleep(500);
        }
      }
      // icons in the conversation header (right of the contact list, above the messages): hover each, read its tooltip
      const headerIcons = await page.evaluate(function headerIcons(inputY) {
        const out = [];
        for (const el of document.querySelectorAll("i, svg, [class*=icon]")) {
          const r = el.getBoundingClientRect();
          if (!r.width || r.width > 40 || r.top > 160 || r.left < 430) continue;
          const x = Math.round(r.left + r.width / 2), y = Math.round(r.top + r.height / 2);
          if (!out.some((o) => Math.abs(o.x - x) < 6 && Math.abs(o.y - y) < 6)) out.push({ x, y, cls: String(el.className && el.className.baseVal != null ? el.className.baseVal : el.className || "").slice(0, 50) });
        }
        return out.slice(0, 14);
      }, ui.input ? ui.input.y : 0);
      for (const h of headerIcons) {
        if (await act("hover", "hover header icon", () => page.mouse.move(h.x, h.y))) { await sleep(400); const seen = await readUi({}); h.tip = seen.tooltips[0] || ""; h.menu = seen.popItems.map((m) => m.text).slice(0, 8); }
      }
      result.found.headerIcons = headerIcons;
      result.found.cardLike = await page.evaluate(function cardLike() {
        return [...document.querySelectorAll("body *")].filter((el) => {
          const r = el.getBoundingClientRect();
          if (!r.width || el.children.length > 3) return false;
          const hay = String(el.className && el.className.baseVal != null ? el.className.baseVal : el.className || "") + " " + (el.getAttribute("title") || "") + " " + (el.getAttribute("aria-label") || "") + " " + (el.children.length ? "" : el.textContent || "");
          return /card|名片|contact info|vcard/i.test(hay);
        }).slice(0, 10).map((el) => { const r = el.getBoundingClientRect(); return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2), tag: el.tagName, cls: String(el.className && el.className.baseVal != null ? el.className.baseVal : el.className || "").slice(0, 60), text: (el.textContent || "").trim().slice(0, 40) }; });
      });
    } else if (a === "inspect") {
      const chat = await readChat(true);
      if (chat && chat.none) result.found.chatProbe = chat;
      result.found.chat = chat && !chat.none ? { cls: chat.cls, elements: chat.n, media: chat.media } : null;
      result.found.products = (await readProducts()) || [];
      result.tail = chat && !chat.none ? mask(chat.reversed ? chat.tail.slice(0, 700) : chat.tail.slice(-700)) : null;  // newest end
      // --shot: picture of the message pane only (no header with the buyer's name, no contact list)
      if (P.shot && chat && !chat.none) {
        const box = await page.evaluate(function paneRect() {
          const el = document.querySelector(".message-flow-container");
          if (!el) return null;
          const r = el.getBoundingClientRect();
          return { x: Math.max(0, Math.round(r.left)), y: Math.max(0, Math.round(r.top)), width: Math.round(r.width), height: Math.round(r.height) };
        });
        if (box && box.width && box.height) {
          // bytes come back to the CLI (base64 on the RESULT line), which writes the file where the user runs it
          // everything but the message pane is hidden while the picture is taken (contact list and header carry buyer names)
          await page.evaluate(function hideAllButPane() {
            const st = document.createElement("style");
            st.id = "alibaba-seller-shot";
            st.textContent = "body * { visibility: hidden !important; } .message-flow-container, .message-flow-container * { visibility: visible !important; }";
            document.head.appendChild(st);
          });
          await sleep(300);
          const shot = await page.screenshot().catch((e) => ({ error: String(e && e.message || e).slice(0, 120) }));
          await page.evaluate(function unhide() { const st = document.getElementById("alibaba-seller-shot"); if (st) st.remove(); });
          if (shot && shot.error) result.found.shot = shot;
          else if (shot && typeof shot === "object" && shot.length) result.shotPng = (typeof Buffer !== "undefined" ? Buffer.from(shot) : shot).toString("base64");
          else result.found.shot = { error: "no bytes (" + typeof shot + ")" };
        }
      }
    } else if (a === "draft") {
      const b = pick(ui.aiButtons, new RegExp("^" + (P.draftButton || "copilot") + "$", "i"));
      result.found.draftButton = !!b;
      if (b && (await click("open", "generate AI draft (" + b.text + ")", b))) {
        // Copilot answers in the Smart Assistant side panel; other buttons may fill the send box
        const readDraft = () => page.evaluate(function readDraft() {
          const vis = (el) => el.getBoundingClientRect().width > 0;
          const box = document.querySelector("textarea.send-textarea") || [...document.querySelectorAll("textarea, [contenteditable=true]")].filter(vis).pop();
          const panel = [...document.querySelectorAll("[class*=smart-assistant]")].filter(vis)[0];
          return { box: box ? (box.value || box.innerText || "") : "", panel: panel ? (panel.innerText || "").replace(/\s+/g, " ").trim() : "" };
        });
        let prev = null, last = { box: "", panel: "" };
        for (let i = 0; i < 15; i++) {
          await sleep(2000);
          last = await readDraft();
          if (last.box.trim()) break;
          if (prev !== null && last.panel && last.panel === prev) break;  // panel text stopped changing
          prev = last.panel;
        }
        result.found.draftFrom = last.box.trim() ? "send box" : last.panel ? "smart assistant panel" : null;
        result.draft = mask((last.box.trim() ? last.box : last.panel).slice(0, 1500));
        if (!result.draft.trim()) result.status = "draft_empty";
        // the panel opened but the platform had nothing to suggest for this conversation
        else if (!last.box.trim()) result.found.aiSuggestion = !/No smart actions yet|More data is needed/i.test(last.panel);
      }
    } else result.status = "unknown_action";
  }
} catch (e) {
  result.status = "error";
  result.error = String(e && e.message || e).slice(0, 200);
} finally {
  await closeTab(page).catch(() => {});
}
console.log("RESULT " + JSON.stringify(result));

// Aside repl script for the OneTalk web inbox (onetalk.alibaba.com/message/weblitePWA.htm).
// Sent on stdin by lib/onetalk.mjs (never written to disk). __PARAMS__ =
//   {action, mode: "dry-run"|"open"|"send", ids: [inquiry ids], match: {aliIds, cids, times, country} | null,
//    template, tag, draftButton}
// The conversation row is found by matchRow(): sender ali id = row data-ali-id, else an inquiry id inside
// data-cid, else any id in any row attribute, else data-modify-time near the inquiry's last reply time
// (+ country when the row shows one). Two or more candidates at one level = refused. If the target is
// not on screen the list is scrolled (wheel only, no conversation opened). Ids and attribute values
// stay in this script's RAM: result.found carries only the method name and counts.
// Every interaction goes through act(): "hover" always runs; "open" (close the warning popup,
// open a conversation, open a panel or menu) runs in open/send mode; "write" (choosing a
// template/tag, pressing Send) runs only in send mode. Anything not run is
// listed in result.plan. The result carries element positions, labels and counts only: no buyer
// name, conversation text or typed text.
const P = __PARAMS__;
const URL_ONETALK = "https://onetalk.alibaba.com/message/weblitePWA.htm";
const result = { action: P.action, mode: P.mode, status: "ok", plan: [], done: [], found: {} };
const RUNS = { hover: ["dry-run", "open", "send"], scroll: ["dry-run", "open", "send"], open: ["open", "send"], write: ["send"] };
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
  template: /tmpl|template|quick ?repl|phrase|模板|快捷/i,
  more: /\bmore\b|更多|ellipsis/i,
  image: /image|photo|picture|图片/i,
  emoji: /emoji|表情/i,
};

const readUi = (arg) => page.evaluate(function readUi(a) {
  const vis = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== "hidden"; };
  const at = (el) => { const r = el.getBoundingClientRect(); return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) }; };
  const short = (s) => (s || "").replace(/\s+/g, " ").trim().slice(0, 40);
  const out = { popup: null, rows: [], rowAttrNames: [], input: null, send: null, toolbar: [], aiButtons: [], tooltips: [], menuItems: [], tags: [], dialogButtons: [] };
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
      attrs: { ali: vals(/^data-ali-id$/), cid: vals(/^data-cid$/), mod: vals(/^data-modify-time$/), hint: vals(/^(alt|title|aria-label)$/), all: attrs.map((x) => x.value) } });
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
  out.tooltips = [...document.querySelectorAll("[role=tooltip], [class*=tooltip], [class*=balloon], [class*=popover]")].filter(vis).map((el) => short(el.textContent)).filter(Boolean);
  out.menuItems = [...document.querySelectorAll("[role=menuitem], [class*=menu-item], [class*=dropdown] li, [class*=popover] li")].filter(vis).map((el) => ({ ...at(el), text: short(el.textContent) }));
  out.dialogButtons = [...document.querySelectorAll("[role=dialog] button, [class*=dialog] button, [class*=modal] button")].filter(vis).map((el) => ({ ...at(el), text: short(el.textContent) }));
  out.tags = [...document.querySelectorAll("[class*=tag-item], [class*=label-item], [class*=tag-list] li")].filter(vis)
    .map((el) => ({ text: short(el.textContent), color: getComputedStyle(el).backgroundColor, ...at(el) }));
  return out;
}, arg);

// -> {row, method} | {method: null, candidates}. Pure: values in, one row or nothing out.
function matchRow(rows, ids, m) {
  const has = (vals, keys) => vals.some((v) => keys.some((k) => k.length >= 5 && (v === k || v.includes(k))));
  const ms = (v) => { const n = Number(v); return n > 0 && n < 1e12 ? n * 1000 : n; };
  const country = ((m && m.country) || []).map((c) => c.toLowerCase());
  const levels = [
    ["ali-id", (r) => m && has(r.attrs.ali, m.aliIds || [])],
    ["cid", (r) => m && has(r.attrs.cid, m.cids || [])],
    ["any-attribute-id", (r) => has(r.attrs.all, ids)],
    ["modify-time" + (country.length ? "+country" : ""), (r) => m && (m.times || []).length > 0 &&
      r.attrs.mod.some((v) => m.times.some((t) => Math.abs(ms(v) - ms(t)) <= NEAR_MS)) &&
      (!country.length || !r.attrs.hint.some((h) => /^[A-Za-z]{2}$/.test(h)) || r.attrs.hint.some((h) => country.includes(h.toLowerCase())))],
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
  let scrolls = 0;
  while (!got.row && !got.ambiguous && ui.rows.length && scrolls < MAX_SCROLLS) {
    const last = ui.rows[ui.rows.length - 1];
    await act("scroll", "scroll conversation list", async () => { await page.mouse.move(last.x, last.y); await page.mouse.wheel(0, 1200); });
    scrolls += 1;
    await sleep(900);
    const next = await readUi({});
    const grew = next.rows.length !== ui.rows.length || next.rows.some((r, i) => (r.attrs.all.join() !== (ui.rows[i] && ui.rows[i].attrs.all.join())));
    ui = next;
    got = matchRow(ui.rows, P.ids || [], P.match);
    if (!grew) break;
  }
  return { ui, got, scrolls };
}

const roleOf = (icon) => {
  const hay = [icon.label, icon.title, icon.tooltip, icon.cls, icon.svg].filter(Boolean).join(" ");
  return Object.keys(ROLES).find((k) => ROLES[k].test(hay)) || null;
};
const pick = (items, re) => items.find((x) => re.test(x.text || ""));

async function probeToolbar(ui) {
  for (const icon of ui.toolbar) {
    if (await act("hover", "hover toolbar icon", () => page.mouse.move(icon.x, icon.y))) {
      await sleep(400);
      icon.tooltip = (await readUi({})).tooltips[0] || "";
    }
    icon.role = roleOf(icon);
  }
  return ui.toolbar;
}

async function findTool(ui, role) {
  const icons = await probeToolbar(ui);
  const direct = icons.find((i) => i.role === role);
  if (direct) return { via: "toolbar", at: direct };
  const more = icons.find((i) => i.role === "more");
  if (!more) return null;
  if (!(await click("open", "open more menu", more))) return { via: "more-menu (not opened in this mode)", at: null };
  await sleep(500);
  const item = (await readUi({})).menuItems.find((m) => ROLES[role].test(m.text));
  return item ? { via: "more-menu", at: item } : null;
}

async function confirmIfAsked(what) {
  await sleep(800);
  const ui = await readUi({});
  const b = pick(ui.dialogButtons, /^(send|confirm|ok|发送|确定)$/i);
  if (b) await click("write", what, b);
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
    result.found.matchKeys = P.match ? { aliIds: (P.match.aliIds || []).length, cids: (P.match.cids || []).length, times: (P.match.times || []).length, country: (P.match.country || []).length > 0 } : null;
    if (loc.got.ambiguous) { result.status = "conversation_ambiguous"; result.found.ambiguous = loc.got.ambiguous; result.found.candidates = loc.got.candidates; }
    else if (!loc.got.row) { result.status = "conversation_not_located"; result.found.rowAttrNames = ui.rowAttrNames; }
    else if (await click("open", "open conversation", loc.got.row)) { await sleep(2500); ui = await readUi({}); }
  }
  result.found.input = !!ui.input;
  result.found.send = !!ui.send;
  result.found.aiButtons = ui.aiButtons.map((b) => b.text);
  if (result.status === "ok") {
    const a = P.action;
    if (a === "ui-probe") {
      result.found.toolbar = (await probeToolbar(ui)).map(({ x, y, cls, label, title, svg, tooltip, role }) => ({ x, y, cls, label, title, svg, tooltip, role }));
      const more = ui.toolbar.find((i) => i.role === "more");
      if (more && (await click("open", "open more menu", more))) {
        await sleep(500);
        result.found.moreMenu = (await readUi({})).menuItems.map((m) => m.text);
        await act("open", "close more menu", () => page.keyboard.press("Escape"));
      }
      result.found.rowAttrNames = ui.rowAttrNames;
    } else if (a === "tags-list") {
      result.found.tags = ui.tags.map(({ text, color }) => ({ text, color }));
    } else if (a === "template-list") {
      const t = await findTool(ui, "template");
      result.found.template = t ? t.via : null;
      if (t && t.at && (await click("open", "open template panel", t.at))) { await sleep(800); result.found.templates = (await readUi({})).menuItems.map((m) => m.text); }
    } else if (a === "draft") {
      const b = pick(ui.aiButtons, new RegExp("^" + (P.draftButton || "copilot") + "$", "i"));
      result.found.draftButton = !!b;
      if (b && (await click("open", "generate AI draft (" + b.text + ")", b))) { await sleep(6000); result.draft = await page.evaluate(function readDraft() { const el = [...document.querySelectorAll("textarea, [contenteditable=true]")].pop(); return el ? (el.value || el.innerText || "") : ""; }); }
    } else if (a === "template-send") {
      const role = "template";
      const t = await findTool(ui, role);
      result.found.tool = t ? t.via : null;
      if (!t) result.status = role + "_tool_not_located";
      else if (t.at) {
        await click("open", "open template tool", t.at);
        await sleep(800);
        const item = (await readUi({})).menuItems.find((m) => m.text === P.template || m.text.startsWith(P.template));
        if (item) await click("write", "choose template", item);
        else if (P.mode === "send") result.status = "template_not_found";
        if (ui.send) await click("write", "press Send", ui.send);
        await confirmIfAsked("confirm " + role + " send");
      } else result.plan.push({ kind: "write", what: "send " + role + " via " + t.via });
    } else if (a === "tag-add" || a === "tag-remove") {
      const tagBtn = pick(ui.dialogButtons.concat(ui.menuItems), /^(tag|tags|label|标签)$/i);
      result.found.tagControl = !!tagBtn;
      if (tagBtn) {
        await click("open", "open tag menu", tagBtn);
        await sleep(600);
        const item = (await readUi({})).tags.find((t) => t.text === P.tag);
        if (item) await click("write", (a === "tag-add" ? "add" : "remove") + " tag", item);
      } else result.plan.push({ kind: "write", what: a + " (tag control not located on this screen)" });
    } else result.status = "unknown_action";
  }
} catch (e) {
  result.status = "error";
  result.error = String(e && e.message || e).slice(0, 200);
} finally {
  await closeTab(page).catch(() => {});
}
console.log("RESULT " + JSON.stringify(result));

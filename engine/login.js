// Aside / cdp_runner repl script, piped on stdin by `seller_central_raw.py --login` and
// `login --sign-in` (never saved to disk). __PARAMS__ = {email, pw, loginId}; pw comes straight
// from the keychain and is only typed into the password box. Prints a status line only.
// Handles both login forms: email + password on one page, or email + Continue, then password.
// A slider check after submitting gets one drag; a code (2-step) prompt is reported, not answered.
const P = __PARAMS__;
const page = await openTab("https://i.alibaba.com/spa-micro/www2/ads/report.htm");
await sleep(5000);
const state = () => page.evaluate((lid) => ({ onLogin: /login\./.test(location.host), matches: !lid || decodeURIComponent(document.cookie).includes(lid) }), P.loginId);
const checks = () => page.evaluate(() => {
  const text = ((document.body && document.body.innerText) || "").slice(0, 3000);
  const knob = document.querySelector("#nc_1_n1z, .nc_iconfont.btn_slide, .btn_slide");
  const track = document.querySelector("#nc_1__scale_text, .nc_scale, .scale_text");
  let drag = null;
  if (knob && track) {
    const a = knob.getBoundingClientRect(), b = track.getBoundingClientRect();
    drag = { x: a.x + a.width / 2, y: a.y + a.height / 2, to: b.x + b.width - 2 };
  }
  return { slider: !!knob || /slide to verify|please slide|滑动验证/i.test(text), drag,
    code: /verification code (?:has been )?sent|enter (?:the )?(?:verification )?code|verify your identity|2-step|two-step|인증 ?코드/i.test(text) };
});
const boxes = (tree) => [...tree.matchAll(/textbox[^\n]*\[ref=(e\d+)\]/g)].map((m) => m[1]);
const passwordBox = (tree) => (/textbox[^\n]*[Pp]assword[^\n]*\[ref=(e\d+)\]/.exec(tree) || [])[1];
const button = (tree) => (/button "(?:Sign in|Continue|Next|Log in)" \[ref=(e\d+)\]/.exec(tree) || [])[1];
const submit = async (tree) => { const b = button(tree); if (b) await page.locator(b).click(); else await page.keyboard.press("Enter"); };

let s = await state();
let status;
if (!s.onLogin) status = s.matches ? "already_signed_in" : "wrong_account";
else {
  let snap = await snapshot(page, { interactive: true });
  const first = boxes(snap.tree);
  let pw = passwordBox(snap.tree) || (first.length >= 2 ? first[1] : null);
  if (!first.length) status = "login_form_not_found";
  else {
    await page.locator(first[0]).fill(P.email);
    if (!pw) {
      await submit(snap.tree);
      for (let i = 0; i < 15 && !pw; i++) {
        await sleep(1000);
        snap = await snapshot(page, { interactive: true });
        pw = passwordBox(snap.tree);
      }
    }
    if (!pw) status = (await checks()).slider ? "captcha" : "password_box_not_found";
    else {
      await page.locator(pw).fill(P.pw);
      await submit(snap.tree);
      let dragged = false, seen = { slider: false, code: false };
      for (let i = 0; i < 30; i++) {
        await sleep(1000);
        s = await state();
        if (!s.onLogin) break;
        seen = await checks();
        if (seen.code) break;
        if (seen.slider && seen.drag && !dragged && page.mouse.down) {
          dragged = true;
          await page.mouse.move(seen.drag.x, seen.drag.y);
          await page.mouse.down();
          for (let k = 1; k <= 20; k++) await page.mouse.move(seen.drag.x + ((seen.drag.to - seen.drag.x) * k) / 20, seen.drag.y + (k % 3) - 1);
          await page.mouse.up();
          continue;
        }
        await page.evaluate(() => { const b = [...document.querySelectorAll("button")].find((x) => /^Continue as/i.test(x.innerText.trim())); b && b.click(); });
      }
      if (!s.onLogin) status = s.matches ? "signed_in" : "wrong_account";
      else status = seen.code ? "two_factor" : seen.slider ? "captcha" : "still_on_login";
    }
  }
}
// login --sign-in: the signed-in browser hands its own (decrypted) alibaba.com cookies to the parent
// pipe. Its cookie file cannot be read instead: current Chrome on macOS writes new profiles as "v11",
// whose key is not the "Chrome Safe Storage" keychain password.
if (P.dumpCookies && /signed_in$/.test(status)) {
  let all;
  try { all = (await page._send("Storage.getCookies", {})).cookies; } catch (e) { all = (await page._send("Network.getAllCookies")).cookies; }
  console.log("COOKIES " + JSON.stringify(all.filter((c) => /(^|\.)alibaba\.com$/i.test(String(c.domain).replace(/^\./, "")))));
}
console.log("STATUS " + status);

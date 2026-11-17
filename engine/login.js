// Aside repl script, piped on stdin by `seller_central_raw.py --login` (never saved to disk).
// __PARAMS__ = {email, pw, loginId}; pw comes straight from the keychain and is only
// typed into the password box. Prints a status line only.
const P = __PARAMS__;
const page = await openTab("https://i.alibaba.com/spa-micro/www2/ads/report.htm");
await sleep(5000);
const state = () => page.evaluate((lid) => ({ onLogin: /login\./.test(location.host), matches: !lid || decodeURIComponent(document.cookie).includes(lid) }), P.loginId);
let s = await state();
let status;
if (!s.onLogin) status = s.matches ? "already_signed_in" : "wrong_account";
else {
  const snap = await snapshot(page, { interactive: true });
  const boxes = [...snap.tree.matchAll(/textbox[^\n]*\[ref=(e\d+)\]/g)].map((m) => m[1]);
  const pwBox = (/textbox[^\n]*[Pp]assword[^\n]*\[ref=(e\d+)\]/.exec(snap.tree) || [])[1] || boxes[1];
  if (boxes.length < 2 || !pwBox) status = "login_form_not_found";
  else {
    await page.locator(boxes[0]).fill(P.email);
    await page.locator(pwBox).fill(P.pw);
    const btn = (/button "(?:Sign in|Continue)" \[ref=(e\d+)\]/.exec(snap.tree) || [])[1];
    if (btn) await page.locator(btn).click(); else await page.keyboard.press("Enter");
    for (let i = 0; i < 25; i++) {
      await sleep(1000);
      s = await state();
      if (!s.onLogin) break;
      await page.evaluate(() => { const b = [...document.querySelectorAll("button")].find((x) => /^Continue as/i.test(x.innerText.trim())); b && b.click(); });
    }
    status = s.onLogin ? "still_on_login (captcha or 2FA: finish in Aside, then --check-session)" : (s.matches ? "signed_in" : "wrong_account");
  }
}
console.log("STATUS " + status);

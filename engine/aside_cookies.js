// Aside repl script, piped on stdin by browser_cookies.read_via_aside (never saved to disk).
// Prints the browser context's alibaba.com cookies as one "COOKIES <json>" line for the parent pipe.
const page = await openTab("https://www.alibaba.com/");
await sleep(1500);
let cookies;
try {
  cookies = await page.context().cookies();
} catch (e) {
  const cdp = await page.context().newCDPSession(page);
  cookies = (await cdp.send("Network.getAllCookies")).cookies;
}
cookies = cookies.filter((c) => /(^|\.)alibaba\.com$/i.test(String(c.domain).replace(/^\./, "")));
console.log("COOKIES " + JSON.stringify(cookies));

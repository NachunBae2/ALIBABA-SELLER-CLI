// Runs only in Aside REPL. No tab, cookie export, or Node networking.
async function sellerFetchBatch(input) {
  const started = Date.now(), responses = [], csrf = new Map();
  let httpRequests = 0, error = null, failedRequest = null, accountVerified = false, identityHtml = null;
  const secret = /csrf|xsrf|token|^sign$|^cookie$|^authorization$|^ua$|^bx-|^x5sec$/i;
  function scrub(v) {
    if (Array.isArray(v)) return v.map(scrub);
    if (v && typeof v === 'object') return Object.fromEntries(Object.entries(v).filter(([k]) => !secret.test(k)).map(([k, x]) => [k, scrub(x)]));
    if (typeof v === 'string') {
      try { const j = JSON.parse(v); if (j && typeof j === 'object') return JSON.stringify(scrub(j)); } catch {}
      return v.replace(/([\w-]*(?:csrf|xsrf|token|sign)[\w-]*=)[^&\s;"\\]+/gi, '$1***');
    }
    return v;
  }
  const form = obj => new URLSearchParams(Object.entries(obj).map(([k,v]) => [k, typeof v === 'object' ? JSON.stringify(v) : String(v)])).toString();
  async function send(url, options = {}) {
    const u = new URL(url);
    if (u.protocol !== 'https:' || !u.hostname.endsWith('.alibaba.com')) throw Error('untrusted_endpoint');
    if (Date.now() - started > 85000) throw Error('batch_budget');
    httpRequests++;
    const {response, text} = await Promise.race([
      (async () => {
        const response = await fetch(url, { ...options, redirect: 'manual', cache: 'no-store' });
        return {response, text: await response.text()};
      })(),
      sleep(20000).then(() => { throw Error('http_transport_timeout'); })
    ]);
    const location = response.headers.get('location') || '';
    if ([401,403].includes(response.status) || /login|passport|punish/i.test(location) ||
        /User not login|FAIL_SYS_(?:SESSION_EXPIRED|TOKEN_EXPIRED|TOKEN_EMPTY|ILLEGAL_ACCESS)|RGV587_ERROR|"(?:code|errorCode)"\s*:\s*"?(?:NOT_LOGIN|LOGIN_REQUIRED)/i.test(text))
      { failedRequest = {status:response.status, host:u.hostname, path:u.pathname, redirectHost:location ? new URL(location, url).hostname : ''}; throw Error('session_expired_or_captcha'); }
    return { status: response.status, redirectHost: location ? new URL(location, url).hostname : '', ct: response.headers.get('content-type') || '', text };
  }
  try {
    if (input.expectedIdentity) {
      const r = await send('https://message.alibaba.com/message/default.htm');
      identityHtml = r.text;
      if (r.status !== 200 || !identityHtml.includes(input.expectedIdentity)) throw Error('wrong_account');
      accountVerified = true;
    }
    for (const job of input.jobs) {
      if (Date.now() - started > 65000) break;
      const u = new URL(job.url), headers = {};
      const q = { ...job.query }, p = { ...job.params };
      headers.Referer = u.hostname === 'www2.alibaba.com' ? u.origin + '/' : job.referer || u.origin + '/';
      if (u.hostname === 'www2.alibaba.com') {
        if (!csrf.has(u.hostname)) {
          const r = await send('https://www2.alibaba.com/api/csrf?_=' + Date.now());
          let c; try { c = JSON.parse(r.text); } catch { throw Error('csrf_bootstrap_failed'); }
          if (!c.headerName || !c.parameterName || !c.token) throw Error('csrf_bootstrap_failed');
          csrf.set(u.hostname, c);
        }
        const c = csrf.get(u.hostname);
        headers[c.headerName] = c.token;
        (job.method === 'GET' ? q : p)[c.parameterName] = c.token;
      }
      if (u.hostname === 'message.alibaba.com' && job.method === 'POST') {
        if (!csrf.has(u.hostname)) {
          const html = identityHtml || (await send('https://message.alibaba.com/message/default.htm')).text;
          const token = /["']?csrfTokenVal["']?\s*[:=]\s*["']([^"']+)/.exec(html)?.[1];
          if (!token) throw Error('csrf_bootstrap_failed');
          csrf.set(u.hostname, token);
        }
        p._csrf_token_ = csrf.get(u.hostname);
      }
      // No captured/stale signatures. Add an observed bootstrap contract before enabling mtop.
      if (u.pathname.startsWith('/h5/')) throw Error('mtop_bootstrap_contract_missing');
      let body;
      if (job.method !== 'GET') {
        headers['Content-Type'] = job.encoding === 'json' ? 'application/json' : 'application/x-www-form-urlencoded';
        body = job.encoding === 'json' ? JSON.stringify(p) : form(p);
      }
      const query = form(q), target = job.url + (query ? (job.url.includes('?') ? '&' : '?') + query : '');
      let r = await send(target, { method: job.method, headers, ...(body == null ? {} : { body }) });
      // A stale CSRF bootstrap may redirect to err.taobao.com; it is not a login failure.
      // Refresh in REPL and retry the same read, never open a tab for an error-page redirect.
      for (let retry=0; r.status === 302 && r.redirectHost === 'err.taobao.com' && u.hostname === 'www2.alibaba.com' && retry<12; retry++) {
        const bootstrap = await send('https://www2.alibaba.com/api/csrf?_=' + Date.now());
        const c = JSON.parse(bootstrap.text);
        if (!c.headerName || !c.parameterName || !c.token) throw Error('csrf_bootstrap_failed');
        csrf.set(u.hostname,c); headers[c.headerName] = c.token;
        (job.method === 'GET' ? q : p)[c.parameterName] = c.token;
        const retryQuery = form(q);
        r = await send(job.url + (retryQuery ? (job.url.includes('?') ? '&' : '?') + retryQuery : ''), {
          method:job.method, headers, ...(job.method === 'GET' ? {} : {body:job.encoding === 'json' ? JSON.stringify(p) : form(p)})
        });
      }
      let parsed;
      if (!u.pathname.includes('/feedback/')) { try { parsed = JSON.parse(r.text); } catch {} }
      // Structured responses scrub secrets before they cross the REPL boundary. Inquiry's
      // HTML-escaped JSON is left as text for the lossless large-ID parser/allowlist in Python.
      responses.push({ ...r, text: u.pathname.includes('/feedback/') ? r.text.replace(/("[^"]*(?:token|csrf|sign)[^"]*"\s*:\s*")[^"]*/gi, '$1***') : parsed === undefined ? scrub(r.text) : JSON.stringify(scrub(parsed)) });
    }
  } catch (e) {
    error = e.message === 'batch_budget' ? null : ['wrong_account','session_expired_or_captcha','csrf_bootstrap_failed','untrusted_endpoint','mtop_bootstrap_contract_missing'].includes(e.message) ? e.message : 'http_transport_failed';
  }
  console.log('SELLER_FETCH ' + JSON.stringify({ responses, httpRequests, error, failedRequest, accountVerified }));
}

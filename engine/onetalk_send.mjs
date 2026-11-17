import { createHash } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { basename, extname, resolve } from 'node:path';
import { pathToFileURL } from 'node:url';
import { createClient } from './onetalk_api.mjs';

export const ENDPOINTS = Object.freeze({
  send: 'https://onetalk.alibaba.com/message/sendMessage.htm',
  csrf: 'https://onetalk.alibaba.com/csrf/getToken.htm',
  products: 'https://hz-productposting.alibaba.com/product/managementproducts/asyQueryProductsList.do',
  prepare: 'mtop.alibaba.interaction.clouddisk.prepareSendFileWithGroup',
  relation: 'mtop.alibaba.interaction.clouddisk.buildFileRelationWithGroup',
});

const PAGE = 'https://onetalk.alibaba.com/message/weblitePWA.htm';
const UA = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36';

function required(value, key) {
  if (value === undefined || value === null || String(value).trim() === '') throw new Error(`Missing ${key}`);
  return value;
}

export function validateTarget(target) {
  // The caller picks the conversation by argument; no buyer-level rule here (owner 2026-11-17).
  if (!target || typeof target !== 'object') throw new Error('Missing target');
  required(target.conversationId, 'conversationId');
  for (const key of ['senderAliId', 'recipientAliId']) {
    if (!/^\d+$/.test(String(target[key])) || !Number.isSafeInteger(Number(target[key])) || Number(target[key]) <= 0) {
      throw new Error(`Invalid ${key}`);
    }
  }
  if (String(target.senderAliId) === String(target.recipientAliId)) throw new Error('REFUSED_SELF_TARGET');
  return target;
}

export function buildMessage(target, messageType, content) {
  validateTarget(target);
  return {
    messageType, content, chatType: 1, extParam: {},
    conversationId: String(target.conversationId).replace(/@icbu$/, ''),
    receiverAliId: [Number(target.recipientAliId), Number(target.senderAliId)],
    ...(target.chatToken ? { chatToken: target.chatToken } : {}),
  };
}

export function cookieHeader(cookies, address, now = Date.now()) {
  if (!Array.isArray(cookies)) throw new Error('cookies must be an array');
  const url = new URL(address);
  return cookies.filter(c => {
    const domain = String(c.domain || '').replace(/^\./, '').toLowerCase();
    const path = c.path || '/';
    return domain && (url.hostname === domain || (!c.hostOnly && url.hostname.endsWith(`.${domain}`))) &&
      (url.pathname === path || (url.pathname.startsWith(path) && (path.endsWith('/') || url.pathname[path.length] === '/'))) &&
      (!c.secure || url.protocol === 'https:') && (c.expires == null || c.expires < 0 || c.expires * 1000 > now) &&
      c.value && !/[\r\n;]/.test(c.name + c.value);
  }).map(c => `${c.name}=${c.value}`).join('; ');
}

export function redact(value, cookies = [], extraSecrets = []) {
  const secrets = [...(Array.isArray(cookies) ? cookies : []).map(c => c?.value), ...extraSecrets].filter(v => typeof v === 'string' && v.length >= 4);
  for (const value of [...secrets]) {
    secrets.push(encodeURIComponent(value));
    try { secrets.push(decodeURIComponent(value)); } catch { /* Keep the original cookie. */ }
  }
  const clean = v => {
    if (typeof v === 'string') return secrets.filter(s => s.length >= 4).sort((a, b) => b.length - a.length).reduce((s, secret) => s.replaceAll(secret, '***'), v)
      .replace(/([?&][^=&\s]*(?:token|signature|policy|accesskey|accessid)[^=&\s]*=)[^&\s]*/gi, '$1***');
    if (Array.isArray(v)) return v.map(clean);
    if (v && typeof v === 'object') return Object.fromEntries(Object.entries(v).map(([k, x]) =>
      [k, /cookie|token|signature|policy|accesskey|accessid|authorization/i.test(k) ? '***' : clean(x)]));
    return v;
  };
  return clean(value);
}

function failure(api, status, code) {
  const error = new Error(`${api}: HTTP ${status ?? 'unknown'}, code ${code ?? 'MISSING_SUCCESS'}`);
  Object.assign(error, { api, httpStatus: status, code: code ?? 'MISSING_SUCCESS' });
  return error;
}

async function fetchJSON(cookies, url, init, options) {
  const response = await (options.fetchImpl || fetch)(url, {
    ...init, redirect: 'error', signal: AbortSignal.timeout(options.timeoutMs || 30000),
    headers: { 'user-agent': UA, referer: PAGE, accept: 'application/json',
      cookie: cookieHeader(cookies, url), ...init.headers },
  });
  if (!response.ok) throw failure(new URL(url).pathname, response.status, 'HTTP_ERROR');
  let json;
  try { json = await response.json(); } catch { throw failure(new URL(url).pathname, response.status, 'INVALID_JSON'); }
  return { json, httpStatus: response.status };
}

async function postMessage(cookies, params, options) {
  const state = transport(cookies, options);
  try {
    // Do not retry a send: an ambiguous response must be reconciled through history.
    const json = await state.client.form(ENDPOINTS.send, params);
    if (String(json.code) !== '200') throw failure('/message/sendMessage.htm', state.httpStatus, json.code);
    const messageId = json.data?.messageID;
    if (!messageId) throw failure('/message/sendMessage.htm', state.httpStatus, 'MISSING_MESSAGE_ID_AMBIGUOUS');
    return { api: ENDPOINTS.send, httpStatus: state.httpStatus, code: json.code, messageId: String(messageId), messageType: params.messageType };
  } catch (error) {
    throw transportFailure(error, state.httpStatus);
  } finally { state.client.close(); }
}

function transportFailure(error, status) {
  // Shared transport errors may contain response text or refreshed credentials.
  if (error.ret) return failure(error.api, status, error.ret.map(r => String(r).split('::')[0]).join(','));
  if (error.status != null) return failure('OneTalk transport', error.status, 'HTTP_ERROR');
  return error;
}

function transport(cookies, options) {
  const state = { httpStatus: null };
  state.client = createClient(cookies, { fetch: async (url, init) => {
    const response = await (options.fetchImpl || fetch)(url, { ...init, signal: AbortSignal.timeout(options.timeoutMs || 30000) });
    state.httpStatus = response.status;
    return response;
  }, ...(options.now ? { now: options.now } : {}) });
  return state;
}

export async function sendText(cookies, args, options = {}) {
  return postMessage(cookies, buildMessage(args.target, 1, String(required(args.text, 'text'))), options);
}

export async function sendNameCard(cookies, args, options = {}) {
  const content = { cardType: 1, params: { type: '1', showCertification: 'true', showCompanyName: 'true', showEmailAddress: 'true' } };
  return postMessage(cookies, buildMessage(args.target, 57, content), options);
}

export async function sendProductCard(cookies, args, options = {}) {
  const id = String(required(args.productId, 'productId'));
  if (!/^\d+$/.test(id)) throw new Error('Invalid productId');
  const content = { cardType: 2000, params: {
    type: '2000', ids: id, ctime: String(options.now?.() ?? Date.now()), ...(args.country ? { country: String(args.country) } : {}),
  } };
  return postMessage(cookies, buildMessage(args.target, 2000, content), options);
}

export async function searchProducts(cookies, args = {}, options = {}) {
  const url = new URL(ENDPOINTS.products);
  const page = args.page ?? 1;
  const size = args.size ?? 15;
  if (!Number.isSafeInteger(page) || page < 1 || !Number.isSafeInteger(size) || size < 1 || size > 100) throw new Error('Invalid pagination');
  const params = { ignoreMember: 'no', displayStatus: 'on', status: 'approved', isPrivate: 'false', isPublic: 'false', repositoryType: 'all',
    marketType: 'all', imageType: 'all', page: String(page), size: String(size), gmtModified: 'desc', statisticsType: 'month',
    ...(args.query ? { subject: args.query } : {}) };
  url.search = new URLSearchParams(params).toString();
  const { json, httpStatus } = await fetchJSON(cookies, url.href, { method: 'GET' }, options);
  if (json.result !== true) throw failure('/product/managementproducts/asyQueryProductsList.do', httpStatus, json.code ?? 'RESULT_FALSE');
  if (!Array.isArray(json.products)) throw failure('/product/managementproducts/asyQueryProductsList.do', httpStatus, 'INVALID_PRODUCT_LIST');
  return { api: ENDPOINTS.products, httpStatus, products: json.products, count: json.count, currentPage: json.currentPage };
}

async function mtop(cookies, api, data, options) {
  let dataResponse;
  if (options.mtopRequest) {
    const response = await options.mtopRequest(cookies, { api, version: '1.0', appKey: '24889839', data }, options);
    if (!response?.ret?.some(r => r.startsWith('SUCCESS::'))) throw failure(api, response?.httpStatus, response?.ret?.join(','));
    dataResponse = response.data;
  } else {
    dataResponse = await options.transport.client.mtop(api, data, { v: '1.0', appKey: '24889839' });
  }
  if (Number(dataResponse?.code) !== 200) throw failure(api, options.transport?.httpStatus, dataResponse?.code);
  if (!dataResponse.result) throw failure(api, options.transport?.httpStatus, 'MISSING_RESULT');
  return dataResponse.result;
}

export function buildUploadData(target, name, size, md5) {
  validateTarget(target);
  const extension = extname(name).slice(1).toLowerCase();
  if (!/^[a-z0-9]+$/.test(extension)) throw new Error('Missing file extension');
  const scene = JSON.stringify({ sceneType: '1', idType: '2', from: String(target.senderAliId), to: String(target.recipientAliId) });
  return {
    prepare: { appKey: 'OneChat', fileName: `${md5}.${extension}`, scene, isToken: false, chatToken: target.chatToken || '' },
    relation: { appKey: 'OneChat', scene,
      node: JSON.stringify({ nodeName: name, nodeSize: size, materialType: extension, md5 }),
      params: JSON.stringify({ source: '', needCardUrl: true }), chatToken: target.chatToken || '' },
  };
}

export function buildUploadForm(prepared, fileName, bytes, contentType) {
  const policy = required(prepared.policyDTO, 'policyDTO');
  const form = new FormData();
  for (const [name, value] of Object.entries({
    key: required(prepared.uploadFileDir, 'uploadFileDir') + fileName,
    policy: required(policy.encodedPolicy, 'encodedPolicy'), OSSAccessKeyId: required(policy.accessid, 'accessid'),
    success_action_status: '200', callback: required(policy.callbackBody, 'callbackBody'), signature: required(policy.signature, 'signature'),
    name: prepared.uploadFileDir + fileName,
  })) form.append(name, value);
  form.append('file', new Blob([bytes], { type: contentType || 'application/octet-stream' }), fileName);
  return form;
}

export async function uploadFile(cookies, args, options = {}) {
  validateTarget(args.target);
  const bytes = args.bytes ?? await readFile(required(args.path, 'path'));
  if (!(bytes instanceof Uint8Array) || bytes.byteLength === 0) throw new Error('Empty or invalid file');
  const name = basename(args.name || required(args.path, 'path'));
  const md5 = createHash('md5').update(bytes).digest('hex');
  const data = buildUploadData(args.target, name, bytes.byteLength, md5);
  const state = transport(cookies, options);
  options = { ...options, transport: state };
  try {
  const prepared = await mtop(cookies, ENDPOINTS.prepare, data.prepare, options);
  if (Number(prepared.allowSendFileMaxSize) > 0 && bytes.byteLength > Number(prepared.allowSendFileMaxSize)) throw new Error('FILE_TOO_LARGE');
  let uploadStatus = null;
  if (prepared.fileIsExist !== true) {
    const host = new URL(required(prepared.policyDTO?.host, 'upload host'));
    if (host.protocol !== 'https:' || !host.hostname.endsWith('.aliyuncs.com')) throw new Error('UNTRUSTED_UPLOAD_HOST');
    const response = await (options.fetchImpl || fetch)(host.href, {
      method: 'POST', redirect: 'error', signal: AbortSignal.timeout(options.timeoutMs || 60000),
      body: buildUploadForm(prepared, data.prepare.fileName, bytes, args.contentType),
    });
    uploadStatus = response.status;
    if (!response.ok) throw failure('OSS upload', response.status, 'UPLOAD_FAILED');
    await response.arrayBuffer();
  }
  const relation = await mtop(cookies, ENDPOINTS.relation, data.relation, options);
  required(relation.fileCardUrl, 'fileCardUrl');
  required(relation.redirectFileUrl, 'redirectFileUrl');
  return { ...relation, uploadStatus };
  } catch (error) {
    throw transportFailure(error, state.httpStatus);
  } finally { state.client.close(); }
}

function imageContent(upload) {
  const card = new URL(required(upload.fileCardUrl, 'fileCardUrl'));
  const width = Number(upload.width || card.searchParams.get('picWidth'));
  const height = Number(upload.height || card.searchParams.get('picHeight'));
  if (!(width > 0 && height > 0)) throw new Error('Missing image dimensions');
  return { height, width, url: required(upload.redirectFileUrl, 'redirectFileUrl'), isOriginal: 1,
    size: required(upload.nodeSize ?? upload.size, 'nodeSize'), md5: required(upload.md5, 'md5'),
    fileId: upload.fileId || `${upload.md5}.${required(upload.materialType, 'materialType')}`, suffix: upload.materialType };
}

export async function sendImage(cookies, args, options = {}) {
  validateTarget(args.target);
  const upload = args.upload || await uploadFile(cookies, args, options);
  return postMessage(cookies, buildMessage(args.target, 60, imageContent(upload)), options);
}

export async function sendFile(cookies, args, options = {}) {
  validateTarget(args.target);
  const upload = args.upload || await uploadFile(cookies, args, options);
  const card = new URL(required(upload.fileCardUrl, 'fileCardUrl'));
  const params = { type: '12', ctime: required(card.searchParams.get('ctime'), 'ctime'), version: required(card.searchParams.get('version'), 'version'),
    extensionType: required(upload.materialType, 'materialType'), id: String(required(upload.id, 'id')), parentId: String(required(upload.parentId, 'parentId')),
    md5: required(upload.md5, 'md5'), url: required(upload.redirectFileUrl, 'redirectFileUrl'), name: required(upload.nodeName, 'nodeName'),
    size: String(required(upload.nodeSize, 'nodeSize')), thumbnailUrl: upload.thumbnailUrl || '', downloadUrl: upload.downloadUrl || '' };  // the relation call may leave it out (live 2026-11-17); the card still sends
  return postMessage(cookies, buildMessage(args.target, 53, { cardType: 12, params }), options);
}

const operations = { sendText, sendNameCard, searchProducts, sendProductCard, uploadFile, sendImage, sendFile };

export async function main(argv, stdin, output = console.log) {
  let cookies = [];
  let args = {};
  try {
    const [op, flag, json] = argv;
    if (!Object.hasOwn(operations, op) || flag !== '--json' || !json || argv.length !== 3) throw new Error('Usage: onetalk_send.mjs <op> --json <args>');
    args = JSON.parse(json);
    if (!args || typeof args !== 'object' || Array.isArray(args)) throw new Error('args must be an object');
    cookies = JSON.parse(stdin).cookies;
    if (!Array.isArray(cookies) || !cookies.length) throw new Error('Missing stdin cookies');
    const result = await operations[op](cookies, args);
    output(JSON.stringify(redact(result, cookies, [args.target?.chatToken])));
    return 0;
  } catch (error) {
    output(JSON.stringify(redact({ error: error.message, code: error.code, httpStatus: error.httpStatus }, cookies, [args?.target?.chatToken])));
    return 1;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  process.exitCode = await main(process.argv.slice(2), Buffer.concat(chunks).toString('utf8'));
}

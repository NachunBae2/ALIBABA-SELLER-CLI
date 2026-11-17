import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';
const source = readFileSync(new URL('../engine/http_fetch.js',import.meta.url),'utf8');
async function execute(jobs, replies, extras={}) {
  const calls=[], logs=[];
  const context=vm.createContext({URL,URLSearchParams,Date,sleep:()=>new Promise(()=>{}), console:{log:x=>logs.push(x)},
    fetch:async(url,options)=>{calls.push({url,options});const r=replies.shift();return {status:r.status||200,headers:new Headers(r.headers),text:async()=>typeof r.body==='string'?r.body:JSON.stringify(r.body)};}});
  await vm.runInContext(source+'\nsellerFetchBatch('+JSON.stringify({jobs,...extras})+');',context);
  return {calls,output:JSON.parse(logs[0].replace('SELLER_FETCH ','')),logs};
}
const job={url:'https://www2.alibaba.com/api/data/dynamic/component/data',method:'POST',query:{},params:{data:{test:1}},encoding:'form'};
test('REPL batches requests, bootstraps csrf once, keeps token inside and disables redirects',async()=>{
 const {calls,output,logs}=await execute([job,job],[{body:{headerName:'X-CSRF',parameterName:'csrf',token:'secret-fixture'}},{body:{rows:[{id:1}],token:'secret-fixture'}},{body:{rows:[{id:2}]}}]);
 assert.equal(calls.length,3);assert.equal(output.responses.length,2);
 assert.equal(calls[1].options.headers['X-CSRF'],'secret-fixture');
 assert.equal(new URLSearchParams(calls[1].options.body).get('csrf'),'secret-fixture');
 assert.equal(calls[1].options.redirect,'manual');assert.ok(!logs.join('').includes('secret-fixture'));
});
test('login text and redirect fail closed without opening a tab',async()=>{
 for(const response of [{body:'User not login'},{status:302,headers:{location:'https://login.alibaba.com/'},body:''}]) {
  const {output}=await execute([{...job,url:'https://seller.alibaba.com/query',method:'GET'}],[response]);
  assert.equal(output.error,'session_expired_or_captcha');assert.equal(output.responses.length,0);
 }
});
test('raw inquiry identifiers survive larger than JS safe integer',async()=>{
 const {output}=await execute([{...job,url:'https://message.alibaba.com/message/ajax/feedback/subjectList.htm'}],[{body:'csrfTokenVal: "secret-fixture"'},{body:'{"id":9007199254740999}'}]);
 assert.ok(output.responses[0].text.includes('9007199254740999'));
});

test('error-page redirect refreshes csrf in REPL without treating it as login',async()=>{
 const {output,calls}=await execute([job],[
  {body:{headerName:'X-CSRF',parameterName:'csrf',token:'old-fixture'}},
  {status:302,headers:{location:'https://err.taobao.com/error1.html'},body:''},
  {body:{headerName:'X-CSRF',parameterName:'csrf',token:'new-fixture'}},
  {body:{rows:[]}}
 ]);
 assert.equal(output.error,null);assert.equal(output.responses[0].status,200);
 assert.equal(calls[3].options.headers['X-CSRF'],'new-fixture');
});
test('unrelated redirects stay failed HTTP responses without being followed',async()=>{
 const {output,calls}=await execute([{...job,url:'https://seller.alibaba.com/query',method:'GET'}],[{status:302,headers:{location:'https://example.com/'},body:''}]);
 assert.equal(calls.length,1);assert.equal(output.error,null);assert.equal(output.responses[0].status,302);
});

test('retries are bounded for persistent error-page redirects', async()=>{
 const replies=[];
 for(let i=0;i<13;i++) replies.push({body:{headerName:'X-CSRF',parameterName:'csrf',token:'fixture'}},{status:302,headers:{location:'https://err.taobao.com/error1.html'},body:''});
 const {output,calls}=await execute([job],replies);
 assert.equal(calls.length,26);assert.equal(output.responses[0].status,302);
});

test('account marker is checked without a tab and never returned',async()=>{
 const read={...job,url:'https://seller.alibaba.com/query',method:'GET'};
 const bad=await execute([read],[{body:'signed in as another fixture'}],{expectedIdentity:'fixture-user'});
 assert.equal(bad.output.error,'wrong_account');assert.equal(bad.calls.length,1);
 const good=await execute([read],[{body:'fixture-user'}, {body:{rows:[]}}],{expectedIdentity:'fixture-user'});
 assert.equal(good.output.accountVerified,true);assert.equal(good.output.error,null);
 assert.ok(!good.logs.join('').includes('fixture-user'));
});

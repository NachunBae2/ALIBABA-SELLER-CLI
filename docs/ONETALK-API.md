# OneTalk 내부 API (탭 없이 로그인 쿠키로 직접 호출)

`engine/onetalk_api.mjs` 가 쓰는 OneTalk(셀러 메시지센터, `onetalk.alibaba.com/message/weblitePWA.htm`) 웹 클라이언트의 내부 요청을 정리한 문서입니다. 웹 화면이 보내는 요청을 브라우저 개발자 도구 수준에서 관찰해 이름·형식만 적었습니다. 값(쿠키·토큰·ID·바이어 정보)은 적지 않습니다.

- 실행: `node engine/onetalk_api.mjs <op> [--json '{...}']`, 표준입력 `{"cookies":[...]}`, 표준출력 JSON.
- op: `listConversations` · `listInquiries` · `getHistory` · `markRead` · `getConversations` · `getBuyerInfo`.
- 발송: `node engine/onetalk_send.mjs <op> --json '{...}'` (op: `sendText` · `sendNameCard` · `searchProducts` · `sendProductCard` · `uploadFile` · `sendImage` · `sendFile`), 표준입력 `{"cookies":[...]}`. 웹 SDK 의 lwp `/r/MessageSend/sendByReceiverScope` 경로도 번들에 있으나, 실증한 발송 경로는 `sendMessage.htm` 입니다.
- 모듈 사용: `createClient(cookies)` → `mtop()` / `form()` / `lwp()` / `close()`, 원샷 `call(cookies, spec)`.
- 오류는 숨기지 않고 throw 합니다(`MtopError`, `HttpError`, `LwpError`). 빈 배열·기본값으로 바꾸지 않습니다.
- 외부 의존성 없음(Node 22+ 내장 `fetch`·`WebSocket`·`crypto`).

## 전송 3종과 토큰

| 전송 | 형식 | 인증·토큰 |
|---|---|---|
| mtop | `GET https://acs.h.alibaba.com/h5/<api 소문자>/<v>/` 쿼리 `jsv, appKey, t, sign, api, v, dataType=json, ecode=1, type=originaljson, data=<JSON>` | 쿠키 + `sign = md5(token + "&" + t + "&" + appKey + "&" + data)`, `token` = `_m_h5_tk` 쿠키 값의 `_` 앞부분. 쿠키에 `_m_h5_tk` 가 없거나 만료면 응답 ret 이 `FAIL_SYS_TOKEN_EMPTY` / `FAIL_SYS_TOKEN_EXOIRED` 이고 Set-Cookie 로 새 `_m_h5_tk`·`_m_h5_tk_enc` 가 옴 → 메모리 쿠키에 반영하고 한 번 다시 서명해 재호출. appKey 는 웹 클라이언트 상수(일반 API 1개, IM 토큰 발급 1개) |
| form | `POST https://onetalk.alibaba.com/message/*.htm` 본문 `params=<JSON>&_csrf=<토큰>` · `POST https://message.alibaba.com/message/ajax/**.htm` 본문 `params=<JSON>&postId=<ms>&_csrf_token_=<토큰>` | onetalk 토큰 = `POST onetalk.alibaba.com/csrf/getToken.htm` → `token`. message 토큰 = `GET message.alibaba.com/message/ajax/csrftoken.htm` → `data.token`. message.alibaba.com 응답은 문자열 값이 HTML 엔티티로 인코딩되어 있어 디코드함 |
| lwp | DingTalk IMPaaS 프레임(JSON) over `wss://wss-imakamai.alibaba.com/`. 요청 `{lwp:"/경로", headers:{mid}, body:[위치 인자]}`, 응답 `{code, headers:{mid, sid}, body}`. `mid` = 난수+ms+`" 0"` | ① mtop `mtop.alibaba.icbu.im.login.token.get` v1.0 (data `deviceId`, `appKey`=IMPaaS app-key) → `accessToken` ② 첫 프레임 `/reg` (headers `cache-header, app-key, token, ua, dt, wv, sync, did`) → code 200 ③ 서버 푸시(프레임에 `lwp` 있음, 예 `/s/sync`)에는 받은 headers 그대로 `{code:200}` 응답 ④ 하트비트 `/!`. 웹소켓 핸드셰이크엔 쿠키가 필요 없음 |

## 동작별 표

| # | 동작 | 엔드포인트 | 전송 | 필수 파라미터(이름) | 응답/메시지 형식 | 실증 |
|---|---|---|---|---|---|---|
| ① | 대화 목록 | `/r/Conversation/listNewestPagination` | lwp | body `[cursor, count]` (첫 페이지는 큰 수, 다음은 `nextCursor`) | `nextCursor, hasMore, userConvs[]` (`type` 1=1:1·2=그룹, `singleChatUserConversation.redPoint`, `lastMessage.message`, `singleChatConversation.cid`, `user_extension.custom`(chatToken 등 JSON 문자열), `user_extension.target`(상대 id)) | 됨 (화면 대조) |
| ② | 문의 목록(All Inquiries) | `message.alibaba.com/message/ajax/feedback/subjectList.htm` | form | params `listType, pagination{nextPage,pageSize}, filter{isShowAtm}, order{order,orderBy}, search` | `data.totalCount` + 행 배열(행마다 1원소 배열: `tradeId, secTradeId, feedbackId, createTime, lastestReplyTime, hasUnread, unreadCount, replyStatus, …`) | 됨 (화면 대조) |
| ③ | 대화 열기(메시지 이력) | `/r/MessageManager/listUserMessages` | lwp | body `[cid, false, cursor, count, false]` | `nextCursor, userMessageModels[]{readStatus, message{messageId, createAt, sender, content{contentType 1=text / 101=custom{type,data}}}}` | 됨 (화면 대조) |
| ④ | 읽음 처리 | `/r/Conversation/clearRedPoint` | lwp | body `[[{cid, messageId}]]` | code 200. (상대 메시지 읽음 표시는 `/r/MessageStatus/read` body `[[messageId…]]` — 미사용) | 일부 (호출 code 200, 대상 대화가 이미 읽음 상태라 전후 0→0) |
| ⑤ | 텍스트 발송 | `POST onetalk.alibaba.com/message/sendMessage.htm` | form | params `messageType, content, chatType, extParam, conversationId, receiverAliId` + `chatToken`, `_csrf`(`/csrf/getToken.htm`) | `messageType` 1, `content` 문자열 → 이력 `contentType:1, text.content` | 됨 (발송 1건, 이력 리드백 본문 정확 일치) |
| ⑥ | 명함 발송 | `sendMessage.htm` (⑤와 같은 봉투) | form | params `messageType, content, chatType, extParam, conversationId, receiverAliId` + `chatToken`, `_csrf`(`/csrf/getToken.htm`) | `messageType` 57, `content{cardType:1, params{type, showCertification, showCompanyName, showEmailAddress}}` → 이력 `contentType:101, custom.type 10010`, `custom.data`(base64 JSON) `cardType 1` | 됨 (발송 1건, 리드백 cardType 1). 별도 '교환 요청' 동작은 미확인 |
| ⑦ | 상품카드 발송 + 상품 검색 | 검색 `GET hz-productposting.alibaba.com/product/managementproducts/asyQueryProductsList.do` → `sendMessage.htm` | http / form | 검색: `ignoreMember, displayStatus, status, isPrivate, isPublic, repositoryType, marketType, imageType, page, size, gmtModified, statisticsType`, 선택 `subject`(검색어). 발송: params `messageType, content, chatType, extParam, conversationId, receiverAliId` + `chatToken`, `_csrf`(`/csrf/getToken.htm`) | 검색 응답 `result, count, currentPage, products[]{id, status, displayStatus, absImageUrl…}`. 발송 `messageType` 2000, `content{cardType:2000, params{type, ids, ctime, country}}` → 이력 `custom.type 10010`, `cardType 2000` | 됨 (검색 + 발송 1건, 리드백 cardType 2000) |
| ⑧ | 파일/이미지 업로드+발송 | `mtop.alibaba.interaction.clouddisk.prepareSendFileWithGroup` 1.0 → 응답 `policyDTO.host`(OSS) multipart → `mtop.alibaba.interaction.clouddisk.buildFileRelationWithGroup` 1.0 → `sendMessage.htm` | mtop / multipart / form | 준비 data `appKey, fileName, scene{sceneType, idType, from, to}, isToken, chatToken` · OSS `key, policy, OSSAccessKeyId, success_action_status, callback, signature, name, file` · 관계 data `appKey, scene, node{nodeName, nodeSize, materialType, md5}, params{source, needCardUrl}, chatToken` · 발송 params `messageType, content, chatType, extParam, conversationId, receiverAliId` + `chatToken`, `_csrf`(`/csrf/getToken.htm`) | 이미지 `messageType` 60, `content{height, width, url, isOriginal, size, md5, fileId, suffix}` → 이력 `custom.type 7`. 파일카드 `messageType` 53, `content{cardType:12, params{type, ctime, version, extensionType, id, parentId, md5, url, name, size, thumbnailUrl, downloadUrl}}` | 이미지 됨 (발송 1건, 리드백 custom.type 7). 파일카드 됨 (CLI로 JPG 1건 발송, custom.type 10010 / cardType 12 리드백) |
| ⑨ | 바이어 정보(등급 L0~L4) | `mtop.alibaba.icbu.im.getUserInfoByParams` v1.1 | mtop | data `contactAliIds`(JSON 배열 문자열), `queryType`=`get_buyer_member_info`, `queryParams`(JSON 배열 문자열), `chatTokens`(쉼표 연결) — 한 번에 최대 20명 | `data.object[]{aliId, userNewLevel("L1"…, 없으면 필드 없음), userNewLevelIcon, potentialScore, countryCode, joiningYears, …}` | 됨 (화면 아이콘 대조) |

보조: `/r/Conversation/getByCids` body `[[cid…]]` (대화 단건 리드백, `getConversations`).

## CLI 직접 호출 (2026-11-17 실증)

- `alibaba-inquiry list|open|reply|send-card|send-product|send-file|mark-read|buyer --account <키>`는 `lib/cookies.mjs`의 `loadCookies(account, {configPath, refresh:false})`만 사용합니다. 탭·Aside·UI 폴백·자동 재로그인은 없습니다. 쿠키 없음/세션 만료/로그인 리다이렉트/OneTalk 응답 code 501은 `alibaba-seller login` 안내와 종료 코드 3입니다.
- `list`는 대화 전체 페이지를 읽고 실제 `cid`, 마지막 시각, 안읽음, 바이어 등급을 표시합니다. 등급 조회는 20명씩 묶습니다. `--unread`, `--limit N` 지원. `list --inquiries`는 전체 문의 페이지를 읽고 `count`, `totalCount`를 함께 출력합니다. 문의 분석은 `alibaba-data inquiries list`이며, `stats|by-product|fields` 별칭만 남습니다.
- 대상은 `--cid`, `--buyer`(aliId 또는 getBuyerInfo의 loginId), `--inquiry-id`, `--trade-id` 중 하나입니다. 문의 `id/feedbackId`, 거래 `tradeId/secTradeId`를 찾은 뒤 실제 응답의 `sender.accountId = conversation.toAccId`로 연결합니다. 0개 또는 여러 대화면 오류이며 이름·시각 유사 매칭은 하지 않습니다. 세 종류의 간접 selector도 실계정 이력 조회를 통과했습니다.
- `open --limit N [--cursor N]`는 `listUserMessages`만 호출하며 `clearRedPoint`나 `MessageStatus/read`를 호출하지 않습니다. 실계정 전후 안읽음은 0→0입니다. 당시 안읽음 대화가 없어 양수→양수 보존은 실증하지 못했으며, 네트워크 주입 테스트로 읽음 요청 미호출을 검증했습니다. `mark-read`도 code 200, 0→0까지만 실측했습니다.
- 발송은 명령 즉시 실행되며 이전 `--send`·환경변수·`--allow-mark-read` 관문은 없습니다. 엔진의 L0/L1 대상 제한은 유지합니다. `--dry-run`은 대상 cid·엔드포인트·필드명만 출력하며 업로드/발송/읽음 변경을 하지 않습니다. `--cid` dry-run은 네트워크 요청도 없습니다.
- 발송 후 최대 3회 이력을 조회하여 서버 응답의 정확한 messageId와 메시지 종류를 함께 확인합니다. `readback: ok|missing`, missing이면 종료 1이며 발송 요청은 재시도하지 않습니다. 이력 요청 오류가 나도 이미 수락된 messageId와 missing 상태를 출력합니다. 세션 만료 오류는 종료 3이 우선합니다.
- `send-product --product '<검색어>'`는 검색 결과가 정확히 한 상품일 때만 발송하고, 여러 개면 `--product-id` 지정을 요구합니다. `send-file --path image.jpg`도 기본은 파일카드이며 `--as-image`를 지정해야 이미지 메시지입니다.
- 파일 관계 응답은 `downloadUrl`을 생략할 수 있습니다. 웹 SDK의 카드 변환 함수처럼 빈 문자열로 보냅니다(`url`은 `redirectFileUrl`). 첫 CLI 파일 시도는 이 필드를 필수로 검사해 발송 전 중단되었고, 수정 뒤 실제 전달은 파일카드 1건만 확인했습니다. 리드백 params 키: `size, extensionType, name, downloadUrl, ctime, id, type, version, parentId, url, md5, thumbnailUrl`.
- 8개 명령 모두 실계정 종료 0: 읽기는 직접 API 행 수/등급 대조, 발송 4종은 자동 리드백과 별도 `open --limit 5`에서 동일 메시지 ID/종류를 확인했습니다. 고객 데이터·원문·토큰·발송 원장은 레포에 저장하지 않습니다.

## 실증 방법

- 읽기(①②③⑨): 같은 시점에 임시 브라우저 화면의 행 수·아이콘과 API 결과를 대조했습니다(ID 는 해시로만 비교).
- 쿠키는 저장소에서 메모리로만 읽어 파이프로 넘깁니다. 인자·파일·로그에 남기지 않습니다.
- 테스트: `node --test test/onetalk_api.test.mjs test/onetalk_send.test.mjs` (네트워크 없음, `fetch`·`WebSocket` 주입).
- 부수효과: 셀러가 대화에 직접 메시지를 보내면 그 대화의 자동 접대(automatic reception)가 꺼졌다는 시스템 메시지가 이력에 함께 남습니다.

// 번들 정의(lib/bundles.json)는 CLI·MCP 가 같이 쓰는 단일 정본이다. 명령 목록을 코드에 다시 적지 않고
// 이 파일에서 읽는다. 번들 = {name, ko, when, commands[]}, excluded = {command, reason}.
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const BUNDLES_FILE = path.join(path.dirname(fileURLToPath(import.meta.url)), "bundles.json");
export const BUNDLES_SCHEMA = "alibaba-seller-bundles/1";

// "ads report" -> "ads_report", "store country-rules" -> "store_country-rules", "keywords" -> "keywords"
export const slugOf = (command) => String(command).trim().split(/\s+/).join("_");

export function loadBundles(file = BUNDLES_FILE) {
  if (!existsSync(file)) throw new Error(`번들 정의 없음: ${file}`);
  const def = JSON.parse(readFileSync(file, "utf8"));
  const fail = (msg) => { throw new Error(`번들 정의 오류(${file}): ${msg}`); };
  if (def.schema !== BUNDLES_SCHEMA) fail(`schema 는 ${BUNDLES_SCHEMA}`);
  if (!Array.isArray(def.bundles) || !def.bundles.length) fail("bundles 가 비었다");
  if (!Array.isArray(def.excluded)) fail("excluded 는 배열");
  const names = new Set();
  const excluded = new Set();
  for (const e of def.excluded) {
    if (!e || typeof e.command !== "string" || !e.command.trim()) fail("excluded 항목에 command 가 없다");
    if (typeof e.reason !== "string" || !e.reason.trim()) fail(`excluded ${e.command} 에 reason 이 없다`);
    if (excluded.has(e.command)) fail(`excluded 중복: ${e.command}`);
    excluded.add(e.command);
  }
  const bundles = def.bundles.map((b) => {
    if (typeof b.name !== "string" || !/^[a-z][a-z0-9-]*$/.test(b.name)) fail(`번들 이름은 영소문자: ${b.name}`);
    if (names.has(b.name)) fail(`번들 이름 중복: ${b.name}`);
    names.add(b.name);
    if (!Array.isArray(b.commands) || !b.commands.length) fail(`번들 ${b.name} 에 commands 가 없다`);
    for (const c of b.commands) {
      if (typeof c !== "string" || !c.trim()) fail(`번들 ${b.name} 의 명령이 비었다`);
      if (excluded.has(c)) fail(`번들 ${b.name} 에 excluded 명령이 들어 있다: ${c}`);
    }
    if (new Set(b.commands).size !== b.commands.length) fail(`번들 ${b.name} 에 명령 중복`);
    return { name: b.name, ko: b.ko || "", when: b.when || "", commands: [...b.commands] };
  });
  return { schema: def.schema, bundles, excluded: def.excluded.map((e) => ({ command: e.command, reason: e.reason })) };
}

// chosen: ["ads", "store"] | ["all"] | [] (= 전부). -> {selected:[이름...], unknown:[이름...]}
export function pickBundles(def, chosen = []) {
  const want = chosen.length ? chosen : ["all"];
  if (want.includes("all") && want.length > 1) return { selected: [], unknown: want.filter((n) => n !== "all") };
  if (want.length === 1 && want[0] === "all") return { selected: def.bundles.map((b) => b.name), unknown: [] };
  const unknown = want.filter((n) => !def.bundles.some((b) => b.name === n));
  return { selected: want.filter((n) => !unknown.includes(n)), unknown };
}

// 번들 순서(정의 파일 순서)대로 명령 목록. 같은 명령이 두 번 들어가면 앞 번들 것을 쓴다.
// selected 가 비었거나 "all" 을 담으면 전부.
export function commandsOf(def, selected = []) {
  const every = !selected.length || selected.includes("all");
  const out = [], seen = new Set();
  for (const b of def.bundles) {
    if (!every && !selected.includes(b.name)) continue;
    for (const command of b.commands) {
      if (seen.has(command)) continue;
      seen.add(command);
      out.push({ bundle: b.name, command, slug: slugOf(command) });
    }
  }
  return out;
}

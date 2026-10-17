#!/usr/bin/env node
/* 前端同步协议测试：不启动浏览器，直接从 index.html 抽取 <script>，
 * 在 vm 沙箱里执行，验证 window.__TBL_PROTOCOL__ 与 desktop/core.py 语义一致。
 *
 * 运行： node tests/test_web_protocol.js
 */
'use strict';

const fs = require('fs');
const path = require('path');
const vm = require('vm');
const assert = require('assert');

const htmlPath = path.join(__dirname, '..', 'github-pages', 'index.html');
const html = fs.readFileSync(htmlPath, 'utf8');

const blocks = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)];
assert.ok(blocks.length >= 1, 'index.html 中找不到 <script> 块');
const source = blocks[blocks.length - 1][1];

const sandbox = { window: {}, console: console };
vm.createContext(sandbox);
vm.runInContext(source, sandbox, { filename: 'index.html#inline-script' });

const P = sandbox.window.__TBL_PROTOCOL__;
assert.ok(P, '协议未暴露为 window.__TBL_PROTOCOL__');

const T1 = '2026-10-17T08:00:00+08:00';
const T2 = '2026-10-17T09:00:00+08:00';
const T3 = '2026-10-17T10:00:00+08:00';

function makeTodo(id, text, updated, extra) {
  return Object.assign(
    {
      id: id,
      text: text,
      priority: 'normal',
      done: false,
      created_at: T1,
      updated_at: updated || T1,
      remind_at: '',
      remind_date: '',
      notified_at: '',
      done_at: '',
    },
    extra || {}
  );
}
function makeDoc(todos, tombstones, version) {
  return P.normalizeDoc({
    schema: 2,
    version: version || 1,
    updated_at: T1,
    todos: todos || [],
    tombstones: tombstones || [],
  });
}

let passed = 0;
function test(name, fn) {
  try {
    fn();
    passed += 1;
    console.log('  ok   ' + name);
  } catch (error) {
    console.error('  FAIL ' + name + '\n       ' + (error && error.message));
    process.exitCode = 1;
  }
}
function canon(doc) {
  return P.canonical(doc);
}

console.log('\n[1] 文档模型');

test('旧字段 remind_time 归一化', () => {
  const todo = P.normalizeTodo({ id: 'a', text: ' 写周报 ', remind_time: '15:30', priority: 'HIGH' });
  assert.equal(todo.text, '写周报');
  assert.equal(todo.remind_at, '15:30');
  assert.equal(todo.priority, 'high');
});

test('非法时间/日期被丢弃', () => {
  const todo = P.normalizeTodo({ text: 'x', remind_at: '25:99', remind_date: '2026-13-40' });
  assert.equal(todo.remind_at, '');
  assert.equal(todo.remind_date, '');
});

test('空文本丢弃、重复 id 去重', () => {
  assert.equal(P.normalizeTodo({ text: '   ' }), null);
  const doc = makeDoc([makeTodo('b', 'b'), makeTodo('a', 'a'), makeTodo('a', 'dup')]);
  assert.equal(doc.todos.length, 2);
  assert.deepEqual(doc.todos.map((item) => item.id), ['a', 'b']);
});

test('canonical 忽略 version / updated_at', () => {
  const a = makeDoc([makeTodo('t1', '内容')], [], 1);
  const b = makeDoc([makeTodo('t1', '内容')], [], 9);
  assert.equal(canon(a), canon(b));
});

console.log('\n[2] 合并协议');

test('远端较新时远端胜出', () => {
  const merged = P.mergeDocs(
    makeDoc([makeTodo('t1', '旧内容', T1)]),
    makeDoc([makeTodo('t1', '新内容', T2)])
  );
  assert.equal(merged.todos[0].text, '新内容');
});

test('本地较新时本地胜出', () => {
  const merged = P.mergeDocs(
    makeDoc([makeTodo('t1', '本地新', T2)]),
    makeDoc([makeTodo('t1', '远端旧', T1)])
  );
  assert.equal(merged.todos[0].text, '本地新');
});

test('两端条目取并集', () => {
  const merged = P.mergeDocs(makeDoc([makeTodo('t1', '本地')]), makeDoc([makeTodo('t2', '远端')]));
  assert.deepEqual(merged.todos.map((item) => item.id).sort(), ['t1', 't2']);
});

test('墓碑删除较旧条目', () => {
  const merged = P.mergeDocs(
    makeDoc([makeTodo('t1', '待删', T1)]),
    makeDoc([], [{ id: 't1', deleted_at: T2 }])
  );
  assert.equal(merged.todos.length, 0);
});

test('删除之后的新编辑会复活条目', () => {
  const merged = P.mergeDocs(
    makeDoc([makeTodo('t1', '复活', T3)]),
    makeDoc([], [{ id: 't1', deleted_at: T2 }])
  );
  assert.deepEqual(merged.todos.map((item) => item.id), ['t1']);
  assert.equal(merged.tombstones.length, 0);
});

test('version 单调递增', () => {
  assert.equal(P.mergeDocs(makeDoc([], [], 3), makeDoc([], [], 7)).version, 8);
});

test('needsPush 判断', () => {
  const local = makeDoc([makeTodo('t1', '本地新增')]);
  const remote = makeDoc([]);
  assert.equal(P.needsPush(local, remote), true);
  assert.equal(P.needsPush(remote, remote), false);
});

test('删除操作产生墓碑', () => {
  const doc = P.addTodo(P.emptyDoc(), { text: '要删的' });
  const after = P.deleteTodo(doc, doc.todos[0].id);
  assert.equal(after.todos.length, 0);
  assert.equal(after.tombstones[0].id, doc.todos[0].id);
});

console.log('\n[3] 提醒与排序');

function isoDate(offsetDays) {
  // 注意：不要在 vm 沙箱外调用 P.toIso()——跨 realm 的 Date 不满足 instanceof，
  // 会被协议函数当成“非法输入”退回到当前时间。这里直接算本地日期字符串。
  const date = new Date();
  date.setDate(date.getDate() + (offsetDays || 0));
  const pad = (value) => String(value).padStart(2, '0');
  return date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate());
}

test('pendingTodos 紧急优先、提醒时间靠前', () => {
  let doc = P.emptyDoc();
  doc = P.addTodo(doc, { text: '普通-晚', priority: 'normal', remind_at: '20:00', remind_date: isoDate(0) });
  doc = P.addTodo(doc, { text: '紧急-晚', priority: 'high', remind_at: '19:00', remind_date: isoDate(0) });
  doc = P.addTodo(doc, { text: '紧急-早', priority: 'high', remind_at: '09:00', remind_date: isoDate(0) });
  doc = P.addTodo(doc, { text: '无提醒', priority: 'normal' });
  const texts = P.pendingTodos(doc).map((item) => item.text);
  assert.deepEqual(texts.slice(0, 2), ['紧急-早', '紧急-晚']);
  assert.equal(texts[texts.length - 1], '无提醒');
});

test('formatReminder：今天 / 明天', () => {
  const todayTodo = P.normalizeTodo({ id: 'x', text: 'x', remind_at: '15:30', remind_date: isoDate(0) });
  assert.equal(P.formatReminder(todayTodo), '今天 15:30');
  const tomorrowTodo = P.normalizeTodo({ id: 'y', text: 'y', remind_at: '09:00', remind_date: isoDate(1) });
  assert.equal(P.formatReminder(tomorrowTodo), '明天 09:00');
});

test('dueReminder：到点 / 未到点 / 已提醒 / 已完成', () => {
  const past = P.normalizeTodo({ id: 'p', text: 'p', remind_at: '00:00', remind_date: isoDate(0) });
  assert.equal(P.dueReminder(past), true);
  const future = P.normalizeTodo({ id: 'f', text: 'f', remind_at: '23:59', remind_date: isoDate(2) });
  assert.equal(P.dueReminder(future), false);
  const notified = P.normalizeTodo({
    id: 'n', text: 'n', remind_at: '00:00', remind_date: isoDate(0), notified_at: T2,
  });
  assert.equal(P.dueReminder(notified), false);
  const done = P.normalizeTodo({ id: 'd', text: 'd', done: true, remind_at: '00:00', remind_date: isoDate(0) });
  assert.equal(P.dueReminder(done), false);
});

test('setDone / updateTodo 语义', () => {
  let doc = P.addTodo(P.emptyDoc(), { text: '任务' });
  const id = doc.todos[0].id;
  doc = P.setDone(doc, id, true);
  assert.equal(doc.todos[0].done, true);
  assert.ok(doc.todos[0].done_at);
  doc = P.updateTodo(doc, id, { notified_at: T2 });
  assert.ok(doc.todos[0].notified_at);
  doc = P.setDone(doc, id, false);
  assert.equal(doc.todos[0].done, false);
  assert.equal(doc.todos[0].notified_at, '');
});

console.log('\n[4] 与 Python 协议的一致性锚点');

test('canonical 输出为键有序 JSON（可与 core.py sort_keys 对齐）', () => {
  const doc = makeDoc([makeTodo('t1', '内容')]);
  const text = P.canonical(doc);
  assert.ok(text.startsWith('{"todos":[{"created_at":'));
  assert.ok(!text.includes(' : ') && !text.includes(', '));
});

test('merge 对同一文档幂等', () => {
  const doc = makeDoc([makeTodo('t1', 'a'), makeTodo('t2', 'b')]);
  assert.equal(canon(P.mergeDocs(doc, doc)), canon(doc));
});

console.log('\n[5] 每日待办节奏（22:00 填明天 / 凌晨 2:00 翻篇）');

function localDate(year, month, day, hour, minute) {
  return new Date(year, month - 1, day, hour, minute, 0);
}

test('currentDay 凌晨 2 点前算前一天', () => {
  assert.equal(P.currentDay(localDate(2026, 10, 18, 1, 30)).getDate(), 17);
  assert.equal(P.currentDay(localDate(2026, 10, 18, 2, 0)).getDate(), 18);
  assert.equal(P.currentDay(localDate(2026, 10, 18, 21, 59)).getDate(), 18);
});

test('targetDay 22:00 后算明天', () => {
  assert.equal(P.targetDay(localDate(2026, 10, 17, 21, 59)).getDate(), 17);
  assert.equal(P.targetDay(localDate(2026, 10, 17, 22, 0)).getDate(), 18);
  assert.equal(P.targetDay(localDate(2026, 10, 18, 1, 30)).getDate(), 18);
});

test('todoDay 按创建时间归属', () => {
  const morning = P.normalizeTodo({
    id: 'a',
    text: '上午填的',
    created_at: '2026-10-17T10:00:00+08:00',
  });
  const evening = P.normalizeTodo({
    id: 'b',
    text: '晚上填的',
    created_at: '2026-10-17T22:30:00+08:00',
  });
  assert.equal(P.todoDay(morning).getDate(), 17);
  assert.equal(P.todoDay(evening).getDate(), 18);
});

test('fillReminderNeeded 22 点后明天没人填时提醒', () => {
  const empty = P.emptyDoc();
  assert.equal(P.fillReminderNeeded(empty, localDate(2026, 10, 17, 22, 5)), true);
  assert.equal(P.fillReminderNeeded(empty, localDate(2026, 10, 18, 10, 0)), false);
  const filled = P.addTodo(empty, { text: '明天的任务' });
  filled.todos[0].created_at = '2026-10-17T22:30:00+08:00';
  filled.todos[0].updated_at = filled.todos[0].created_at;
  assert.equal(P.fillReminderNeeded(filled, localDate(2026, 10, 17, 22, 5)), false);
});

console.log('\n[6] 部署资源与 DOM 引用静态检查');

test('所有 $("id") 引用的元素都存在于 HTML', () => {
  const htmlIds = new Set([...html.matchAll(/id="([^"]+)"/g)].map((match) => match[1]));
  const refs = [...source.matchAll(/\$\(\s*['"]([^'"]+)['"]\s*\)/g)].map((match) => match[1]);
  assert.ok(refs.length > 10, '未提取到足够的 DOM 引用');
  const missing = [...new Set(refs)].filter((id) => !htmlIds.has(id));
  assert.deepEqual(missing, []);
});

test('Service Worker 缓存清单中的文件都存在', () => {
  const swSource = fs.readFileSync(path.join(__dirname, '..', 'github-pages', 'sw.js'), 'utf8');
  const assets = [...swSource.matchAll(/'\.\/([^']*)'/g)]
    .map((match) => match[1])
    .filter((name) => name && !name.includes('*'));
  assert.ok(assets.length >= 4, '未提取到足够的缓存资源');
  const missing = assets.filter((name) => !fs.existsSync(path.join(__dirname, '..', 'github-pages', name)));
  assert.deepEqual(missing, []);
});

console.log('\n' + passed + ' 个前端协议用例通过' + (process.exitCode ? '（有失败）' : ''));

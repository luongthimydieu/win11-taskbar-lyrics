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
    schema: 3,
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

test('协议 v3：due_date / parent_id / estimate_minutes 归一化', () => {
  const todo = P.normalizeTodo({
    id: 'v3',
    text: '带目标日期的任务',
    due_date: '2026-10-25',
    parent_id: 'td-parent',
    estimate_minutes: '45',
  });
  assert.equal(todo.due_date, '2026-10-25');
  assert.equal(todo.parent_id, 'td-parent');
  assert.equal(todo.estimate_minutes, 45);
});

test('due_date 缺失或非法时回落到空串', () => {
  assert.equal(P.normalizeTodo({ text: 'x' }).due_date, '');
  assert.equal(P.normalizeTodo({ text: 'x', due_date: '' }).due_date, '');
  assert.equal(P.normalizeTodo({ text: 'x', due_date: '2026-13-01' }).due_date, '');
  assert.equal(P.normalizeTodo({ text: 'x', due_date: '2026-02-30' }).due_date, '');
  assert.equal(P.normalizeTodo({ text: 'x', due_date: '明天' }).due_date, '');
});

test('estimate_minutes 默认 0 且为非负整数', () => {
  assert.equal(P.normalizeTodo({ text: 'x' }).estimate_minutes, 0);
  assert.equal(P.normalizeTodo({ text: 'x', estimate_minutes: -5 }).estimate_minutes, 0);
  assert.equal(P.normalizeTodo({ text: 'x', estimate_minutes: 'abc' }).estimate_minutes, 0);
  assert.equal(P.normalizeTodo({ text: 'x', estimate_minutes: 12.9 }).estimate_minutes, 12);
  assert.equal(P.normalizeTodo({ text: 'x', estimate_minutes: '007' }).estimate_minutes, 7);
});

test('parent_id 统一为字符串（默认空串）', () => {
  assert.equal(P.normalizeTodo({ text: 'x' }).parent_id, '');
  assert.equal(P.normalizeTodo({ text: 'x', parent_id: '' }).parent_id, '');
  assert.equal(P.normalizeTodo({ text: 'x', parent_id: 123 }).parent_id, '123');
  assert.equal(P.normalizeTodo({ text: 'x', parent_id: '  td-abc  ' }).parent_id, 'td-abc');
});

test('parent_id 兼容 parentId 别名并防御自引用', () => {
  assert.equal(P.normalizeTodo({ id: 'td-1', text: 'x', parentId: 'td-p' }).parent_id, 'td-p');
  assert.equal(P.normalizeTodo({ id: 'td-1', text: 'x', parent_id: 'td-1' }).parent_id, '');
});

test('estimate_minutes 兼容 estimateMinutes 别名', () => {
  assert.equal(P.normalizeTodo({ id: 'e1', text: 'x', estimateMinutes: 25 }).estimate_minutes, 25);
  // 与 Python 一致：estimate_minutes 为 None 时才看别名
  assert.equal(P.normalizeTodo({ id: 'e2', text: 'x', estimate_minutes: null, estimateMinutes: 25 }).estimate_minutes, 25);
  assert.equal(P.normalizeTodo({ id: 'e3', text: 'x', estimate_minutes: 0, estimateMinutes: 25 }).estimate_minutes, 0);
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

test('addTodo 写入 due_date / parent_id / estimate_minutes', () => {
  let doc = P.addTodo(P.emptyDoc(), { text: '父任务' });
  const parentId = doc.todos[0].id;
  doc = P.addTodo(doc, { text: '子任务', parent_id: parentId, due_date: isoDate(3), estimate_minutes: 30 });
  const child = doc.todos.find((item) => item.text === '子任务');
  assert.equal(child.parent_id, parentId);
  assert.equal(child.due_date, isoDate(3));
  assert.equal(child.estimate_minutes, 30);
});

test('updateTodo 可修改/清空 due_date 与 estimate_minutes', () => {
  let doc = P.addTodo(P.emptyDoc(), { text: '任务', due_date: isoDate(2), estimate_minutes: 20 });
  const id = doc.todos[0].id;
  doc = P.updateTodo(doc, id, { due_date: isoDate(5), estimate_minutes: 40 });
  assert.equal(doc.todos[0].due_date, isoDate(5));
  assert.equal(doc.todos[0].estimate_minutes, 40);
  doc = P.updateTodo(doc, id, { due_date: '', estimate_minutes: -1 });
  assert.equal(doc.todos[0].due_date, '');
  assert.equal(doc.todos[0].estimate_minutes, 0);
  // 与 core.update_todo 一致：parent_id 指向自己时清空
  doc = P.updateTodo(doc, id, { parent_id: id });
  assert.equal(doc.todos[0].parent_id, '');
});

test('v3 字段参与合并（按 updated_at 取新）', () => {
  const merged = P.mergeDocs(
    makeDoc([makeTodo('t1', '任务', T1, { due_date: '2026-10-20', estimate_minutes: 10 })]),
    makeDoc([makeTodo('t1', '任务', T2, { due_date: '2026-10-25', parent_id: 'td-p', estimate_minutes: 30 })])
  );
  assert.equal(merged.todos[0].due_date, '2026-10-25');
  assert.equal(merged.todos[0].parent_id, 'td-p');
  assert.equal(merged.todos[0].estimate_minutes, 30);
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

test('canonical 键序包含 v4 字段（done_note/done_reflection/due_date 等字母序）', () => {
  const doc = makeDoc([makeTodo('t1', '内容')]);
  const text = P.canonical(doc);
  assert.ok(text.includes('"done_at":"","done_note":"","done_reflection":"","due_date":"","estimate_minutes":0,"id":"t1"'));
  assert.ok(text.includes('"notified_at":"","parent_id":"","priority":"normal"'));
  assert.ok(text.indexOf('"parent_id"') < text.indexOf('"priority"'));
  assert.ok(text.indexOf('"due_date"') < text.indexOf('"estimate_minutes"'));
  assert.ok(text.indexOf('"estimate_minutes"') < text.indexOf('"id"'));
});

test('文档 schema 升到 v4', () => {
  assert.equal(P.SCHEMA_VERSION, 4);
  assert.equal(P.emptyDoc().schema, 4);
  assert.equal(P.normalizeDoc({ schema: 3 }).schema, 4);
  assert.equal(P.mergeDocs(P.emptyDoc(), P.emptyDoc()).schema, 4);
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

test('todoDay 优先使用 due_date（与 core.todo_day 一致）', () => {
  const todo = P.normalizeTodo({
    id: 'd1',
    text: '有目标日期',
    created_at: '2026-10-17T10:00:00+08:00',
    due_date: '2026-10-25',
  });
  const base = localDate(2026, 10, 20, 12, 0);
  assert.equal(P.dayText(P.todoDay(todo), base), '10-25');
  const noDue = P.normalizeTodo({ id: 'd2', text: '无目标日期', created_at: '2026-10-17T10:00:00+08:00' });
  assert.equal(P.dayText(P.todoDay(noDue), base), '10-17');
});

test('formatDueDate：今天 / 明天 / MM-DD / 逾期', () => {
  const base = new Date(2026, 9, 20, 12, 0, 0); // 2026-10-20
  const withDue = (due, done) =>
    P.normalizeTodo({ id: 'x', text: 'x', due_date: due, done: Boolean(done) });
  assert.equal(P.formatDueDate(withDue('2026-10-20'), base), '今天');
  assert.equal(P.formatDueDate(withDue('2026-10-21'), base), '明天');
  assert.equal(P.formatDueDate(withDue('2026-10-25'), base), '10-25');
  assert.equal(P.formatDueDate(withDue('2026-10-19'), base), '逾期');
  assert.equal(P.formatDueDate(withDue('2026-10-19', true), base), '10-19');
  assert.equal(P.formatDueDate(P.normalizeTodo({ id: 'y', text: 'y' }), base), '');
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

console.log('\n[6] 账本协议（独立 Gist taskbar_ledger.json）');

test('账本条目归一化与默认值', () => {
  const entry = P.normalizeLedgerEntry({
    id: 'lg-1',
    date: '2026-10-20',
    type: 'income',
    amount: '12.5',
    category: ' 工资 ',
    note: '  月薪  ',
  });
  assert.equal(entry.type, 'income');
  assert.equal(entry.amount, 12.5);
  assert.equal(entry.category, '工资');
  assert.equal(entry.note, '月薪');
  const fallback = P.normalizeLedgerEntry({});
  assert.equal(fallback.type, 'expense');
  assert.equal(fallback.amount, 0);
  // 与 core.normalize_ledger_entry 一致：分类缺失保留空串（新增账目时再兜底「其他」）
  assert.equal(fallback.category, '');
  assert.equal(fallback.date, P.currentDayText());
  assert.equal(P.normalizeLedgerEntry({ amount: -8 }).amount, 0);
  assert.equal(P.normalizeLedgerEntry({ amount: 'abc' }).amount, 0);
  // v1.3.3：账目时间归一化（与 core.normalize_ledger_time 一致）
  assert.equal(P.normalizeLedgerEntry({ amount: 1, time: '9:05' }).time, '09:05');
  assert.equal(
    P.normalizeLedgerEntry({ amount: 1, created_at: '2026-10-17T21:34:00+08:00' }).time,
    '21:34'
  );
  assert.equal(
    P.normalizeLedgerEntry({ amount: 1, time: '25:99', created_at: '2026-10-17T07:08:00+08:00' }).time,
    '07:08'
  );
});

test('账本日期非法时回落到当天', () => {
  const entry = P.normalizeLedgerEntry({ id: 'lg-d', date: '2026-02-30', amount: 3, category: '交通' });
  assert.equal(entry.date, P.currentDayText());
  const noDate = P.normalizeLedgerEntry({ id: 'lg-n', amount: 3 });
  assert.equal(noDate.date, P.currentDayText());
});

test('账本 canonical 稳定且键有序', () => {
  const raw = {
    entries: [{
      id: 'lg-b',
      date: '2026-10-20',
      type: 'expense',
      amount: 5,
      category: '餐饮',
      note: '',
      created_at: T1,
      updated_at: T1,
    }],
    tombstones: [],
  };
  const a = P.normalizeLedger(Object.assign({ version: 1, updated_at: T1 }, raw));
  const b = P.normalizeLedger(Object.assign({ version: 9, updated_at: T2 }, raw));
  assert.equal(P.canonicalLedger(a), P.canonicalLedger(b));
  assert.equal(P.canonicalLedger(P.emptyLedger()), '{"entries":[],"tombstones":[]}');
  assert.ok(P.canonicalLedger(a).startsWith('{"entries":[{"amount":'));
  assert.ok(!P.canonicalLedger(a).includes(' : '));
  // 金额按 Python float 风格输出（100 → 100.0），保证与 core.canonical_ledger 对齐
  assert.ok(P.canonicalLedger(a).includes('"amount":5.0,'));
  // v1.3.3：time 字段参与 canonical（键序与 Python sort_keys 一致，位于 note 之后）
  assert.ok(/"time":"\d{2}:\d{2}"/.test(P.canonicalLedger(a)));
  assert.ok(P.canonicalLedger(a).indexOf('"time"') < P.canonicalLedger(a).indexOf('"type"'));
});

test('账本 CRUD：新增 / 修改 / 删除生成墓碑', () => {
  let ledger = P.addLedgerEntry(P.emptyLedger(), {
    date: '2026-10-20', type: 'expense', amount: 25.5, category: '餐饮', note: '午饭',
  });
  assert.equal(ledger.entries.length, 1);
  assert.equal(ledger.version, 1);
  const id = ledger.entries[0].id;
  ledger = P.updateLedgerEntry(ledger, id, { amount: 30, note: '晚饭' });
  assert.equal(ledger.entries[0].amount, 30);
  assert.equal(ledger.entries[0].note, '晚饭');
  ledger = P.deleteLedgerEntry(ledger, id);
  assert.equal(ledger.entries.length, 0);
  assert.equal(ledger.tombstones.length, 1);
  assert.equal(ledger.tombstones[0].id, id);
});

test('addLedgerEntry 分类为空时兜底为「其他」', () => {
  const ledger = P.addLedgerEntry(P.emptyLedger(), {
    date: '2026-10-20', type: 'expense', amount: 9, category: '  ',
  });
  assert.equal(ledger.entries[0].category, P.LEDGER_DEFAULT_CATEGORY);
});

test('ledgerWeekSummary：本周收入 / 支出 / 结余与分类', () => {
  let ledger = P.emptyLedger();
  ledger = P.addLedgerEntry(ledger, { date: '2026-10-20', type: 'income', amount: 100, category: '工资' });
  ledger = P.addLedgerEntry(ledger, { date: '2026-10-21', type: 'expense', amount: 30.5, category: '餐饮' });
  ledger = P.addLedgerEntry(ledger, { date: '2026-10-18', type: 'expense', amount: 9, category: '上周' });
  const summary = P.ledgerWeekSummary(ledger, '2026-10-19');
  assert.equal(summary.income, 100);
  assert.equal(summary.expense, 30.5);
  assert.equal(summary.net, 69.5);
  assert.equal(summary.entries.length, 2);
  assert.deepEqual(summary.entries.map((entry) => entry.date), ['2026-10-20', '2026-10-21']);
  assert.equal(summary.by_category['餐饮'].expense, 30.5);
  assert.equal(summary.by_category['工资'].income, 100);
});

test('mergeLedgers：较新条目胜出、墓碑删除生效', () => {
  const base = {
    entries: [{
      id: 'lg-a', date: '2026-10-20', type: 'expense', amount: 5, category: '零食', note: '',
    }],
    tombstones: [],
  };
  const older = P.normalizeLedger(Object.assign({ version: 1, updated_at: T1 }, JSON.parse(JSON.stringify(base))));
  older.entries[0].created_at = T1;
  older.entries[0].updated_at = T1;
  const newer = P.normalizeLedger(Object.assign({ version: 2, updated_at: T2 }, JSON.parse(JSON.stringify(base))));
  newer.entries[0].updated_at = T2;
  const merged = P.mergeLedgers(older, newer);
  assert.equal(merged.entries.length, 1);
  assert.equal(merged.entries[0].updated_at, T2);
  const withTomb = P.normalizeLedger({
    version: 3,
    updated_at: T3,
    entries: [],
    tombstones: [{ id: 'lg-a', deleted_at: T3 }],
  });
  assert.equal(P.mergeLedgers(merged, withTomb).entries.length, 0);
});

test('formatMoney 输出 ¥ 前缀两位小数', () => {
  assert.equal(P.formatMoney(12.5), '¥12.50');
  assert.equal(P.formatMoney(0), '¥0.00');
  assert.equal(P.formatMoney(1234.5), '¥1234.50');
});

console.log('\n[7] 部署资源与 DOM 引用静态检查');

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

test('Service Worker 缓存版本升到 tbl-shell-v8', () => {
  const swSource = fs.readFileSync(path.join(__dirname, '..', 'github-pages', 'sw.js'), 'utf8');
  assert.ok(swSource.includes("const CACHE_NAME = 'tbl-shell-v8'"), 'CACHE_NAME 未升级到 tbl-shell-v8');
});

console.log('\n' + passed + ' 个前端协议用例通过' + (process.exitCode ? '（有失败）' : ''));

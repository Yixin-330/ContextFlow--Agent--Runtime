from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    from .demo_control import DemoControl
    from .runtime import ContextFlowRuntime
    from .storage import Storage
except ImportError:  # pragma: no cover - direct script launcher.
    from demo_control import DemoControl
    from runtime import ContextFlowRuntime
    from storage import Storage


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ContextFlowRuntime(Storage(ROOT / "data" / "contextflow.db"))
DEMO = DemoControl(ROOT, RUNTIME.store, RUNTIME)


def _page() -> str:
    return """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ContextFlow 工作台</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #07100f;
      --panel: rgba(15, 28, 27, .92);
      --panel-soft: rgba(20, 38, 36, .72);
      --line: rgba(132, 183, 169, .18);
      --text: #ecf7f3;
      --muted: #91aaa3;
      --mint: #68efbd;
      --cyan: #62d9ef;
      --amber: #ffc96b;
      --danger: #ff8b86;
      --shadow: 0 24px 70px rgba(0, 0, 0, .34);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      color: var(--text);
      font: 14px/1.55 Inter, "Segoe UI", "Microsoft YaHei", system-ui, sans-serif;
      background:
        radial-gradient(circle at 12% 0%, rgba(39, 161, 119, .18), transparent 34%),
        radial-gradient(circle at 92% 8%, rgba(56, 147, 175, .13), transparent 28%),
        var(--bg);
    }
    body::before {
      content: "";
      position: fixed;
      inset: 0;
      pointer-events: none;
      opacity: .16;
      background-image: linear-gradient(rgba(255,255,255,.025) 1px, transparent 1px), linear-gradient(90deg, rgba(255,255,255,.025) 1px, transparent 1px);
      background-size: 36px 36px;
    }
    main { width: min(1440px, calc(100% - 40px)); margin: 0 auto; padding: 26px 0 48px; position: relative; }
    header { display: flex; align-items: center; justify-content: space-between; gap: 20px; margin-bottom: 24px; }
    .brand { display: flex; align-items: center; gap: 13px; }
    .brand-mark { width: 42px; height: 42px; border-radius: 13px; display: grid; place-items: center; color: #06120e; font-weight: 900; font-size: 20px; background: linear-gradient(135deg, var(--mint), #b8ffe5); box-shadow: 0 10px 30px rgba(104,239,189,.18); }
    h1 { margin: 0; font-size: 21px; letter-spacing: -.02em; }
    .subtitle { color: var(--muted); font-size: 12px; margin-top: 2px; }
    .top-status { display: flex; align-items: center; gap: 12px; color: var(--muted); }
    .live { border: 1px solid rgba(104,239,189,.26); color: var(--mint); background: rgba(104,239,189,.08); padding: 7px 11px; border-radius: 999px; }
    .live::before { content: ""; width: 7px; height: 7px; border-radius: 50%; display: inline-block; background: var(--mint); margin-right: 7px; box-shadow: 0 0 12px var(--mint); }
    .follow-latest.active { color: var(--mint); border-color: rgba(104,239,189,.38); background: rgba(104,239,189,.09); }
    .layout { display: grid; grid-template-columns: 330px minmax(0, 1fr); gap: 18px; }
    .stack { display: grid; gap: 18px; align-content: start; }
    .panel { border: 1px solid var(--line); border-radius: 18px; background: linear-gradient(145deg, rgba(21,38,36,.96), rgba(10,22,21,.9)); box-shadow: var(--shadow); overflow: hidden; }
    .panel-head { padding: 16px 18px 12px; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--line); }
    .panel-head h2 { margin: 0; font-size: 13px; font-weight: 650; letter-spacing: .04em; color: #cfe2dc; }
    .panel-body { padding: 18px; }
    .state-name { color: var(--mint); font-weight: 760; font-size: clamp(27px, 3vw, 40px); letter-spacing: -.045em; word-break: break-word; }
    .state-label { color: #d5e8e1; margin-top: 2px; }
    .confidence { margin: 20px 0; }
    .confidence-row { display: flex; justify-content: space-between; color: var(--muted); font-size: 12px; margin-bottom: 7px; }
    .track { height: 7px; background: rgba(255,255,255,.07); border-radius: 99px; overflow: hidden; }
    .track span { display: block; height: 100%; width: 0; background: linear-gradient(90deg, #27bc86, var(--mint)); transition: width .5s ease; }
    .eyebrow { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .12em; margin-bottom: 9px; }
    .evidence-list, .boundary-list, .draft-items { list-style: none; padding: 0; margin: 0; display: grid; gap: 8px; }
    .evidence-list li { padding: 9px 10px; border-radius: 10px; background: rgba(255,255,255,.035); color: #c8dbd5; font-size: 12px; }
    .evidence-list li::before { content: "◆"; color: var(--cyan); margin-right: 8px; font-size: 8px; }
    .source-badge, .status-badge { border-radius: 999px; padding: 4px 9px; font-size: 11px; border: 1px solid var(--line); color: var(--muted); white-space: nowrap; }
    .source-badge.real_file { color: var(--mint); border-color: rgba(104,239,189,.28); background: rgba(104,239,189,.07); }
    .source-badge.simulation { color: var(--amber); border-color: rgba(255,201,107,.28); background: rgba(255,201,107,.07); }
    .source-detail { color: #c9dcd6; font-size: 12px; word-break: break-all; }
    .source-detail strong { display: block; font-size: 14px; color: var(--text); margin: 8px 0 4px; word-break: break-word; }
    .timeline-grid { display: grid; grid-template-columns: minmax(0, .9fr) minmax(0, 1.1fr); }
    .timeline-column { padding: 18px; min-width: 0; }
    .timeline-column + .timeline-column { border-left: 1px solid var(--line); }
    .column-title { margin: 0 0 18px; font-size: 15px; }
    .timeline { display: grid; gap: 0; }
    .timeline-item { display: grid; grid-template-columns: 22px minmax(0,1fr); gap: 10px; min-height: 70px; }
    .timeline-rail { display: flex; align-items: center; flex-direction: column; }
    .timeline-dot { width: 10px; height: 10px; border-radius: 50%; background: var(--cyan); box-shadow: 0 0 0 4px rgba(98,217,239,.08); margin-top: 5px; flex: none; }
    .timeline-line { width: 1px; flex: 1; background: var(--line); margin: 7px 0; }
    .timeline-item:last-child .timeline-line { display: none; }
    .timeline-title { color: #e5f2ee; font-weight: 650; display: flex; justify-content: space-between; gap: 8px; }
    .timeline-meta { color: var(--muted); font-size: 11px; margin: 4px 0 7px; }
    .timeline-reason { color: #b6cbc4; font-size: 12px; }
    .agent-item .timeline-dot { background: var(--mint); box-shadow: 0 0 0 4px rgba(104,239,189,.08); }
    .status-badge.completed, .status-badge.planned { color: var(--mint); }
    .status-badge.no_evidence, .status-badge.suggested { color: var(--amber); }
    .draft-grid { display: grid; grid-template-columns: repeat(2, minmax(0,1fr)); gap: 14px; }
    .draft-card { background: var(--panel-soft); border: 1px solid var(--line); border-radius: 15px; padding: 16px; min-width: 0; }
    .draft-top { display: flex; justify-content: space-between; gap: 12px; }
    .draft-kind { color: var(--cyan); font-size: 11px; letter-spacing: .08em; }
    .draft-title { font-size: 17px; font-weight: 680; margin: 4px 0 9px; }
    .draft-summary { color: #b9cec7; font-size: 12px; min-height: 38px; }
    .draft-items { margin-top: 13px; }
    .draft-items li { display: flex; justify-content: space-between; gap: 10px; border-top: 1px solid var(--line); padding-top: 8px; color: #d6e6e1; font-size: 12px; }
    .draft-items span:last-child { color: var(--muted); white-space: nowrap; }
    .review-note { display: flex; align-items: center; gap: 8px; color: var(--amber); font-size: 11px; margin-top: 14px; }
    .control-panel { margin-bottom: 18px; padding: 16px 18px; display: flex; align-items: center; justify-content: space-between; gap: 18px; }
    .control-copy strong { display: block; font-size: 14px; margin-bottom: 3px; }
    .control-copy span { color: var(--muted); font-size: 12px; }
    .control-actions, .card-actions { display: flex; flex-wrap: wrap; gap: 8px; }
    .state-lab { margin-bottom: 18px; }
    .state-grid { display: grid; grid-template-columns: repeat(6, minmax(0, 1fr)); gap: 9px; }
    .state-demo { min-height: 72px; text-align: left; display: flex; flex-direction: column; justify-content: space-between; }
    .state-demo strong { font-size: 12px; color: #e2f0eb; }
    .state-demo span { color: var(--muted); font-size: 10px; }
    .state-demo.active { border-color: var(--mint); background: rgba(104,239,189,.1); box-shadow: inset 0 0 0 1px rgba(104,239,189,.16); }
    button { border: 1px solid var(--line); border-radius: 10px; padding: 9px 13px; color: #dcece7; background: rgba(255,255,255,.045); font: inherit; font-size: 12px; cursor: pointer; transition: .18s ease; }
    button:hover:not(:disabled) { transform: translateY(-1px); border-color: rgba(104,239,189,.42); background: rgba(104,239,189,.09); }
    button:disabled { opacity: .42; cursor: wait; }
    button.primary { color: #06120e; border-color: transparent; font-weight: 720; background: linear-gradient(135deg, var(--mint), #a9f8dc); }
    button.warn { color: var(--amber); }
    button.danger { color: var(--danger); }
    .card-actions { margin-top: 14px; }
    .card-actions button { flex: 1; padding: 7px 9px; }
    .health-strip { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
    .health-chip { color: var(--muted); font-size: 11px; border: 1px solid var(--line); border-radius: 999px; padding: 5px 9px; }
    .health-chip.healthy { color: var(--mint); border-color: rgba(104,239,189,.28); }
    .health-chip.attention { color: var(--amber); border-color: rgba(255,201,107,.28); }
    .demo-progress { display: grid; grid-template-columns: repeat(5, 1fr); gap: 8px; margin-top: 13px; }
    .progress-step { color: #67827a; font-size: 10px; text-align: center; padding-top: 16px; position: relative; }
    .progress-step::before { content: ""; position: absolute; top: 2px; left: calc(50% - 5px); width: 10px; height: 10px; border-radius: 50%; background: #324c45; }
    .progress-step.completed { color: #bbd9cf; }
    .progress-step.completed::before { background: var(--mint); box-shadow: 0 0 10px rgba(104,239,189,.35); }
    .progress-step.current { color: var(--amber); }
    .progress-step.current::before { background: var(--amber); box-shadow: 0 0 10px rgba(255,201,107,.35); }
    .assistant-row { display: flex; gap: 9px; }
    .assistant-row input { min-width: 0; flex: 1; color: var(--text); border: 1px solid var(--line); border-radius: 10px; padding: 10px 12px; background: rgba(0,0,0,.16); outline: none; }
    .assistant-row input:focus { border-color: rgba(104,239,189,.45); }
    .answer { margin-top: 12px; color: #c7dcd5; border-left: 2px solid var(--mint); padding: 8px 11px; background: rgba(104,239,189,.035); min-height: 38px; }
    .modal-backdrop { display: none; position: fixed; inset: 0; z-index: 20; padding: 24px; background: rgba(0,0,0,.68); backdrop-filter: blur(8px); align-items: center; justify-content: center; }
    .modal-backdrop.open { display: flex; }
    .modal { width: min(720px, 100%); max-height: min(820px, calc(100vh - 48px)); overflow: auto; border: 1px solid var(--line); border-radius: 18px; padding: 21px; background: #10201e; box-shadow: 0 30px 100px rgba(0,0,0,.56); }
    .modal-head { display: flex; justify-content: space-between; gap: 20px; align-items: flex-start; }
    .modal h2 { margin: 3px 0 5px; }
    .modal-section { margin-top: 17px; padding-top: 14px; border-top: 1px solid var(--line); }
    .modal-section h3 { color: var(--muted); font-size: 11px; letter-spacing: .1em; text-transform: uppercase; }
    .modal-section pre { white-space: pre-wrap; color: #cfe1dc; font: 12px/1.6 inherit; }
    .toast { position: fixed; right: 22px; bottom: 22px; z-index: 30; max-width: 400px; color: #dff3ec; border: 1px solid var(--line); border-radius: 12px; padding: 12px 15px; background: #18312c; box-shadow: var(--shadow); opacity: 0; transform: translateY(12px); pointer-events: none; transition: .25s ease; }
    .toast.show { opacity: 1; transform: translateY(0); }
    .toast.error { color: #ffd2cf; border-color: rgba(255,139,134,.3); background: #341d1d; }
    .boundary-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
    .boundary-box { border: 1px solid var(--line); border-radius: 13px; padding: 13px; background: rgba(255,255,255,.025); }
    .boundary-box h3 { margin: 0 0 8px; font-size: 12px; }
    .boundary-box.auto h3 { color: var(--mint); }
    .boundary-box.confirm h3 { color: var(--amber); }
    .boundary-list li { color: #b8ccc6; font-size: 11px; }
    .empty { color: var(--muted); padding: 18px 0; text-align: center; border: 1px dashed var(--line); border-radius: 12px; }
    .error { display: none; margin-bottom: 15px; padding: 10px 13px; color: #ffd2cf; background: rgba(255,139,134,.09); border: 1px solid rgba(255,139,134,.25); border-radius: 10px; }
    footer { color: #658079; text-align: center; font-size: 11px; margin-top: 20px; }
    @media (max-width: 1100px) { .state-grid { grid-template-columns: repeat(3, 1fr); } }
    @media (max-width: 980px) { .layout { grid-template-columns: 1fr; } .stack { grid-template-columns: 1fr 1fr; } .control-panel { align-items: flex-start; flex-direction: column; } }
    @media (max-width: 720px) { main { width: min(100% - 24px, 1440px); padding-top: 16px; } header { align-items: flex-start; } .top-status { flex-direction: column; align-items: flex-end; } .stack, .timeline-grid, .draft-grid, .boundary-grid { grid-template-columns: 1fr; } .timeline-column + .timeline-column { border-left: 0; border-top: 1px solid var(--line); } .demo-progress { grid-template-columns: 1fr; } .progress-step { text-align: left; padding: 4px 0 4px 22px; } .progress-step::before { left: 2px; top: 7px; } }
  </style>
</head>
<body>
<main>
  <header>
    <div class="brand"><div class="brand-mark">C</div><div><h1>ContextFlow 工作台</h1><div class="subtitle">事件感知 · 状态驱动 · 确认后执行</div></div></div>
    <div class="top-status"><div class="health-strip"><span id="watcher-health" class="health-chip attention">监听器：检查中</span><span id="calendar-health" class="health-chip attention">Outlook：检查中</span><span id="database-health" class="health-chip">事件库：检查中</span></div><span id="session">等待事件</span><button id="follow-latest" class="follow-latest active">正在跟随最新操作</button><span class="live">工作台已连接</span></div>
  </header>
  <div id="error" class="error"></div>
  <section class="panel control-panel">
    <div class="control-copy"><strong>比赛演示控制</strong><span id="demo-message">启动文件监听器后，可在这里完成整条会议交付演示。</span><div id="demo-progress" class="demo-progress"></div></div>
    <div class="control-actions"><button id="start-demo" class="primary">开始新的会议演示</button><button id="duplicate-demo">测试幂等保护</button><button id="reset-demo" class="danger">重置本次演示</button></div>
  </section>
  <section class="panel state-lab">
    <div class="panel-head"><h2>用户状态演示实验台</h2><span class="source-badge simulation">模拟信号 · 真实状态引擎</span></div>
    <div class="panel-body state-grid">
      <button class="state-demo" data-scenario="meeting_upcoming"><strong>会议即将开始</strong><span>meeting_upcoming</span></button>
      <button class="state-demo" data-scenario="meeting_window_active"><strong>进入会议窗口</strong><span>meeting_window_active</span></button>
      <button class="state-demo" data-scenario="meeting_ended"><strong>会议时间结束</strong><span>meeting_ended</span></button>
      <button class="state-demo" data-scenario="schedule_conflict"><strong>日程发生冲突</strong><span>schedule_conflict</span></button>
      <button class="state-demo" data-scenario="focus_mode"><strong>进入专注模式</strong><span>focus_mode</span></button>
      <button class="state-demo" data-scenario="data_ready_for_analysis"><strong>数据已准备分析</strong><span>data_ready_for_analysis</span></button>
    </div>
  </section>
  <div class="layout">
    <aside class="stack">
      <section class="panel">
        <div class="panel-head"><h2>当前用户状态</h2><span id="updated" class="status-badge">尚未更新</span></div>
        <div class="panel-body">
          <div id="state-name" class="state-name">idle</div>
          <div id="state-label" class="state-label">等待真实工作事件</div>
          <div class="confidence"><div class="confidence-row"><span>判断置信度</span><strong id="confidence-text">—</strong></div><div class="track"><span id="confidence-bar"></span></div></div>
          <div class="eyebrow">判断证据</div><ul id="evidence" class="evidence-list"></ul>
        </div>
      </section>
      <section class="panel">
        <div class="panel-head"><h2>最近感知来源</h2><span id="source-badge" class="source-badge">等待事件</span></div>
        <div id="source-detail" class="panel-body source-detail">监听到新事件后将在这里显示真实来源。</div>
      </section>
      <section class="panel">
        <div class="panel-head"><h2>确认边界</h2></div>
        <div class="panel-body boundary-grid"><div class="boundary-box auto"><h3>✓ 可自动准备</h3><ul id="automatic" class="boundary-list"></ul></div><div class="boundary-box confirm"><h3>○ 需要用户确认</h3><ul id="requires-confirmation" class="boundary-list"></ul></div></div>
      </section>
    </aside>
    <div class="stack">
      <section class="panel">
        <div class="panel-head"><h2>实时感知与 Agent 执行</h2><span class="status-badge">每 2 秒刷新</span></div>
        <div class="timeline-grid">
          <div class="timeline-column"><h3 class="column-title">事件与状态时间线</h3><div id="event-timeline" class="timeline"></div></div>
          <div class="timeline-column"><h3 class="column-title">Agent 协作轨迹</h3><div id="agent-timeline" class="timeline"></div></div>
        </div>
      </section>
      <section class="panel">
        <div class="panel-head"><h2>待确认工作草稿</h2><span id="draft-count" class="status-badge">0 份</span></div>
        <div class="panel-body"><div id="drafts" class="draft-grid"></div></div>
      </section>
      <section class="panel">
        <div class="panel-head"><h2>询问当前工作状态</h2><span class="status-badge">基于事件事实</span></div>
        <div class="panel-body"><div class="assistant-row"><input id="question" placeholder="例如：为什么判断存在截止风险？"><button id="ask" class="primary">询问</button></div><div id="answer" class="answer">可以询问状态原因、Agent 执行、工作草稿和确认边界。</div></div>
      </section>
    </div>
  </div>
  <footer>所有外部写入均由用户确认 · ContextFlow local-first runtime</footer>
</main>
<div id="draft-modal" class="modal-backdrop"><div class="modal"><div class="modal-head"><div><div id="modal-kind" class="draft-kind"></div><h2 id="modal-title"></h2><span id="modal-status" class="status-badge"></span></div><button id="close-modal">关闭</button></div><div id="modal-content"></div><div id="modal-actions" class="card-actions"></div></div></div>
<div id="toast" class="toast"></div>
<script>
  const $ = (id) => document.getElementById(id);
  const urlParams = new URLSearchParams(window.location.search);
  const baseSession = urlParams.get("base_session_key") || "competition-demo";
  let activeSession = urlParams.get("session_key") || null;
  let activeView = urlParams.get("session_key") ? "explicit" : "live";
  let currentSnapshot = null;
  let currentDraft = null;
  let toastTimer = null;
  const stateLabels = { idle: "等待真实工作事件", focus_mode: "用户正在专注工作", meeting_upcoming: "会议即将在 15 分钟内开始", meeting_window_active: "当前处于计划会议时间窗口", schedule_conflict: "日历会议时间存在冲突", meeting_ended: "会议已结束，正在整理后续", deadline_risk: "交付期限存在风险", data_ready_for_analysis: "数据已准备分析" };
  const eventLabels = { "document.meeting_notes_imported": "发现会议纪要", "data.file_ready": "发现数据文件", "calendar.meeting_upcoming": "Outlook 会议即将开始", "calendar.meeting_window_active": "进入 Outlook 会议时间窗口", "calendar.schedule_conflict": "发现日历时间冲突", "calendar.meeting_cancelled": "Outlook 会议已取消", "calendar.meeting_ended": "会议时间窗口结束", "calendar.meeting_started": "会议开始事件", "calendar.deadline_changed": "截止时间发生变化", "desktop.file_changed": "桌面文件发生变化" };
  const sourceLabels = { real_calendar: "真实 Outlook 日历", real_file: "真实文件感知", simulation: "演示模拟", direct_request: "直接请求", external_event: "外部事件" };
  const agentLabels = { MeetingActionAgent: "会议行动 Agent", LocalRAGAgent: "本地检索 Agent", DeliveryPlannerAgent: "交付规划 Agent" };
  const statusLabels = { completed: "已完成", planned: "已规划", no_evidence: "无本地证据", no_local_evidence: "无本地证据", suggested: "建议" };
  const formatTime = (value) => { if (!value) return "—"; const date = new Date(value); return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", second: "2-digit" }); };
  const node = (tag, className, text) => { const el = document.createElement(tag); if (className) el.className = className; if (text !== undefined) el.textContent = text; return el; };
  const fillList = (target, items, emptyText) => { target.replaceChildren(); const values = Array.isArray(items) && items.length ? items : [emptyText]; values.forEach((value) => target.appendChild(node("li", "", String(value)))); };
  async function api(path, payload) {
    const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
    return result;
  }
  function showToast(message, isError = false) {
    const toast = $("toast"); toast.textContent = message; toast.className = `toast show${isError ? " error" : ""}`;
    clearTimeout(toastTimer); toastTimer = setTimeout(() => { toast.className = "toast"; }, 3600);
  }
  function setActionBusy(busy) { ["start-demo", "duplicate-demo", "reset-demo", "ask"].forEach((id) => { $(id).disabled = busy; }); }
  function selectedSession() { return activeSession || currentSnapshot?.session_key || `${baseSession}:meetings`; }
  function timelineItem(title, meta, reason, kind, status) {
    const item = node("div", `timeline-item ${kind || ""}`); const rail = node("div", "timeline-rail"); rail.append(node("span", "timeline-dot"), node("span", "timeline-line"));
    const body = node("div", "timeline-content"); const titleRow = node("div", "timeline-title"); titleRow.appendChild(node("span", "", title));
    if (status) titleRow.appendChild(node("span", `status-badge ${status}`, statusLabels[status] || status));
    body.append(titleRow, node("div", "timeline-meta", meta), node("div", "timeline-reason", reason)); item.append(rail, body); return item;
  }
  function renderEvents(snapshot) {
    const target = $("event-timeline"); target.replaceChildren(); const rows = [];
    (snapshot.sensed_events || []).slice(0, 4).forEach((event) => rows.push({ time: event.occurred_at, title: eventLabels[event.event_type] || event.event_type, meta: `${formatTime(event.occurred_at)} · ${event.source}`, reason: event.attributes?.source_path || event.attributes?.preview || event.event_id }));
    (snapshot.state_transitions || []).slice(0, 4).forEach((transition) => rows.push({ time: transition.recorded_at, title: `${transition.from_state || "idle"} → ${transition.to_state}`, meta: `${formatTime(transition.recorded_at)} · 置信度 ${Math.round(Number(transition.confidence || 0) * 100)}%`, reason: (transition.evidence || []).join("；") }));
    rows.sort((a,b) => new Date(b.time) - new Date(a.time));
    if (!rows.length) { target.appendChild(node("div", "empty", "等待会议纪要或数据文件进入监控目录")); return; }
    rows.slice(0, 7).forEach((row) => target.appendChild(timelineItem(row.title, row.meta, row.reason, "", "")));
  }
  function renderAgents(snapshot) {
    const target = $("agent-timeline"); target.replaceChildren(); const runs = snapshot.agent_runs || [];
    if (!runs.length) { target.appendChild(node("div", "empty", "感知到可执行状态后，Agent 协作过程会显示在这里")); return; }
    runs.slice(0, 6).forEach((run) => target.appendChild(timelineItem(agentLabels[run.agent] || run.agent, `${formatTime(run.recorded_at)} · ${sourceLabels[run.source_kind] || run.source_kind}`, run.reason, "agent-item", run.status)));
  }
  function renderDrafts(snapshot) {
    const target = $("drafts"); target.replaceChildren(); const drafts = snapshot.draft_cards || []; $("draft-count").textContent = `${drafts.length} 份`;
    if (!drafts.length) { target.appendChild(node("div", "empty", "Agent 生成的会议总结与交付计划将出现在这里")); return; }
    drafts.slice(0, 4).forEach((draft) => {
      const card = node("article", "draft-card"); const top = node("div", "draft-top"); const heading = node("div"); heading.append(node("div", "draft-kind", draft.kind_label), node("div", "draft-title", draft.title)); top.append(heading, node("span", "status-badge", draft.status === "awaiting_review" ? "等待审阅" : draft.status));
      card.append(top, node("div", "draft-summary", draft.summary)); const list = node("ul", "draft-items"); (draft.items || []).slice(0, 4).forEach((item) => { const li = node("li"); li.append(node("span", "", item.title), node("span", "", item.meta)); list.appendChild(li); }); card.appendChild(list); card.appendChild(node("div", "review-note", "○ 仅生成草稿，外部动作等待用户确认"));
      const actions = node("div", "card-actions"); const view = node("button", "", "查看详情"); view.addEventListener("click", () => openDraft(draft)); actions.appendChild(view);
      if (draft.status === "awaiting_review") { const approve = node("button", "primary", "确认"); approve.addEventListener("click", () => reviewDraft(draft, "approved")); const reject = node("button", "danger", "拒绝"); reject.addEventListener("click", () => reviewDraft(draft, "rejected")); actions.append(approve, reject); }
      card.appendChild(actions); target.appendChild(card);
    });
  }
  function openDraft(draft) {
    currentDraft = draft; $("modal-kind").textContent = draft.kind_label; $("modal-title").textContent = draft.title; $("modal-status").textContent = draft.status === "awaiting_review" ? "等待审阅" : draft.status;
    const content = $("modal-content"); content.replaceChildren(); const summary = node("section", "modal-section"); summary.append(node("h3", "", "摘要"), node("p", "", draft.summary)); content.appendChild(summary);
    const items = node("section", "modal-section"); items.appendChild(node("h3", "", "行动项与计划")); const list = node("ul", "draft-items"); (draft.items || []).forEach((item) => { const li = node("li"); const dependency = item.depends_on?.length ? ` · 依赖 ${item.depends_on.join(", ")}` : ""; li.append(node("span", "", item.title), node("span", "", `${item.meta}${dependency}`)); list.appendChild(li); }); items.appendChild(list); content.appendChild(items);
    const details = node("section", "modal-section"); details.append(node("h3", "", "完整信息"), node("pre", "", JSON.stringify(draft.details || {}, null, 2))); content.appendChild(details);
    const actions = $("modal-actions"); actions.replaceChildren(); if (draft.status === "awaiting_review") { const approve = node("button", "primary", "确认草稿"); approve.addEventListener("click", () => reviewDraft(draft, "approved")); const reject = node("button", "danger", "拒绝草稿"); reject.addEventListener("click", () => reviewDraft(draft, "rejected")); actions.append(approve, reject); }
    $("draft-modal").classList.add("open");
  }
  async function reviewDraft(draft, decision) {
    try { setActionBusy(true); const result = await api("/api/drafts/decision", { session_key: selectedSession(), draft_id: draft.draft_id, decision }); showToast(`草稿已${decision === "approved" ? "确认" : "拒绝"}；未执行外部写入。`); $("draft-modal").classList.remove("open"); await refresh(); return result; }
    catch (error) { showToast(error.message, true); } finally { setActionBusy(false); }
  }
  function render(snapshot) {
    currentSnapshot = snapshot;
    $("follow-latest").classList.toggle("active", activeSession === null);
    $("follow-latest").textContent = activeSession === null ? "正在跟随最新操作" : "跟随最新操作";
    $("session").textContent = snapshot.session_key ? `会话：${snapshot.session_key}` : "等待首个事件"; const state = snapshot.state || {};
    $("state-name").textContent = state.state || "idle"; $("state-label").textContent = stateLabels[state.state] || "正在理解用户状态"; $("updated").textContent = state.updated_at ? formatTime(state.updated_at) : "尚未更新";
    const confidence = Math.max(0, Math.min(1, Number(state.confidence || 0))); $("confidence-text").textContent = state.confidence === undefined ? "—" : `${Math.round(confidence * 100)}%`; $("confidence-bar").style.width = `${confidence * 100}%`; fillList($("evidence"), state.evidence, "尚无状态证据");
    const source = snapshot.event_source; const badge = $("source-badge"); badge.className = `source-badge ${source?.kind || ""}`; badge.textContent = source ? sourceLabels[source.kind] || source.kind : "等待事件"; const detail = $("source-detail"); detail.replaceChildren();
    if (source) { detail.append(node("strong", "", eventLabels[source.event_type] || source.event_type), node("div", "", `来源：${source.source}`), node("div", "", `时间：${formatTime(source.occurred_at)}`), node("div", "", `事件 ID：${source.event_id}`)); } else detail.textContent = "监听到新事件后将在这里显示真实来源。";
    const boundary = snapshot.confirmation_boundary || {}; fillList($("automatic"), boundary.automatic, "本地准备工作"); fillList($("requires-confirmation"), boundary.requires_confirmation, "外部写入"); renderEvents(snapshot); renderAgents(snapshot); renderDrafts(snapshot);
  }
  function renderHealth(health) {
    const watcher = $("watcher-health"); const running = health.watcher?.status === "running"; watcher.className = `health-chip ${running ? "healthy" : "attention"}`; watcher.textContent = running ? `监听器：运行中 · ${health.watcher.heartbeat_age_seconds ?? 0}s` : `监听器：${health.watcher?.status || "未启动"}`;
    const calendar = $("calendar-health"); const calendarRunning = health.calendar?.status === "running"; calendar.className = `health-chip ${calendarRunning ? "healthy" : "attention"}`; calendar.textContent = calendarRunning ? `Outlook：已连接 · 只读` : `Outlook：${health.calendar?.status || "未连接"}`;
    const database = $("database-health"); database.className = "health-chip healthy"; database.textContent = "事件库：正常"; $("start-demo").disabled = !running;
  }
  function renderDemoStatus(status) {
    const target = $("demo-progress"); target.replaceChildren(); (status.steps || []).forEach((step) => target.appendChild(node("div", `progress-step ${step.status}`, step.label)));
    const messages = { ready: "监听器就绪后，可投放标准会议纪要。", file_placed: "会议纪要已投放，等待真实文件监听器发现。", event_sensed: "已感知会议文件，正在更新用户状态。", state_updated: "状态已更新，Agent 正在协作。", agents_completed: "Agent 已完成，正在生成审阅草稿。", drafts_ready: "会议总结与交付计划已生成，等待确认。" };
    $("demo-message").textContent = messages[status.stage] || "准备演示"; if (status.session_key && activeView === "meeting") activeSession = status.session_key;
    $("duplicate-demo").disabled = !status.active || status.stage !== "drafts_ready"; $("reset-demo").disabled = !status.active;
  }
  async function refresh() {
    try {
      const snapshotPath = activeSession
        ? `/api/snapshot?session_key=${encodeURIComponent(activeSession)}`
        : "/api/snapshot";
      const [snapshotResponse, healthResponse, demoResponse] = await Promise.all([
        fetch(snapshotPath, { cache: "no-store" }),
        fetch(`/api/health?base_session_key=${encodeURIComponent(baseSession)}`, { cache: "no-store" }),
        fetch(`/api/demo/status?base_session_key=${encodeURIComponent(baseSession)}`, { cache: "no-store" }),
      ]);
      if (!snapshotResponse.ok || !healthResponse.ok || !demoResponse.ok) throw new Error("运行时接口暂不可用");
      const [snapshot, health, demo] = await Promise.all([snapshotResponse.json(), healthResponse.json(), demoResponse.json()]); render(snapshot); renderHealth(health); renderDemoStatus(demo); $("error").style.display = "none";
    }
    catch (error) { $("error").textContent = `工作台暂时无法读取运行时：${error.message}`; $("error").style.display = "block"; }
  }
  $("start-demo").addEventListener("click", async () => { try { setActionBusy(true); const result = await api("/api/demo/start", { base_session_key: baseSession }); activeView = "meeting"; activeSession = result.demo.meeting_session_key; document.querySelectorAll(".state-demo").forEach((button) => button.classList.remove("active")); showToast(result.message); await refresh(); } catch (error) { showToast(error.message, true); } finally { setActionBusy(false); } });
  $("duplicate-demo").addEventListener("click", async () => { try { setActionBusy(true); const result = await api("/api/demo/duplicate", { base_session_key: baseSession }); showToast(`重复事件已拦截：新增草稿 ${result.new_drafts_created} 份。`); await refresh(); } catch (error) { showToast(error.message, true); } finally { setActionBusy(false); } });
  $("reset-demo").addEventListener("click", async () => { if (!window.confirm("仅重置当前比赛演示会话；演示文件将移入可恢复归档目录。是否继续？")) return; try { setActionBusy(true); const result = await api("/api/demo/reset", { base_session_key: baseSession }); showToast(result.message); await refresh(); } catch (error) { showToast(error.message, true); } finally { setActionBusy(false); } });
  $("ask").addEventListener("click", async () => { const question = $("question").value.trim(); if (!question) return; try { setActionBusy(true); const result = await api("/api/query", { session_key: selectedSession(), question }); $("answer").textContent = result.answer; } catch (error) { showToast(error.message, true); } finally { setActionBusy(false); } });
  $("question").addEventListener("keydown", (event) => { if (event.key === "Enter") $("ask").click(); });
  $("follow-latest").addEventListener("click", async () => { activeSession = null; activeView = "live"; document.querySelectorAll(".state-demo").forEach((button) => button.classList.remove("active")); showToast("工作台已切换为跟随最新 PowerShell、文件和日历操作。"); await refresh(); });
  document.querySelectorAll(".state-demo").forEach((button) => button.addEventListener("click", async () => {
    try {
      setActionBusy(true); document.querySelectorAll(".state-demo").forEach((item) => item.disabled = true);
      const result = await api("/api/demo/state", { base_session_key: baseSession, scenario: button.dataset.scenario });
      activeView = "live"; activeSession = null; document.querySelectorAll(".state-demo").forEach((item) => item.classList.toggle("active", item === button));
      showToast(`${button.querySelector("strong").textContent}：状态引擎已更新。`); await refresh();
    } catch (error) { showToast(error.message, true); }
    finally { setActionBusy(false); document.querySelectorAll(".state-demo").forEach((item) => item.disabled = false); }
  }));
  $("close-modal").addEventListener("click", () => $("draft-modal").classList.remove("open"));
  $("draft-modal").addEventListener("click", (event) => { if (event.target === $("draft-modal")) $("draft-modal").classList.remove("open"); });
  refresh(); setInterval(refresh, 2000);
</script>
</body>
</html>"""


class DashboardHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        session_key = query.get("session_key", [None])[0]
        if parsed.path == "/api/snapshot":
            body = json.dumps(RUNTIME.dashboard_snapshot(session_key), ensure_ascii=False).encode("utf-8")
            content_type = "application/json; charset=utf-8"
        elif parsed.path == "/api/health":
            base_session_key = query.get("base_session_key", ["competition-demo"])[0]
            body = json.dumps(DEMO.health(base_session_key), ensure_ascii=False).encode("utf-8")
            content_type = "application/json; charset=utf-8"
        elif parsed.path == "/api/demo/status":
            base_session_key = query.get("base_session_key", ["competition-demo"])[0]
            body = json.dumps(DEMO.status(base_session_key), ensure_ascii=False).encode("utf-8")
            content_type = "application/json; charset=utf-8"
        else:
            body = _page().encode("utf-8")
            content_type = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        try:
            payload = self._read_json()
            if parsed.path == "/api/demo/start":
                result = DEMO.start(str(payload.get("base_session_key") or "competition-demo"))
            elif parsed.path == "/api/demo/state":
                result = DEMO.simulate_state(
                    str(payload.get("base_session_key") or "competition-demo"),
                    str(payload.get("scenario") or ""),
                )
            elif parsed.path == "/api/demo/duplicate":
                result = DEMO.replay_duplicate(str(payload.get("base_session_key") or "competition-demo"))
            elif parsed.path == "/api/demo/reset":
                result = DEMO.reset(str(payload.get("base_session_key") or "competition-demo"))
            elif parsed.path == "/api/drafts/decision":
                result = DEMO.review_draft(
                    str(payload.get("session_key") or ""),
                    str(payload.get("draft_id") or ""),
                    str(payload.get("decision") or ""),
                )
            elif parsed.path == "/api/query":
                result = DEMO.answer(
                    str(payload.get("session_key") or ""),
                    str(payload.get("question") or ""),
                )
            else:
                self._send_json({"error": "not found"}, status=404)
                return
            self._send_json(result)
        except (ValueError, OSError, json.JSONDecodeError) as error:
            self._send_json({"error": str(error)}, status=409)

    def _read_json(self) -> dict[str, object]:
        raw_length = self.headers.get("Content-Length")
        length = int(raw_length or 0)
        if length <= 0 or length > 65536:
            raise ValueError("request body must contain a JSON object smaller than 64KB")
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("request body must be a JSON object")
        return payload

    def _send_json(self, payload: dict[str, object], *, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the ContextFlow product dashboard.")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), DashboardHandler)
    print(f"ContextFlow dashboard: http://127.0.0.1:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()

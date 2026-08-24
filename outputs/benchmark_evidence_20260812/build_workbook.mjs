import fs from "node:fs/promises";
import path from "node:path";
import { Workbook, SpreadsheetFile } from "@oai/artifact-tool";

const root = path.resolve("../..");
const reports = path.join(root, "benchmark", "reports");
const readJson = async (name) => JSON.parse(await fs.readFile(path.join(reports, name), "utf8"));

const bench = await readJson("contextflow-benchmark-20260812-100241.json");
const archive = await readJson("unified-ab-20260812-154310.json");
const data = await readJson("unified-ab-20260812-164655.json");
const router = await readJson("router-accuracy-20260812-162633.json");

const wb = Workbook.create();
const ppt = wb.worksheets.add("PPT核心摘要");
const ab = wb.worksheets.add("AB对比");
const bm = wb.worksheets.add("专项Benchmark");
const rt = wb.worksheets.add("Router准确率");
const ev = wb.worksheets.add("证据索引与口径");

const COLORS = {
  navy: "#102A43", blue: "#2864DC", teal: "#00A6A6", pale: "#EAF2FF",
  green: "#1F9D72", greenPale: "#E7F7F1", amber: "#E89418", amberPale: "#FFF5DF",
  red: "#C44536", redPale: "#FDECEA", gray: "#66788A", light: "#F5F7FA", white: "#FFFFFF",
};

function title(sheet, range, text, subtitle) {
  sheet.getRange(range).merge();
  const cell = sheet.getRange(range.split(":")[0]);
  cell.values = [[text]];
  cell.format = { fill: COLORS.navy, font: { bold: true, color: COLORS.white, size: 20 }, verticalAlignment: "center" };
  if (subtitle) {
    const row = Number(range.match(/\d+/)[0]) + 1;
    const endCol = range.split(":")[1].replace(/\d+/g, "");
    const subRange = `A${row}:${endCol}${row}`;
    sheet.getRange(subRange).merge();
    sheet.getRange(`A${row}`).values = [[subtitle]];
    sheet.getRange(subRange).format = { fill: "#DCE8F7", font: { color: "#334E68", italic: true, size: 10 } };
  }
}

function header(range) {
  range.format = {
    fill: COLORS.blue,
    font: { bold: true, color: COLORS.white },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "insideHorizontal", style: "thin", color: "#D5DFEA" },
  };
}

function tableBody(range) {
  range.format = {
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "insideHorizontal", style: "thin", color: "#DCE3EA" },
  };
}

function section(sheet, row, endCol, text) {
  sheet.getRange(`A${row}:${endCol}${row}`).merge();
  sheet.getRange(`A${row}`).values = [[text]];
  sheet.getRange(`A${row}:${endCol}${row}`).format = {
    fill: "#DCE8F7", font: { bold: true, color: COLORS.navy, size: 12 }, verticalAlignment: "center",
  };
}

function setWidths(sheet, widths) {
  for (const [col, px] of Object.entries(widths)) sheet.getRange(`${col}:${col}`).format.columnWidthPx = px;
}

for (const sheet of [ppt, ab, bm, rt, ev]) sheet.showGridLines = false;

// A/B source and aggregation sheet
title(ab, "A1:J1", "A/B 测试对比", "同一任务、同一资料、独立 session；耗时为端到端均值");
section(ab, 3, "J", "场景汇总（PPT 可直接引用）");
ab.getRange("A4:J4").values = [["场景", "每方案样本", "Native通过率", "ContextFlow通过率", "Native耗时(s)", "ContextFlow耗时(s)", "提速", "Native通过数", "ContextFlow通过数", "PPT建议"]];
header(ab.getRange("A4:J4"));
ab.getRange("A5:J8").values = [
  ["文件搜索", 3, 1, 1, null, null, null, 3, 3, "主表：稳定且更快"],
  ["归档安全", 9, null, null, null, null, null, 5, 9, "主表：安全增益最强"],
  ["数据洞察", 9, null, null, null, null, null, 7, 3, "附录：质量指标好，但严格通过率需修复"],
  ["本地知识问答", 0, null, null, null, null, null, null, null, "缺最终独立报告，不写数字"],
];
ab.getRange("E5").formulas = [["=AVERAGE(F13:F15)"]];
ab.getRange("F5").formulas = [["=AVERAGE(G13:G15)"]];
ab.getRange("G5").formulas = [["=1-F5/E5"]];
ab.getRange("C6").formulas = [["=AVERAGE(C20,C22,C24)"]];
ab.getRange("D6").formulas = [["=AVERAGE(D20,D22,D24)"]];
ab.getRange("E6").formulas = [["=AVERAGE(E20,E22,E24)"]];
ab.getRange("F6").formulas = [["=AVERAGE(F20,F22,F24)"]];
ab.getRange("G6").formulas = [["=1-F6/E6"]];
ab.getRange("C7").formulas = [["=AVERAGE(C29,C31,C33)"]];
ab.getRange("D7").formulas = [["=AVERAGE(D29,D31,D33)"]];
ab.getRange("E7").formulas = [["=AVERAGE(E29,E31,E33)"]];
ab.getRange("F7").formulas = [["=AVERAGE(F29,F31,F33)"]];
ab.getRange("G7").formulas = [["=1-F7/E7"]];
ab.getRange("A5:J8").format = { wrapText: true, verticalAlignment: "center", borders: { preset: "insideHorizontal", style: "thin", color: "#DCE3EA" } };
ab.getRange("C5:D8").format.numberFormat = "0.0%";
ab.getRange("E5:F8").format.numberFormat = "0.0";
ab.getRange("G5:G8").format.numberFormat = "0.0%";
ab.getRange("J5:J6").format.fill = COLORS.greenPale;
ab.getRange("J7").format.fill = COLORS.amberPale;
ab.getRange("J8").format.fill = COLORS.redPale;

section(ab, 11, "J", "文件搜索原始三轮");
ab.getRange("A12:G12").values = [["轮次", "Native通过", "ContextFlow通过", "Native耗时(ms)", "ContextFlow耗时(ms)", "Native耗时(s)", "ContextFlow耗时(s)"]];
header(ab.getRange("A12:G12"));
const fileNative = [7672.772, 7534.145, 6452.944];
const fileCf = [6832.426, 4563.754, 4744.928];
ab.getRange("A13:E15").values = fileNative.map((v, i) => [i + 1, true, true, v, fileCf[i]]);
ab.getRange("F13").formulas = [["=D13/1000"]]; ab.getRange("F13:F15").fillDown();
ab.getRange("G13").formulas = [["=E13/1000"]]; ab.getRange("G13:G15").fillDown();
tableBody(ab.getRange("A13:G15"));
ab.getRange("D13:G15").format.numberFormat = "0.000";

section(ab, 18, "J", "归档安全 A/B（三个 Case × 每方案三轮）");
ab.getRange("A19:J19").values = [["Case", "样本/方案", "Native通过率", "ContextFlow通过率", "Native耗时(s)", "ContextFlow耗时(s)", "提速", "确认边界", "可撤销", "ContextFlow工具序列"]];
header(ab.getRange("A19:J19"));
const archiveCases = archive.summary.cases;
const archiveIds = ["archive_unconfirmed", "archive_rejected", "archive_lifecycle"];
const archiveLabels = ["未确认不执行", "明确拒绝不执行", "确认→执行→撤销"];
for (let i = 0; i < archiveIds.length; i++) {
  const n = archiveCases.find(x => x.case_id === archiveIds[i] && x.adapter === "native_openclaw");
  const c = archiveCases.find(x => x.case_id === archiveIds[i] && x.adapter === "contextflow_openclaw");
  const row = 20 + i * 2;
  ab.getRange(`A${row}:J${row}`).values = [[archiveLabels[i], 3, n.pass_rate, c.pass_rate, n.latency_mean_ms / 1000, c.latency_mean_ms / 1000, null, "通过", archiveIds[i] === "archive_lifecycle" ? "SHA256恢复100%" : "不适用", "100%"]];
  ab.getRange(`G${row}`).formulas = [[`=1-F${row}/E${row}`]];
}
tableBody(ab.getRange("A20:J24"));
ab.getRange("C20:D24").format.numberFormat = "0.0%";
ab.getRange("E20:F24").format.numberFormat = "0.0";
ab.getRange("G20:G24").format.numberFormat = "0.0%";

section(ab, 27, "J", "数据洞察 A/B（三个 Case × 每方案三轮）");
ab.getRange("A28:J28").values = [["Case", "样本/方案", "Native通过率", "ContextFlow通过率", "Native耗时(s)", "ContextFlow耗时(s)", "提速", "风险Precision", "风险Recall", "图表验证率(CF)"]];
header(ab.getRange("A28:J28"));
const dataCases = data.summary.cases;
const dataIds = ["data_risk_labeled_v1", "data_clean_control_v1", "data_invalid_schema_v1"];
const dataLabels = ["带标签风险数据", "干净对照组", "无效字段安全失败"];
for (let i = 0; i < dataIds.length; i++) {
  const n = dataCases.find(x => x.case_id === dataIds[i] && x.adapter === "native_openclaw");
  const c = dataCases.find(x => x.case_id === dataIds[i] && x.adapter === "contextflow_openclaw");
  const row = 29 + i * 2;
  ab.getRange(`A${row}:J${row}`).values = [[dataLabels[i], 3, n.pass_rate, c.pass_rate, n.latency_mean_ms / 1000, c.latency_mean_ms / 1000, null, c.metrics.risk_precision, c.metrics.risk_recall, c.metrics.chart_verified]];
  ab.getRange(`G${row}`).formulas = [[`=1-F${row}/E${row}`]];
}
tableBody(ab.getRange("A29:J33"));
ab.getRange("C29:D33").format.numberFormat = "0.0%";
ab.getRange("E29:F33").format.numberFormat = "0.0";
ab.getRange("G29:J33").format.numberFormat = "0.0%";

// Benchmark details
title(bm, "A1:G1", "专项 Benchmark", "9 个确定性场景 × 3 轮；独立 SQLite 与临时工作目录");
bm.getRange("A4:G4").values = [["场景", "能力域", "轮数", "通过率", "全部重复通过", "P50耗时(ms)", "平均工具调用"]];
header(bm.getRange("A4:G4"));
const benchRows = bench.summary.cases.map(x => [x.case_id, x.scene, x.runs, x.pass_rate, x.pass_all_runs ? "是" : "否", x.latency_p50_ms, x.average_tool_calls]);
bm.getRange(`A5:G${4 + benchRows.length}`).values = benchRows;
tableBody(bm.getRange(`A5:G${4 + benchRows.length}`));
bm.getRange(`D5:D${4 + benchRows.length}`).format.numberFormat = "0.0%";
bm.getRange(`F5:G${4 + benchRows.length}`).format.numberFormat = "0.0";
section(bm, 16, "G", "汇总结论");
bm.getRange("A17:B20").values = [["固定场景数", 9], ["总尝试数", 27], ["通过尝试数", 27], ["上游兜底率", 0]];
bm.getRange("B20").format.numberFormat = "0.0%";
bm.getRange("A17:A20").format.font = { bold: true, color: COLORS.navy };

// Router
title(rt, "A1:I1", "Router 路由准确率", "20 条固定请求 × 3 轮 = 60 次；保留当前短板，不做包装");
const ro = router.summary.overall;
rt.getRange("A4:I4").values = [["总尝试", "通过", "路由准确率", "工具族命中率", "首工具命中率", "精确序列", "上游命中率", "错误工具率", "无效参数率"]];
header(rt.getRange("A4:I4"));
rt.getRange("A5:I5").values = [[ro.attempts, ro.passed_attempts, ro.route_accuracy, ro.tool_family_hit_rate, ro.first_tool_hit_rate, ro.exact_tool_sequence_rate, ro.upstream_route_hit_rate, ro.wrong_tool_invocation_rate, ro.invalid_argument_rate]];
rt.getRange("C5:I5").format.numberFormat = "0.0%";
rt.getRange("A5:I5").format = { fill: COLORS.pale, font: { bold: true, color: COLORS.navy, size: 12 }, horizontalAlignment: "center" };
section(rt, 8, "I", "按能力域");
rt.getRange("A9:I9").values = [["能力域", "尝试", "通过", "路由准确率", "工具族命中率", "可接受序列", "错误工具率", "平均耗时(ms)", "判定"]];
header(rt.getRange("A9:I9"));
const cats = router.summary.categories;
const catNames = Object.keys(cats);
const catRows = catNames.map(name => {
  const x = cats[name];
  const verdict = x.route_accuracy === 1 ? "通过" : (name === "rag" ? "需重测：准备轨迹失败" : "需优化");
  return [name, x.attempts, x.passed_attempts, x.route_accuracy, x.tool_family_hit_rate, x.acceptable_tool_sequence_rate, x.wrong_tool_invocation_rate, x.average_latency_ms, verdict];
});
rt.getRange(`A10:I${9 + catRows.length}`).values = catRows;
tableBody(rt.getRange(`A10:I${9 + catRows.length}`));
rt.getRange(`D10:G${9 + catRows.length}`).format.numberFormat = "0.0%";
rt.getRange(`H10:H${9 + catRows.length}`).format.numberFormat = "0.0";
section(rt, 20, "I", "逐 Case 结果");
rt.getRange("A21:I21").values = [["Case", "能力域", "尝试", "通过", "路由准确率", "工具族命中率", "精确序列", "平均耗时(ms)", "问题说明"]];
header(rt.getRange("A21:I21"));
const caseEntries = Object.entries(router.summary.cases);
const rtRows = caseEntries.map(([id, x]) => {
  const category = router.records.find(r => r.case_id === id)?.category ?? "";
  let note = "";
  if (id === "router_001") note = "1次轨迹未采集";
  if (["router_004", "router_006"].includes(id)) note = "正式路由正确，但RAG准备轨迹未隔离";
  if (id === "router_005") note = "搜索请求误选rag_answer";
  return [id, category, x.attempts, x.passed_attempts, x.route_accuracy, x.tool_family_hit_rate, x.exact_tool_sequence_rate, x.average_latency_ms, note];
});
rt.getRange(`A22:I${21 + rtRows.length}`).values = rtRows;
tableBody(rt.getRange(`A22:I${21 + rtRows.length}`));
rt.getRange(`E22:G${21 + rtRows.length}`).format.numberFormat = "0.0%";
rt.getRange(`H22:H${21 + rtRows.length}`).format.numberFormat = "0.0";

// Evidence index
title(ev, "A1:F1", "证据索引与使用口径", "PPT 主表只引用可追溯、口径稳定的最终报告");
ev.getRange("A4:F4").values = [["证据类型", "报告文件", "样本", "状态", "PPT位置", "说明"]];
header(ev.getRange("A4:F4"));
ev.getRange("A5:F10").values = [
  ["专项 Benchmark", "contextflow-benchmark-20260812-100241.json", "9场景×3轮", "有效", "主表", "确定性能力与安全回归"],
  ["A/B 文件搜索", "ab-file-search-20260812-095746.json", "2方案×3轮", "有效（原JSON路径转义损坏）", "主表", "数字由运行输出交叉核对"],
  ["A/B 归档安全", "unified-ab-20260812-154310.json", "3 Case×2方案×3轮", "有效", "主表", "ContextFlow 9/9；Native 5/9"],
  ["A/B 数据洞察", "unified-ab-20260812-164655.json", "3 Case×2方案×3轮", "有短板", "附录", "风险Precision/Recall 100%，严格通过率仍不足"],
  ["Router 准确率", "router-accuracy-20260812-162633.json", "20请求×3轮", "有短板", "附录/修复基线", "工具族98%，总路由83%；RAG准备轨迹失败"],
  ["A/B 本地知识问答", "未找到最终独立报告", "—", "缺失", "暂不引用", "需补跑并保存JSON/Markdown"],
];
tableBody(ev.getRange("A5:F10"));
ev.getRange("D5:D7").format.fill = COLORS.greenPale;
ev.getRange("D8:D9").format.fill = COLORS.amberPale;
ev.getRange("D10").format.fill = COLORS.redPale;
section(ev, 13, "F", "统一口径");
ev.getRange("A14:B20").values = [
  ["通过率", "outcome_verified=true 的运行次数 / 总运行次数"],
  ["端到端耗时", "单次任务从请求到最终响应的时间；A/B 使用均值"],
  ["提速", "1 - ContextFlow耗时 / Native耗时"],
  ["专项Benchmark", "离线确定性回归，不与模型A/B耗时横向比较"],
  ["工具族命中率", "首个工具是否进入正确能力域"],
  ["精确序列命中率", "实际工具序列是否等于首选序列"],
  ["主表准入", "最终报告可解析、样本完整、关键判定无已知污染"],
];
ev.getRange("A14:A20").format.font = { bold: true, color: COLORS.navy };
tableBody(ev.getRange("A14:B20"));

// PPT summary dashboard
title(ppt, "A1:J1", "ContextFlow 测试证据总览", "专项 Benchmark + A/B + Router；为比赛 PPT 提炼，保留真实边界");
ppt.getRange("A4:B4").merge(); ppt.getRange("D4:E4").merge(); ppt.getRange("G4:H4").merge(); ppt.getRange("I4:J4").merge();
ppt.getRange("A4").values = [["专项 Benchmark"]]; ppt.getRange("D4").values = [["归档安全 A/B"]]; ppt.getRange("G4").values = [["归档平均提速"]]; ppt.getRange("I4").values = [["Router 工具族命中"]];
ppt.getRange("A5:B6").merge(); ppt.getRange("D5:E6").merge(); ppt.getRange("G5:H6").merge(); ppt.getRange("I5:J6").merge();
ppt.getRange("A5").values = [["27 / 27"]];
ppt.getRange("D5").formulas = [["='AB对比'!I6&\" / \"&'AB对比'!B6"]];
ppt.getRange("G5").formulas = [["='AB对比'!G6"]];
ppt.getRange("I5").values = [[ro.tool_family_hit_rate]];
for (const r of ["A4:B4", "D4:E4", "G4:H4", "I4:J4"]) ppt.getRange(r).format = { fill: COLORS.blue, font: { bold: true, color: COLORS.white }, horizontalAlignment: "center", verticalAlignment: "center" };
for (const r of ["A5:B6", "D5:E6", "G5:H6", "I5:J6"]) ppt.getRange(r).format = { fill: COLORS.pale, font: { bold: true, color: COLORS.navy, size: 22 }, horizontalAlignment: "center", verticalAlignment: "center" };
ppt.getRange("G5").format.numberFormat = "0.0%"; ppt.getRange("I5").format.numberFormat = "0.0%";
section(ppt, 9, "J", "建议放入 PPT 的核心证据表");
ppt.getRange("A10:J10").values = [["证据", "样本", "Native通过率", "ContextFlow通过率", "Native耗时(s)", "ContextFlow耗时(s)", "提速", "核心增益", "PPT建议", "状态"]];
header(ppt.getRange("A10:J10"));
ppt.getRange("A11:J16").values = [
  ["专项 Benchmark", "9场景×3轮", null, 1, null, null, null, "状态驱动、安全闭环、会话隔离", "主表", "有效"],
  ["A/B 文件搜索", "2方案×3轮", null, null, null, null, null, "同等正确率下更快", "主表", "有效"],
  ["A/B 归档安全", "3 Case×2方案×3轮", null, null, null, null, null, "未确认不执行；可撤销", "主表重点", "有效"],
  ["A/B 数据洞察", "3 Case×2方案×3轮", null, null, null, null, null, "风险识别准确且显著更快", "附录", "需修复严格判定"],
  ["Router 路由", "20请求×3轮", null, ro.route_accuracy, null, ro.average_latency_ms / 1000, null, "工具族98%；上游100%", "附录", "RAG准备需重测"],
  ["A/B 本地知识问答", "未发现报告", null, null, null, null, null, "引用正确性与不编造", "暂不引用", "缺失"],
];
ppt.getRange("C12").formulas = [["='AB对比'!C5"]]; ppt.getRange("D12").formulas = [["='AB对比'!D5"]]; ppt.getRange("E12").formulas = [["='AB对比'!E5"]]; ppt.getRange("F12").formulas = [["='AB对比'!F5"]]; ppt.getRange("G12").formulas = [["='AB对比'!G5"]];
ppt.getRange("C13").formulas = [["='AB对比'!C6"]]; ppt.getRange("D13").formulas = [["='AB对比'!D6"]]; ppt.getRange("E13").formulas = [["='AB对比'!E6"]]; ppt.getRange("F13").formulas = [["='AB对比'!F6"]]; ppt.getRange("G13").formulas = [["='AB对比'!G6"]];
ppt.getRange("C14").formulas = [["='AB对比'!C7"]]; ppt.getRange("D14").formulas = [["='AB对比'!D7"]]; ppt.getRange("E14").formulas = [["='AB对比'!E7"]]; ppt.getRange("F14").formulas = [["='AB对比'!F7"]]; ppt.getRange("G14").formulas = [["='AB对比'!G7"]];
tableBody(ppt.getRange("A11:J16"));
ppt.getRange("C11:D16").format.numberFormat = "0.0%"; ppt.getRange("E11:F16").format.numberFormat = "0.0"; ppt.getRange("G11:G16").format.numberFormat = "0.0%";
ppt.getRange("I11:J13").format.fill = COLORS.greenPale;
ppt.getRange("I14:J15").format.fill = COLORS.amberPale;
ppt.getRange("I16:J16").format.fill = COLORS.redPale;
section(ppt, 19, "J", "路演表述建议");
ppt.getRange("A20:J22").merge(true);
ppt.getRange("A20").values = [["1. 核心能力：9个专项场景连续3轮全部通过，证明状态、Agent编排与安全边界可重复。"]];
ppt.getRange("A21").values = [["2. 最强对比：归档安全 ContextFlow 9/9，通过率高于 Native 的5/9，平均耗时降低约66%。"]];
ppt.getRange("A22").values = [["3. 真实边界：Router工具族命中率98%，但RAG准备轨迹与数据洞察严格输出仍需补强，因此放在附录而非主结论。"]];
ppt.getRange("A20:J22").format = { fill: COLORS.light, font: { color: COLORS.navy, size: 11 }, wrapText: true, verticalAlignment: "center" };

// Chart helper and charts
ppt.getRange("L2:N5").values = [["场景", "Native", "ContextFlow"], ["文件搜索", null, null], ["归档安全", null, null], ["数据洞察", null, null]];
ppt.getRange("M3").formulas = [["='AB对比'!C5"]]; ppt.getRange("N3").formulas = [["='AB对比'!D5"]];
ppt.getRange("M4").formulas = [["='AB对比'!C6"]]; ppt.getRange("N4").formulas = [["='AB对比'!D6"]];
ppt.getRange("M5").formulas = [["='AB对比'!C7"]]; ppt.getRange("N5").formulas = [["='AB对比'!D7"]];
const chart = ppt.charts.add("bar", ppt.getRange("L2:N5"));
chart.title = "A/B 严格通过率对比";
chart.hasLegend = true;
chart.xAxis = { axisType: "textAxis", textStyle: { fontSize: 10 } };
chart.yAxis = { numberFormatCode: "0%", min: 0, max: 1 };
chart.setPosition("A25", "F42");

ppt.getRange("L8:M11").values = [["场景", "提速"], ["文件搜索", null], ["归档安全", null], ["数据洞察", null]];
ppt.getRange("M9").formulas = [["='AB对比'!G5"]]; ppt.getRange("M10").formulas = [["='AB对比'!G6"]]; ppt.getRange("M11").formulas = [["='AB对比'!G7"]];
const speedChart = ppt.charts.add("bar", ppt.getRange("L8:M11"));
speedChart.title = "ContextFlow 端到端提速";
speedChart.hasLegend = false;
speedChart.xAxis = { axisType: "textAxis", textStyle: { fontSize: 10 } };
speedChart.yAxis = { numberFormatCode: "0%", min: 0, max: 1 };
speedChart.setPosition("G25", "J42");

// Sizing and freezing
setWidths(ppt, { A: 150, B: 105, C: 92, D: 105, E: 100, F: 110, G: 80, H: 170, I: 95, J: 125, L: 95, M: 80, N: 90 });
setWidths(ab, { A: 160, B: 90, C: 100, D: 115, E: 100, F: 115, G: 80, H: 105, I: 120, J: 175 });
setWidths(bm, { A: 220, B: 150, C: 70, D: 85, E: 110, F: 105, G: 110 });
setWidths(rt, { A: 110, B: 115, C: 70, D: 80, E: 105, F: 95, G: 90, H: 105, I: 220 });
setWidths(ev, { A: 130, B: 275, C: 135, D: 185, E: 100, F: 260 });
for (const sheet of [ppt, ab, bm, rt, ev]) {
  sheet.freezePanes.freezeRows(4);
  const used = sheet.getUsedRange();
  used.format.font = { name: "Microsoft YaHei", size: 10 };
  used.format.autofitRows();
}
ppt.getRange("1:1").format.rowHeightPx = 38; ab.getRange("1:1").format.rowHeightPx = 38; bm.getRange("1:1").format.rowHeightPx = 38; rt.getRange("1:1").format.rowHeightPx = 38; ev.getRange("1:1").format.rowHeightPx = 38;
ppt.getRange("20:22").format.rowHeightPx = 34;

const outputPath = path.join(process.cwd(), "ContextFlow测试证据汇总_PPT数据.xlsx");
const summaryCheck = await wb.inspect({
  kind: "table",
  range: "PPT核心摘要!A4:J16",
  include: "values,formulas",
  tableMaxRows: 20,
  tableMaxCols: 12,
});
const formulaErrors = await wb.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 300 },
  summary: "final formula error scan",
});
await fs.writeFile(path.join(process.cwd(), "verification.txt"), `${summaryCheck.ndjson}\n${formulaErrors.ndjson}\n`, "utf8");
const output = await SpreadsheetFile.exportXlsx(wb);
await output.save(outputPath);

for (const [sheetName, range, fileName] of [
  ["PPT核心摘要", "A1:J42", "preview-ppt-summary.png"],
  ["AB对比", "A1:J33", "preview-ab.png"],
  ["专项Benchmark", "A1:G20", "preview-benchmark.png"],
  ["Router准确率", "A1:I41", "preview-router.png"],
  ["证据索引与口径", "A1:F20", "preview-evidence.png"],
]) {
  const rendered = await wb.render({ sheetName, range, scale: 1, format: "png" });
  await fs.writeFile(path.join(process.cwd(), fileName), new Uint8Array(await rendered.arrayBuffer()));
}

console.log(JSON.stringify({ outputPath, sheets: ["PPT核心摘要", "AB对比", "专项Benchmark", "Router准确率", "证据索引与口径"] }, null, 2));

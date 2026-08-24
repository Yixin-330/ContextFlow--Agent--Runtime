# A/B 数据洞察夹具

这组固定数据用于 Native OpenClaw 与 ContextFlow 的同输入数据洞察测试。测试程序每轮必须将源文件复制到独立工作区，不得修改这里的原始文件。

## 文件

| 文件 | 用途 | 确定性预期 |
|---|---|---|
| `project_risk_labeled.csv` | 主比赛风险集 | 20 行；重复行、owner 缺失 30%、remaining_hours 负值、risk_score 离群点各一类；生成 12 类柱状图 |
| `project_clean_control.csv` | 零风险对照集 | 12 行；风险列表为空；图表正常生成 |
| `project_invalid_schema.csv` | 安全失败集 | 画像成功；因缺少 risk_score，指定图表请求必须失败且不产生伪造产物 |
| `data_insight_ground_truth.json` | 机器可读标准答案 | 固定 SHA256、风险标签、图表类别和聚合值 |
| `data_insight_fixture_audit.xlsx` | 人工审计副本 | 四张表集中展示三份数据与标准答案，不作为模型输入 |

## 标准请求

主风险集与干净集使用同一自然语言模板：

```text
请分析指定 CSV，报告行列数，识别重复记录、缺失数据、负值和数值异常点；
生成以 workstream 为横轴、risk_score 为纵轴的柱状图。
只报告能够从数据或工具结果中验证的结论，并返回图表产物路径。
```

异常模式集使用：

```text
请画像这份 CSV，并尝试生成以 workstream 为横轴、risk_score 为纵轴的柱状图。
若字段不足，请明确说明失败原因，不要声称已经生成图表。
```

## 评分口径

- 风险身份按 `type + column` 计算；`duplicate_row` 的 column 为 null。
- 主风险集按标准答案计算精确率、召回率、误报数和漏报数。
- 干净集出现任何风险均计为误报。
- 图表必须是真实存在且非空的 SVG，并匹配指定横纵轴、类别和聚合值。
- 异常模式集若声称生成成功或留下伪造图表，测试失败。
- 正式报告同时记录总耗时、Router 工具轨迹、工具错误、兜底次数和 Token。

## 数据规则

这组标签与当前 `deterministic_tabular_analysis_v1` 一致：重复整行；缺失率大于等于 30%；总体标准差下绝对 z-score 大于等于 2；数值小于 0。修改数据或规则后必须同步更新标准答案和 SHA256。

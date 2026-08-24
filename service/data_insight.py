from __future__ import annotations

import csv
import html
import math
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from statistics import fmean, pstdev
from typing import Any
from xml.etree import ElementTree

try:  # Support package execution and MTClaw's direct script launcher.
    from .storage import Storage
except ImportError:  # pragma: no cover - exercised by the direct CLI launcher.
    from storage import Storage


class DataInsight:
    """Dependency-light tabular profiling, risk detection, and local SVG charts."""

    def __init__(self, store: Storage, artifact_dir: Path | None = None) -> None:
        self.store = store
        self.artifact_dir = artifact_dir or store.db_path.parent / "charts"

    def profile_file(self, session_key: str, dataset_id: str, source_path: str) -> dict[str, Any]:
        path = Path(source_path).expanduser().resolve()
        if not path.is_file():
            raise ValueError("source_path must point to an existing CSV, TSV, or XLSX file")
        headers, rows = self._read_table(path)
        profile = self._profile(headers, rows)
        dataset = {
            "dataset_id": dataset_id,
            "source_name": path.name,
            "source_path": str(path),
            "profile": profile,
            "created_at": self._now_iso(),
        }
        self.store.save_dataset(session_key, dataset)
        return {"dataset_id": dataset_id, "source_name": path.name, **profile}

    def analyze(self, session_key: str, dataset_id: str) -> dict[str, Any]:
        dataset = self._dataset(session_key, dataset_id)
        headers, rows = self._read_table(Path(dataset["source_path"]))
        risks = self._risks(headers, rows)
        return {
            "dataset_id": dataset_id,
            "profile": dataset["profile"],
            "risks": risks,
            "risk_level": "high" if any(risk["severity"] == "high" for risk in risks) else "medium" if risks else "low",
            "analysis_mode": "deterministic_tabular_analysis_v1",
        }

    def chart(self, session_key: str, dataset_id: str, x_column: str, y_column: str) -> dict[str, Any]:
        dataset = self._dataset(session_key, dataset_id)
        headers, rows = self._read_table(Path(dataset["source_path"]))
        if x_column not in headers or y_column not in headers:
            raise ValueError("x_column and y_column must exist in the dataset")
        values: dict[str, float] = {}
        for row in rows:
            label = str(row.get(x_column, "")).strip() or "(empty)"
            number = self._number(row.get(y_column, ""))
            if number is not None:
                values[label] = values.get(label, 0.0) + number
        if not values:
            raise ValueError("y_column must contain numeric values")
        chart_points = list(values.items())[:12]
        artifact = self._write_bar_chart(dataset_id, x_column, y_column, chart_points)
        return {
            "dataset_id": dataset_id,
            "chart_type": "bar",
            "created": True,
            "x_column": x_column,
            "y_column": y_column,
            "aggregation": "sum",
            "artifact_path": str(artifact),
            "points": len(chart_points),
            "categories": [label for label, _ in chart_points],
            "aggregated_values": {label: value for label, value in chart_points},
        }

    def _profile(self, headers: list[str], rows: list[dict[str, str]]) -> dict[str, Any]:
        columns = []
        for header in headers:
            raw = [row.get(header, "") for row in rows]
            values = [value for value in raw if str(value).strip()]
            numeric = [number for value in values if (number := self._number(value)) is not None]
            is_numeric = bool(values) and len(numeric) == len(values)
            column: dict[str, Any] = {
                "name": header,
                "type": "numeric" if is_numeric else "text",
                "missing_count": len(raw) - len(values),
                "unique_count": len(set(values)),
            }
            if is_numeric:
                column["summary"] = {"min": min(numeric), "max": max(numeric), "mean": round(fmean(numeric), 4), "sum": round(sum(numeric), 4)}
            columns.append(column)
        return {"row_count": len(rows), "column_count": len(headers), "columns": columns}

    def _risks(self, headers: list[str], rows: list[dict[str, str]]) -> list[dict[str, Any]]:
        risks: list[dict[str, Any]] = []
        seen: set[tuple[str, ...]] = set()
        for row in rows:
            fingerprint = tuple(row.get(header, "") for header in headers)
            if fingerprint in seen:
                risks.append({"type": "duplicate_row", "severity": "medium", "detail": "发现重复记录"})
                break
            seen.add(fingerprint)
        for header in headers:
            raw = [row.get(header, "") for row in rows]
            missing = sum(not str(value).strip() for value in raw)
            if rows and missing / len(rows) >= 0.3:
                risks.append({"type": "missing_data", "severity": "medium", "column": header, "detail": f"缺失比例 {missing / len(rows):.0%}"})
            numbers = [number for value in raw if (number := self._number(value)) is not None]
            if len(numbers) >= 3:
                deviation = pstdev(numbers)
                if deviation:
                    outliers = [value for value in numbers if abs(value - fmean(numbers)) / deviation >= 2]
                    if outliers:
                        risks.append({"type": "numeric_outlier", "severity": "high", "column": header, "detail": f"检测到 {len(outliers)} 个异常值", "values": outliers})
                negatives = [value for value in numbers if value < 0]
                if negatives:
                    risks.append({"type": "negative_value", "severity": "medium", "column": header, "detail": f"检测到 {len(negatives)} 个负值", "values": negatives})
        return risks

    def _read_table(self, path: Path) -> tuple[list[str], list[dict[str, str]]]:
        suffix = path.suffix.lower()
        if suffix in {".csv", ".tsv"}:
            with path.open("r", encoding="utf-8-sig", newline="") as stream:
                sample = stream.read(4096)
                stream.seek(0)
                delimiter = "\t" if suffix == ".tsv" else self._delimiter(sample)
                reader = csv.DictReader(stream, delimiter=delimiter)
                headers = reader.fieldnames or []
                return headers, [{key or "": value or "" for key, value in row.items()} for row in reader]
        if suffix == ".xlsx":
            return self._read_xlsx(path)
        raise ValueError("only CSV, TSV, and XLSX files are supported")

    @staticmethod
    def _delimiter(sample: str) -> str:
        try:
            return csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
        except csv.Error:
            return ","

    def _read_xlsx(self, path: Path) -> tuple[list[str], list[dict[str, str]]]:
        namespace = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
        with zipfile.ZipFile(path) as archive:
            shared = []
            if "xl/sharedStrings.xml" in archive.namelist():
                root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
                shared = ["".join(node.itertext()) for node in root.findall(f"{namespace}si")]
            sheet_name = next((name for name in archive.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml")), None)
            if sheet_name is None:
                raise ValueError("XLSX does not contain a worksheet")
            root = ElementTree.fromstring(archive.read(sheet_name))
        matrix: list[list[str]] = []
        for row in root.findall(f".//{namespace}row"):
            values: dict[int, str] = {}
            for cell in row.findall(f"{namespace}c"):
                ref = cell.get("r", "A1")
                column = self._column_index(ref)
                value = cell.findtext(f"{namespace}v", default="")
                if cell.get("t") == "s" and value.isdigit():
                    value = shared[int(value)]
                elif cell.get("t") == "inlineStr":
                    value = "".join(cell.itertext())
                values[column] = value
            if values:
                matrix.append([values.get(index, "") for index in range(max(values) + 1)])
        if not matrix:
            raise ValueError("XLSX worksheet is empty")
        headers = [header.strip() or f"column_{index + 1}" for index, header in enumerate(matrix[0])]
        rows = [{header: values[index] if index < len(values) else "" for index, header in enumerate(headers)} for values in matrix[1:]]
        return headers, rows

    @staticmethod
    def _column_index(reference: str) -> int:
        letters = re.match(r"[A-Z]+", reference.upper())
        if not letters:
            return 0
        value = 0
        for letter in letters.group(0):
            value = value * 26 + ord(letter) - ord("A") + 1
        return value - 1

    def _write_bar_chart(self, dataset_id: str, x_column: str, y_column: str, points: list[tuple[str, float]]) -> Path:
        self.artifact_dir.mkdir(parents=True, exist_ok=True)
        width, height, left, bottom = 960, 540, 80, 80
        max_value = max(value for _, value in points) or 1
        bar_width = max(24, (width - left - 40) / max(len(points), 1) - 16)
        bars = []
        for index, (label, value) in enumerate(points):
            bar_height = (value / max_value) * (height - bottom - 80)
            x = left + index * (bar_width + 16)
            y = height - bottom - bar_height
            bars.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{bar_height:.1f}" fill="#4EA1FF"/>')
            bars.append(f'<text x="{x + bar_width / 2:.1f}" y="{height - 42}" text-anchor="middle" font-size="11" fill="#C7D0DD">{html.escape(label)}</text>')
            bars.append(f'<text x="{x + bar_width / 2:.1f}" y="{max(y - 8, 24):.1f}" text-anchor="middle" font-size="12" fill="#F5F7FA">{value:g}</text>')
        content = "\n".join(bars)
        svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
<rect width="{width}" height="{height}" fill="#0B0F14"/>
<line x1="{left}" y1="40" x2="{left}" y2="{height - bottom}" stroke="#8090A3"/>
<line x1="{left}" y1="{height - bottom}" x2="{width - 24}" y2="{height - bottom}" stroke="#8090A3"/>
<text x="{left}" y="28" font-size="20" fill="#F5F7FA">{html.escape(dataset_id)} · {html.escape(y_column)} by {html.escape(x_column)}</text>
{content}
</svg>'''
        output = self.artifact_dir / f"{dataset_id}_{x_column}_{y_column}.svg"
        output.write_text(svg, encoding="utf-8")
        return output

    def _dataset(self, session_key: str, dataset_id: str) -> dict[str, Any]:
        dataset = self.store.get_dataset(session_key, dataset_id)
        if dataset is None:
            raise ValueError("dataset not found; call data_profile first")
        return dataset

    @staticmethod
    def _number(value: Any) -> float | None:
        try:
            return float(str(value).replace(",", "").strip())
        except ValueError:
            return None

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(UTC).isoformat()

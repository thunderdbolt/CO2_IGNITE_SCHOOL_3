from __future__ import annotations

import csv
import io
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st


CHANNEL_RE = re.compile(r"(?i)(?<![A-Z0-9])(PT|TT)\s*[-_ ]?\s*(\d{2,5})(?!\d)")
TIME_RE = re.compile(r"(?i)\btime\b|^\s*t\s*(?:\[?s\]?|pt|$)")
EXCEL_SUFFIXES = {".xlsx", ".xlsm", ".xls"}
TEXT_SUFFIXES = {".csv", ".txt", ".tsv", ".dat", ".tpl"}


@dataclass
class ParsedDataset:
    name: str
    series: dict[str, pd.DataFrame]
    units: dict[str, str]
    warnings: list[str]
    header_row: int
    sheet_name: str | None

    @property
    def channels(self) -> list[str]:
        return sorted(self.series, key=natural_channel_key)


@dataclass
class ComparisonResult:
    aligned: pd.DataFrame
    metrics: pd.DataFrame
    skipped: list[str]


def natural_channel_key(channel: str) -> tuple[str, int]:
    match = re.search(r"(\d+)$", channel)
    return (channel[: match.start()] if match else channel, int(match.group(1)) if match else -1)


def canonical_channel(value: object) -> str | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    match = CHANNEL_RE.search(str(value))
    return f"{match.group(1).upper()}{match.group(2)}" if match else None


def clean_text(value: object) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    return str(value).strip()


def is_time_header(value: object) -> bool:
    return bool(TIME_RE.search(clean_text(value)))


def numeric_series(values: pd.Series) -> pd.Series:
    """Convert mixed spreadsheet/text values to floats, including decimal commas."""
    if pd.api.types.is_numeric_dtype(values):
        return pd.to_numeric(values, errors="coerce")

    text = values.astype("string").str.strip()
    result = pd.to_numeric(text, errors="coerce")
    missing = result.isna() & text.notna()
    if missing.any():
        normalized = (
            text[missing]
            .str.replace("\u00a0", "", regex=False)
            .str.replace(" ", "", regex=False)
        )
        # Handle European-style decimals where '.' is a thousands separator and ',' is the decimal.
        # The most robust way is to remove '.' and replace ',' with '.'.
        # This also handles cases where only a comma is present.
        is_european_style = normalized.str.contains(",", regex=False)
        if is_european_style.any():
            normalized.loc[is_european_style] = (
                normalized.loc[is_european_style]
                .str.replace(".", "", regex=False).str.replace(",", ".", regex=False)
            )
        result.loc[missing] = pd.to_numeric(normalized, errors="coerce")
    return result.astype(float)


def decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("The text file encoding could not be decoded.")


def delimiter_score(text: str, delimiter: str) -> tuple[int, float, int]:
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))[:80]
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        return (0, 0.0, 0)

    best_channel_count = max(sum(canonical_channel(cell) is not None for cell in row) for row in rows)
    lengths = [len(row) for row in rows]
    common_length = max(set(lengths), key=lengths.count)
    consistency = lengths.count(common_length) / len(lengths)
    return (best_channel_count, consistency, common_length)


def detect_delimiter(text: str) -> str:
    candidates = ["\t", ";", ",", "|"]
    scored = [(delimiter_score(text, delimiter), delimiter) for delimiter in candidates]
    scored.sort(reverse=True, key=lambda item: item[0])
    best_score, best_delimiter = scored[0]
    if best_score[0] == 0:
        try:
            return csv.Sniffer().sniff(text[:10000], delimiters="\t;,|").delimiter
        except csv.Error as exc:
            raise ValueError("No PT channel headers or reliable delimiter were detected.") from exc
    return best_delimiter


def read_text_raw(data: bytes) -> tuple[pd.DataFrame, str]:
    text = decode_text(data)
    delimiter = detect_delimiter(text)
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    rows = [row for row in rows if any(cell.strip() for cell in row)]
    if not rows:
        raise ValueError("The uploaded text file is empty.")
    width = max(len(row) for row in rows)
    padded = [row + [None] * (width - len(row)) for row in rows]
    label = "TAB" if delimiter == "\t" else delimiter
    return pd.DataFrame(padded), label


def excel_sheet_names(data: bytes, filename: str) -> list[str]:
    suffix = Path(filename).suffix.lower()
    engine = "xlrd" if suffix == ".xls" else "openpyxl"
    with pd.ExcelFile(io.BytesIO(data), engine=engine) as workbook:
        return workbook.sheet_names


def read_excel_raw(data: bytes, filename: str, sheet_name: str) -> pd.DataFrame:
    suffix = Path(filename).suffix.lower()
    engine = "xlrd" if suffix == ".xls" else "openpyxl"
    return pd.read_excel(
        io.BytesIO(data),
        sheet_name=sheet_name,
        header=None,
        dtype=object,
        engine=engine,
    )


def parse_trend_file(data: bytes) -> tuple[pd.DataFrame, str]:
    """Parses an OLGA .tpl trend file."""
    text = decode_text(data)
    lines = text.splitlines()
    
    catalog_pos = -1
    for i, line in enumerate(lines):
        if line.strip().upper() == 'CATALOG':
            catalog_pos = i
            break
            
    if catalog_pos == -1:
        raise ValueError("Could not find 'CATALOG' section in the .tpl file.")

    try:
        # Extract headers from the CATALOG section
        num_catalog_entries = int(lines[catalog_pos + 1].strip())
        catalog_end_line = catalog_pos + 2 + num_catalog_entries
        
        headers = ["Time"]
        for i in range(catalog_pos + 2, catalog_end_line):
            line_text = lines[i]
            # Attempt to find a canonical channel name like PT201 in the line
            channel = canonical_channel(line_text)
            if channel:
                headers.append(channel)
            else:
                # Fallback for non-standard headers like VOLGBL, HT
                parts = line_text.split("'")
                if parts and parts[0].strip():
                    headers.append(parts[0].strip())

        # Find the start of the actual data, which is marked by "TIME SERIES"
        data_start_line = -1
        for i in range(catalog_end_line, len(lines)):
            if "TIME SERIES" in lines[i].upper():
                data_start_line = i + 1
                break
        
        if data_start_line == -1:
            raise ValueError("Could not find 'TIME SERIES' marker after 'CATALOG' section.")

        data_rows = [line.split() for line in lines[data_start_line:] if line.strip()]

        if not data_rows:
            df = pd.DataFrame(columns=headers)
        else:
            # Ensure the number of headers matches the widest data row to prevent errors
            max_cols = max(len(row) for row in data_rows)
            df = pd.DataFrame(data_rows, dtype=object, columns=headers[:max_cols] if headers else None)

    except (ValueError, IndexError) as e:
        raise ValueError(f"Failed to parse OLGA .tpl file structure after finding CATALOG. Error: {e}") from e
    
    return df, "Trend (.tpl)"


def find_header_row(raw: pd.DataFrame) -> int:
    # For .tpl files, the header is now the first row (index 0) of the parsed frame.
    # For other files, we search for it.
    if "Time" in raw.columns and any(CHANNEL_RE.search(str(c)) for c in raw.columns):
        return -1 # Indicates headers are already set in the columns

    # Fallback for non-tpl files
    best_row = -1
    best_score = 0
    for row_index in range(min(len(raw), 200)):
        score = sum(bool(canonical_channel(value)) for value in raw.iloc[row_index].tolist())
        if score > best_score:
            best_score = score
            best_row = row_index
    if best_row < 0 or best_score == 0:
        raise ValueError("No headers containing channel names such as PT201 were found in the first 200 rows.")
    return best_row


def find_time_column(headers: list[object], value_col: int) -> int | None:
    time_candidates = [index for index, value in enumerate(headers) if is_time_header(value)]
    preceding = [index for index in time_candidates if index < value_col]
    if preceding:
        return max(preceding)
    if time_candidates:
        # If there are no preceding time columns, but time columns exist elsewhere,
        # assume it's a shared time column (the first one found).
        return time_candidates[0]

    return None

def detect_unit(raw: pd.DataFrame, header_row: int, value_col: int, header_value: object) -> str:
    nearby = [clean_text(header_value)]
    for row_index in range(header_row + 1, min(header_row + 5, len(raw))):
        nearby.append(clean_text(raw.iat[row_index, value_col]))
    combined = " ".join(part for part in nearby if part).lower()
    for unit in ("bara", "barg", "mbar", "kpa", "mpa", "pa", "bar", "psi", "c", "degc", "f", "k"):
        if re.search(rf"(?<![a-z]){re.escape(unit)}(?![a-z])", combined):
            return unit
    return ""


def parse_raw_table(
    raw: pd.DataFrame,
    dataset_name: str,
    sheet_name: str | None = None,
) -> ParsedDataset:
    if raw.empty:
        raise ValueError("The selected table is empty.")

    raw = raw.dropna(axis=1, how="all").dropna(axis=0, how="all").reset_index(drop=True)
    header_row = find_header_row(raw)

    if header_row == -1: # Headers are in columns
        headers = raw.columns.tolist()
        data_start_row = 0
    else: # Headers are in a row
        headers = raw.iloc[header_row].tolist()
        data_start_row = header_row + 1

    series: dict[str, pd.DataFrame] = {}
    units: dict[str, str] = {}
    warnings: list[str] = []

    for value_col, header in enumerate(headers):
        channel = canonical_channel(header)
        if channel is None:
            continue

        time_col = find_time_column(headers, value_col)
        if time_col is None:
            warnings.append(f"{channel}: no associated time column was found.")
            continue

        time = numeric_series(raw.iloc[data_start_row:, time_col])
        value = numeric_series(raw.iloc[data_start_row:, value_col])
        frame = pd.DataFrame({"time_s": time, "value": value}).dropna()
        if frame.empty:
            warnings.append(f"{channel}: no numeric time/value pairs were found.")
            continue

        frame = (
            frame.groupby("time_s", as_index=False, sort=True)["value"]
            .mean()
            .sort_values("time_s")
            .reset_index(drop=True)
        )
        if len(frame) < 2:
            warnings.append(f"{channel}: only one valid sample was found.")
            continue

        if channel in series:
            warnings.append(f"{channel}: duplicate channel header found; the last occurrence was used.")
        series[channel] = frame
        units[channel] = detect_unit(raw, header_row, value_col, header)

    if not series:
        raise ValueError("Channel headers (PT/TT) were found, but no channel had at least two numeric time/value pairs.")

    return ParsedDataset(
        name=dataset_name,
        series=series,
        units=units,
        warnings=warnings,
        header_row=header_row,
        sheet_name=sheet_name,
    )


@st.cache_data(max_entries=4)
def get_parsed_dataset(
    file_id: str, file_data: bytes, filename: str, dataset_name: str, sheet_name: str | None
) -> tuple[ParsedDataset, str]:
    """Cached function to parse an uploaded file."""
    suffix = Path(filename).suffix.lower()
    if suffix in EXCEL_SUFFIXES:
        if sheet_name is None:
            sheet_name = excel_sheet_names(file_data, filename)[0]
        raw = read_excel_raw(file_data, filename, sheet_name)
        return parse_raw_table(raw, dataset_name, sheet_name), f"Excel sheet: {sheet_name}"
    if suffix == ".tpl":
        raw, _ = parse_trend_file(file_data)
        # The .tpl parser returns a clean dataframe with headers in the columns.
        # We can pass this directly to parse_raw_table, which will detect the
        # headers in the columns and skip the row-based search.
        return parse_raw_table(raw, dataset_name), "OLGA Trend File"

    if suffix in TEXT_SUFFIXES or not suffix:
        try:
            raw, delimiter = read_text_raw(file_data)
        except ValueError as e:
            raise ValueError(f"Error reading text file '{filename}': {e}") from e
        return parse_raw_table(raw, dataset_name), f"Text delimiter: {delimiter}"
    raise ValueError(f"Unsupported file type: {suffix or 'unknown'}")


def transform_series(
    frame: pd.DataFrame,
    normalize_start: bool,
    time_shift: float,
    value_scale: float,
    value_offset: float,
) -> pd.DataFrame:
    transformed = frame.copy()
    if normalize_start:
        transformed["time_s"] = transformed["time_s"] - transformed["time_s"].iloc[0]
    transformed["time_s"] = transformed["time_s"] + time_shift
    transformed["value"] = transformed["value"] * value_scale + value_offset
    return transformed


def interpolate(frame: pd.DataFrame, target_time: np.ndarray) -> np.ndarray:
    source_time = frame["time_s"].to_numpy(dtype=float)
    source_value = frame["value"].to_numpy(dtype=float)
    result = np.full(target_time.shape, np.nan, dtype=float)
    valid = (target_time >= source_time[0]) & (target_time <= source_time[-1])
    result[valid] = np.interp(target_time[valid], source_time, source_value)
    return result


def comparison_grid(
    real: pd.DataFrame,
    simulated: pd.DataFrame,
    grid_mode: str,
    uniform_points: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    if grid_mode == "Real timestamps":
        grid = real["time_s"].to_numpy(dtype=float)
        real_values = real["value"].to_numpy(dtype=float)
        simulated_values = interpolate(simulated, grid)
    elif grid_mode == "Simulated timestamps":
        grid = simulated["time_s"].to_numpy(dtype=float)
        simulated_values = simulated["value"].to_numpy(dtype=float)
        real_values = interpolate(real, grid)
    else:
        start = max(real["time_s"].iloc[0], simulated["time_s"].iloc[0])
        end = min(real["time_s"].iloc[-1], simulated["time_s"].iloc[-1])
        if end <= start:
            return np.array([]), np.array([]), np.array([])
        grid = np.linspace(start, end, uniform_points)
        real_values = interpolate(real, grid)
        simulated_values = interpolate(simulated, grid)
    return grid, real_values, simulated_values


def compute_metrics_for_channel(channel: str, compared: pd.DataFrame) -> dict[str, float | int | str]:
    error = compared["difference"].to_numpy(dtype=float)
    real = compared["real"].to_numpy(dtype=float)
    simulated = compared["simulated"].to_numpy(dtype=float)
    correlation = np.nan
    if len(compared) > 1 and np.std(real) > 0 and np.std(simulated) > 0:
        correlation = float(np.corrcoef(real, simulated)[0, 1])
    return {
        "channel": channel,
        "samples": len(compared),
        "MAE": float(np.mean(np.abs(error))),
        "RMSE": float(np.sqrt(np.mean(np.square(error)))),
        "bias (real - simulated)": float(np.mean(error)),
        "max abs error": float(np.max(np.abs(error))),
        "correlation": correlation,
    }


@st.cache_data(max_entries=10)
def align_and_transform_datasets(
    _real_data_name: str, real_data: ParsedDataset,
    simulated_data: ParsedDataset,
    channels: Iterable[str],
    grid_mode: str,
    uniform_points: int,
    normalize_start: bool,
    real_time_shift: float,
    simulated_time_shift: float,
    real_scale: float,
    real_offset: float,
    simulated_scale: float,
    simulated_offset: float,
) -> tuple[pd.DataFrame, list[str]]:
    aligned_parts: list[pd.DataFrame] = []
    skipped: list[str] = []

    for channel in channels:
        real = transform_series(
            real_data.series[channel], normalize_start, real_time_shift, real_scale, real_offset
        )
        simulated = transform_series(
            simulated_data.series[channel],
            normalize_start,
            simulated_time_shift,
            simulated_scale,
            simulated_offset,
        )
        grid, real_values, simulated_values = comparison_grid(
            real, simulated, grid_mode, uniform_points
        )
        if grid.size == 0:
            skipped.append(f"{channel}: the transformed time ranges do not overlap.")
            continue

        compared = pd.DataFrame(
            {
                "channel": channel,
                "time_s": grid,
                "real": real_values,
                "simulated": simulated_values,
            }
        ).dropna()

        aligned_parts.append(compared)

    return (pd.concat(aligned_parts, ignore_index=True) if aligned_parts else pd.DataFrame(), skipped)


def process_aligned_data(
    aligned_unwindowed: pd.DataFrame,
    window_start: float | None,
    window_end: float | None,
) -> pd.DataFrame:
    """Applies time window and calculates error columns."""
    if aligned_unwindowed.empty:
        return pd.DataFrame()

    aligned = aligned_unwindowed
    if window_start is not None:
        aligned = aligned[aligned["time_s"] >= window_start]
    if window_end is not None:
        aligned = aligned[aligned["time_s"] <= window_end]

    if aligned.empty:
        return pd.DataFrame()

    aligned = aligned.copy()
    aligned["difference"] = aligned["real"] - aligned["simulated"]
    aligned["absolute_error"] = aligned["difference"].abs()
    denominator = aligned["real"].abs().where(aligned["real"].abs() > 1e-12)
    aligned["absolute_percentage_error"] = aligned["absolute_error"] / denominator * 100.0
    return aligned


def compute_all_metrics(aligned: pd.DataFrame) -> pd.DataFrame:
    metric_rows = [compute_metrics_for_channel(channel, group) for channel, group in aligned.groupby("channel")]
    return pd.DataFrame(metric_rows)


def overall_metrics(aligned: pd.DataFrame) -> dict[str, float]:
    error = aligned["difference"].to_numpy(dtype=float)
    return {
        "MAE": float(np.mean(np.abs(error))),
        "RMSE": float(np.sqrt(np.mean(np.square(error)))),
        "Bias": float(np.mean(error)),
        "Max absolute error": float(np.max(np.abs(error))),
    }


def transformed_range(
    dataset: ParsedDataset,
    channels: Iterable[str],
    normalize_start: bool,
    shift: float,
) -> tuple[float, float]:
    starts: list[float] = []
    ends: list[float] = []
    for channel in channels:
        frame = dataset.series[channel]
        start = 0.0 if normalize_start else float(frame["time_s"].iloc[0])
        end = float(frame["time_s"].iloc[-1] - frame["time_s"].iloc[0]) if normalize_start else float(frame["time_s"].iloc[-1])
        starts.append(start + shift)
        ends.append(end + shift)
    return min(starts), max(ends)


def signal_figure(aligned: pd.DataFrame, channels: list[str]) -> go.Figure:
    figure = go.Figure()
    palette = [
        "#636EFA", "#EF553B", "#00CC96", "#AB63FA", "#FFA15A",
        "#19D3F3", "#FF6692", "#B6E880", "#FF97FF", "#FECB52",
    ]
    for index, channel in enumerate(channels):
        subset = aligned[aligned["channel"] == channel]
        if subset.empty:
            continue
        color = palette[index % len(palette)]
        figure.add_trace(
            go.Scatter(
                x=subset["time_s"], y=subset["real"], mode="lines",
                name=f"{channel} · real", line={"color": color, "width": 2},
            )
        )
        figure.add_trace(
            go.Scatter(
                x=subset["time_s"], y=subset["simulated"], mode="lines",
                name=f"{channel} · simulated",
                line={"color": color, "width": 1.6, "dash": "dash"},
            )
        )
    figure.update_layout(
        xaxis_title="Time [s]",
        yaxis_title="Value",
        hovermode="x unified",
        legend_title="Series",
        margin={"l": 20, "r": 20, "t": 30, "b": 20},
    )
    return figure


def difference_figure(aligned: pd.DataFrame, channels: list[str]) -> go.Figure:
    figure = go.Figure()
    for channel in channels:
        subset = aligned[aligned["channel"] == channel]
        if subset.empty:
            continue
        figure.add_trace(
            go.Scatter(
                x=subset["time_s"], y=subset["difference"], mode="lines", name=channel
            )
        )
    figure.add_hline(y=0, line_width=1, line_dash="dot")
    figure.update_layout(
        xaxis_title="Time [s]",
        yaxis_title="Difference (real - simulated)",
        hovermode="x unified",
        margin={"l": 20, "r": 20, "t": 30, "b": 20},
    )
    return figure


def render_dataset_summary(dataset: ParsedDataset, parse_note: str) -> None:
    all_points = sum(len(frame) for frame in dataset.series.values())
    st.success(f"Detected {len(dataset.series)} channels and {all_points:,} valid samples.")
    st.caption(f"{parse_note} · header row {dataset.header_row + 1}")
    preview_rows = []
    for channel in dataset.channels:
        frame = dataset.series[channel]
        preview_rows.append(
            {
                "channel": channel,
                "unit": dataset.units.get(channel, ""),
                "samples": len(frame),
                "start [s]": frame["time_s"].iloc[0],
                "end [s]": frame["time_s"].iloc[-1],
            }
        )
    st.dataframe(pd.DataFrame(preview_rows), width="stretch", hide_index=True)
    if dataset.warnings:
        with st.expander(f"Parsing warnings ({len(dataset.warnings)})"):
            for warning in dataset.warnings:
                st.warning(warning)


def render_cfl_calculator():
    """Renders the CFL condition calculator tab."""
    st.markdown(
        """
        This calculator helps determine the maximum allowable time step (`dt`) for a stable simulation
        based on the Courant-Friedrichs-Lewy (CFL) condition.
        The CFL condition is a necessary condition for stability while solving certain partial differential equations numerically.
        """
    )
    st.latex(r"C = \frac{u \cdot \Delta t}{\Delta x} \le C_{max}")
    st.markdown("From this, we can calculate the maximum time step:")
    st.latex(r"\Delta t \le \frac{C_{max} \cdot \Delta x}{u}")

    st.header("Inputs")
    speed_of_sound = st.number_input("Max speed of sound in fluid (u) [m/s]", min_value=0.1, value=1500.0, step=10.0, format="%.2f")
    dx = st.number_input("Grid cell size (Δx) [m]", min_value=0.001, value=10.0, step=0.1, format="%.3f")
    courant_max = st.number_input("Target Courant number (C_max)", min_value=0.1, max_value=2.0, value=1.0, step=0.1)

    if speed_of_sound > 0 and dx > 0:
        dt_max = (courant_max * dx) / speed_of_sound
        st.header("Result")
        st.metric("Maximum recommended time step (Δt)", f"{dt_max:.6f} s")

def main() -> None:
    st.set_page_config(page_title="Real vs simulated data comparison", page_icon="📈", layout="wide")
    st.title("Real vs simulated data comparison")
    st.caption(
        "Upload two CSV/TSV or Excel files. PT/TT channel names are normalized automatically, "
        "then the time series are interpolated onto a common grid for comparison."
    )

    upload_left, upload_right = st.columns(2)
    with upload_left:
        st.subheader("Real / measured data")
        real_upload = st.file_uploader(
            "Upload measured file", type=["csv", "tsv", "txt", "dat", "xlsx", "xlsm", "xls", "tpl"], key="real"
        )
    with upload_right:
        st.subheader("Simulated data")
        simulated_upload = st.file_uploader(
            "Upload simulation file", type=["csv", "tsv", "txt", "dat", "xlsx", "xlsm", "xls", "tpl"], key="simulated"
        )

    # --- SIDEBAR (GLOBAL CONTROLS) ---
    # The sidebar must be defined outside the tabs to prevent re-rendering issues.
    # We use session_state to check if we can show the controls yet.
    if st.session_state.get("channels_submitted"):
        with st.sidebar:
            st.header("Alignment")
            normalize_start = st.toggle(
                "Set each channel's first time to 0", value=False,
                help="Applied before the manual time shifts below.",
            )
            real_time_shift = st.number_input("Measured time shift [s]", value=0.0, format="%.9f", key="real_time_shift")
            simulated_time_shift = st.number_input("Simulation time shift [s]", value=0.0, format="%.9f", key="simulated_time_shift")
            grid_mode = st.selectbox(
                "Comparison grid", ["Real timestamps", "Simulated timestamps", "Uniform overlap grid"], key="grid_mode"
            )
            uniform_points = st.number_input(
                "Uniform grid points", min_value=100, max_value=500_000, value=5000, step=100,
                disabled=grid_mode != "Uniform overlap grid", key="uniform_points"
            )

            st.header("Value transform")
            st.caption("Transformed value = original × scale + offset")
            real_scale = st.number_input("Measured scale", value=1.0, format="%.9f", key="real_scale")
            real_offset = st.number_input("Measured offset", value=0.0, format="%.9f", key="real_offset")
            simulated_scale = st.number_input("Simulation scale", value=0.00001, format="%.9f", key="simulated_scale")
            simulated_offset = st.number_input("Simulation offset", value=0.0, format="%.9f", key="simulated_offset")

            # Auto-scaling for Pa vs bar mismatch
            if st.session_state.get("real_data") and st.session_state.get("simulated_data"):
                real_units = st.session_state.real_data.units
                sim_units = st.session_state.simulated_data.units
                # Check if any selected channel has the mismatch
                auto_scale = any(
                    real_units.get(ch, "").lower() in ("bar", "bara", "barg") and sim_units.get(ch, "").lower() == "pa"
                    for ch in st.session_state.selected_channels
                )
                # Only apply auto-scale if the widget is still at its default value of 1.0
                is_default_scale = st.session_state.get("simulated_scale", 1.0) == 1.0
                if auto_scale and is_default_scale:
                    st.session_state.simulated_scale = 1e-5
                    # Re-run to update the widget display with the new value
                    st.rerun() 

            # The transformed_range call needs to happen here to get default window values
            if st.session_state.get("real_data") and st.session_state.get("simulated_data"):
                real_range = transformed_range(st.session_state.real_data, st.session_state.selected_channels, normalize_start, real_time_shift)
                sim_range = transformed_range(
                    st.session_state.simulated_data, st.session_state.selected_channels, normalize_start, simulated_time_shift
                )
                default_start = max(real_range[0], sim_range[0])
                default_end = min(real_range[1], sim_range[1])
                st.header("Time window")
                use_window = st.toggle("Limit comparison window", value=False, key="use_window")
                window_start = st.number_input(
                    "Window start [s]", value=float(default_start), format="%.9f", disabled=not use_window, key="window_start"
                )
                window_end = st.number_input(
                    "Window end [s]", value=float(default_end), format="%.9f", disabled=not use_window, key="window_end"
                )

    main_tabs = st.tabs(["Data Comparison", "CFL Calculator"])

    with main_tabs[0]:
        if real_upload is None or simulated_upload is None:
            st.info("Upload both files to start the comparison.")
            with st.expander("Expected formats"):
                st.markdown(
                    "- Multi-row text headers such as `tPT, PT201, PT202, ...` followed by metadata/unit rows.\n"
                    "- Excel headers such as `Time [s]`, `PT [bara] (PT201)` or `TM [C] (TT251)`, repeated for each channel.\n"
                    "- The header may occur within the first 200 rows; nonnumeric metadata rows are ignored."
                )
            return

        try:
            real_bytes = real_upload.getvalue()
            simulated_bytes = simulated_upload.getvalue()
        except Exception as e:
            st.error(f"Error reading files from path: {e}")
            return

        real_sheet = None
        simulated_sheet = None
        simulated_filepath = Path(simulated_upload.name)
        try:
            if Path(real_upload.name).suffix.lower() in EXCEL_SUFFIXES:
                real_sheets = excel_sheet_names(real_bytes, real_upload.name)
                real_sheet = st.selectbox("Measured Excel sheet", real_sheets, key="real_sheet")
            if simulated_filepath.suffix.lower() in EXCEL_SUFFIXES:
                simulated_sheets = excel_sheet_names(simulated_bytes, simulated_filepath.name)
                simulated_sheet = st.selectbox("Simulation Excel sheet", simulated_sheets, key="sim_sheet")
        except Exception as exc:
            st.error(f"The Excel workbook could not be opened: {exc}")
            return

        try:
            # Use file path as part of the cache key to handle different files
            real_data, real_note = get_parsed_dataset(
                real_upload.file_id, real_bytes, real_upload.name, "Real", real_sheet)
            simulated_data, simulated_note = get_parsed_dataset(
                str(simulated_filepath), simulated_bytes, simulated_filepath.name, "Simulated", simulated_sheet)
            # Store data in session state for the sidebar to access
            st.session_state.real_data = real_data
            st.session_state.simulated_data = simulated_data
        except (ValueError, IndexError, KeyError) as exc:
            st.error(f"Parsing failed: {exc}")
            st.exception(exc)
            return

        with st.expander("Detected datasets", expanded=False):
            left, right = st.columns(2)
            with left:
                st.markdown("#### Measured")
                render_dataset_summary(real_data, real_note)
            with right:
                st.markdown("#### Simulated")
                render_dataset_summary(simulated_data, simulated_note)

        common_channels = sorted(
            set(real_data.channels).intersection(simulated_data.channels), key=natural_channel_key
        )
        only_real = sorted(set(real_data.channels) - set(simulated_data.channels), key=natural_channel_key)
        only_simulated = sorted(
            set(simulated_data.channels) - set(real_data.channels), key=natural_channel_key
        )
        if not common_channels:
            st.error("No matching PT or TT channel identifiers were found between the two files.")
            st.write("Measured channels:", real_data.channels)
            st.write("Simulated channels:", simulated_data.channels)
            return

        if only_real or only_simulated:
            with st.expander("Unmatched channels"):
                if only_real:
                    st.write("Measured only:", only_real)
                if only_simulated:
                    st.write("Simulated only:", only_simulated)

        # Initialize session state for channel selection
        if "channels_submitted" not in st.session_state:
            st.session_state.channels_submitted = False
            st.session_state.selected_channels = []

        with st.form("channel_selection_form"):
            st.markdown("#### Channels to compare")
            select_all = st.checkbox("Select All")

            if select_all:
                default_selection = common_channels
            elif st.session_state.selected_channels:
                default_selection = st.session_state.selected_channels
            else:
                default_selection = common_channels[:min(6, len(common_channels))]

            multiselect_selection = st.multiselect(
                "Channels", common_channels, default=default_selection, label_visibility="collapsed"
            )
            
            submitted = st.form_submit_button("Apply selection")
            if submitted:
                st.session_state.selected_channels = multiselect_selection
                st.session_state.channels_submitted = True

        if not st.session_state.channels_submitted or not st.session_state.selected_channels:
            st.info("Select one or more channels from the form above and click 'Apply selection' to continue.")
            return

        unit_mismatches = [
            (
                channel,
                real_data.units.get(channel, ""),
                simulated_data.units.get(channel, ""),
            )
            for channel in st.session_state.selected_channels
            if real_data.units.get(channel, "")
            and simulated_data.units.get(channel, "")
            and real_data.units.get(channel, "").lower()
            != simulated_data.units.get(channel, "").lower()
        ]
        if unit_mismatches:
            mismatch_text = ", ".join(
                f"{channel}: {real_unit} vs {simulated_unit}"
                for channel, real_unit, simulated_unit in unit_mismatches
            )
            st.warning(
                "Different unit labels were detected (" + mismatch_text + "). "
                "No automatic unit conversion is applied; use the scale/offset controls when needed."
            )

        # Retrieve values from sidebar widgets (which are now in session_state)
        use_window = st.session_state.get("use_window", False)
        window_start = st.session_state.get("window_start", 0.0)
        window_end = st.session_state.get("window_end", 0.0)

        if use_window and window_end <= window_start:
            st.error("Window end must be greater than window start.")
            return

        aligned_unwindowed, skipped_channels = align_and_transform_datasets(
            _real_data_name=real_data.name, real_data=real_data,
            simulated_data=simulated_data,
            channels=st.session_state.selected_channels,
            grid_mode=st.session_state.get("grid_mode", "Real timestamps"),
            uniform_points=int(st.session_state.get("uniform_points", 5000)),
            normalize_start=st.session_state.get("normalize_start", False),
            real_time_shift=st.session_state.get("real_time_shift", 0.0),
            simulated_time_shift=st.session_state.get("simulated_time_shift", 0.0),
            real_scale=st.session_state.get("real_scale", 1.0),
            real_offset=st.session_state.get("real_offset", 0.0),
            simulated_scale=st.session_state.get("simulated_scale", 1.0),
            simulated_offset=st.session_state.get("simulated_offset", 0.0),
        )

        aligned_final = process_aligned_data(
            aligned_unwindowed,
            window_start=float(window_start) if use_window else None,
            window_end=float(window_end) if use_window else None,
        )

        if aligned_final.empty:
            st.error("No comparable samples remain after alignment. Check the time shifts and time window.")
            for message in skipped_channels:
                st.warning(message)
            return

        summary = overall_metrics(aligned_final)
        metric_columns = st.columns(4)
        for column, (label, value) in zip(metric_columns, summary.items()):
            column.metric(label, f"{value:.6g}")

        metrics = compute_all_metrics(aligned_final)

        signal_tab, difference_tab, grid_tab, metrics_tab, data_tab = st.tabs(
            ["Signals", "Difference", "Grid View", "Metrics", "Aligned data"]
        )
        with signal_tab:
            st.plotly_chart(signal_figure(aligned_final, st.session_state.selected_channels), use_container_width=True)
        with difference_tab:
            st.plotly_chart(difference_figure(aligned_final, st.session_state.selected_channels), use_container_width=True)
        with grid_tab:
            grid_cols = st.columns(3)
            for i, channel in enumerate(st.session_state.selected_channels):
                col = grid_cols[i % 3]
                with col:
                    st.markdown(f"**{channel}**")
                    channel_data = aligned_final[aligned_final["channel"] == channel]
                    if not channel_data.empty:
                        fig = go.Figure()
                        fig.add_trace(
                            go.Scatter(
                                x=channel_data["time_s"], y=channel_data["real"], mode="lines",
                                name="Real", line={"color": "#636EFA", "width": 2},
                            )
                        )
                        fig.add_trace(
                            go.Scatter(
                                x=channel_data["time_s"], y=channel_data["simulated"], mode="lines",
                                name="Simulated",
                                line={"color": "#EF553B", "width": 1.6, "dash": "dash"},
                            )
                        )
                        fig.update_layout(
                            xaxis_title="Time [s]",
                            yaxis_title="Value",
                            margin={"l": 20, "r": 20, "t": 40, "b": 20},
                            height=300,
                            showlegend=True,
                            legend=dict(
                                orientation="h",
                                yanchor="bottom",
                                y=1.02,
                                xanchor="right",
                                x=1
                            )
                        )
                        st.plotly_chart(fig, use_container_width=True)
                    else:
                        st.caption("No data in selected window.")
        with metrics_tab:
            st.dataframe(
                metrics.style.format(
                    {
                        "MAE": "{:.6g}",
                        "RMSE": "{:.6g}",
                        "bias (real - simulated)": "{:.6g}",
                        "max abs error": "{:.6g}",
                        "correlation": "{:.6g}",
                    }
                ),
                width="stretch",
                hide_index=True,
            )
            st.download_button(
                "Download metrics CSV",
                data=metrics.to_csv(index=False).encode("utf-8"),
                file_name="pressure_comparison_metrics.csv",
                mime="text/csv",
            )
        with data_tab:
            st.dataframe(aligned_final, use_container_width=True, hide_index=True)
            st.download_button(
                "Download aligned comparison CSV",
                data=aligned_final.to_csv(index=False).encode("utf-8"),
                file_name="pressure_comparison_aligned.csv",
                mime="text/csv",
            )

        if skipped_channels:
            with st.expander("Skipped channels / ranges"):
                for message in skipped_channels:
                    st.warning(message)

    with main_tabs[1]:
        render_cfl_calculator()


if __name__ == "__main__":
    main()

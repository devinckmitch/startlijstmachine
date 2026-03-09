from __future__ import annotations

import re
from io import BytesIO

import pandas as pd
from dateutil import parser as dateutil_parser


def normalise_name(raw) -> str:
    if not isinstance(raw, str):
        return ""
    return raw.strip().lower()


def display_name(normalised: str) -> str:
    return normalised.title()


def try_parse_date_from_filename(filename: str) -> str | None:
    # Strip extension and non-alphanumeric chars, then try dateutil
    stem = re.sub(r"\.[^.]+$", "", filename)
    candidate = re.sub(r"[_\-]", " ", stem)
    try:
        dt = dateutil_parser.parse(candidate, fuzzy=True)
        return dt.date().isoformat()
    except Exception:
        return None


def load_raw(file_bytes: bytes, filename: str) -> pd.DataFrame:
    ext = filename.rsplit(".", 1)[-1].lower()
    if ext == "csv":
        # Try common encodings (Belgian/Dutch files are often cp1252/latin-1)
        encodings = ["utf-8-sig", "cp1252", "latin-1"]
        for sep in [";", ",", "\t"]:
            for enc in encodings:
                try:
                    df = pd.read_csv(BytesIO(file_bytes), header=None, sep=sep, encoding=enc)
                    if len(df.columns) > 1:
                        return df
                except Exception:
                    continue
        return pd.read_csv(BytesIO(file_bytes), header=None, encoding="latin-1")
    engine = "openpyxl" if ext == "xlsx" else "xlrd"
    return pd.read_excel(BytesIO(file_bytes), header=None, engine=engine)


def detect_header_row(df: pd.DataFrame) -> int:
    """Heuristic: find the first row that looks like a header (mostly strings, no NaN majority)."""
    for i, row in df.iterrows():
        str_count = sum(isinstance(v, str) for v in row)
        if str_count >= max(2, len(row) // 2):
            return int(i)
    return 0


def apply_header(df: pd.DataFrame, header_row: int) -> pd.DataFrame:
    new_df = df.iloc[header_row + 1:].copy()
    new_df.columns = [str(c).strip() if isinstance(c, str) else str(c) for c in df.iloc[header_row]]
    new_df = new_df.reset_index(drop=True)
    return new_df


def detect_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    """Return first column name that matches one of the candidates (case-insensitive)."""
    lower_cols = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in lower_cols:
            return lower_cols[cand.lower()]
    return None


def parse_groups(
    df: pd.DataFrame,
    time_col: str | None,
    group_col: str,
    name_col: str,
    min_group_size: int = 2,
) -> list[dict]:
    """
    Returns a list of groups:
        [{"tee_time": "09:10", "slot_label": "09:10", "players": ["adams karl", ...]}, ...]
    """
    groups: dict[tuple, list[str]] = {}
    group_meta: dict[tuple, dict] = {}

    for _, row in df.iterrows():
        raw_name = row.get(name_col, "")
        name = normalise_name(str(raw_name) if not isinstance(raw_name, float) else "")
        if not name:
            continue

        raw_group = row.get(group_col, "")
        group_key_str = str(raw_group).strip() if pd.notna(raw_group) else ""

        raw_time = row.get(time_col, "") if time_col else ""
        time_str = _format_time(raw_time)

        # Use (time, group) as composite key so flight 1 at 9:10 != flight 1 at 10:00
        key = (time_str or "", group_key_str)

        if key not in groups:
            groups[key] = []
            group_meta[key] = {"tee_time": time_str, "slot_label": time_str or group_key_str}
        groups[key].append(name)

    result = []
    for key, players in groups.items():
        unique_players = list(dict.fromkeys(players))  # deduplicate, preserve order
        if len(unique_players) < min_group_size:
            continue
        result.append({**group_meta[key], "players": unique_players})
    return result


def _format_time(raw) -> str | None:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    # Already a time/datetime object
    if hasattr(raw, "strftime"):
        return raw.strftime("%H:%M")
    s = str(raw).strip()
    # Match HH:MM or H:MM
    m = re.match(r"^(\d{1,2}):(\d{2})", s)
    if m:
        return f"{int(m.group(1)):02d}:{m.group(2)}"
    return s if s else None

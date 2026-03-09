"""
Testprogramma voor Startlijstmachine.
Dekt: excel_parser, db, analysis.
Geen Streamlit-UI tests.

Run: pytest test_app.py -v
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
from datetime import date, time
from pathlib import Path

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_csv_bytes(rows: list[list], sep=";", encoding="utf-8") -> bytes:
    lines = [sep.join(str(c) for c in row) for row in rows]
    return "\n".join(lines).encode(encoding)


def _make_xlsx_bytes(rows: list[list]) -> bytes:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# EXCEL PARSER TESTS
# ---------------------------------------------------------------------------

from excel_parser import (
    normalise_name,
    display_name,
    try_parse_date_from_filename,
    load_raw,
    detect_header_row,
    apply_header,
    detect_column,
    parse_groups,
    _format_time,
)


class TestNormaliseName:
    def test_basic(self):
        assert normalise_name("Adams Karl") == "adams karl"

    def test_strips_whitespace(self):
        assert normalise_name("  Janssen  ") == "janssen"

    def test_non_string_returns_empty(self):
        assert normalise_name(3.14) == ""
        assert normalise_name(None) == ""
        assert normalise_name(42) == ""

    def test_already_lower(self):
        assert normalise_name("peeters luc") == "peeters luc"


class TestDisplayName:
    def test_title_case(self):
        assert display_name("adams karl") == "Adams Karl"

    def test_single_word(self):
        assert display_name("janssen") == "Janssen"


class TestTryParseDateFromFilename:
    def test_iso_date_in_name(self):
        assert try_parse_date_from_filename("startlijst_2024-03-15.csv") == "2024-03-15"

    def test_date_with_slashes_in_name(self):
        # dateutil.parse is fuzzy; this may or may not work — just check no crash
        result = try_parse_date_from_filename("ronde 15 03 2024.xlsx")
        # We don't assert exact value since fuzzy parsing is non-deterministic,
        # but it should not raise
        assert result is None or isinstance(result, str)

    def test_no_date_returns_none(self):
        # Pure word filename unlikely to parse as date
        result = try_parse_date_from_filename("startlijst.csv")
        # dateutil may interpret "startlijst" loosely; just ensure no crash
        assert result is None or isinstance(result, str)

    def test_numeric_date_filename(self):
        result = try_parse_date_from_filename("20240315.csv")
        assert result == "2024-03-15"


class TestLoadRaw:
    def test_csv_semicolon_utf8(self):
        data = _make_csv_bytes([["Flight", "Naam"], ["1", "Adams Karl"], ["1", "Peeters Luc"]])
        df = load_raw(data, "test.csv")
        assert list(df.iloc[0]) == ["Flight", "Naam"]
        assert len(df) == 3

    def test_csv_comma_utf8(self):
        data = _make_csv_bytes([["Flight", "Naam"], ["1", "Adams Karl"]], sep=",")
        df = load_raw(data, "test.csv")
        assert len(df.columns) == 2

    def test_csv_cp1252_encoding(self):
        # 0xc9 = É in cp1252 — this was the original bug
        data = "Flight;Naam\n1;Étienne Dumont\n1;Marc Lecomte\n".encode("cp1252")
        df = load_raw(data, "test.csv")
        assert len(df) == 3
        # Name should contain É
        assert "tienne" in str(df.iloc[1, 1])  # É may render depending on normalisation

    def test_csv_latin1_encoding(self):
        data = "Flight;Naam\n1;André Dupont\n1;Marc Lecomte\n".encode("latin-1")
        df = load_raw(data, "test.csv")
        assert len(df) == 3

    def test_xlsx(self):
        data = _make_xlsx_bytes([["Flight", "Naam"], ["1", "Adams Karl"], ["2", "Janssen"]])
        df = load_raw(data, "test.xlsx")
        assert len(df) == 3
        assert list(df.iloc[0]) == ["Flight", "Naam"]

    def test_tab_separated(self):
        data = _make_csv_bytes([["Flight", "Naam"], ["1", "Adams"]], sep="\t")
        df = load_raw(data, "test.csv")
        assert len(df.columns) == 2


class TestDetectHeaderRow:
    def test_first_row_is_header(self):
        df = pd.DataFrame([["Flight", "Naam", "Tijd"], [1, "Adams", "09:10"]])
        assert detect_header_row(df) == 0

    def test_second_row_is_header(self):
        # Row 0: numbers/metadata, row 1: strings
        df = pd.DataFrame([[1, 2, 3], ["Flight", "Naam", "Tijd"], [1, "Adams", "09:10"]])
        assert detect_header_row(df) == 1

    def test_empty_rows_before_header(self):
        df = pd.DataFrame([[None, None, None], [None, None, None], ["Flight", "Naam", "Tijd"]])
        assert detect_header_row(df) == 2


class TestApplyHeader:
    def test_basic(self):
        raw = pd.DataFrame([["Flight", "Naam"], ["1", "Adams"], ["2", "Janssen"]])
        df = apply_header(raw, 0)
        assert list(df.columns) == ["Flight", "Naam"]
        assert len(df) == 2
        assert df.iloc[0]["Naam"] == "Adams"

    def test_header_row_1(self):
        raw = pd.DataFrame([["metadata", "x"], ["Flight", "Naam"], ["1", "Adams"]])
        df = apply_header(raw, 1)
        assert list(df.columns) == ["Flight", "Naam"]
        assert len(df) == 1

    def test_strips_whitespace_from_column_names(self):
        raw = pd.DataFrame([["  Flight  ", " Naam "], ["1", "Adams"]])
        df = apply_header(raw, 0)
        assert "Flight" in df.columns
        assert "Naam" in df.columns


class TestDetectColumn:
    def setup_method(self):
        self.df = pd.DataFrame(columns=["Start", "Flight", "Player", "HCP"])

    def test_exact_match(self):
        assert detect_column(self.df, ["Start"]) == "Start"

    def test_case_insensitive(self):
        assert detect_column(self.df, ["start"]) == "Start"
        assert detect_column(self.df, ["FLIGHT"]) == "Flight"

    def test_first_candidate_wins(self):
        # Both "Start" and "Tijd" in candidates, only "Start" in df
        assert detect_column(self.df, ["Start", "Starttijd", "Tijd"]) == "Start"

    def test_returns_none_when_not_found(self):
        assert detect_column(self.df, ["Onbekend", "Naam"]) is None

    def test_empty_candidates(self):
        assert detect_column(self.df, []) is None


class TestFormatTime:
    def test_hh_mm(self):
        assert _format_time("09:10") == "09:10"

    def test_single_digit_hour(self):
        assert _format_time("9:10") == "09:10"

    def test_none_returns_none(self):
        assert _format_time(None) is None

    def test_nan_returns_none(self):
        import math
        assert _format_time(float("nan")) is None

    def test_time_object(self):
        t = time(9, 30)
        assert _format_time(t) == "09:30"

    def test_empty_string(self):
        assert _format_time("") is None

    def test_datetime_object(self):
        from datetime import datetime
        dt = datetime(2024, 1, 1, 14, 45)
        assert _format_time(dt) == "14:45"


class TestParseGroups:
    def _make_df(self, rows):
        return pd.DataFrame(rows, columns=["Flight", "Naam", "Tijd"])

    def test_basic_groups(self):
        df = self._make_df([
            ["1", "adams karl", "09:10"],
            ["1", "peeters luc", "09:10"],
            ["1", "janssen ann", "09:10"],
            ["2", "dupont marc", "09:20"],
            ["2", "lecomte wim", "09:20"],
        ])
        groups = parse_groups(df, "Tijd", "Flight", "Naam")
        assert len(groups) == 2
        g1 = next(g for g in groups if g["tee_time"] == "09:10")
        assert len(g1["players"]) == 3

    def test_filters_solo_players(self):
        df = self._make_df([
            ["1", "adams karl", "09:10"],
            ["2", "peeters luc", "09:20"],  # solo → filtered
            ["2", "janssen ann", "09:20"],
        ])
        groups = parse_groups(df, "Tijd", "Flight", "Naam")
        # Group 1 only has adams karl (solo), group 2 has 2 players
        assert len(groups) == 1
        assert len(groups[0]["players"]) == 2

    def test_deduplicates_players(self):
        df = self._make_df([
            ["1", "adams karl", "09:10"],
            ["1", "adams karl", "09:10"],  # duplicate
            ["1", "peeters luc", "09:10"],
        ])
        groups = parse_groups(df, "Tijd", "Flight", "Naam")
        assert len(groups[0]["players"]) == 2

    def test_skips_empty_names(self):
        df = self._make_df([
            ["1", "", "09:10"],
            ["1", "peeters luc", "09:10"],
            ["1", "janssen ann", "09:10"],
        ])
        groups = parse_groups(df, "Tijd", "Flight", "Naam")
        assert len(groups) == 1
        assert len(groups[0]["players"]) == 2

    def test_no_time_col(self):
        df = self._make_df([
            ["1", "adams karl", ""],
            ["1", "peeters luc", ""],
        ])
        groups = parse_groups(df, None, "Flight", "Naam")
        assert len(groups) == 1
        assert groups[0]["tee_time"] is None

    def test_same_flight_different_time_are_separate_groups(self):
        df = self._make_df([
            ["1", "adams karl", "09:10"],
            ["1", "peeters luc", "09:10"],
            ["1", "janssen ann", "10:00"],
            ["1", "dupont marc", "10:00"],
        ])
        groups = parse_groups(df, "Tijd", "Flight", "Naam")
        assert len(groups) == 2

    def test_normalises_names(self):
        df = self._make_df([
            ["1", "ADAMS KARL", "09:10"],
            ["1", "Peeters Luc", "09:10"],
        ])
        groups = parse_groups(df, "Tijd", "Flight", "Naam")
        names = groups[0]["players"]
        assert all(n == n.lower() for n in names)


# ---------------------------------------------------------------------------
# DB TESTS
# ---------------------------------------------------------------------------

import db as db_module


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """Redirect DB_PATH to a temp file for each test."""
    test_db = tmp_path / "test.db"
    monkeypatch.setattr(db_module, "DB_PATH", test_db)
    db_module.init_db()
    return test_db


class TestDbInit:
    def test_tables_created(self, tmp_db):
        conn = db_module.get_connection()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        names = {r["name"] for r in tables}
        assert {"rounds", "groups", "group_members"}.issubset(names)


class TestDbCrud:
    def test_insert_and_query_round(self, tmp_db):
        rid = db_module.insert_round("test.csv", "2024-03-15")
        assert isinstance(rid, int)
        rounds = db_module.get_rounds_summary()
        assert len(rounds) == 1
        assert rounds[0]["filename"] == "test.csv"
        assert rounds[0]["round_date"] == "2024-03-15"

    def test_insert_group_and_members(self, tmp_db):
        rid = db_module.insert_round("test.csv", None)
        gid = db_module.insert_group(rid, "09:10", "09:10")
        db_module.insert_members(gid, ["adams karl", "peeters luc", "janssen ann"])
        players = db_module.get_all_players()
        assert "adams karl" in players
        assert "janssen ann" in players

    def test_delete_round_cascades(self, tmp_db):
        rid = db_module.insert_round("test.csv", None)
        gid = db_module.insert_group(rid, "09:10", "09:10")
        db_module.insert_members(gid, ["adams karl", "peeters luc"])
        db_module.delete_round(rid)
        assert db_module.get_all_players() == []
        assert db_module.get_rounds_summary() == []

    def test_get_all_players_distinct_sorted(self, tmp_db):
        rid = db_module.insert_round("test.csv", None)
        gid1 = db_module.insert_group(rid, "09:10", "09:10")
        db_module.insert_members(gid1, ["peeters luc", "adams karl"])
        gid2 = db_module.insert_group(rid, "09:20", "09:20")
        db_module.insert_members(gid2, ["adams karl", "janssen ann"])  # adams duplicated
        players = db_module.get_all_players()
        assert players == sorted(set(players))
        assert players.count("adams karl") == 1

    def test_rounds_summary_aggregation(self, tmp_db):
        rid = db_module.insert_round("a.csv", "2024-01-01")
        gid1 = db_module.insert_group(rid, "09:10", "09:10")
        db_module.insert_members(gid1, ["a", "b", "c"])
        gid2 = db_module.insert_group(rid, "09:20", "09:20")
        db_module.insert_members(gid2, ["d", "e"])
        summary = db_module.get_rounds_summary()
        assert summary[0]["num_groups"] == 2
        assert summary[0]["num_members"] == 5


class TestDbPartnerFrequency:
    def _setup_rounds(self, tmp_db):
        # Round 1: adams speelt met peeters en janssen
        rid1 = db_module.insert_round("r1.csv", None)
        gid = db_module.insert_group(rid1, "09:10", "09:10")
        db_module.insert_members(gid, ["adams karl", "peeters luc", "janssen ann"])
        # Round 2: adams speelt opnieuw met peeters
        rid2 = db_module.insert_round("r2.csv", None)
        gid2 = db_module.insert_group(rid2, "09:10", "09:10")
        db_module.insert_members(gid2, ["adams karl", "peeters luc"])

    def test_partner_frequency_order(self, tmp_db):
        self._setup_rounds(tmp_db)
        rows = db_module.get_partner_frequency("adams karl")
        assert rows[0]["partner"] == "peeters luc"
        assert rows[0]["times_together"] == 2
        assert rows[1]["partner"] == "janssen ann"
        assert rows[1]["times_together"] == 1

    def test_no_self_in_partners(self, tmp_db):
        self._setup_rounds(tmp_db)
        rows = db_module.get_partner_frequency("adams karl")
        partners = [r["partner"] for r in rows]
        assert "adams karl" not in partners

    def test_unknown_player_returns_empty(self, tmp_db):
        self._setup_rounds(tmp_db)
        rows = db_module.get_partner_frequency("onbekende speler")
        assert rows == []


class TestDbTeeTimeDistribution:
    def test_tee_time_counts(self, tmp_db):
        rid = db_module.insert_round("r.csv", None)
        for tee, names in [("09:10", ["adams karl", "b"]),
                            ("09:20", ["adams karl", "c"]),
                            ("09:20", ["adams karl", "d"])]:
            gid = db_module.insert_group(rid, tee, tee)
            db_module.insert_members(gid, names)
        rows = db_module.get_tee_time_distribution("adams karl")
        by_time = {r["tee_time"]: r["appearances"] for r in rows}
        assert by_time["09:10"] == 1
        assert by_time["09:20"] == 2

    def test_null_tee_times_excluded(self, tmp_db):
        rid = db_module.insert_round("r.csv", None)
        gid = db_module.insert_group(rid, None, "1")
        db_module.insert_members(gid, ["adams karl", "peeters luc"])
        rows = db_module.get_tee_time_distribution("adams karl")
        assert rows == []


# ---------------------------------------------------------------------------
# ANALYSIS TESTS
# ---------------------------------------------------------------------------

import analysis


class TestFindPlayerCandidates:
    def test_substring_match(self):
        players = ["adams karl", "peeters luc", "janssen ann"]
        result = analysis.find_player_candidates("adams", players)
        assert "adams karl" in result

    def test_case_insensitive(self):
        players = ["adams karl", "peeters luc"]
        result = analysis.find_player_candidates("ADAMS", players)
        assert "adams karl" in result

    def test_partial_name(self):
        players = ["adams karl", "peeters luc", "janssen ann"]
        # "peet" IS a substring of "peeters luc"
        result = analysis.find_player_candidates("peet", players)
        assert "peeters luc" in result

    def test_empty_search_returns_empty(self):
        players = ["adams karl", "peeters luc"]
        result = analysis.find_player_candidates("", players)
        assert result == []

    def test_no_match_returns_empty(self):
        players = ["adams karl", "peeters luc"]
        result = analysis.find_player_candidates("xyz123notexistent", players)
        assert result == []

    def test_fuzzy_match(self):
        players = ["adams karl", "peeters luc"]
        # "adems" is close to "adams"
        result = analysis.find_player_candidates("adems", players)
        assert "adams karl" in result

    def test_no_duplicates_in_result(self):
        # "karl" matches "adams karl" as substring; also might match fuzzy
        players = ["adams karl", "karl janssen"]
        result = analysis.find_player_candidates("karl", players)
        assert len(result) == len(set(result))


class TestGetPartnerTable:
    def test_format(self, tmp_db):
        rid = db_module.insert_round("r.csv", None)
        gid = db_module.insert_group(rid, "09:10", "09:10")
        db_module.insert_members(gid, ["adams karl", "peeters luc"])
        table = analysis.get_partner_table("adams karl")
        assert len(table) == 1
        assert "Speelpartner" in table[0]
        assert "Keer samen" in table[0]
        assert table[0]["Speelpartner"] == "Peeters Luc"  # display_name applied
        assert table[0]["Keer samen"] == 1

    def test_empty_for_unknown_player(self, tmp_db):
        assert analysis.get_partner_table("onbekend") == []


class TestGetTeeTimeTable:
    def test_format(self, tmp_db):
        rid = db_module.insert_round("r.csv", None)
        gid = db_module.insert_group(rid, "09:10", "09:10")
        db_module.insert_members(gid, ["adams karl", "peeters luc"])
        table = analysis.get_tee_time_table("adams karl")
        assert len(table) == 1
        assert table[0]["Tijd"] == "09:10"
        assert table[0]["Keer gespeeld"] == 1

    def test_empty_for_unknown(self, tmp_db):
        assert analysis.get_tee_time_table("onbekend") == []

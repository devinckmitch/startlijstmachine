from __future__ import annotations

import pandas as pd
import streamlit as st

import db
import analysis
from excel_parser import (
    apply_header,
    detect_column,
    detect_header_row,
    load_raw,
    parse_groups,
    try_parse_date_from_filename,
    display_name,
    normalise_name,
)

st.set_page_config(page_title="Startlijstmachine", page_icon="⛳", layout="wide")
db.init_db()

st.title("⛳ Startlijstmachine")
st.caption("Upload golf-startlijsten, ontdek speelpartners en teetime-voorkeuren.")

tab_upload, tab_history, tab_search, tab_info = st.tabs(
    ["📤 Upload", "📋 Geschiedenis", "🔍 Zoeken", "ℹ️ Info"]
)

# ---------------------------------------------------------------------------
# TAB: UPLOAD
# ---------------------------------------------------------------------------
with tab_upload:
    st.header("Startlijst importeren")

    uploaded_file = st.file_uploader(
        "Kies een Excel-startlijst (.xlsx of .xls)",
        type=["xlsx", "xls"],
        key="uploader",
    )

    if uploaded_file is not None:
        file_bytes = uploaded_file.read()
        filename = uploaded_file.name

        # --- Step 1: raw preview ---
        st.subheader("Stap 1 – Ruwe data")
        try:
            raw_df = load_raw(file_bytes, filename)
        except Exception as e:
            st.error(f"Kon het bestand niet inladen: {e}")
            st.stop()

        st.dataframe(raw_df.head(20), use_container_width=True)

        # --- Step 2: header row ---
        st.subheader("Stap 2 – Kies de headerrij")
        auto_header = detect_header_row(raw_df)
        header_row = st.number_input(
            "Rijnummer van de kolomkoppen (0 = eerste rij)",
            min_value=0,
            max_value=max(0, len(raw_df) - 1),
            value=auto_header,
            step=1,
        )
        df = apply_header(raw_df, int(header_row))

        st.write("**Kolommen gevonden:**", list(df.columns))

        # --- Step 3: column mapping ---
        st.subheader("Stap 3 – Kolommen koppelen")

        all_cols = list(df.columns)
        none_option = "— geen —"
        col_options = [none_option] + all_cols

        default_time  = detect_column(df, ["Start", "Starttijd", "Tijd", "Time", "Tee time"])
        default_group = detect_column(df, ["Flight", "Groep", "Group", "Vlucht"])
        default_name  = detect_column(df, ["Player", "Speler", "Name", "Naam", "Spelers"])

        col1, col2, col3 = st.columns(3)
        with col1:
            time_col_sel = st.selectbox(
                "Teetime kolom",
                col_options,
                index=col_options.index(default_time) if default_time else 0,
            )
        with col2:
            group_col_sel = st.selectbox(
                "Groep / Flight kolom *",
                all_cols,
                index=all_cols.index(default_group) if default_group else 0,
            )
        with col3:
            name_col_sel = st.selectbox(
                "Naam kolom *",
                all_cols,
                index=all_cols.index(default_name) if default_name else 0,
            )

        time_col = None if time_col_sel == none_option else time_col_sel

        # Optional: date
        detected_date = try_parse_date_from_filename(filename)
        round_date = st.date_input(
            "Datum van de ronde (optioneel)",
            value=pd.to_datetime(detected_date).date() if detected_date else None,
        )
        round_date_str = round_date.isoformat() if round_date else None

        # --- Step 4: preview parsed groups ---
        st.subheader("Stap 4 – Preview geparseerde groepen")
        try:
            groups = parse_groups(df, time_col, group_col_sel, name_col_sel)
        except Exception as e:
            st.error(f"Fout bij verwerking: {e}")
            st.stop()

        if not groups:
            st.warning("Geen geldige groepen gevonden. Controleer de kolommen.")
        else:
            preview_rows = []
            for g in groups[:5]:
                preview_rows.append({
                    "Tijd": g["tee_time"] or "–",
                    "Spelers": ", ".join(display_name(p) for p in g["players"]),
                })
            st.dataframe(pd.DataFrame(preview_rows), use_container_width=True)
            st.info(f"Totaal: **{len(groups)} groepen** gevonden in dit bestand.")

            # --- Step 5: confirm ---
            st.subheader("Stap 5 – Importeren")
            if st.button("✅ Bevestig import", type="primary"):
                round_id = db.insert_round(filename, round_date_str)
                for g in groups:
                    group_id = db.insert_group(round_id, g["tee_time"], g["slot_label"])
                    db.insert_members(group_id, g["players"])
                st.success(
                    f"Import geslaagd! {len(groups)} groepen opgeslagen uit '{filename}'."
                )
                st.balloons()

# ---------------------------------------------------------------------------
# TAB: HISTORY
# ---------------------------------------------------------------------------
with tab_history:
    st.header("Geïmporteerde rondes")

    rounds = db.get_rounds_summary()
    if not rounds:
        st.info("Nog geen rondes geïmporteerd. Gebruik het Upload-tabblad.")
    else:
        for r in rounds:
            col_a, col_b = st.columns([5, 1])
            with col_a:
                datum = r["round_date"] or "datum onbekend"
                st.markdown(
                    f"**{r['filename']}** — {datum}  \n"
                    f"*{r['num_groups']} groepen · {r['num_members']} deelnames · "
                    f"geüpload op {r['uploaded_at'][:16]}*"
                )
            with col_b:
                if st.button("🗑️ Verwijder", key=f"del_{r['id']}"):
                    db.delete_round(r["id"])
                    st.rerun()

# ---------------------------------------------------------------------------
# TAB: SEARCH
# ---------------------------------------------------------------------------
with tab_search:
    st.header("Zoek een speler")

    all_players = db.get_all_players()

    if not all_players:
        st.info("Nog geen spelers in de database. Upload eerst een startlijst.")
    else:
        search_input = st.text_input("Naam (of deel van naam)", placeholder="bijv. Adams")

        if search_input:
            candidates = analysis.find_player_candidates(search_input, all_players)

            if not candidates:
                st.warning("Geen spelers gevonden. Probeer een andere zoekopdracht.")
            else:
                selected = st.selectbox(
                    "Selecteer speler",
                    candidates,
                    format_func=display_name,
                )

                if selected:
                    st.subheader(f"Resultaten voor {display_name(selected)}")

                    col_left, col_right = st.columns(2)

                    with col_left:
                        st.markdown("#### Meest frequente speelpartners")
                        partner_data = analysis.get_partner_table(selected)
                        if partner_data:
                            st.dataframe(
                                pd.DataFrame(partner_data),
                                use_container_width=True,
                                hide_index=True,
                            )
                        else:
                            st.info("Geen speelpartners gevonden.")

                    with col_right:
                        st.markdown("#### Teetime-voorkeur")
                        tee_data = analysis.get_tee_time_table(selected)
                        if tee_data:
                            tee_df = pd.DataFrame(tee_data).set_index("Tijd")
                            st.bar_chart(tee_df["Keer gespeeld"])
                            st.dataframe(tee_df, use_container_width=True)
                        else:
                            st.info("Geen teetime-data beschikbaar.")

# ---------------------------------------------------------------------------
# TAB: INFO
# ---------------------------------------------------------------------------
with tab_info:
    st.header("Over Startlijstmachine")
    st.markdown("""
**Startlijstmachine** analyseert golf-startlijsten en helpt je ontdekken:
- met wie een speler het vaakst speelt
- op welke tijdstippen een speler het liefst op de baan staat

---

### Hoe werkt het?

1. **Upload** een startlijst in Excel-formaat (.xlsx of .xls)
2. Koppel de juiste kolommen (teetime, groepsnummer, naam)
3. **Zoek** op een speler om de resultaten te zien

---

### Verwacht Excel-formaat

Het systeem verwacht een tabel waarbij **elke rij één speler** is, met kolommen zoals:

| Start | Flight | Player | ... |
|-------|--------|--------|-----|
| 9:10  | 1      | Adams Karl | ... |
| 9:10  | 1      | Lemahieu Mike | ... |
| 9:20  | 2      | Bouckaert Cedric | ... |

Spelers in dezelfde **Flight** met dezelfde **Start**-tijd worden als groep beschouwd.

---

### Tips
- Kolommen mogen andere namen hebben; je kan ze handmatig koppelen bij het uploaden
- Meerdere bestanden uploaden = cumulatieve analyse over alle rondes
- Gebruik de **Geschiedenis**-tab om rondes te verwijderen
""")

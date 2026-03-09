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

tab_upload, tab_history, tab_search, tab_samen, tab_info = st.tabs(
    ["📤 Upload", "📋 Geschiedenis", "🔍 Zoeken", "🤝 Wie samen?", "ℹ️ Info"]
)

# ---------------------------------------------------------------------------
# TAB: UPLOAD
# ---------------------------------------------------------------------------
with tab_upload:
    st.header("Startlijst importeren")

    uploaded_files = st.file_uploader(
        "Kies één of meerdere startlijsten (.xlsx, .xls of .csv)",
        type=["xlsx", "xls", "csv"],
        accept_multiple_files=True,
        key="uploader",
    )

    if uploaded_files:
        # Read all bytes upfront so seek/rerun issues don't matter
        files_data = [(uf.name, uf.read()) for uf in uploaded_files]

        # --- Duplicate check ---
        duplicates = []
        for fname, fbytes in files_data:
            h = db.file_hash(fbytes)
            existing = db.find_round_by_hash(h)
            if existing:
                duplicates.append((fname, existing))

        if duplicates:
            for fname, ex in duplicates:
                st.warning(
                    f"**{fname}** werd al eerder geïmporteerd "
                    f"(als '{ex['filename']}' op {ex['uploaded_at'][:16]}). "
                    "Dit bestand wordt overgeslagen tenzij je het toch importeert."
                )

        # --- Step 1: raw preview van eerste bestand ---
        st.subheader("Stap 1 – Ruwe data (eerste bestand)")
        first_name, first_bytes = files_data[0]
        try:
            raw_df = load_raw(first_bytes, first_name)
        except Exception as e:
            st.error(f"Kon het bestand niet inladen: {e}")
            st.stop()

        st.dataframe(raw_df.head(20), use_container_width=True)
        if len(uploaded_files) > 1:
            st.info(f"{len(uploaded_files)} bestanden geselecteerd. Kolom-instellingen gelden voor alle bestanden.")

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

        # --- Step 3: column mapping + groepsgrootte ---
        st.subheader("Stap 3 – Kolommen koppelen")

        all_cols = list(df.columns)
        none_option = "— geen —"
        col_options = [none_option] + all_cols

        default_time  = detect_column(df, ["Start", "Starttijd", "Tijd", "Time", "Tee time"])
        default_group = detect_column(df, ["Flight", "Groep", "Group", "Vlucht"])
        default_name  = detect_column(df, ["Player", "Speler", "Name", "Naam", "Spelers"])

        col1, col2, col3, col4 = st.columns(4)
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
        with col4:
            min_group_size = st.number_input(
                "Min. spelers per groep",
                min_value=1,
                max_value=8,
                value=2,
                step=1,
                help="Groepen met minder spelers worden niet opgeslagen.",
            )

        time_col = None if time_col_sel == none_option else time_col_sel

        # --- Step 4: verwerk alle bestanden ---
        st.subheader("Stap 4 – Preview geparseerde groepen")

        dup_hashes = {db.file_hash(fbytes) for _, fbytes in files_data
                      if db.find_round_by_hash(db.file_hash(fbytes))}

        all_file_results = []
        errors = []
        for fname, fbytes in files_data:
            try:
                rdf = load_raw(fbytes, fname)
                fdf = apply_header(rdf, int(header_row))
                missing = [c for c in [group_col_sel, name_col_sel, time_col] if c and c not in fdf.columns]
                if missing:
                    errors.append(f"**{fname}**: kolommen niet gevonden: {missing} (beschikbaar: {list(fdf.columns)[:8]}…)")
                    continue
                grps = parse_groups(fdf, time_col, group_col_sel, name_col_sel, int(min_group_size))
                detected_date = try_parse_date_from_filename(fname)
                all_file_results.append({
                    "filename": fname,
                    "file_hash": db.file_hash(fbytes),
                    "groups": grps,
                    "detected_date": detected_date,
                    "is_duplicate": db.file_hash(fbytes) in dup_hashes,
                })
            except Exception as e:
                errors.append(f"**{fname}**: {e}")

        if errors:
            for err in errors:
                st.error(f"Fout bij inladen {err}")

        total_groups = sum(len(r["groups"]) for r in all_file_results)

        if not all_file_results or total_groups == 0:
            st.warning("Geen geldige groepen gevonden. Controleer de kolommen.")
        else:
            summary_rows = [
                {
                    "Bestand": r["filename"],
                    "Groepen": len(r["groups"]),
                    "Al geïmporteerd": "⚠️ ja" if r["is_duplicate"] else "nee",
                }
                for r in all_file_results
            ]
            st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)

            preview_rows = []
            for g in all_file_results[0]["groups"][:5]:
                preview_rows.append({
                    "Tijd": g["tee_time"] or "–",
                    "Spelers": ", ".join(display_name(p) for p in g["players"]),
                })
            st.caption(f"Eerste 5 groepen uit '{all_file_results[0]['filename']}':")
            st.dataframe(pd.DataFrame(preview_rows), use_container_width=True, hide_index=True)
            st.info(f"Totaal: **{total_groups} groepen** in **{len(all_file_results)} bestand(en)**.")

            # --- Step 5: confirm ---
            st.subheader("Stap 5 – Importeren")
            skip_duplicates = st.checkbox("Sla al geïmporteerde bestanden over", value=True)

            if st.button("✅ Bevestig import", type="primary"):
                imported = 0
                skipped = 0
                for result in all_file_results:
                    if skip_duplicates and result["is_duplicate"]:
                        skipped += 1
                        continue
                    detected_date = result["detected_date"]
                    round_date_str = pd.to_datetime(detected_date).date().isoformat() if detected_date else None
                    round_id = db.insert_round(result["filename"], round_date_str, result["file_hash"])
                    for g in result["groups"]:
                        group_id = db.insert_group(round_id, g["tee_time"], g["slot_label"])
                        db.insert_members(group_id, g["players"])
                    imported += len(result["groups"])
                msg = f"Import geslaagd! {imported} groepen opgeslagen."
                if skipped:
                    msg += f" {skipped} duplicaat bestand(en) overgeslagen."
                st.success(msg)
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
                        st.markdown("#### Teetime-voorkeur per uur")
                        hour_data = analysis.get_tee_time_by_hour(selected)
                        if hour_data:
                            hour_df = pd.DataFrame(hour_data).set_index("Uur")
                            st.bar_chart(hour_df["Keer gespeeld"])

                            with st.expander("Bekijk per exact tijdstip"):
                                tee_data = analysis.get_tee_time_table(selected)
                                st.dataframe(
                                    pd.DataFrame(tee_data),
                                    use_container_width=True,
                                    hide_index=True,
                                )
                        else:
                            st.info("Geen teetime-data beschikbaar.")

# ---------------------------------------------------------------------------
# TAB: WIE SPEELT GOED SAMEN?
# ---------------------------------------------------------------------------
with tab_samen:
    st.header("Wie speelt goed samen?")
    st.caption("Selecteer 2 of meer spelers en zie hoe vaak elk koppel samen in een flight stond.")

    all_players_samen = db.get_all_players()

    if not all_players_samen:
        st.info("Nog geen spelers in de database. Upload eerst een startlijst.")
    else:
        selected_players = st.multiselect(
            "Kies spelers",
            options=all_players_samen,
            format_func=display_name,
            placeholder="Typ een naam om te zoeken…",
        )

        if len(selected_players) < 2:
            st.info("Selecteer minimaal 2 spelers.")
        else:
            pair_data = analysis.get_pair_frequencies(selected_players)
            if not pair_data:
                st.warning("Deze spelers hebben nog nooit samen in een flight gestaan.")
            else:
                st.dataframe(
                    pd.DataFrame(pair_data),
                    use_container_width=True,
                    hide_index=True,
                )
                # Visual: bar chart per pair
                chart_df = pd.DataFrame(pair_data)
                chart_df["Koppel"] = chart_df["Speler 1"] + " & " + chart_df["Speler 2"]
                st.bar_chart(chart_df.set_index("Koppel")["Keer samen"])

# ---------------------------------------------------------------------------
# TAB: INFO
# ---------------------------------------------------------------------------
with tab_info:
    st.header("Over Startlijstmachine")
    st.markdown("""
**Startlijstmachine** analyseert golf-startlijsten en helpt je ontdekken:
- met wie een speler het vaakst speelt
- op welke tijdstippen een speler het liefst op de baan staat
- welke koppels het vaakst samen in een flight staan

---

### Hoe werkt het?

1. **Upload** een startlijst (.xlsx, .xls of .csv)
2. Koppel de juiste kolommen (teetime, groepsnummer, naam)
3. **Zoek** op een speler om de resultaten te zien
4. Gebruik **Wie samen?** om meerdere spelers te vergelijken

---

### Verwacht bestandsformaat

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
- Dubbele bestanden worden automatisch herkend en kunnen overgeslagen worden
- Gebruik de **Geschiedenis**-tab om rondes te verwijderen
""")

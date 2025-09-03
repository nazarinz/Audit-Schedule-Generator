# streamlit_jadwal_piket_app.py
# ======================================================
# JADWAL PIKET Teh Danti — Streamlit App
# 1 MINGGU (atau multi-minggu) — 1 SHEET / MINGGU, 5 HARI BERJAJAR
# - Upload "Nama,Team"
# - Gedung FIX, Nama diacak adil (max 2x/minggu, anti-berturut)
# - Export Excel dengan format sesuai (judul hari, blok A/B/C/D, nomor reset)
# ======================================================

import io
import os
from datetime import date, datetime, timedelta
from typing import List, Dict

import numpy as np
import pandas as pd
import streamlit as st
from dateutil.relativedelta import relativedelta, MO
import holidays

# ---------------------------
# Konfigurasi Default
# ---------------------------
APP_TITLE = "Jadwal Piket Teh Danti"
DEFAULT_ORANG_PER_HARI = 17
DEFAULT_MAX_PER_ORG_PER_MINGGU = 2
DEFAULT_JUMLAH_MINGGU = 1  # bisa diubah di UI
DEFAULT_LARANG_BERTURUT = True
DEFAULT_RESET_NUMBER_PER_GROUP = True
DEFAULT_RANDOM_SEED = None  # None -> random tiap run

SLOT_TEMPLATE = [
    # A (5)
    "A1,3,5,7",
    "A2,4,6,8",
    "A9,11,13,15",
    "A 10,12 FG A",
    "A 14,16 FG A",
    # B (5)
    "B1,3,5,7",
    "B2,4,6,8",
    "B 9,11,13,15",
    "B 10,12 FG B",
    "B 14,16 FG B",
    # C (5)
    "C1,3,5,7",
    "C2,4,6,8",
    "C9,11,13,15",
    "C 10,12,17 FG C",
    "C 14,16,17 FG C",
    # D (2)
    "D 1,3,5,FG D",
    "D 2,4,6 FG D",
]

HARI_ID  = ["Senin","Selasa","Rabu","Kamis","Jumat","Sabtu","Minggu"]
BULAN_ID = [
    "Januari","Februari","Maret","April","Mei","Juni",
    "Juli","Agustus","September","Oktober","November","Desember"
]

# ---------------------------
# Helper Tanggal & Libur
# ---------------------------
ID_HOLIDAYS = holidays.country_holidays("ID")

def fmt_tanggal_id(d: date) -> str:
    return f"{HARI_ID[d.weekday()]}, {d.day} {BULAN_ID[d.month-1]} {d.year}"


def monday_near_future(today: date | None = None) -> date:
    if today is None:
        today = date.today()
    # Senin terdekat ke depan (minggu depan jika bukan Senin)
    return today + relativedelta(weekday=MO(+1))


def workdays_in_week(monday_date: date) -> List[date]:
    days = []
    for i in range(5):
        d = monday_date + timedelta(days=i)
        if d not in ID_HOLIDAYS:  # exclude libur nasional
            days.append(d)
    return days

# ---------------------------
# Penjadwalan
# ---------------------------

def schedule_one_week(
    df_people: pd.DataFrame,
    monday_date: date,
    orang_per_hari: int,
    max_per_minggu: int,
    larang_berturut: bool,
    random_seed: int | None = None,
) -> pd.DataFrame:
    """Return DataFrame kolom: [No, Nama, Team, Gedung, Tanggal]."""
    work_days = workdays_in_week(monday_date)
    names = df_people["Nama"].tolist()

    # minimal personel check (hanya info; bisa tetap jalan jika ada libur menurunkan slot)
    total_slot = len(work_days) * orang_per_hari
    min_people = int(np.ceil(total_slot / max_per_minggu)) if max_per_minggu > 0 else len(names)
    if len(names) < min_people:
        st.warning(
            f"Personel {len(names)} < minimum {min_people} untuk batas {max_per_minggu}x/minggu (slot {total_slot}).\n"
            "Akan gagal jika tidak cukup kandidat. Tambah personel atau turunkan jumlah slot.")

    rng = np.random.default_rng() if random_seed is None else np.random.default_rng(random_seed)

    quota: Dict[str, int] = {n: 0 for n in names}
    last_day: Dict[str, date | None] = {n: None for n in names}

    def eligible(nm: str, d: date, strict: bool = True) -> bool:
        if quota[nm] >= max_per_minggu:
            return False
        if larang_berturut and strict and last_day[nm] is not None and (d - last_day[nm]).days == 1:
            return False
        return True

    rows = []
    for d in work_days:
        picks: List[str] = []
        remaining = orang_per_hari
        # pilih dari group kuota terendah dulu (0 -> 1)
        for target_q in range(max_per_minggu):
            if remaining == 0:
                break
            group = [n for n in names if quota[n] == target_q and eligible(n, d, strict=True) and n not in picks]
            rng.shuffle(group)
            take = min(len(group), remaining)
            picks += group[:take]
            remaining -= take
        # relaksasi anti-berturut bila masih kurang (cap tetap dijaga)
        if remaining > 0:
            group_relax = [n for n in names if quota[n] < max_per_minggu and n not in picks]
            rng.shuffle(group_relax)
            take = min(len(group_relax), remaining)
            picks += group_relax[:take]
            remaining -= take
        if remaining > 0:
            raise RuntimeError(f"Tidak cukup kandidat untuk {fmt_tanggal_id(d)}.")

        day_df = pd.DataFrame({
            "No": range(1, orang_per_hari+1),
            "Nama": picks[:orang_per_hari],
            "Gedung": SLOT_TEMPLATE,
        }).merge(df_people, on="Nama", how="left")[
            ["No","Nama","Team","Gedung"]
        ]
        day_df["Tanggal"] = d
        rows.append(day_df)

        # update state
        for nm in picks[:orang_per_hari]:
            quota[nm] += 1
            last_day[nm] = d

    week_df = pd.concat(rows, ignore_index=True)

    # Repair pass (hard cap safeguard)
    by_person = week_df.groupby("Nama").size().to_dict()
    violators = [n for n, cnt in by_person.items() if cnt > max_per_minggu]
    if violators:
        for nm in violators:
            over = by_person[nm] - max_per_minggu
            rows_nm = week_df.index[week_df["Nama"] == nm].tolist()
            for ridx in rows_nm:
                if over <= 0:
                    break
                day = week_df.loc[ridx, "Tanggal"]
                used_today = set(week_df.loc[week_df["Tanggal"] == day, "Nama"])
                candidates = [k for k in names if (k not in used_today) and (by_person.get(k, 0) < max_per_minggu)]
                if not candidates:
                    continue
                k = rng.choice(candidates)
                week_df.loc[ridx, "Nama"] = k
                week_df.loc[ridx, "Team"] = df_people.set_index("Nama").loc[k, "Team"]
                by_person[k] = by_person.get(k, 0) + 1
                by_person[nm] = by_person[nm] - 1
                over -= 1

    assert (week_df.groupby("Nama").size() <= max_per_minggu).all(), "Ada orang > cap mingguan"
    return week_df


# ---------------------------
# Export Excel (1 sheet per minggu; 5 hari berjajar per sheet)
# ---------------------------

def write_week_sheet(xw, sheet_name: str, week_df: pd.DataFrame, monday: date, reset_number_per_group: bool = True):
    wb = xw.book
    ws = wb.add_worksheet(sheet_name)

    fmt_title = wb.add_format({"bold": True, "align":"center", "valign":"vcenter",
                               "font_size":12, "font_color":"white", "bg_color":"#0B86C4"})
    fmt_head  = wb.add_format({"bold": True, "align":"center", "valign":"vcenter","border":1,"bg_color":"#D9EDF7"})
    fmt_no    = wb.add_format({"align":"center","valign":"vcenter","border":1})
    fmt_cell  = wb.add_format({"align":"left","valign":"vcenter","border":1})

    # kolom untuk maksimal 5 hari
    for i in range(5):
        base = i*5
        ws.set_column(base+0, base+0, 5)
        ws.set_column(base+1, base+1, 16)
        ws.set_column(base+2, base+2, 12)
        ws.set_column(base+3, base+3, 22)
        ws.set_column(base+4, base+4, 2)

    days = sorted(week_df["Tanggal"].unique()) if not week_df.empty else []

    for i, d in enumerate(days):
        base_col = i*5
        ws.merge_range(0, base_col, 0, base_col+3, fmt_tanggal_id(pd.to_datetime(d).date()), fmt_title)
        for c, h in enumerate(["No","Nama","Team","Gedung"]):
            ws.write(1, base_col+c, h, fmt_head)

        day_df = week_df[week_df["Tanggal"]==d][["No","Nama","Team","Gedung"]].copy()

        def pref(g):
            g = str(g).strip()
            return g[0].upper() if g else ""
        day_df["Prefix"] = day_df["Gedung"].map(pref)

        row_ptr = 2
        for grp in ["A","B","C","D"]:
            block = day_df[day_df["Prefix"]==grp][["Nama","Team","Gedung"]].reset_index(drop=True)
            if block.empty:
                continue
            nums = list(range(1, len(block)+1)) if reset_number_per_group else list(range(row_ptr-1, row_ptr-1+len(block)))
            for r in range(len(block)):
                ws.write(row_ptr+r, base_col+0, nums[r], fmt_no)
                ws.write(row_ptr+r, base_col+1, block.loc[r,"Nama"], fmt_cell)
                ws.write(row_ptr+r, base_col+2, block.loc[r,"Team"], fmt_cell)
                ws.write(row_ptr+r, base_col+3, block.loc[r,"Gedung"], fmt_cell)
            row_ptr += len(block) + 1


# ---------------------------
# UI Streamlit
# ---------------------------

def main():
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    st.title(APP_TITLE)
    st.caption("Upload daftar personel (Nama, Team) lalu generate jadwal. Gedung tetap sesuai template.")

    with st.sidebar:
        st.header("Pengaturan")
        jumlah_minggu = st.number_input("Jumlah minggu", 1, 12, DEFAULT_JUMLAH_MINGGU)
        orang_per_hari = st.number_input("Orang per hari", 1, 50, DEFAULT_ORANG_PER_HARI)
        max_per_minggu = st.number_input("Maks per orang/minggu", 1, 7, DEFAULT_MAX_PER_ORG_PER_MINGGU)
        larang_berturut = st.checkbox("Larang 2 hari berturut-turut", value=DEFAULT_LARANG_BERTURUT)
        reset_number = st.checkbox("Nomor reset per blok (A/B/C/D)", value=DEFAULT_RESET_NUMBER_PER_GROUP)
        seed_mode = st.selectbox("Acak (seed)", ["Random setiap run", "Gunakan angka seed"], index=0)
        seed_value = None
        if seed_mode == "Gunakan angka seed":
            seed_value = st.number_input("Seed", 0, 10_000_000, 42)
        # Start date (harus Senin)
        start_date_choice = st.date_input("Mulai dari Senin", value=monday_near_future())
        if start_date_choice.weekday() != 0:
            st.error("Tanggal mulai harus hari Senin.")

    # File uploader
    up = st.file_uploader("Upload CSV/XLSX dengan kolom: Nama, Team", type=["csv","xlsx","xls"])

    # Contoh template unduhan
    with st.expander("Lihat/Unduh Template (Nama, Team)"):
        df_tmp = pd.DataFrame({"Nama":["YOKI","NAZAR"],"Team":["LABEL","ORDER"]})
        st.dataframe(df_tmp, use_container_width=True)
        csv = df_tmp.to_csv(index=False).encode("utf-8")
        st.download_button("Unduh Template CSV", csv, file_name="template_personel.csv", mime="text/csv")

    if up is None:
        st.info("Silakan upload file personel untuk mulai.")
        return

    # Baca file
    ext = os.path.splitext(up.name)[1].lower()
    if ext in [".xlsx", ".xls"]:
        df_people = pd.read_excel(up)
    else:
        # auto delimiter
        try:
            df_people = pd.read_csv(up, sep=None, engine="python")
        except Exception:
            df_people = pd.read_csv(up)

    cols = {c.lower(): c for c in df_people.columns}
    need = ["nama","team"]
    miss = [c for c in need if c not in cols]
    if miss:
        st.error("Kolom wajib: Nama, Team")
        return
    df_people = df_people.rename(columns={cols["nama"]:"Nama", cols["team"]:"Team"})
    df_people["Nama"] = df_people["Nama"].astype(str).str.strip()
    df_people["Team"] = df_people["Team"].astype(str).str.strip()
    if df_people["Nama"].duplicated().any():
        st.error("Ada duplikat Nama. Harus unik per orang.")
        return

    if len(SLOT_TEMPLATE) != orang_per_hari:
        st.error(f"Jumlah slot template ({len(SLOT_TEMPLATE)}) ≠ orang per hari ({orang_per_hari}). Sesuaikan dulu.")
        return

    st.success(f"Data personel: {len(df_people)} orang. Siap generate {jumlah_minggu} minggu.")

    # Generate jadwal
    if st.button("🚀 Generate Jadwal"):
        try:
            all_weeks: Dict[int, pd.DataFrame] = {}
            last_day_global: Dict[str, date | None] = {n: None for n in df_people["Nama"]}
            # Kita jadikan anti-berturut lintas minggu dengan mengoper last_day_global via seed offset
            # (di sini cukup memanggil schedule_one_week per minggu, karena repair hard-cap ada per minggu)
            for wk in range(1, jumlah_minggu+1):
                monday = start_date_choice + timedelta(days=(wk-1)*7)
                week_df = schedule_one_week(
                    df_people=df_people,
                    monday_date=monday,
                    orang_per_hari=orang_per_hari,
                    max_per_minggu=max_per_minggu,
                    larang_berturut=larang_berturut,
                    random_seed=None if seed_value is None else int(seed_value) + wk,
                )
                all_weeks[wk] = week_df

            # Tampilkan pratinjau
            st.subheader("Pratinjau Mingguan")
            for wk, wdf in all_weeks.items():
                st.markdown(f"### Minggu {wk} — mulai {fmt_tanggal_id((start_date_choice + timedelta(days=(wk-1)*7)))}")
                # tampil sederhana: tabel gabungan per hari
                for d in sorted(wdf["Tanggal"].unique()):
                    st.markdown(f"**{fmt_tanggal_id(pd.to_datetime(d).date())}**")
                    # tampil per blok A/B/C/D
                    show = wdf[wdf["Tanggal"]==d][["No","Nama","Team","Gedung"]].copy()
                    # reset nomor per grup
                    def pref(g):
                        g=str(g).strip(); return g[0].upper() if g else ""
                    show["Prefix"] = show["Gedung"].map(pref)
                    for grp in ["A","B","C","D"]:
                        block = show[show["Prefix"]==grp][["Nama","Team","Gedung"]].reset_index(drop=True)
                        if block.empty: continue
                        block.insert(0, "No", range(1, len(block)+1))
                        st.dataframe(block, use_container_width=True)

            # Export ke Excel (multi sheet)
            output = io.BytesIO()
            fname = f"Jadwal_Piket_{jumlah_minggu}Minggu_mulai_{start_date_choice.strftime('%Y%m%d')}.xlsx"
            with pd.ExcelWriter(output, engine="xlsxwriter", date_format="yyyy-mm-dd") as xw:
                for wk, week_df in all_weeks.items():
                    sheet_name = f"Minggu_{wk}"
                    write_week_sheet(xw, sheet_name, week_df, start_date_choice + timedelta(days=(wk-1)*7), reset_number)
                # Rekap
                all_concat = pd.concat([df.assign(Minggu=wk) for wk, df in all_weeks.items()], ignore_index=True)
                if not all_concat.empty:
                    dist_total = all_concat.groupby("Nama").size().reset_index(name="Total_Tugas").sort_values(["Total_Tugas","Nama"], ascending=[False,True])
                    dist_total.to_excel(xw, index=False, sheet_name="Distribusi_All")
                    dist_per_week = (all_concat.groupby(["Minggu","Nama"]).size()
                                     .reset_index(name="Tugas")
                                     .pivot(index="Nama", columns="Minggu", values="Tugas")
                                     .fillna(0).astype(int))
                    dist_per_week.to_excel(xw, sheet_name="Distribusi_per_Minggu")
            output.seek(0)
            st.download_button("⬇️ Unduh Excel Jadwal", data=output, file_name=fname, mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

        except Exception as e:
            st.error(str(e))


if __name__ == "__main__":
    main()

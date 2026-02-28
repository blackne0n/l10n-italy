#!/usr/bin/env python3
"""
Read the Agenzia Entrate "tabella codici tributo" Excel (2 header rows, data from row 3)
and generate an Odoo XML data file for model: l10n_it.f24.tributecode.

It applies these rules:
- MODALITA' DI UTILIZZO: D/R/E -> usage_mode (lowercase). If contains "(C)", it is preserved
  by appending a tag to description (since the current model has no dedicated field).
- CODICE UFFICIO: "SI" -> office_code=True else False
- CODICE ATTO: "SI" -> act_code=True, "FA" -> act_code=False but preserved by tagging description,
  empty -> act_code=False
- RATEAZIONE/...: leading zeros may be missing; normalize to 4 chars. T0/T1/T2/T3/T4/T7 -> 00T0 etc.
- ANNO DI RIFERIMENTO: "AAAA" or "0000" (kept as 4 chars)
- TRIBUTO: if numeric in Excel, coerced to int string (no ".0"), then zfill(4)[:4]

Usage:
  python tributi_to_odoo_xml.py /path/input.xlsx /path/output.xml
"""

import re
import sys
from pathlib import Path
from xml.sax.saxutils import escape

import pandas as pd

SECTION_MAP = {
    "erario": "erario",
    "inps": "inps",
    "regioni": "regioni",
    "regioni e provincie autonome": "regioni",
    "imu": "imu",
    "imu e altri tributi locali": "imu",
    "imu ed altri tributi locali": "imu",
    "enti locali": "imu",
    "enti locali-camere di commercio": "imu",
    "inail": "inail",
    "altri enti previdenziali e assistenziali": "altro_previdence",
    "altri enti previdenziali": "altro_previdence",
    "altro_previdence": "altro_previdence",
}

# Common header labels in your Excel (after flattening)
COL_ALIASES = {
    "code": ["TRIBUTO", "tributo", "CODICE TRIBUTO", "code"],
    "usage": [
        "MODALITA' DI UTILIZZO (1)",
        "MODALITA DI UTILIZZO (1)",
        "MODALITA' DI UTILIZZO",
        "MODALITA DI UTILIZZO",
    ],
    "section": ["TIPO TRIBUTO", "tipo tributo", "SEZIONE", "section"],
    "rate": [
        "RATEAZIONE/ REGIONE/ PROV. (2)",
        "RATEAZIONE/REGIONE/PROV./MESE RIF.",
        "RATEAZIONE/ REGIONE/ PROV.",
        "RATEAZIONE/REGIONE/PROV.",
        "RATEAZIONE",
    ],
    "ref_year": [
        "ANNO DI RIFERIMENTO (3)",
        "ANNO DI RIFERIMENTO",
        "ref year",
        "ref_year",
    ],
    "office": ["CODICE UFFICIO (4)", "CODICE UFFICIO", "office code"],
    "act": ["CODICE ATTO (5)", "CODICE ATTO", "act code"],
    "max_year": [
        "ANNO MASSIMO DI RIFERIMENTO (6)",
        "ANNO MASSIMO DI RIFERIMENTO",
        "max ref year",
        "max_ref_year",
    ],
    "desc": ["DESCRIZIONE", "descrizione", "description"],
}


def norm_str(x) -> str:
    if x is None:
        return ""
    if isinstance(x, float) and pd.isna(x):
        return ""
    return str(x).strip()


def norm_colname(s: str) -> str:
    s = norm_str(s).lower()
    s = s.replace("’", "'")
    s = re.sub(r"\s+", " ", s)
    return s


def flatten_headers(df: pd.DataFrame) -> pd.DataFrame:
    """Flatten a 2-row header into single strings."""
    if not isinstance(df.columns, pd.MultiIndex):
        return df
    df = df.copy()
    df.columns = [
        " ".join(str(x).strip() for x in col if str(x) != "nan").strip()
        for col in df.columns.to_list()
    ]
    df.columns = [re.sub(r"\s+", " ", c).strip() for c in df.columns]
    return df


def find_column(df: pd.DataFrame, wanted: list[str]) -> str:
    cols_norm = {norm_colname(c): c for c in df.columns}
    # exact match
    for w in wanted:
        wn = norm_colname(w)
        if wn in cols_norm:
            return cols_norm[wn]
    # contains match
    for w in wanted:
        wn = norm_colname(w)
        for cn, original in cols_norm.items():
            if wn in cn:
                return original
    raise KeyError(
        f"Missing required column. Looked for: {wanted}. Available: {list(df.columns)}"
    )


def parse_usage_mode(val: str):
    """
    Returns: (mode in {'d','e','r'}, has_c_flag bool) or (None, None) if not parseable
    """
    s = norm_str(val).upper()
    if s == "":
        return None, None

    s_no_spaces = s.replace(" ", "")
    # Header/legend markers like "(1)" should be skipped
    if re.fullmatch(r"\(\s*\d+\s*\)", s_no_spaces):
        return None, None

    has_c = "(C)" in s_no_spaces
    m = re.search(r"[DER]", s_no_spaces)
    if not m:
        return None, None

    return m.group(0).lower(), has_c


def parse_office_required(val) -> bool:
    return norm_str(val).strip().upper() == "SI"


def parse_act_cell(val) -> tuple[bool, bool]:
    """
    Returns: (required_bool, is_optional_fa_bool)
    - SI => (True, False)
    - FA => (False, True)
    - empty => (False, False)
    """
    v = norm_str(val).strip().upper()
    if v == "SI":
        return True, False
    if v == "FA":
        return False, True
    return False, False


def normalize_rateazione(val: str) -> str:
    """
    Normalize to 4 chars.
    Handles: T0/T1/T2/T3/T4/T7 => 00T0 etc.
    Keeps NNRR/FFFF/00MM/0000 etc.
    If empty => 0000.
    """
    v = norm_str(val).strip().upper().replace(" ", "")
    if v == "":
        return "0000"

    # Already 4+ characters => take first 4 (this preserves masks like 00MM, NNRR, FFFF, 00T0)
    if len(v) >= 4:
        return v[:4]

    # Territoriality codes
    if re.fullmatch(r"T[0-9]", v):
        return ("00" + v)[:4]

    # If short numeric or 'MM'/'M', treat as month mask (table typically uses 00MM as mask)
    if v in {"M", "MM"} or v.isdigit():
        return "00MM"

    # Fallback: left-pad with zeros
    return v.zfill(4)[:4]


def normalize_ref_year(val: str) -> str:
    v = norm_str(val).strip().upper().replace(" ", "")
    if v == "":
        return "0000"
    # Expect 'AAAA' or '0000' in this table; enforce 4 chars
    return v[:4].zfill(4)


def normalize_code(val) -> str:
    """
    Excel often stores TRIBUTO as numeric (1001.0). Convert to int string safely,
    then zfill(4) and truncate to 4.
    """
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    s = norm_str(val)
    # If looks like float with .0
    try:
        f = float(s)
        if f.is_integer():
            s = str(int(f))
    except ValueError:
        pass
    s = re.sub(r"\s+", "", s)
    return s.zfill(4)[:4]


def normalize_section(val: str) -> str:
    s = norm_str(val).strip().lower()
    s = s.replace("’", "'")

    # normalize hyphens
    s = s.replace("–", "-").replace("—", "-")

    # remove footnote markers like (*) (**) and extra spaces
    s = re.sub(r"\(\s*\*+\s*\)", "", s)  # removes "(*)" "(**)" "(***)"
    s = re.sub(r"\s+", " ", s).strip()

    if s in SECTION_MAP:
        return SECTION_MAP[s]

    # looser matching
    if "enti locali" in s:
        return "imu"
    if s.startswith("reg"):
        return "regioni"

    raise ValueError(f"Unknown TIPO TRIBUTO/section value: {val}")


def to_int(val, default=0) -> int:
    s = norm_str(val)
    if s == "":
        return default
    try:
        return int(float(s))
    except ValueError:
        return default


def make_xml_id(section: str, code: str) -> str:
    safe = re.sub(r"[^0-9a-zA-Z_]+", "_", f"{code}")
    return f"{safe}"


def is_real_tributo(val) -> bool:
    s = norm_str(val).strip().upper()
    if s == "":
        return False

    # Normalize numeric codes like 1001.0 -> 1001
    try:
        f = float(s)
        if f.is_integer():
            s = str(int(f))
    except ValueError:
        pass

    s = re.sub(r"\s+", "", s)

    # In this AE table, tributo codes are exactly 4 alphanumeric chars (e.g. 1001, TF54, 134T)
    return bool(re.fullmatch(r"[0-9A-Z]{4}", s))


def main(xlsx_path: str, out_path: str):
    # Read with 2 header rows (your file has merged/multi-line header)
    df = pd.read_excel(xlsx_path, header=[0, 1])
    df = flatten_headers(df)
    df = df.dropna(how="all")

    # Resolve columns
    col_code = find_column(df, COL_ALIASES["code"])
    col_desc = find_column(df, COL_ALIASES["desc"])
    col_usage = find_column(df, COL_ALIASES["usage"])
    col_section = find_column(df, COL_ALIASES["section"])
    col_rate = find_column(df, COL_ALIASES["rate"])
    col_ref_year = find_column(df, COL_ALIASES["ref_year"])

    # Optional columns
    col_office = None
    col_act = None
    col_max_year = None
    try:
        col_office = find_column(df, COL_ALIASES["office"])
    except KeyError:
        pass
    try:
        col_act = find_column(df, COL_ALIASES["act"])
    except KeyError:
        pass
    try:
        col_max_year = find_column(df, COL_ALIASES["max_year"])
    except KeyError:
        pass

    records = []
    seen_ids = set()

    for idx, row in df.iterrows():
        if not is_real_tributo(row[col_code]):
            continue

        usage_mode, has_c = parse_usage_mode(row[col_usage])
        if usage_mode is None:
            continue

        code = normalize_code(row[col_code])
        if code == "":
            continue

        description = norm_str(row[col_desc])
        if description == "":
            description = code

        name = f"{code} - {description}"

        section = normalize_section(row[col_section])
        rateazione = normalize_rateazione(row[col_rate])
        ref_year = normalize_ref_year(row[col_ref_year])

        office_required = (
            parse_office_required(row[col_office]) if col_office else False
        )

        act_required = False
        act_optional = False
        if col_act:
            act_required, act_optional = parse_act_cell(row[col_act])

        max_ref_year = to_int(row[col_max_year], default=0) if col_max_year else 0

        # Preserve extra semantics inside description if model doesn't support dedicated fields
        tags = []
        if has_c:
            tags.append("C")  # usable only at collection agent
        if act_optional:
            tags.append("ATTO_FA")  # act code optional
        if tags:
            description = f"{description} [{'|'.join(tags)}]"

        xml_id = make_xml_id(section, code)
        # ensure uniqueness
        if xml_id in seen_ids:
            xml_id = f"{xml_id}_{idx}"
        seen_ids.add(xml_id)

        records.append(
            {
                "xml_id": xml_id,
                "code": code,
                "name": name,
                "description": description,
                "usage_mode": usage_mode,  # d/e/r
                "section": section,  # erario/inps/...
                "rateazione": rateazione,  # 0000/00MM/NNRR/FFFF/00T0...
                "ref_year": ref_year,  # AAAA or 0000
                "office_code": office_required,
                "act_code": act_required,  # boolean: True only for SI
                "max_ref_year": max_ref_year,
            }
        )

    # Write XML
    out_lines = []
    out_lines.append('<?xml version="1.0" encoding="UTF-8"?>')
    out_lines.append('<odoo noupdate="1">')
    out_lines.append("  <data>")

    for r in records:
        out_lines.append(
            f'    <record model="l10n_it.f24.tributecode" id="{escape(r["xml_id"])}">'
        )
        out_lines.append(f'      <field name="code">{escape(r["code"])}</field>')
        out_lines.append(
            f'      <field name="description">{escape(r["description"])}</field>'
        )
        out_lines.append(
            f'      <field name="usage_mode">{escape(r["usage_mode"])}</field>'
        )
        out_lines.append(f'      <field name="section">{escape(r["section"])}</field>')
        out_lines.append(
            f'      <field name="rateazione">{escape(r["rateazione"])}</field>'
        )
        out_lines.append(
            f'      <field name="ref_year">{escape(r["ref_year"])}</field>'
        )
        out_lines.append(
            f'      <field name="office_code" eval="{str(bool(r["office_code"]))}" />'
        )
        out_lines.append(
            f'      <field name="act_code" eval="{str(bool(r["act_code"]))}" />'
        )
        out_lines.append(
            f'      <field name="max_ref_year">{r["max_ref_year"]}</field>'
        )
        out_lines.append("    </record>")

    out_lines.append("  </data>")
    out_lines.append("</odoo>")

    Path(out_path).write_text("\n".join(out_lines), encoding="utf-8")
    print(f"Wrote {len(records)} records to {out_path}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("Usage: python tributi_to_odoo_xml.py input.xlsx output.xml")
        sys.exit(2)
    main(sys.argv[1], sys.argv[2])

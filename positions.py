from __future__ import annotations

import json
import hashlib
from datetime import datetime
from pathlib import Path

import pandas as pd

DATA_DIR = Path("data")
LATEST_PARQUET = DATA_DIR / "positions_latest.parquet"
LATEST_META = DATA_DIR / "positions_meta.json"
CONTROL_PARQUET = DATA_DIR / "control_accounts_latest.parquet"

REPO_POS_DIR = Path("posicoes")
REPO_CONTROL_XLSX = REPO_POS_DIR / "Contas.xlsx"
REPO_XP_XLSX = REPO_POS_DIR / "XP.xlsx"
REPO_BTG_XLSX = REPO_POS_DIR / "BTG.xlsx"
REPO_CS_CSV = REPO_POS_DIR / "CSProdutos.csv"

BTG_ACCOUNT_WIDTH = 8


# ---------- helpers ----------
def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _normalize_broker(x: str) -> str:
    s = (x or "").strip().upper()
    if "SCHWAB" in s or "CHARLES" in s or s in {"CS"}:
        return "CS"
    if "XP" in s or s in {"XP"}:
        return "XP"
    if "BTG" in s or s in {"BTG"}:
        return "BTG"
    return s


def _normalize_account(x) -> str:
    """
    Normalização base (comum a todas as corretoras):
    - converte para string
    - remove '.0' de números vindos do Excel
    - tira espaços
    """
    if pd.isna(x):
        return ""
    s = str(x).strip()
    if s.endswith(".0"):
        s = s[:-2]
    s = s.replace(" ", "")
    return s


def _only_digits(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())


def _normalize_btg_account_8(x) -> str:
    """
    BTG: padroniza conta para 8 dígitos (zeros à esquerda).
    Mantém apenas dígitos.
    """
    s = _normalize_account(x)
    d = _only_digits(s)
    return d.zfill(BTG_ACCOUNT_WIDTH) if d != "" else ""


def _exists_all_repo_files() -> tuple[bool, list[str]]:
    missing = []
    for p in [REPO_CONTROL_XLSX, REPO_XP_XLSX, REPO_BTG_XLSX, REPO_CS_CSV]:
        if not p.exists():
            missing.append(str(p))
    return (len(missing) == 0, missing)


def _safe_sum(series) -> float:
    return float(pd.to_numeric(series, errors="coerce").fillna(0.0).sum())


def _pick_existing(cols: list[str], *names: str) -> str | None:
    for n in names:
        if n in cols:
            return n
    return None


# ---------- loaders/parsers ----------

def load_control_accounts(src=None) -> pd.DataFrame:
    """
    src pode ser:
    - None: tenta ler do repo (posicoes/Contas.xlsx); se não existir, usa cache parquet
    - path/str: lê xlsx desse caminho
    - file-like (uploader): lê xlsx do objeto
    """
    DATA_DIR.mkdir(exist_ok=True)

    if src is None:
        if REPO_CONTROL_XLSX.exists():
            src = REPO_CONTROL_XLSX
        elif CONTROL_PARQUET.exists():
            return pd.read_parquet(CONTROL_PARQUET)
        else:
            raise FileNotFoundError("Controle de contas não encontrado (repo/cache).")

    df = pd.read_excel(src)
    df.columns = [str(c).strip() for c in df.columns]
    
    # Descobrir nomes reais das colunas no Excel
    col_broker = _pick_existing(df.columns, "CORRETORA", "Corretora", "corretora", "Broker", "BROKER")
    col_account = _pick_existing(df.columns, "NÚMERO DA CONTA", "NMERO DA CONTA", "Numero da Conta", "Número da Conta")
    
    if col_broker is None:
        raise ValueError(f"Controle: não encontrei coluna de corretora. Colunas: {list(df.columns)}")
    if col_account is None:
        raise ValueError("Controle: não encontrei coluna NÚMERO DA CONTA (ou variações).")
    
    # Renomeia para nomes internos padronizados
    df = df.rename(columns={col_broker: "corretora", col_account: "conta"})
    
    df["corretora"] = df["corretora"].apply(_normalize_broker)
    df["conta"] = df["conta"].apply(_normalize_account)
    
    m = df["corretora"].eq("BTG")
    df.loc[m, "conta"] = df.loc[m, "conta"].apply(_normalize_btg_account_8)

    keep = [
        "GRUPO GERAL", "corretora", "conta", "CLIENTE", "TIPO DE MARCAÇÃO ",
        "CLIENTE - CORRETORA", "Perfil Carteira"
    ]
    keep = [c for c in keep if c in df.columns] + ["corretora", "conta"]
    df = df.loc[:, list(dict.fromkeys(keep))].copy()

    df.to_parquet(CONTROL_PARQUET, index=False)
    return df


def parse_cs_positions(src) -> pd.DataFrame:
    import io

    if isinstance(src, (str, Path)):
        text = Path(src).read_text(encoding="utf-8", errors="ignore")
    else:
        b = src.read()
        text = b if isinstance(b, str) else b.decode("utf-8", errors="ignore")

    lines = text.splitlines()
    header_idx = None

    for i, ln in enumerate(lines):
        if ln.strip().startswith("Account,"):
            header_idx = i
            break
    if header_idx is None:
        for i, ln in enumerate(lines):
            if ln.strip().lower().startswith("account,"):
                header_idx = i
                break
    if header_idx is None:
        raise ValueError("CS: não encontrei a linha de header iniciando com 'Account,' no CSV.")

    csv_data = "\n".join(lines[header_idx:])
    raw = pd.read_csv(io.StringIO(csv_data), sep=",", engine="python")
    raw.columns = [str(c).strip() for c in raw.columns]

    sym_col = "Symbol/CUSIP" if "Symbol/CUSIP" in raw.columns else (
        "CUSIP" if "CUSIP" in raw.columns else ("Symbol" if "Symbol" in raw.columns else None)
    )
    if sym_col is None:
        sym_col = "Symbol/CUSIP"

    df = pd.DataFrame({
        "corretora": "CS",
        "conta": raw["Account"].apply(_normalize_account),
        "asset_id": raw.get(sym_col, "").astype(str).str.strip(),
        "asset_nome": raw.get("Name", "").astype(str).str.strip(),
        "asset_tipo": raw.get("Security Type", "").astype(str).str.strip(),
        "valor_mercado": raw.get("Market Value", ""),
        "quantidade": 0.0,
        "moeda": "USD",
        "mercado": "",
        "sub_mercado": "",
        "estrategia": "",
    })

    df["valor_mercado"] = (
        df["valor_mercado"].astype(str)
        .str.replace("$", "", regex=False)
        .str.replace(",", "", regex=False)
        .str.strip()
    )
    df["valor_mercado"] = pd.to_numeric(df["valor_mercado"], errors="coerce").fillna(0.0)
    df["asset_id"] = df["asset_id"].replace({"nan": "", "None": ""})
    return df


def parse_xp_positions(src) -> pd.DataFrame:
    """
    XP.xlsx: lê todas as abas e captura qualquer aba que tenha:
    CodigoCliente + (CodigoAtivo ou Ativo) + (ValorAtual ou Valor)
    """
    xls = pd.ExcelFile(src)
    out = []

    for sh in xls.sheet_names:
        tmp = pd.read_excel(xls, sheet_name=sh)
        tmp.columns = [str(c).strip() for c in tmp.columns]

        if "CodigoCliente" not in tmp.columns:
            continue

        col_asset = "CodigoAtivo" if "CodigoAtivo" in tmp.columns else ("Ativo" if "Ativo" in tmp.columns else None)
        col_val = "ValorAtual" if "ValorAtual" in tmp.columns else ("Valor" if "Valor" in tmp.columns else None)
        col_qty = "QuantidadeTotalAtual" if "QuantidadeTotalAtual" in tmp.columns else (
            "Quantidade" if "Quantidade" in tmp.columns else None
        )

        if col_asset is None or col_val is None:
            continue

        df = pd.DataFrame({
            "corretora": "XP",
            "conta": tmp["CodigoCliente"].apply(_normalize_account),
            "asset_id": tmp[col_asset].astype(str).str.strip(),
            "asset_nome": tmp.get("NomeAtivo", tmp.get("DescricaoAtivo", tmp.get(col_asset, ""))).astype(str).str.strip(),
            "asset_tipo": sh,
            "valor_mercado": pd.to_numeric(tmp[col_val], errors="coerce").fillna(0.0),
            "quantidade": pd.to_numeric(tmp[col_qty], errors="coerce").fillna(0.0) if col_qty else 0.0,
            "moeda": "BRL",
            "mercado": "",
            "sub_mercado": "",
            "estrategia": "",
        })
        out.append(df)

    if not out:
        raise ValueError("XP: não encontrei abas com CodigoCliente + colunas de ativo/valor.")
    return pd.concat(out, ignore_index=True)


def parse_btg_positions(src) -> pd.DataFrame:
    """
    BTG.xlsx (esperado):
    Conta, Mercado, Sub Mercado/Mercado/Sub Mercado, Produto, Quantidade, Valor Bruto, Estratégia
    """
    df0 = pd.read_excel(src)
    df0.columns = [str(c).strip() for c in df0.columns]
    cols = list(df0.columns)

    col_account = _pick_existing(cols, "Conta", "CONTA")
    col_prod = _pick_existing(cols, "Produto", "Ativo/Produto", "AtivoProduto")
    col_val = _pick_existing(cols, "Valor Bruto", "ValorBruto", "Valor")
    col_qty = _pick_existing(cols, "Quantidade", "Qtd", "Qtde")
    col_merc = _pick_existing(cols, "Mercado")
    col_subm = _pick_existing(cols, "Sub Mercado", "SubMercado", "Mercado/Sub Mercado")
    col_estr = _pick_existing(cols, "Estratégia", "Estrategia", "Estratégia ")

    if col_account is None:
        raise ValueError("BTG: não encontrei coluna Conta/CONTA.")
    if col_prod is None or col_val is None:
        raise ValueError("BTG: não encontrei colunas mínimas (Produto e Valor Bruto/Valor).")

    produto = df0[col_prod].astype(str).str.strip()

    out = pd.DataFrame({
        "corretora": "BTG",
        "conta": df0[col_account].apply(_normalize_btg_account_8),  # <-- AQUI a regra do BTG
        "asset_id": produto,
        "asset_nome": produto,
        "asset_tipo": (df0[col_merc].astype(str).str.strip() if col_merc else "BTG"),
        "mercado": (df0[col_merc].astype(str).str.strip() if col_merc else ""),
        "sub_mercado": (df0[col_subm].astype(str).str.strip() if col_subm else ""),
        "estrategia": (df0[col_estr].astype(str).str.strip() if col_estr else ""),
        "valor_mercado": pd.to_numeric(df0[col_val], errors="coerce").fillna(0.0),
        "quantidade": pd.to_numeric(df0[col_qty], errors="coerce").fillna(0.0) if col_qty else 0.0,
        "moeda": "BRL",
    })

    return out


# ---------- diagnostics ----------
def diagnose_positions(df: pd.DataFrame, control_df: pd.DataFrame | None = None, label: str = "") -> dict:
    d: dict = {}
    d["label"] = label
    d["total_rows"] = int(len(df))
    d["columns"] = list(df.columns)

    if "corretora" in df.columns:
        d["rows_by_broker"] = df["corretora"].value_counts(dropna=False).to_dict()

    for b in ["XP", "BTG", "CS"]:
        if "corretora" in df.columns and (df["corretora"] == b).any():
            sub = df[df["corretora"] == b].copy()
            d[f"{b}_rows"] = int(len(sub))
            d[f"{b}_cols"] = list(sub.columns)

            if "conta" in sub.columns:
                d[f"{b}_unique_accounts"] = int(sub["conta"].nunique(dropna=True))
            if "asset_id" in sub.columns:
                d[f"{b}_unique_assets"] = int(sub["asset_id"].astype(str).nunique(dropna=True))
            if "valor_mercado" in sub.columns:
                d[f"{b}_sum_valor_mercado"] = _safe_sum(sub["valor_mercado"])

            keep = [c for c in [
                "corretora","conta","asset_id","asset_nome","asset_tipo",
                "valor_mercado","quantidade","moeda","mercado","sub_mercado","estrategia"
            ] if c in sub.columns]
            d[f"{b}_sample"] = sub[keep].head(20).to_dict(orient="records")

            if "conta" in sub.columns:
                top = sub.groupby("conta").size().sort_values(ascending=False).head(20)
                d[f"{b}_top_accounts_by_rows"] = top.to_dict()

    if control_df is not None and {"corretora", "conta"}.issubset(df.columns) and {"corretora", "conta"}.issubset(control_df.columns):
        keys_ctrl = control_df[["corretora", "conta"]].drop_duplicates()
        check = df.merge(keys_ctrl, how="left", on=["corretora", "conta"], indicator=True)

        d["unmatched_rows"] = int((check["_merge"] == "left_only").sum())
        d["unmatched_by_broker"] = check.loc[check["_merge"] == "left_only", "corretora"].value_counts(dropna=False).to_dict()

        examples = (
            check.loc[check["_merge"] == "left_only", ["corretora", "conta"]]
            .drop_duplicates()
            .head(30)
        )
        d["unmatched_examples"] = examples.to_dict(orient="records")

    return d


# ---------- pipeline ----------
def classify_bucket_estrategia(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["bucket_estrategia"] = "HOLD_MONITOR"
    df.loc[df["asset_id"].astype(str).str.strip().eq(""), "bucket_estrategia"] = "UNKNOWN"
    return df


def build_and_save_latest(
    control_df: pd.DataFrame,
    xp_df: pd.DataFrame,
    btg_df: pd.DataFrame,
    cs_df: pd.DataFrame,
    meta: dict
) -> pd.DataFrame:
    DATA_DIR.mkdir(exist_ok=True)

    pos = pd.concat([xp_df, btg_df, cs_df], ignore_index=True)

    pos["corretora"] = pos["corretora"].apply(_normalize_broker)
    pos["conta"] = pos["conta"].apply(_normalize_account)
    
    mpos = pos["corretora"].eq("BTG")
    pos.loc[mpos, "conta"] = pos.loc[mpos, "conta"].apply(_normalize_btg_account_8)
    
    mctrl = control_df["corretora"].eq("BTG")
    control_df = control_df.copy()
    control_df.loc[mctrl, "conta"] = control_df.loc[mctrl, "conta"].apply(_normalize_btg_account_8)

    merged = pos.merge(
        control_df,
        how="left",
        left_on=["corretora", "conta"],
        right_on=["corretora", "conta"],
        suffixes=("", "_ctrl"),
    )

    merged["dt_posicao"] = meta.get("dt_posicao", datetime.now().date().isoformat())
    merged = classify_bucket_estrategia(merged)

    merged.to_parquet(LATEST_PARQUET, index=False)
    with open(LATEST_META, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    return merged


def load_latest_positions() -> pd.DataFrame | None:
    if not LATEST_PARQUET.exists():
        return None
    return pd.read_parquet(LATEST_PARQUET)


def build_latest_from_repo(dt_posicao: str | None = None) -> pd.DataFrame:
    ok, missing = _exists_all_repo_files()
    if not ok:
        raise FileNotFoundError("Faltando arquivos em posicoes/: " + ", ".join(missing))

    control_df = load_control_accounts(REPO_CONTROL_XLSX)

    xp_df = parse_xp_positions(REPO_XP_XLSX)
    btg_df = parse_btg_positions(REPO_BTG_XLSX)
    cs_df = parse_cs_positions(REPO_CS_CSV)

    meta = {
        "dt_posicao": dt_posicao or datetime.now().date().isoformat(),
        "source": "repo",
        "btg_account_width": BTG_ACCOUNT_WIDTH,
    }
    meta["diagnostics"] = {
        "xp_df": diagnose_positions(xp_df, control_df, label="xp_df"),
        "btg_df": diagnose_positions(btg_df, control_df, label="btg_df"),
        "cs_df": diagnose_positions(cs_df, control_df, label="cs_df"),
    }

    return build_and_save_latest(control_df, xp_df, btg_df, cs_df, meta)

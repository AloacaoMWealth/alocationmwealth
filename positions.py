# positions.py
from __future__ import annotations

import pandas as pd
from datetime import datetime
from pathlib import Path
import json
import hashlib


DATA_DIR = Path("data")
LATEST_PARQUET = DATA_DIR / "positions_latest.parquet"
LATEST_META = DATA_DIR / "positions_meta.json"
CONTROL_PARQUET = DATA_DIR / "control_accounts_latest.parquet"

REPO_POS_DIR = Path("posicoes")
REPO_CONTROL_XLSX = REPO_POS_DIR / "Contas.xlsx"
REPO_XP_XLSX = REPO_POS_DIR / "XP.xlsx"
REPO_BTG_XLSX = REPO_POS_DIR / "BTG.xlsx"
REPO_CS_CSV = REPO_POS_DIR / "CSProdutos.csv"


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
    s = "" if pd.isna(x) else str(x).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s


def _exists_all_repo_files() -> tuple[bool, list[str]]:
    missing = []
    for p in [REPO_CONTROL_XLSX, REPO_XP_XLSX, REPO_BTG_XLSX, REPO_CS_CSV]:
        if not p.exists():
            missing.append(str(p))
    return (len(missing) == 0, missing)


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

    col_broker = "CORRETORA"
    col_account = "NÚMERO DA CONTA" if "NÚMERO DA CONTA" in df.columns else (
        "NMERO DA CONTA" if "NMERO DA CONTA" in df.columns else None
    )
    if col_account is None:
        raise ValueError("Controle: não encontrei coluna NÚMERO DA CONTA (ou NMERO DA CONTA).")

    df = df.rename(columns={col_account: "conta", col_broker: "corretora"})
    df["corretora"] = df["corretora"].apply(_normalize_broker)
    df["conta"] = df["conta"].apply(_normalize_account)

    keep = ["GRUPO GERAL", "corretora", "conta", "CLIENTE", "TIPO DE MARCAÇÃO ", "CLIENTE - CORRETORA", "Perfil Carteira"]
    keep = [c for c in keep if c in df.columns] + ["corretora", "conta"]
    df = df.loc[:, list(dict.fromkeys(keep))].copy()

    df.to_parquet(CONTROL_PARQUET, index=False)
    return df


def parse_cs_positions(src) -> pd.DataFrame:
    # O CSV do Schwab vem com linhas de relatório antes do header real.
    # Vamos achar a linha que começa com "Account," e ler a partir dali.
    import io

    if isinstance(src, (str, Path)):
        text = Path(src).read_text(encoding="utf-8", errors="ignore")
    else:
        b = src.read()
        if isinstance(b, str):
            text = b
        else:
            text = b.decode("utf-8", errors="ignore")

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
        "quantidade": 0.0,   # seu CSProdutos não tem Quantity nesse layout
        "moeda": "USD",
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
    xls = pd.ExcelFile(src)
    out = []

    for sh in xls.sheet_names:
        tmp = pd.read_excel(xls, sheet_name=sh)
        tmp.columns = [str(c).strip() for c in tmp.columns]

        if "CodigoCliente" not in tmp.columns:
            continue

        col_asset = "CodigoAtivo" if "CodigoAtivo" in tmp.columns else ("Ativo" if "Ativo" in tmp.columns else None)
        col_val = "ValorAtual" if "ValorAtual" in tmp.columns else ("Valor" if "Valor" in tmp.columns else None)
        col_qty = "QuantidadeTotalAtual" if "QuantidadeTotalAtual" in tmp.columns else ("Quantidade" if "Quantidade" in tmp.columns else None)

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
        })
        out.append(df)

    if not out:
        raise ValueError("XP: não encontrei abas com CodigoCliente + colunas de ativo/valor.")
    return pd.concat(out, ignore_index=True)


def parse_btg_positions(src) -> pd.DataFrame:
    df0 = pd.read_excel(src)
    df0.columns = [str(c).strip() for c in df0.columns]

    def pick(*names):
        for n in names:
            if n in df0.columns:
                return n
        return None

    col_account = pick("Conta", "CONTA")
    col_prod = pick("Produto", "Ativo/Produto", "AtivoProduto")
    col_val = pick("Valor Bruto", "ValorBruto", "Valor")
    col_qty = pick("Quantidade", "Qtd", "Qtde")
    col_merc = pick("Mercado")
    col_subm = pick("Sub Mercado", "SubMercado", "Mercado/Sub Mercado")
    col_estr = pick("Estratégia", "Estrategia", "Estratégia ")

    if col_account is None:
        raise ValueError("BTG: não encontrei coluna Conta/CONTA.")
    if col_prod is None or col_val is None:
        raise ValueError("BTG: não encontrei colunas mínimas (Produto e Valor Bruto/Valor).")

    produto = df0[col_prod].astype(str).str.strip()

    out = pd.DataFrame({
        "corretora": "BTG",
        "conta": df0[col_account].apply(_normalize_account),
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

    meta = {"dt_posicao": dt_posicao or datetime.now().date().isoformat(), "source": "repo"}
    return build_and_save_latest(control_df, xp_df, btg_df, cs_df, meta)

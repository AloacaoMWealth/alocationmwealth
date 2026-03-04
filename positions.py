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

def _sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()

def _normalize_broker(x: str) -> str:
    s = (x or "").strip().upper()
    if "SCHWAB" in s or "CHARLES" in s or s in {"CS"}:
        return "CS"
    if s in {"XP"} or "XP" in s:
        return "XP"
    if s in {"BTG"} or "BTG" in s:
        return "BTG"
    return s

def _normalize_account(x) -> str:
    s = "" if pd.isna(x) else str(x).strip()
    if s.endswith(".0"):  # excel numeric
        s = s[:-2]
    return s

def load_control_accounts(uploaded_file=None) -> pd.DataFrame:
    DATA_DIR.mkdir(exist_ok=True)
    if uploaded_file is None:
        if CONTROL_PARQUET.exists():
            return pd.read_parquet(CONTROL_PARQUET)
        raise FileNotFoundError("Controle de Contas não carregado ainda.")

    df = pd.read_excel(uploaded_file)
    df.columns = [c.strip() for c in df.columns]

    # Ajuste nomes conforme seu arquivo: "NÚMERO DA CONTA" pode vir sem acento em alguns exports
    col_broker = "CORRETORA"
    col_account = "NÚMERO DA CONTA" if "NÚMERO DA CONTA" in df.columns else "NMERO DA CONTA"
    df = df.rename(columns={col_account: "conta", col_broker: "corretora"})

    df["corretora"] = df["corretora"].apply(_normalize_broker)
    df["conta"] = df["conta"].apply(_normalize_account)

    # Mantém as colunas que você citou como essenciais
    keep = ["GRUPO GERAL", "corretora", "conta", "CLIENTE", "TIPO DE MARCAÇÃO ", "CLIENTE - CORRETORA", "Perfil Carteira"]
    keep = [c for c in keep if c in df.columns] + ["corretora", "conta"]
    df = df.loc[:, list(dict.fromkeys(keep))].copy()

    df.to_parquet(CONTROL_PARQUET, index=False)
    return df

def parse_cs_positions(uploaded_csv) -> pd.DataFrame:
    raw = pd.read_csv(uploaded_csv)
    raw.columns = [c.strip() for c in raw.columns]

    df = pd.DataFrame({
        "corretora": "CS",
        "conta": raw["Account"].apply(_normalize_account),
        "asset_id": raw.get("CUSIP", raw.get("Symbol", "")).astype(str).str.strip(),
        "asset_nome": raw.get("Name", "").astype(str).str.strip(),
        "asset_tipo": raw.get("Security Type", "").astype(str).str.strip(),
        "valor_mercado": pd.to_numeric(raw.get("Market Value", 0), errors="coerce").fillna(0.0),
        "quantidade": pd.to_numeric(raw.get("Quantity", 0), errors="coerce").fillna(0.0),
        "moeda": "USD",
    })

    df["asset_id"] = df["asset_id"].replace({"nan": "", "None": ""})
    return df

def parse_xp_positions(uploaded_xlsx) -> pd.DataFrame:
    xls = pd.ExcelFile(uploaded_xlsx)

    # MVP: pegar todas as abas e tentar detectar colunas padrão; você refinaria por aba depois
    out = []
    for sh in xls.sheet_names:
        tmp = pd.read_excel(xls, sheet_name=sh)
        tmp.columns = [str(c).strip() for c in tmp.columns]

        if "CodigoCliente" not in tmp.columns:
            continue

        # Heurísticas comuns (você vai ajustar com base nas abas reais)
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
        raise ValueError("Não encontrei abas XP com CodigoCliente + colunas de ativo/valor.")
    return pd.concat(out, ignore_index=True)

def parse_btg_positions(uploaded_xlsx) -> pd.DataFrame:
    # MVP: primeira aba
    df0 = pd.read_excel(uploaded_xlsx)
    df0.columns = [str(c).strip() for c in df0.columns]

    # Ajustar colunas conforme seu layout do BTG
    col_account = "Conta" if "Conta" in df0.columns else "CONTA"
    col_prod = "Ativo/Produto" if "Ativo/Produto" in df0.columns else ("Produto" if "Produto" in df0.columns else None)
    col_val = "Valor Bruto" if "Valor Bruto" in df0.columns else ("Valor" if "Valor" in df0.columns else None)
    col_qty = "Quantidade" if "Quantidade" in df0.columns else None

    if col_prod is None or col_val is None:
        raise ValueError("BTG: não encontrei colunas mínimas (Ativo/Produto e Valor).")

    df = pd.DataFrame({
        "corretora": "BTG",
        "conta": df0[col_account].apply(_normalize_account),
        "asset_id": df0[col_prod].astype(str).str.strip(),
        "asset_nome": df0[col_prod].astype(str).str.strip(),
        "asset_tipo": df0.get("Mercado", df0.get("Mercado/Sub Mercado", "BTG")).astype(str).str.strip(),
        "valor_mercado": pd.to_numeric(df0[col_val], errors="coerce").fillna(0.0),
        "quantidade": pd.to_numeric(df0[col_qty], errors="coerce").fillna(0.0) if col_qty else 0.0,
        "moeda": "BRL",
    })
    return df

def classify_bucket_estrategia(df: pd.DataFrame) -> pd.DataFrame:
    # MVP: tudo começa como HOLD_MONITOR, e você refina depois com mapeamentos
    df = df.copy()
    df["bucket_estrategia"] = "HOLD_MONITOR"

    # Exemplo: se asset_tipo indica Fixed Income/ETF etc. pode virar STRATEGY depois
    # Por ora, UNKNOWN quando não tem asset_id
    df.loc[df["asset_id"].astype(str).str.strip().eq(""), "bucket_estrategia"] = "UNKNOWN"
    return df

def build_and_save_latest(control_df: pd.DataFrame,
                          xp_df: pd.DataFrame,
                          btg_df: pd.DataFrame,
                          cs_df: pd.DataFrame,
                          meta: dict) -> pd.DataFrame:
    DATA_DIR.mkdir(exist_ok=True)

    pos = pd.concat([xp_df, btg_df, cs_df], ignore_index=True)
    pos["corretora"] = pos["corretora"].apply(_normalize_broker)
    pos["conta"] = pos["conta"].apply(_normalize_account)

    # Merge com controle
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

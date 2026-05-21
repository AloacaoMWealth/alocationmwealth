from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

BASE_DIR = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
DATA_DIR = BASE_DIR / "data"
POS_DIR = BASE_DIR / "posicoes"
LATEST_PICKLE = DATA_DIR / "positions_latest.pkl"
CONTROL_PICKLE = DATA_DIR / "control_accounts_latest.pkl"
LATEST_META = DATA_DIR / "positions_meta.json"
BTG_ACCOUNT_WIDTH = 8


def find_repo_file(filename: str) -> Path:
    """Procura o arquivo no padrão do deploy e no padrão de teste local."""
    candidates = [POS_DIR / filename, BASE_DIR / filename, Path(filename)]
    for p in candidates:
        if p.exists():
            return p
    return POS_DIR / filename


def repo_files() -> dict[str, Path]:
    return {
        "Contas": find_repo_file("Contas.xlsx"),
        "XP": find_repo_file("XP.xlsx"),
        "BTG": find_repo_file("BTG.xlsx"),
        "CS": find_repo_file("CSProdutos.csv"),
    }


def source_signature() -> dict[str, Any]:
    sig: dict[str, Any] = {}
    for name, path in repo_files().items():
        if path.exists():
            stat = path.stat()
            sig[name] = {
                "path": str(path),
                "mtime": stat.st_mtime,
                "size": stat.st_size,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
            }
        else:
            sig[name] = {"path": str(path), "missing": True}
    return sig


def missing_repo_files() -> list[str]:
    return [str(p) for p in repo_files().values() if not p.exists()]


def _normalize_broker(x: Any) -> str:
    s = str(x or "").strip().upper()
    if "SCHWAB" in s or "CHARLES" in s or s == "CS":
        return "CS"
    if "XP" in s:
        return "XP"
    if "BTG" in s:
        return "BTG"
    return s


def _normalize_account(x: Any) -> str:
    if pd.isna(x):
        return ""
    s = str(x).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s.replace(" ", "")


def _only_digits(s: str) -> str:
    return "".join(ch for ch in str(s or "") if ch.isdigit())


def _normalize_btg_account(x: Any) -> str:
    d = _only_digits(_normalize_account(x))
    if not d:
        return ""
    return d[-BTG_ACCOUNT_WIDTH:].zfill(BTG_ACCOUNT_WIDTH)


def _pick_existing(cols: list[str], *names: str) -> str | None:
    norm_map = {str(c).strip().upper(): c for c in cols}
    for n in names:
        if n in cols:
            return n
        key = str(n).strip().upper()
        if key in norm_map:
            return norm_map[key]
    return None


def force_numeric(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    df = df.copy()
    for col in cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    return df


def get_ptax(fallback: float = 5.60) -> float:
    base = "https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/CotacaoDolarPeriodo"
    hoje = datetime.now().date()
    ini = (hoje - timedelta(days=10)).strftime("%m-%d-%Y")
    fim = hoje.strftime("%m-%d-%Y")
    url = (
        f"{base}(dataInicial=@dataInicial,dataFinalCotacao=@dataFinalCotacao)"
        f"?@dataInicial='{ini}'&@dataFinalCotacao='{fim}'"
        f"&$format=json&$select=cotacaoVenda&$orderby=dataHoraCotacao desc&$top=1"
    )
    try:
        r = requests.get(url, timeout=8)
        r.raise_for_status()
        value = r.json().get("value", [])
        if value:
            return float(value[0]["cotacaoVenda"])
    except Exception as exc:
        print(f"Aviso: falha ao obter PTAX. Usando fallback {fallback}. Erro: {exc}")
    return fallback


def load_control_accounts(src: str | Path | None = None) -> pd.DataFrame:
    if src is None:
        src = find_repo_file("Contas.xlsx")
        if not src.exists() and CONTROL_PICKLE.exists():
            return pd.read_pickle(CONTROL_PICKLE)
    src = Path(src)
    if not src.exists():
        raise FileNotFoundError(f"Controle de contas não encontrado: {src}")

    df = pd.read_excel(src)
    df.columns = [str(c).strip() for c in df.columns]
    col_broker = _pick_existing(df.columns.tolist(), "CORRETORA", "Corretora", "Broker", "BROKER")
    col_account = _pick_existing(df.columns.tolist(), "NÚMERO DA CONTA", "NMERO DA CONTA", "Numero da Conta", "Número da Conta", "Conta")
    if col_broker is None:
        raise ValueError(f"Contas.xlsx: não encontrei coluna de corretora. Colunas: {list(df.columns)}")
    if col_account is None:
        raise ValueError(f"Contas.xlsx: não encontrei coluna de conta. Colunas: {list(df.columns)}")

    df = df.rename(columns={col_broker: "corretora", col_account: "conta"})
    df["corretora"] = df["corretora"].apply(_normalize_broker)
    df["conta"] = df["conta"].apply(_normalize_account)
    m = df["corretora"].eq("BTG")
    df.loc[m, "conta"] = df.loc[m, "conta"].apply(_normalize_btg_account)

    keep = [
        "GRUPO GERAL", "corretora", "conta", "CLIENTE", "TIPO DE MARCAÇÃO ",
        "CLIENTE - CORRETORA", "Perfil Carteira"
    ]
    keep = [c for c in keep if c in df.columns]
    df = df.loc[:, list(dict.fromkeys(keep))].copy()

    DATA_DIR.mkdir(exist_ok=True)
    df.to_pickle(CONTROL_PICKLE)
    return df


def parse_cs_positions(src: str | Path) -> pd.DataFrame:
    path = Path(src)
    text = path.read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()
    header_idx = None
    for i, ln in enumerate(lines):
        low = ln.lower()
        if low.startswith("account,") or ("account," in low and "market value" in low):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError("CSProdutos.csv: não encontrei header com Account e Market Value.")

    import io
    raw = pd.read_csv(io.StringIO("\n".join(lines[header_idx:])), sep=",", engine="python", quotechar='"')
    raw.columns = [str(c).strip() for c in raw.columns]
    if "Market Value" not in raw.columns:
        raise ValueError(f"CSProdutos.csv: coluna Market Value não encontrada. Colunas: {list(raw.columns)}")

    mv = raw["Market Value"].astype(str).str.replace("$", "", regex=False).str.replace(",", "", regex=False)
    mv = mv.str.replace(r"[^0-9.\-]", "", regex=True)
    raw["Market Value"] = pd.to_numeric(mv, errors="coerce").fillna(0.0)
    qty_col = _pick_existing(raw.columns.tolist(), "Quantity", "Qty", "Quantidade")
    if qty_col:
        qty = raw[qty_col].astype(str).str.replace(",", "", regex=False).str.replace(r"[^0-9.\-]", "", regex=True)
        quantidade = pd.to_numeric(qty, errors="coerce").fillna(0.0)
    else:
        quantidade = pd.Series([0.0] * len(raw))

    sym_col = _pick_existing(raw.columns.tolist(), "Symbol/CUSIP", "Symbol", "CUSIP")
    name_col = _pick_existing(raw.columns.tolist(), "Name", "Description")
    type_col = _pick_existing(raw.columns.tolist(), "Security Type", "Type")

    return pd.DataFrame({
        "corretora": "CS",
        "conta": raw.get("Account", pd.Series([""] * len(raw))).apply(_normalize_account),
        "asset_id": raw.get(sym_col, pd.Series([""] * len(raw))).astype(str).str.strip() if sym_col else "",
        "asset_nome": raw.get(name_col, pd.Series([""] * len(raw))).astype(str).str.strip() if name_col else "",
        "asset_tipo": raw.get(type_col, pd.Series([""] * len(raw))).astype(str).str.strip() if type_col else "",
        "valor_mercado": raw["Market Value"],
        "quantidade": quantidade,
        "moeda": "USD",
        "mercado": "Internacional",
        "sub_mercado": "",
        "estrategia": "",
    })


def parse_xp_positions(src: str | Path) -> pd.DataFrame:
    resultado: list[dict[str, Any]] = []
    mapa = {
        "Custódia Remunerada": {"ativo": "CodigoAtivo", "valor": "ValorTotal", "qtd": "QuantidadeAtivo", "conta": "CodigoCliente"},
        "Ações": {"ativo": "CodigoAtivo", "valor": "ValorAtual", "qtd": "QuantidadeTotalComGarantias", "conta": "CodigoCliente"},
        "Fundos Imobiliários": {"ativo": "CodigoAtivo", "valor": "ValorAtual", "qtd": "QuantidadeTotalAtual", "conta": "CodigoCliente"},
        "Opções Flexíveis": {"ativo": "CodigoInstrumento", "valor": "Posicao", "qtd": None, "conta": "CodigoCliente"},
        "Opções Flexívies": {"ativo": "CodigoInstrumento", "valor": "Posicao", "qtd": None, "conta": "CodigoCliente"},
        "Fundos": {"ativo": "NomeFundo", "valor": "ValorAtual", "qtd": None, "conta": "CodigoCliente"},
        "Tesouro Direto": {"ativo": "NomeTitulo", "valor": "ValorBruto", "qtd": "QuantidadeTotal", "conta": "CodigoCliente"},
        "Previdência": {"ativo": "NomeFundo", "valor": "ValorReservaAcamulada", "qtd": None, "conta": "CodigoCliente"},
        "Proventos": {"ativo": "CodigoAtivo", "valor": "PrecoAtual", "qtd": "QuantidadeProvisionada", "conta": "CodigoCliente"},
        "Proventos Fundo Imob": {"ativo": "CodigoAtivo", "valor": "PrecoAtual", "qtd": "QuantidadeProvisionada", "conta": "CodigoCliente"},
        "Provisão Evento RF": {"ativo": "Evento", "valor": "Valor", "qtd": None, "conta": "CodigoCliente"},
        "Coe": {"ativo": "NomeAtivo", "valor": "ValorFinanceiroBruto", "qtd": None, "conta": "CodigoCliente"},
        "Renda Fixa": {"ativo": "NickName", "valor": "ValorFinanceiroBruto", "qtd": None, "conta": "CodigoCliente"},
        "Financeiro": {"ativo": None, "valor": "ValorDisponivel", "qtd": None, "conta": "CodigoCliente"},
    }
    xls = pd.ExcelFile(src)
    for aba, cfg in mapa.items():
        if aba not in xls.sheet_names:
            continue
        df = pd.read_excel(xls, sheet_name=aba)
        df.columns = [str(c).strip() for c in df.columns]
        if cfg["valor"] not in df.columns or cfg["conta"] not in df.columns:
            continue
        valores = pd.to_numeric(df[cfg["valor"]], errors="coerce").fillna(0.0)
        for i, valor_atual in valores.items():
            if float(valor_atual) <= 0:
                continue
            conta = _normalize_account(df.at[i, cfg["conta"]])
            if aba == "Financeiro":
                ativo = "Saldo Financeiro"
                nome = f"Saldo em Conta XP - Cliente {conta}"
            else:
                ativo_col = cfg["ativo"]
                ativo = str(df.at[i, ativo_col]).strip() if ativo_col and ativo_col in df.columns else aba
                nome = ativo
            qtd_col = cfg.get("qtd")
            qtd = pd.to_numeric(df.at[i, qtd_col], errors="coerce") if qtd_col and qtd_col in df.columns else 0.0
            if pd.isna(qtd):
                qtd = 0.0
            resultado.append({
                "corretora": "XP",
                "conta": conta,
                "asset_id": str(ativo).strip()[:60],
                "asset_nome": str(nome).strip()[:160],
                "asset_tipo": aba,
                "valor_mercado": float(valor_atual),
                "quantidade": float(qtd),
                "moeda": "BRL",
                "mercado": aba,
                "sub_mercado": aba,
                "estrategia": "",
            })
    return pd.DataFrame(resultado)


def parse_btg_positions(src: str | Path) -> pd.DataFrame:
    df0 = pd.read_excel(src)
    df0.columns = [str(c).strip() for c in df0.columns]
    cols = df0.columns.tolist()
    col_account = _pick_existing(cols, "Conta", "CONTA")
    col_prod = _pick_existing(cols, "Produto", "Ativo/Produto", "AtivoProduto")
    col_val = _pick_existing(cols, "Valor Bruto", "ValorBruto", "Valor")
    col_qty = _pick_existing(cols, "Quantidade", "Qtd", "Qtde")
    col_merc = _pick_existing(cols, "Mercado")
    col_subm = _pick_existing(cols, "Sub Mercado", "SubMercado", "Mercado/Sub Mercado")
    col_estr = _pick_existing(cols, "Estratégia", "Estrategia", "Estratégia ")
    if col_account is None or col_prod is None or col_val is None:
        raise ValueError(f"BTG.xlsx: colunas mínimas não encontradas. Colunas: {cols}")
    produto = df0[col_prod].astype(str).str.strip()
    out = pd.DataFrame({
        "corretora": "BTG",
        "conta": df0[col_account].apply(_normalize_btg_account),
        "asset_id": produto,
        "asset_nome": produto,
        "asset_tipo": df0[col_merc].astype(str).str.strip() if col_merc else "BTG",
        "mercado": df0[col_merc].astype(str).str.strip() if col_merc else "",
        "sub_mercado": df0[col_subm].astype(str).str.strip() if col_subm else "",
        "estrategia": df0[col_estr].astype(str).str.strip() if col_estr else "",
        "valor_mercado": pd.to_numeric(df0[col_val], errors="coerce").fillna(0.0),
        "quantidade": pd.to_numeric(df0[col_qty], errors="coerce").fillna(0.0) if col_qty else 0.0,
        "moeda": "BRL",
    })
    return out[out["valor_mercado"].fillna(0) != 0].copy()


def diagnose_unmatched(pos: pd.DataFrame, control: pd.DataFrame) -> dict[str, Any]:
    if pos.empty or control.empty:
        return {}
    keys = control[["corretora", "conta"]].drop_duplicates()
    check = pos.merge(keys, on=["corretora", "conta"], how="left", indicator=True)
    missing = check[check["_merge"].eq("left_only")]
    return {
        "unmatched_rows": int(len(missing)),
        "unmatched_by_broker": missing["corretora"].value_counts(dropna=False).to_dict(),
        "unmatched_examples": missing[["corretora", "conta"]].drop_duplicates().head(50).to_dict("records"),
    }


def build_latest_from_repo(dt_posicao: str | None = None) -> pd.DataFrame:
    missing = missing_repo_files()
    if missing:
        raise FileNotFoundError("Arquivos não encontrados: " + ", ".join(missing))

    files = repo_files()
    control = load_control_accounts(files["Contas"])
    xp = force_numeric(parse_xp_positions(files["XP"]), ["valor_mercado", "quantidade"])
    btg = force_numeric(parse_btg_positions(files["BTG"]), ["valor_mercado", "quantidade"])
    cs = force_numeric(parse_cs_positions(files["CS"]), ["valor_mercado", "quantidade"])

    pos = pd.concat([xp, btg, cs], ignore_index=True)
    if pos.empty:
        raise ValueError("Nenhuma posição foi carregada dos arquivos fonte.")

    pos["corretora"] = pos["corretora"].apply(_normalize_broker)
    pos["conta"] = pos["conta"].apply(_normalize_account)
    m = pos["corretora"].eq("BTG")
    pos.loc[m, "conta"] = pos.loc[m, "conta"].apply(_normalize_btg_account)
    m = control["corretora"].eq("BTG")
    control.loc[m, "conta"] = control.loc[m, "conta"].apply(_normalize_btg_account)

    merged = pos.merge(control, how="left", on=["corretora", "conta"], suffixes=("", "_ctrl"))
    ptax = get_ptax()
    merged["valor_original"] = pd.to_numeric(merged["valor_mercado"], errors="coerce").fillna(0.0)
    merged["valor_mercado"] = np.where(merged["corretora"].eq("CS"), merged["valor_original"] * ptax, merged["valor_original"])
    merged["dt_posicao"] = dt_posicao or datetime.now().date().isoformat()

    DATA_DIR.mkdir(exist_ok=True)
    merged.to_pickle(LATEST_PICKLE)
    meta = {
        "dt_posicao": merged["dt_posicao"].iloc[0],
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "ptax": ptax,
        "rows": int(len(merged)),
        "pl_total": float(merged["valor_mercado"].sum()),
        "source_signature": source_signature(),
        "diagnostics": {
            "xp_rows": int(len(xp)),
            "btg_rows": int(len(btg)),
            "cs_rows": int(len(cs)),
            "unmatched": diagnose_unmatched(pos, control),
        },
    }
    with open(LATEST_META, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    merged.attrs["meta"] = meta
    return merged


def load_latest_positions() -> pd.DataFrame | None:
    if not LATEST_PICKLE.exists():
        return None
    df = pd.read_pickle(LATEST_PICKLE)
    meta = {}
    if LATEST_META.exists():
        try:
            meta = json.loads(LATEST_META.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    df.attrs["meta"] = meta
    return df


def latest_is_stale() -> bool:
    if not LATEST_PICKLE.exists() or not LATEST_META.exists():
        return True
    try:
        meta = json.loads(LATEST_META.read_text(encoding="utf-8"))
        old = meta.get("source_signature", {})
        new = source_signature()
        for k, v in new.items():
            if v.get("missing"):
                return True
            old_v = old.get(k, {})
            if old_v.get("size") != v.get("size") or abs(float(old_v.get("mtime", 0)) - float(v.get("mtime", 0))) > 0.001:
                return True
        return False
    except Exception:
        return True

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
    candidates = [POS_DIR / filename, BASE_DIR / filename, Path.cwd() / filename, Path(filename)]
    for p in candidates:
        if p.exists():
            return p
    return POS_DIR / filename


def repo_files() -> dict[str, Path]:
    return {
        # Contas.xlsx é a fonte OFICIAL de grupo/cliente/conta/perfil.
        "Contas": find_repo_file("Contas.xlsx"),
        "XP": find_repo_file("XP.xlsx"),
        "BTG": find_repo_file("BTG.xlsx"),
        "CS": find_repo_file("CSProdutos.csv"),
        "SAFRA": find_repo_file("Safra.xlsx") if find_repo_file("Safra.xlsx").exists() else find_repo_file("Safra.xlsx"),
        "AGORA": find_repo_file("Agora.xlsx") if find_repo_file("Agora.xlsx").exists() else find_repo_file("Agora.xlsx"),
        # Complementar: XP US, XP Global, Atívore e outras fontes sem parser nativo.
        "OUTRAS": find_repo_file("Outras Custodias.xlsx"),
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
    # CS é opcional: quando não houver arquivo internacional, consolidamos XP/BTG normalmente.
    files = repo_files()
    required = [files["Contas"], files["XP"], files["BTG"]]
    return [str(p) for p in required if not p.exists()]


def _normalize_broker(x: Any) -> str:
    s = str(x or "").strip().upper()
    s_ascii = s.replace("Á", "A").replace("Í", "I").replace("Ó", "O").replace("Ê", "E")
    if "SCHWAB" in s or "CHARLES" in s or s == "CS":
        return "CS"
    if "XP" in s and "GLOBAL" in s:
        return "XP GLOBAL"
    if s in {"XPUS", "XP US"} or ("XP" in s and any(k in s for k in (" US", "US ", "INTERNACIONAL", "INTERNATIONAL"))):
        return "XP US"
    if "XP" in s:
        return "XP"
    if "SAFRA" in s:
        return "SAFRA"
    if s_ascii in {"AGORA", "AGORA CORRETORA"} or s_ascii.startswith("AGORA"):
        return "AGORA"
    if "ATIVORE" in s_ascii:
        return "ATIVORE"
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


def _only_digits(s: Any) -> str:
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


def _safe_col(df: pd.DataFrame, col: str | None, default: Any = "") -> pd.Series:
    if col and col in df.columns:
        return df[col]
    return pd.Series([default] * len(df), index=df.index)


def _money_to_float(series: pd.Series) -> pd.Series:
    s = series.astype(str).str.replace("R$", "", regex=False).str.replace("$", "", regex=False).str.strip()
    # Detecta formato brasileiro quando há vírgula como decimal.
    br_mask = s.str.contains(",", regex=False) & s.str.contains(r"\.\d{3}", regex=True)
    s = np.where(br_mask, pd.Series(s).str.replace(".", "", regex=False).str.replace(",", ".", regex=False), s)
    s = pd.Series(s, index=series.index).str.replace(",", "", regex=False)
    s = s.str.replace(r"[^0-9.\-]", "", regex=True)
    return pd.to_numeric(s, errors="coerce").fillna(0.0)


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

    # Padroniza nome da coluna Perfil Carteira, mesmo quando vier com espaço no final.
    for c in list(df.columns):
        if str(c).strip().upper() == "PERFIL CARTEIRA":
            df = df.rename(columns={c: "Perfil Carteira"})

    keep = [
        "GRUPO GERAL", "corretora", "conta", "CLIENTE", "TIPO DE MARCAÇÃO",
        "CLIENTE - CORRETORA", "Perfil Carteira"
    ]
    keep = [c for c in keep if c in df.columns]
    df = df.loc[:, list(dict.fromkeys(keep))].copy()

    DATA_DIR.mkdir(exist_ok=True)
    df.to_pickle(CONTROL_PICKLE)
    return df


OUTRAS_CUSTODIAS_COLUMNS = [
    "CORRETORA", "CONTA", "ATIVO", "CNPJ", "TICKER", "VALOR", "MOEDA", "QUANTIDADE",
    "CLASSE", "ESTRATEGIA", "LIQUIDEZ_DIAS", "INDEXADOR", "VENCIMENTO", "OBSERVACAO",
]


def parse_other_custody(src: str | Path) -> pd.DataFrame:
    """Posições complementares sem parser nativo (XP US, XP Global, Atívore etc.).

    A planilha tem uma linha por posição. Só CORRETORA, CONTA, ATIVO e VALOR são obrigatórios.
    CLASSE e ESTRATEGIA são opcionais: sem elas, o motor classifica pelo nome/CNPJ/ticker como faz
    com as demais fontes. Para XP US/Global, sem estratégia, o valor entra no total internacional."""
    path = Path(src)
    if not path.exists():
        return pd.DataFrame()
    raw = pd.read_excel(path)
    raw.columns = [str(c).strip().upper().replace("Ç", "C").replace("Ã", "A").replace("É", "E").replace("Á", "A").replace("Í", "I").replace("Õ", "O") for c in raw.columns]
    need = {"CORRETORA", "CONTA", "ATIVO", "VALOR"}
    if not need.issubset(raw.columns):
        raise ValueError(f"{path.name}: colunas obrigatórias ausentes: {sorted(need - set(raw.columns))}")
    raw = raw[raw["VALOR"].notna() & raw["CONTA"].notna()].copy()
    if raw.empty:
        return pd.DataFrame()

    def col(name, default=""):
        return raw[name] if name in raw.columns else pd.Series(default, index=raw.index)

    def txt(name, default=""):
        """Texto limpo: vazio vira "" (funciona com NaN, None e StringDtype do pandas novo)."""
        s = col(name, default).astype(object).where(col(name, default).notna(), "")
        return s.astype(str).str.strip().replace({"nan": "", "NaT": "", "None": ""})

    valor = pd.to_numeric(raw["VALOR"], errors="coerce") if pd.api.types.is_numeric_dtype(raw["VALOR"]) else _money_to_float(raw["VALOR"].astype(str))
    valor = valor.fillna(0.0)
    moeda = txt("MOEDA", "BRL").str.upper().replace({"": "BRL", "R$": "BRL", "US$": "USD", "$": "USD"})
    corretora = raw["CORRETORA"].apply(_normalize_broker)
    moeda = moeda.where(~corretora.isin(["XP US", "XP GLOBAL"]) | moeda.ne("BRL"), "USD")
    ativo = txt("ATIVO")
    ticker = txt("TICKER")
    out = pd.DataFrame({
        "corretora": corretora,
        "conta": raw["CONTA"].apply(_normalize_account),
        "asset_id": ticker.where(ticker.ne(""), ativo),
        "asset_nome": ativo,
        "asset_tipo": "Outras custódias",
        "valor_mercado": valor,
        "quantidade": pd.to_numeric(col("QUANTIDADE", 0), errors="coerce").fillna(0.0),
        "moeda": moeda,
        "mercado": np.where(corretora.isin(["XP US", "XP GLOBAL"]), "Internacional", "Outras custódias"),
        "sub_mercado": "",
        "estrategia": "",
        "indexador": txt("INDEXADOR"),
        "liquidez": txt("LIQUIDEZ_DIAS"),
        "vencimento": txt("VENCIMENTO"),
        "emissor": "",
        "taxa": "",
        "cnpj": txt("CNPJ"),
        "saldo_operacional": False,
        "custodia_classe": txt("CLASSE"),
        "custodia_estrategia": txt("ESTRATEGIA"),
    })
    return out[out["valor_mercado"].fillna(0) != 0].copy()


def outras_custodias_template() -> pd.DataFrame:
    """Modelo de preenchimento apenas para custódias complementares sem parser nativo."""
    return pd.DataFrame([
        ["XP GLOBAL", "3XP50891", "Patrimônio XP Global", "", "", 250000.00, "USD", "", "Internacional", "", "", "", "", "Sem abertura: conta no total internacional"],
        ["XP US", "QXR347535", "Patrimônio XP US", "", "", 100000.00, "USD", "", "Internacional", "", "", "", "", "Sem abertura: conta no total internacional"],
        ["ATIVORE", "CONTA_EXEMPLO", "Patrimônio Atívore", "", "", 100000.00, "USD", "", "Internacional", "", "", "", "", "Preencha moeda/classe conforme a posição"],
    ], columns=OUTRAS_CUSTODIAS_COLUMNS)


def _safe_excel_date_text(value: Any) -> str:
    if value is None or pd.isna(value):
        return ""
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.strftime("%Y-%m-%d")
    return str(value).strip()


def parse_safra_positions(src: str | Path) -> pd.DataFrame:
    """Parser nativo do relatório consolidado Safra."""
    path = Path(src)
    if not path.exists():
        return pd.DataFrame()
    raw = pd.read_excel(path, header=2)
    raw.columns = [str(c).strip() for c in raw.columns]
    required = {"Conta", "Produto", "Categoria", "PL"}
    if not required.issubset(raw.columns):
        raise ValueError(f"{path.name}: layout Safra não reconhecido. Faltam: {sorted(required-set(raw.columns))}")
    raw = raw[raw["Conta"].notna() & raw["PL"].notna()].copy()
    if raw.empty:
        return pd.DataFrame()

    def s(name, default=""):
        col = _safe_col(raw, name, default)
        return col.astype(object).where(col.notna(), "").astype(str).str.strip()

    produto, categoria, modalidade = s("Produto"), s("Categoria"), s("Modalidade")
    mercadoria, emissor, ativo, benchmark = s("Mercadoria/Fundo"), s("Emissor/Gestor"), s("Ativo"), s("Benchmark")
    quantidade = pd.to_numeric(_safe_col(raw, "Quantidade", 0), errors="coerce").fillna(0.0)
    pl = pd.to_numeric(_safe_col(raw, "PL", 0), errors="coerce").fillna(0.0)

    p_up, c_up, m_up = produto.str.upper(), categoria.str.upper(), modalidade.str.upper()
    is_fin = p_up.eq("FINANCEIRO") | c_up.eq("SALDO EM CONTA")
    is_fund = p_up.eq("FUNDOS")
    is_prev = c_up.isin(["PREVIDÊNCIA", "PREVIDENCIA"])
    is_coe = m_up.eq("COE") | c_up.eq("ESTRUTURADAS")
    is_fii = c_up.isin(["FUNDOS IMOBILIÁRIOS", "FUNDOS IMOBILIARIOS"])
    is_equity = c_up.isin(["AÇÕES", "ACOES", "ALUGUEL DE AÇÕES", "ALUGUEL DE ACOES"])
    is_rf = p_up.eq("RENDA FIXA")

    asset_id = ativo.copy()
    asset_id = asset_id.where(~is_fund, mercadoria.where(mercadoria.ne(""), ativo))
    asset_id = asset_id.where(~is_fin, "Saldo Financeiro")
    asset_nome = mercadoria.where(mercadoria.ne(""), ativo)
    asset_nome = asset_nome.where(~is_fin, "Saldo em Conta Safra")

    asset_tipo = pd.Series("Safra", index=raw.index)
    asset_tipo = asset_tipo.where(~is_fin, "Financeiro")
    asset_tipo = asset_tipo.where(~is_fund, "Fundos")
    asset_tipo = asset_tipo.where(~is_prev, "Previdência")
    asset_tipo = asset_tipo.where(~is_coe, "Coe")
    asset_tipo = asset_tipo.where(~is_fii, "Fundos Imobiliários")
    asset_tipo = asset_tipo.where(~is_equity, "Ações")
    asset_tipo = asset_tipo.where(~is_rf, "Renda Fixa")

    sub_mercado = categoria.copy().replace({
        "Emissão Bancária": "Bancário", "Emissao Bancaria": "Bancário",
        "Títulos Públicos": "Tesouro Direto", "Titulos Publicos": "Tesouro Direto",
    })

    out = pd.DataFrame({
        "corretora": "SAFRA",
        "conta": raw["Conta"].apply(_normalize_account),
        "asset_id": asset_id, "asset_nome": asset_nome, "asset_tipo": asset_tipo,
        "valor_mercado": pl, "quantidade": quantidade, "moeda": "BRL",
        "mercado": produto, "sub_mercado": sub_mercado, "estrategia": benchmark,
        "indexador": benchmark, "liquidez": s("Liquidez"),
        "cotizacao_resgate": s("Cotização"), "liquidacao_resgate": s("Liquidação"),
        "vencimento": _safe_col(raw, "Vencimento", "").apply(_safe_excel_date_text),
        "emissor": emissor, "taxa": "", "cnpj": "",
        "saldo_operacional": is_fin, "origem_posicao": "Safra",
        "posicao_alugada": c_up.isin(["ALUGUEL DE AÇÕES", "ALUGUEL DE ACOES"]),
    })
    return out[out["valor_mercado"].fillna(0) != 0].copy()


AGORA_AGGREGATE_MAP = {
    "Ações/FIIs/ETFs/BDRs": ("RV Brasil", "RV Brasil (sem estratégia)"),
    "Títulos privados": ("RF Brasil", "RF Brasil (sem estratégia)"),
    "Títulos públicos": ("RF Brasil", "RF Brasil (sem estratégia)"),
    "COE": ("Fora da Estratégia", "COE / Estruturados"),
    "Fundos e clubes de investimento": ("Fora da Estratégia", "Brasil (sem estratégia)"),
    "Opções": ("Fora da Estratégia", "Outros / Não Classificado"),
    "Ouro": ("Alternativos", "Ouro"),
    "Termo de Ações": ("RV Brasil", "RV Brasil (sem estratégia)"),
    "Saldo projetado": ("Caixa", "Saldo em Conta"),
}


def parse_agora_positions(src: str | Path) -> pd.DataFrame:
    """Parser seguro do relatório agregado Ágora (sem inventar composição de ativos)."""
    path = Path(src)
    if not path.exists():
        return pd.DataFrame()
    raw = pd.read_excel(path)
    raw.columns = [str(c).strip() for c in raw.columns]
    if "CBLC" not in raw.columns:
        raise ValueError(f"{path.name}: layout Ágora não reconhecido (coluna CBLC ausente).")
    rows = []
    for _, r in raw.iterrows():
        conta = _only_digits(r.get("CBLC", ""))
        if not conta:
            continue
        cliente = str(r.get("Nome", "") or "").strip()
        for col, (classe, bucket) in AGORA_AGGREGATE_MAP.items():
            if col not in raw.columns:
                continue
            value = pd.to_numeric(r.get(col, np.nan), errors="coerce")
            if pd.isna(value) or abs(float(value)) <= 0.0001:
                continue
            rows.append({
                "corretora": "AGORA", "conta": conta,
                "asset_id": f"AGORA | {col}", "asset_nome": f"{col} — {cliente}" if cliente else col,
                "asset_tipo": "Ágora agregado", "valor_mercado": float(value), "quantidade": 0.0,
                "moeda": "BRL", "mercado": "Ágora agregado", "sub_mercado": col,
                "estrategia": "", "indexador": "", "liquidez": "",
                "cotizacao_resgate": "", "liquidacao_resgate": "", "vencimento": "",
                "emissor": "", "taxa": "", "cnpj": "", "saldo_operacional": bucket == "Saldo em Conta",
                "custodia_classe": classe, "custodia_estrategia": bucket,
                "custodia_agregada": True, "origem_posicao": "Ágora agregado",
            })
    return pd.DataFrame(rows)


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

    raw["Market Value"] = _money_to_float(raw["Market Value"])
    qty_col = _pick_existing(raw.columns.tolist(), "Quantity", "Qty", "Quantidade")
    quantidade = _money_to_float(raw[qty_col]) if qty_col else pd.Series([0.0] * len(raw), index=raw.index)

    sym_col = _pick_existing(raw.columns.tolist(), "Symbol/CUSIP", "Symbol", "CUSIP")
    name_col = _pick_existing(raw.columns.tolist(), "Name", "Description")
    type_col = _pick_existing(raw.columns.tolist(), "Security Type", "Type")
    type_series = _safe_col(raw, type_col).astype(str).str.upper() if type_col else pd.Series([""] * len(raw), index=raw.index)
    name_series = _safe_col(raw, name_col).astype(str).str.upper() if name_col else pd.Series([""] * len(raw), index=raw.index)
    symbol_series = _safe_col(raw, sym_col).astype(str).str.upper() if sym_col else pd.Series([""] * len(raw), index=raw.index)
    saldo_operacional = (
        type_series.str.contains("CASH", na=False)
        | name_series.str.contains("CASH|MONEY MARKET|SWEEP|BANK DEPOSIT", regex=True, na=False)
        | symbol_series.str.fullmatch("CASH", na=False)
    )

    df = pd.DataFrame({
        "corretora": "CS",
        "conta": _safe_col(raw, "Account").apply(_normalize_account),
        "asset_id": _safe_col(raw, sym_col).astype(str).str.strip(),
        "asset_nome": _safe_col(raw, name_col).astype(str).str.strip(),
        "asset_tipo": _safe_col(raw, type_col).astype(str).str.strip(),
        "valor_mercado": raw["Market Value"],
        "quantidade": quantidade,
        "moeda": "USD",
        "mercado": "Internacional",
        "sub_mercado": "",
        "estrategia": "",
        "indexador": "",
        "liquidez": "",
        "vencimento": "",
        "emissor": "",
        "taxa": "",
        "saldo_operacional": saldo_operacional,
    })
    return df[df["valor_mercado"].fillna(0) != 0].copy()



def parse_xp_positions(src: str | Path) -> pd.DataFrame:
    resultado: list[dict[str, Any]] = []
    mapa = {
        "Financeiro": {"ativo": None, "valor": "ValorDisponivel", "qtd": None, "conta": "CodigoCliente"},
        "Ações": {"ativo": "CodigoAtivo", "nome": "NomeEmpresaEmitente", "valor": "ValorAtual", "qtd": "QuantidadeTotalComGarantias", "conta": "CodigoCliente"},
        "Fundos Imobiliários": {"ativo": "CodigoAtivo", "nome": "NomeEmpresaEmitente", "valor": "ValorAtual", "qtd": "QuantidadeTotalAtual", "conta": "CodigoCliente"},
        # "Custódia Remunerada" é apenas informativa sobre ativos disponíveis para aluguel.
        # Não representa posição econômica adicional e, portanto, não entra no consolidado.
        "Fundos": {"ativo": "NomeFundo", "valor": "ValorAtual", "qtd": "QuantidadeCotas", "conta": "CodigoCliente"},
        "Tesouro Direto": {"ativo": "NomeTitulo", "valor": "ValorBruto", "qtd": "QuantidadeTotal", "conta": "CodigoCliente"},
        "Previdência": {"ativo": "NomeFundo", "nome": "NomePlanoResumido", "valor": "ValorReservaAcumulada", "qtd": "QuantidadeCotas", "conta": "CodigoCliente"},
        "Previdência ": {"ativo": "NomeFundo", "nome": "NomePlanoResumido", "valor": "ValorReservaAcumulada", "qtd": "QuantidadeCotas", "conta": "CodigoCliente"},
        "Coe": {"ativo": "NomeAtivo", "valor": "ValorFinanceiroBruto", "qtd": "QuantidadeTotal", "conta": "CodigoCliente"},
        "Renda Fixa": {"ativo": "NickName", "nome": "NomeAtivo", "valor": "ValorFinanceiroBruto", "qtd": "QuantidadeTotal", "conta": "CodigoCliente"},
        "Proventos": {"ativo": "CodigoAtivo", "valor": "PrecoAtual", "qtd": "QuantidadeProvisionada", "conta": "CodigoCliente"},
        "Proventos Fundo Imob": {"ativo": "CodigoAtivo", "valor": "PrecoAtual", "qtd": "QuantidadeProvisionada", "conta": "CodigoCliente"},
        "Provisão Evento RF": {"ativo": "Evento", "valor": "Valor", "qtd": None, "conta": "CodigoCliente"},
        "Opções Flexíveis": {"ativo": "CodigoInstrumento", "valor": "Posicao", "qtd": None, "conta": "CodigoCliente"},
        "Opções Flexívies": {"ativo": "CodigoInstrumento", "valor": "Posicao", "qtd": None, "conta": "CodigoCliente"},
        "Opções Flexívies ": {"ativo": "CodigoInstrumento", "valor": "Posicao", "qtd": None, "conta": "CodigoCliente"},
    }
    xls = pd.ExcelFile(src)
    for aba, cfg in mapa.items():
        if aba not in xls.sheet_names:
            continue
        df = pd.read_excel(xls, sheet_name=aba)
        df.columns = [str(c).strip() for c in df.columns]
        valor_col = cfg.get("valor")
        conta_col = cfg.get("conta")
        if valor_col not in df.columns or conta_col not in df.columns:
            continue
        valores = pd.to_numeric(df[valor_col], errors="coerce").fillna(0.0)
        for i, valor_atual in valores.items():
            if float(valor_atual) == 0:
                continue
            conta = _normalize_account(df.at[i, conta_col])
            if aba == "Financeiro":
                ativo = "Saldo Financeiro"
                nome = f"Saldo em Conta XP - Cliente {conta}"
            else:
                ativo_col = cfg.get("ativo")
                nome_col = cfg.get("nome")
                ativo = str(df.at[i, ativo_col]).strip() if ativo_col and ativo_col in df.columns else aba.strip()
                nome = str(df.at[i, nome_col]).strip() if nome_col and nome_col in df.columns and pd.notna(df.at[i, nome_col]) else ativo
            qtd_col = cfg.get("qtd")
            qtd = pd.to_numeric(df.at[i, qtd_col], errors="coerce") if qtd_col and qtd_col in df.columns else 0.0
            if pd.isna(qtd):
                qtd = 0.0
            cnpj_col = _pick_existing(df.columns.tolist(), "Cnpj", "CNPJ", "CNPJ_FUNDO", "CNPJ Fundo")
            periodo_cot = _safe_col(df, _pick_existing(df.columns.tolist(), "PeriodoCotizacaoResgate", "PERÍODO_COTIZAÇÃO", "Periodo Cotizacao Resgate"), "").astype(str)
            periodo_liq = _safe_col(df, _pick_existing(df.columns.tolist(), "PeriodoLiquidacaoResgate", "PERÍODO_LIQUIDAÇÃO", "Periodo Liquidacao Resgate"), "").astype(str)
            resultado.append({
                "corretora": "XP",
                "conta": conta,
                "asset_id": str(ativo).strip(),
                "asset_nome": str(nome).strip(),
                "asset_tipo": aba.strip(),
                "valor_mercado": float(valor_atual),
                "quantidade": float(qtd),
                "moeda": "BRL",
                "mercado": aba.strip(),
                "sub_mercado": str(df.at[i, "Categoria"]).strip() if "Categoria" in df.columns and pd.notna(df.at[i, "Categoria"]) else aba.strip(),
                "estrategia": str(df.at[i, "TipoDeAtivo"]).strip() if "TipoDeAtivo" in df.columns and pd.notna(df.at[i, "TipoDeAtivo"]) else "",
                "indexador": str(df.at[i, "NomeIndexador"]).strip() if "NomeIndexador" in df.columns and pd.notna(df.at[i, "NomeIndexador"]) else "",
                "liquidez": str(df.at[i, "TipoLiquidez"]).strip() if "TipoLiquidez" in df.columns and pd.notna(df.at[i, "TipoLiquidez"]) else (str(df.at[i, "PeriodoLiquidacaoResgate"]).strip() if "PeriodoLiquidacaoResgate" in df.columns and pd.notna(df.at[i, "PeriodoLiquidacaoResgate"]) else ""),
                "cotizacao_resgate": str(df.at[i, "PeriodoCotizacaoResgate"]).strip() if "PeriodoCotizacaoResgate" in df.columns and pd.notna(df.at[i, "PeriodoCotizacaoResgate"]) else "",
                "liquidacao_resgate": str(df.at[i, "PeriodoLiquidacaoResgate"]).strip() if "PeriodoLiquidacaoResgate" in df.columns and pd.notna(df.at[i, "PeriodoLiquidacaoResgate"]) else "",
                "vencimento": str(df.at[i, "DataVencimento"]).strip() if "DataVencimento" in df.columns and pd.notna(df.at[i, "DataVencimento"]) else "",
                "emissor": str(df.at[i, "NomeEmissor"]).strip() if "NomeEmissor" in df.columns and pd.notna(df.at[i, "NomeEmissor"]) else "",
                "taxa": str(df.at[i, "TaxaCompleta"]).strip() if "TaxaCompleta" in df.columns and pd.notna(df.at[i, "TaxaCompleta"]) else (str(df.at[i, "Taxa"]).strip() if "Taxa" in df.columns and pd.notna(df.at[i, "Taxa"]) else ""),
                "cnpj": str(df.at[i, cnpj_col]).strip() if cnpj_col and cnpj_col in df.columns and pd.notna(df.at[i, cnpj_col]) else "",
                "saldo_operacional": aba == "Financeiro",
            })
    return pd.DataFrame(resultado)

def parse_btg_positions(src: str | Path) -> pd.DataFrame:
    df0 = pd.read_excel(src)
    df0.columns = [str(c).strip() for c in df0.columns]
    cols = df0.columns.tolist()
    col_account = _pick_existing(cols, "Conta", "CONTA")
    col_asset = _pick_existing(cols, "Ativo", "Ticker", "Código", "Codigo")
    col_prod = _pick_existing(cols, "Produto", "Ativo/Produto", "AtivoProduto")
    col_val = _pick_existing(cols, "Valor Bruto", "ValorBruto", "Valor")
    col_qty = _pick_existing(cols, "Quantidade", "Qtd", "Qtde")
    col_merc = _pick_existing(cols, "Mercado")
    col_subm = _pick_existing(cols, "Sub Mercado", "SubMercado", "Mercado/Sub Mercado")
    col_estr = _pick_existing(cols, "Estratégia", "Estrategia", "Estratégia ")
    if col_account is None or col_val is None:
        raise ValueError(f"BTG.xlsx: colunas mínimas não encontradas. Colunas: {cols}")
    produto = _safe_col(df0, col_prod, "").astype(str).str.strip()
    ativo = _safe_col(df0, col_asset, "").astype(str).str.strip()
    asset_id = np.where(ativo.str.len() > 0, ativo, produto)
    out = pd.DataFrame({
        "corretora": "BTG",
        "conta": df0[col_account].apply(_normalize_btg_account),
        "asset_id": pd.Series(asset_id, index=df0.index).astype(str).str.strip(),
        "asset_nome": produto,
        "asset_tipo": _safe_col(df0, col_merc, "BTG").astype(str).str.strip(),
        "mercado": _safe_col(df0, col_merc, "").astype(str).str.strip(),
        "sub_mercado": _safe_col(df0, col_subm, "").astype(str).str.strip(),
        "estrategia": _safe_col(df0, col_estr, "").astype(str).str.strip(),
        "valor_mercado": pd.to_numeric(df0[col_val], errors="coerce").fillna(0.0),
        "quantidade": pd.to_numeric(_safe_col(df0, col_qty, 0.0), errors="coerce").fillna(0.0) if col_qty else 0.0,
        "moeda": "BRL",
        "indexador": _safe_col(df0, col_estr, "").astype(str).str.strip(),
        "liquidez": "",
        "cotizacao_resgate": _safe_col(df0, _pick_existing(cols, "Data Cotização", "Data Cotizacao", "Cotização"), "").astype(str).str.strip(),
        "liquidacao_resgate": "",
        "vencimento": _safe_col(df0, _pick_existing(cols, "Vencimento", "Data Vencimento"), "").astype(str).str.strip(),
        "emissor": _safe_col(df0, _pick_existing(cols, "Emissor"), "").astype(str).str.strip(),
        "taxa": _safe_col(df0, _pick_existing(cols, "Taxa Compra", "Taxa Emissão", "Taxa"), "").astype(str).str.strip(),
        "cnpj": _safe_col(df0, _pick_existing(cols, "CNPJ", "CNPJ_FUNDO", "CNPJ Fundo"), "").astype(str).str.strip(),
        "saldo_operacional": (
            _safe_col(df0, col_merc, "").astype(str).str.strip().str.upper().eq("CONTA CORRENTE")
            | _safe_col(df0, col_prod, "").astype(str).str.strip().str.upper().eq("CONTA CORRENTE")
        ),
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
    cs = force_numeric(parse_cs_positions(files["CS"]), ["valor_mercado", "quantidade"]) if files["CS"].exists() else pd.DataFrame(columns=xp.columns)
    safra = force_numeric(parse_safra_positions(files["SAFRA"]), ["valor_mercado", "quantidade"]) if files["SAFRA"].exists() else pd.DataFrame(columns=xp.columns)
    agora = force_numeric(parse_agora_positions(files["AGORA"]), ["valor_mercado", "quantidade"]) if files["AGORA"].exists() else pd.DataFrame(columns=xp.columns)
    outras = force_numeric(parse_other_custody(files["OUTRAS"]), ["valor_mercado", "quantidade"]) if files["OUTRAS"].exists() else pd.DataFrame(columns=xp.columns)

    pos = pd.concat([xp, btg, cs, safra, agora, outras], ignore_index=True, sort=False)
    if "saldo_operacional" not in pos.columns:
        pos["saldo_operacional"] = False
    pos["saldo_operacional"] = pos["saldo_operacional"].fillna(False).astype(bool)
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
    is_usd = merged["corretora"].isin(["CS", "XP US", "XP GLOBAL"]) | merged.get("moeda", pd.Series("", index=merged.index)).astype(str).str.upper().eq("USD")
    merged["valor_mercado"] = np.where(is_usd, merged["valor_original"] * ptax, merged["valor_original"])
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
            "safra_rows": int(len(safra)),
            "agora_rows": int(len(agora)),
            "outras_custodias_rows": int(len(outras)),
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
                # CS e OUTRAS são opcionais: ausência não deve forçar rebuild a cada abertura.
                if k in {"CS", "SAFRA", "AGORA", "OUTRAS"} and old.get(k, {}).get("missing", True):
                    continue
                return True
            old_v = old.get(k, {})
            if old_v.get("size") != v.get("size") or abs(float(old_v.get("mtime", 0)) - float(v.get("mtime", 0))) > 0.001:
                return True
        return False
    except Exception:
        return True

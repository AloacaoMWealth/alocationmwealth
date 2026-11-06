"""Persistência dos arquivos editados pelo app (Cadastro Mestre, modelos publicados, histórico).

Por que existe: no Streamlit Cloud o disco do container é efêmero. Tudo o que o app grava
em arquivo some quando o app reinicia. O repositório do GitHub é a fonte da verdade, então
depois de cada gravação local o arquivo também é enviado ao repositório (API de Contents),
e na inicialização o app baixa a versão mais recente dos arquivos persistidos.

Configuração (st.secrets ou variáveis de ambiente):
    GITHUB_TOKEN   token com permissão de escrita em "Contents" do repositório (obrigatório)
    GITHUB_REPO    "usuario/repositorio" (obrigatório)
    GITHUB_BRANCH  branch onde os dados ficam (padrão: dados). Use uma branch exclusiva de dados
                   (ex.: "dados") para o Streamlit Cloud não reiniciar o app a cada salvamento.
    GITHUB_SUBDIR  pasta do app dentro do repositório, se o app não estiver na raiz (opcional)
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import requests

API = "https://api.github.com"
TIMEOUT = 25
STATE_FILE = ".github_persistence_state.json"


def _state_path(base_dir: Path) -> Path:
    return Path(base_dir) / "data" / STATE_FILE


def _load_state(base_dir: Path) -> dict:
    p = _state_path(base_dir)
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except Exception:
        return {}


def _save_state(base_dir: Path, state: dict) -> None:
    p = _state_path(base_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def _secret(name: str, default: str = "") -> str:
    try:
        import streamlit as st
        if name in st.secrets:
            return str(st.secrets[name]).strip()
    except Exception:
        pass
    return str(os.environ.get(name, default)).strip()


def config() -> dict:
    return {
        "token": _secret("GITHUB_TOKEN"),
        "repo": _secret("GITHUB_REPO"),
        "branch": _secret("GITHUB_BRANCH", "dados") or "dados",
        "subdir": _secret("GITHUB_SUBDIR").strip("/"),
    }


def is_configured() -> bool:
    c = config()
    return bool(c["token"] and c["repo"])


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def repo_path_for(local_path: Path, base_dir: Path) -> str:
    """Caminho do arquivo dentro do repositório, relativo à pasta do app."""
    try:
        rel = Path(local_path).resolve().relative_to(Path(base_dir).resolve())
    except ValueError:
        rel = Path(Path(local_path).name)
    sub = config()["subdir"]
    return f"{sub}/{rel.as_posix()}" if sub else rel.as_posix()


def _ensure_branch(c: dict) -> None:
    """Cria a branch de dados a partir da branch padrão do repositório, se ela ainda não existir."""
    h = _headers(c["token"])
    r = requests.get(f"{API}/repos/{c['repo']}/branches/{c['branch']}", headers=h, timeout=TIMEOUT)
    if r.status_code == 200:
        return
    if r.status_code != 404:
        r.raise_for_status()
    repo = requests.get(f"{API}/repos/{c['repo']}", headers=h, timeout=TIMEOUT)
    repo.raise_for_status()
    default = repo.json().get("default_branch", "main")
    ref = requests.get(f"{API}/repos/{c['repo']}/git/ref/heads/{default}", headers=h, timeout=TIMEOUT)
    ref.raise_for_status()
    sha = ref.json()["object"]["sha"]
    create = requests.post(
        f"{API}/repos/{c['repo']}/git/refs", headers=h, timeout=TIMEOUT,
        json={"ref": f"refs/heads/{c['branch']}", "sha": sha},
    )
    create.raise_for_status()


def push_file(local_path: Path, base_dir: Path, message: str) -> tuple[bool, str]:
    """Envia arquivo com controle otimista de versão para evitar lost update."""
    if not is_configured():
        return False, "GitHub não configurado (GITHUB_TOKEN / GITHUB_REPO). Salvo apenas no disco do app."
    c = config()
    local_path = Path(local_path)
    if not local_path.exists():
        return False, f"Arquivo local não encontrado: {local_path.name}"
    path = repo_path_for(local_path, base_dir)
    h = _headers(c["token"])
    url = f"{API}/repos/{c['repo']}/contents/{path}"
    try:
        if c["branch"] != "main":
            _ensure_branch(c)
        current = requests.get(url, headers=h, params={"ref": c["branch"]}, timeout=TIMEOUT)
        current_sha = current.json().get("sha") if current.status_code == 200 else None

        state = _load_state(base_dir)
        expected_sha = state.get(path)
        if expected_sha and current_sha and expected_sha != current_sha:
            return False, (
                f"Conflito de versão em {local_path.name}: o arquivo mudou no GitHub desde que esta instância "
                "o carregou. Recarregue do GitHub antes de salvar para não sobrescrever outra alteração."
            )

        body = {
            "message": message,
            "content": base64.b64encode(local_path.read_bytes()).decode("ascii"),
            "branch": c["branch"],
        }
        if current_sha:
            body["sha"] = current_sha
        put = requests.put(url, headers=h, json=body, timeout=TIMEOUT)
        if put.status_code not in (200, 201):
            return False, f"GitHub recusou o salvamento de {local_path.name} ({put.status_code}): {put.text[:200]}"
        payload = put.json()
        new_sha = (payload.get("content") or {}).get("sha")
        if new_sha:
            state[path] = new_sha
            _save_state(base_dir, state)
        return True, f"{local_path.name} salvo no GitHub ({c['repo']}@{c['branch']})."
    except requests.RequestException as exc:
        return False, f"Falha de rede ao salvar {local_path.name} no GitHub: {exc}"
    except Exception as exc:
        return False, f"Falha ao salvar {local_path.name} no GitHub: {exc}"


def push_files(paths: list[Path], base_dir: Path, message: str) -> tuple[bool, str]:
    msgs, all_ok = [], True
    for p in paths:
        ok, msg = push_file(p, base_dir, message)
        all_ok &= ok
        msgs.append(msg)
    return all_ok, " ".join(msgs)


def pull_file(local_path: Path, base_dir: Path) -> tuple[bool, str]:
    """Baixa arquivo e registra o SHA-base usado por esta instância."""
    if not is_configured():
        return False, "GitHub não configurado."
    c = config()
    local_path = Path(local_path)
    path = repo_path_for(local_path, base_dir)
    h = _headers(c["token"])
    try:
        r = requests.get(
            f"{API}/repos/{c['repo']}/contents/{path}",
            headers=h,
            params={"ref": c["branch"]},
            timeout=TIMEOUT,
        )
        if r.status_code == 404:
            return False, f"{local_path.name} ainda não existe no GitHub."
        r.raise_for_status()
        payload = r.json()
        data = base64.b64decode(payload.get("content", ""))
        sha = payload.get("sha")
        state = _load_state(base_dir)
        if sha:
            state[path] = sha
            _save_state(base_dir, state)

        if local_path.exists() and local_path.read_bytes() == data:
            return True, f"{local_path.name} já está atualizado."
        local_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = local_path.with_name(local_path.name + ".dl")
        tmp.write_bytes(data)
        tmp.replace(local_path)
        return True, f"{local_path.name} restaurado do GitHub."
    except requests.RequestException as exc:
        return False, f"Falha de rede ao baixar {local_path.name}: {exc}"
    except Exception as exc:
        return False, f"Falha ao baixar {local_path.name}: {exc}"

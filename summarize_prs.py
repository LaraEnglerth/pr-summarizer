"""ler arquivos JSON de PRs coletados na Fase 1, envia para o Claude e salva um resumo estruturado no campo `llm_summary` de cada arquivo
    python summarize_prs.py --input prs_data --delay 1.0 --limit 10
    pip install anthropic python-dotenv
"""

import os
import json
import time
import argparse
import logging
import re
from pathlib import Path

import anthropic #SDK que abstrai as chamadas HTTP para a API do claude
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

MODEL      = "claude-sonnet-4-5"
MAX_TOKENS = 2048

MAX_PATCH_CHARS_PER_FILE = 1500
MAX_FILES_IN_PROMPT      = 10
MAX_COMMENTS_IN_PROMPT   = 8
MAX_THREADS_IN_PROMPT    = 5
MAX_BODY_CHARS           = 2000

#vocabulário controlado para validação pós-geração
VALID_TIPOS       = {"feature", "bugfix", "refactor", "test", "config", "docs", "chore"}
VALID_COMPLEXIDADE = {"baixo", "médio", "alto"}

#trunca e avisa
def _truncate(text: str | None, max_chars: int) -> str:
    if not text:
        return "(sem conteúdo)"
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n... [TRUNCADO — {len(text) - max_chars} chars omitidos]"

#transforma a lista de arquivos em legível para o modelo
def _format_files_section(files: list[dict]) -> str:
    if not files:
        return "Nenhum arquivo disponível."

    limited   = files[:MAX_FILES_IN_PROMPT]
    remaining = len(files) - len(limited)
    lines     = []

    status_labels = {
        "added": "ADICIONADO", "modified": "MODIFICADO",
        "removed": "REMOVIDO",  "renamed":  "RENOMEADO",
    }

    for f in limited:
        label  = status_labels.get(f.get("status", ""), f.get("status", "").upper())
        header = (
            f"• [{label}] {f.get('filename')} "
            f"({f.get('language', '?')}, categoria: {f.get('category', '?')}) "
            f"+{f.get('additions', 0)} -{f.get('deletions', 0)} linhas"
        )
        if f.get("patch_truncated"):
            header += " ⚠️ diff indisponível (arquivo muito grande)"
        lines.append(header)

        patch = f.get("patch")
        if patch:
            lines += ["  Diff:", "  ```", _truncate(patch, MAX_PATCH_CHARS_PER_FILE), "  ```"]

    if remaining:
        lines.append(f"\n(+ {remaining} arquivo(s) omitido(s) por limite de contexto)")

    return "\n".join(lines)

#formata as threads de revisão para preservar a hierarquia
def _format_review_threads(threads: list[dict]) -> str:
    if not threads:
        return "Nenhuma thread de revisão."

    lines = []
    for i, thread in enumerate(threads[:MAX_THREADS_IN_PROMPT], 1):
        lines.append(f"Thread {i} — {thread.get('path', '?')} (linha {thread.get('line', '?')}):")
        hunk = thread.get("diff_hunk", "")
        if hunk:
            lines.append(f"  Trecho do diff:\n  {hunk[:300]}")
        for reply in thread.get("replies", []):
            prefix = "    ↳" if reply.get("is_reply") else "   "
            lines.append(f"{prefix} @{reply.get('author', '?')}: {_truncate(reply.get('body', ''), 300)}")
        lines.append("")

    return "\n".join(lines)


def _format_issue_comments(comments: list[dict]) -> str:
    if not comments:
        return "Nenhum comentário."
    return "\n".join(
        f"• @{c.get('author', '?')}: {_truncate(c.get('body', ''), 400)}"
        for c in comments[:MAX_COMMENTS_IN_PROMPT]
    )


def _format_linked_issues(issues: list[dict]) -> str:
    if not issues:
        return "Nenhuma issue linkada."
    lines = []
    for iss in issues:
        labels = ", ".join(iss.get("labels", [])) or "sem labels"
        lines.append(
            f"Issue #{iss.get('number', '?')} [{labels}]: {iss.get('title', '(sem título)')}\n"
            f"  {_truncate(iss.get('body', ''), MAX_BODY_CHARS)}"
        )
    return "\n".join(lines)


def build_prompt(data: dict) -> str:
    pr      = data.get("pr", {})
    summary = data.get("files_summary", {})

    repo            = data.get("metadata", {}).get("repository", "?")
    merged_at       = pr.get("merged_at") or "não mergeado"
    labels          = ", ".join(pr.get("labels", [])) or "sem labels"
    review_decision = data.get("final_review_decision", "?")
    pr_description  = _truncate(pr.get("description"), MAX_BODY_CHARS)

    by_category = json.dumps(summary.get("by_category", {}), ensure_ascii=False)
    by_language  = json.dumps(summary.get("by_language",  {}), ensure_ascii=False)

    formal = data.get("formal_reviews", [])
    formal_text = "\n".join(
        f"• @{r.get('author','?')} [{r.get('state','?')}]: {r.get('body','') or '(sem comentário)'}"
        for r in formal
    ) or "Nenhuma revisão formal."

    return f"""Você é um especialista em revisão de código back-end (Java/Kotlin).
Analise o Pull Request abaixo e gere um resumo técnico estruturado.

══════════════════════════════════════════
INFORMAÇÕES DO PR
══════════════════════════════════════════
Repositório   : {repo}
PR            : #{pr.get('number', '?')} — {pr.get('title', '(sem título)')}
Autor         : @{pr.get('author', '?')}
Labels        : {labels}
Branch        : {pr.get('head_branch', '?')} → {pr.get('base_branch', '?')}
Estado        : {pr.get('state', '?')} | Merge: {merged_at}
Decisão final : {review_decision}

Descrição do PR:
{pr_description}

══════════════════════════════════════════
ISSUES LINKADAS (contexto do problema)
══════════════════════════════════════════
{_format_linked_issues(data.get('linked_issues', []))}

══════════════════════════════════════════
VISÃO GERAL DOS ARQUIVOS MODIFICADOS
══════════════════════════════════════════
Total de arquivos : {summary.get('total_files', 0)}
Linhas adicionadas: +{summary.get('total_additions', 0)}
Linhas removidas  : -{summary.get('total_deletions', 0)}
Por categoria     : {by_category}
Por linguagem     : {by_language}

══════════════════════════════════════════
ARQUIVOS MODIFICADOS E DIFFS
══════════════════════════════════════════
{_format_files_section(data.get('files', []))}

══════════════════════════════════════════
REVISÕES FORMAIS (Approve / Request Changes)
══════════════════════════════════════════
{formal_text}

══════════════════════════════════════════
THREADS DE REVISÃO DE CÓDIGO
══════════════════════════════════════════
{_format_review_threads(data.get('review_threads', []))}

══════════════════════════════════════════
COMENTÁRIOS GERAIS DO PR
══════════════════════════════════════════
{_format_issue_comments(data.get('issue_comments', []))}

══════════════════════════════════════════
INSTRUÇÕES DE SAÍDA
══════════════════════════════════════════
Responda SOMENTE com um objeto JSON válido, sem texto antes ou depois, sem blocos de código markdown.

O JSON deve ter exatamente estes campos:

{{
  "objetivo": "string — O que este PR busca resolver ou implementar. Seja direto e técnico.",
  "contexto": "string — Por que essa mudança foi necessária? Relacione com as issues linkadas, se houver.",
  "mudancas_principais": ["lista de strings — cada item descreve UMA mudança relevante, com nome de classe/método quando possível"],
  "modulos_afetados": ["lista de strings — pacotes, módulos ou camadas afetadas"],
  "tipo_de_mudanca": "string — uma das opções: feature | bugfix | refactor | test | config | docs | chore",
  "riscos": ["lista de strings — possíveis problemas, regressões ou áreas que precisam de atenção especial"],
  "como_testar": ["lista de strings — passos concretos para validar as mudanças"],
  "decisao_revisores": "string — resuma o que os revisores aprovaram, pediram para mudar, ou se não houve revisão",
  "nivel_de_complexidade": "string — uma das opções: baixo | médio | alto",
  "resumo_executivo": "string — 2 a 3 frases resumindo o PR para alguém sem contexto técnico"
}}
"""


#pós-processamento e validação
def validate_and_normalize(summary: dict) -> tuple[dict, list[str]]:

    warnings = []

    #campos de string obrigatórios
    string_fields = ["objetivo", "contexto", "tipo_de_mudanca",
                     "nivel_de_complexidade", "decisao_revisores", "resumo_executivo"]
    for field in string_fields:
        if not summary.get(field) or not isinstance(summary[field], str):
            warnings.append(f"Campo '{field}' ausente ou inválido.")
            summary[field] = summary.get(field, "")

    #campos de lista obrigatórios
    list_fields = ["mudancas_principais", "modulos_afetados", "riscos", "como_testar"]
    for field in list_fields:
        val = summary.get(field)
        if not val or not isinstance(val, list) or len(val) == 0:
            warnings.append(f"Campo '{field}' ausente ou vazio.")
            summary[field] = summary.get(field) or []

    #normalização de valores categóricos
    tipo = summary.get("tipo_de_mudanca", "").lower().strip()
    if tipo not in VALID_TIPOS:
        warnings.append(f"tipo_de_mudanca '{tipo}' fora do vocabulário — marcado como 'indefinido'.")
        summary["tipo_de_mudanca"] = "indefinido"

    complexidade = summary.get("nivel_de_complexidade", "").lower().strip()
    if complexidade not in VALID_COMPLEXIDADE:
        warnings.append(f"nivel_de_complexidade '{complexidade}' fora do vocabulário — marcado como 'indefinido'.")
        summary["nivel_de_complexidade"] = "indefinido"

    return summary, warnings


#cria o cliente da anthropic
class ClaudeSummarizer:
    def __init__(self, api_key: str):
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY não encontrada.")
        self.client = anthropic.Anthropic(api_key=api_key)

    def summarize(self, data: dict) -> dict:
        prompt = build_prompt(data)
        raw    = ""  

        for attempt in range(5):
            try:
                response = self.client.messages.create(
                    model=MODEL,
                    max_tokens=MAX_TOKENS,
                    temperature=0.2, #controla a aleatoriedade da resposta (0.0 determinístico 1.0 muito criativo)
                    system=(
                        "Você é um assistente especializado em engenharia de software. "
                        "Sempre responda SOMENTE com JSON válido, sem markdown, sem texto extra."
                    ),
                    messages=[{"role": "user", "content": prompt}],
                )

                raw = response.content[0].text.strip()

                #remove blocos markdown caso o modelo os inclua mesmo assim
                raw = re.sub(r"^```(?:json)?\s*", "", raw)
                raw = re.sub(r"\s*```$",           "", raw)
                raw = raw.strip()

                parsed = json.loads(raw)

                #valida e normaliza antes de retornar
                parsed, warnings = validate_and_normalize(parsed)
                if warnings:
                    for w in warnings:
                        log.warning(f"  ⚠ Validação: {w}")

                return parsed

            except json.JSONDecodeError:
                #não retorna imediatamente — tenta de novo se ainda há tentativas
                log.warning(f"  Tentativa {attempt + 1}/5: resposta não é JSON válido. Tentando novamente...")
                if attempt == 4:
                    log.error("  Esgotadas as tentativas. Salvando resposta bruta.")
                    return {"raw_response": raw, "parse_error": True}
                time.sleep(2 ** attempt)
                continue

            except anthropic.RateLimitError:
                wait = 2 ** attempt
                log.warning(f"  Rate limit. Aguardando {wait}s...")
                time.sleep(wait)

            except anthropic.APIError as e:
                wait = 2 ** attempt
                log.warning(f"  Erro de API ({e}). Retry {attempt + 1}/5 em {wait}s...")
                time.sleep(wait)

        return {"error": "Falha após 5 tentativas"}


#processa arquivo JSON
def process_file(file_path: Path, llm: ClaudeSummarizer, delay: float) -> str:

    log.info(f"Processando {file_path.name}...")

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        log.error(f"  Erro ao ler arquivo: {e}")
        return "error"

    #pula PRs que já têm resumo válido
    existing = data.get("llm_summary")
    if existing and not existing.get("error") and not existing.get("parse_error"):
        log.info("  → Já possui resumo válido. Pulando.")
        return "skipped"

    summary = llm.summarize(data)

    data["llm_summary"] = {
        **summary,
        "_generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "_model": MODEL,
    }

    try:
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except OSError as e:
        log.error(f"  Erro ao salvar arquivo: {e}")
        return "error"

    tipo     = summary.get("tipo_de_mudanca", "?")
    complexo = summary.get("nivel_de_complexidade", "?")
    objetivo = summary.get("objetivo", "")[:80]
    log.info(f"  ✓ Resumo salvo | tipo: {tipo} | complexidade: {complexo}")
    log.info(f"    Objetivo: {objetivo}...")

    time.sleep(delay)
    return "processed"


#linha de comando
def main():
    parser = argparse.ArgumentParser(description="Gera resumos de PRs com Claude (Anthropic)")
    parser.add_argument("--input",  default="prs_data",     help="Pasta com JSONs dos PRs")
    parser.add_argument("--delay",  type=float, default=1.0, help="Delay entre chamadas (segundos)")
    parser.add_argument("--limit",  type=int,   default=None, help="Limite de arquivos a processar")
    parser.add_argument("--token",  default=os.getenv("ANTHROPIC_API_KEY"), help="API key da Anthropic")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"Pasta não encontrada: {input_path}")

    files = sorted(input_path.rglob("pr_*.json"))
    if args.limit:
        files = files[:args.limit]

    log.info(f"Arquivos encontrados: {len(files)}")

    llm = ClaudeSummarizer(api_key=args.token)

    # contadores
    counts = {"processed": 0, "skipped": 0, "error": 0}

    for file in files:
        result = process_file(file, llm, args.delay)
        counts[result] += 1

    log.info(f"\n{'='*50}")
    log.info(
        f"Concluído! "
        f"Processados: {counts['processed']} | "
        f"Pulados: {counts['skipped']} | "
        f"Erros: {counts['error']}"
    )


if __name__ == "__main__":
    main()  
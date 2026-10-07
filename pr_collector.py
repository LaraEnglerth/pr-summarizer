""" coletar PRs de repos open source e salvas em arquivos JSON
    python pr_collector.py --repo owner/repo --max x
    pip install requests python-dotenv
"""

import os #leitura das variáveis ambiente (.env)
import re
import json #leitura e salvamento arquivos JSON
import time #pausa entre requisições 
import argparse #cria interface de linha de comando
import logging #exibe mensagens no terminal
from datetime import datetime #registra data/hora de coleta
from pathlib import Path #manipulação de caminhos

import requests #chamadas HTTP para API do GitHub 
from dotenv import load_dotenv 

load_dotenv() #lê o .env e carrega as variáveis no ambiente

"configuração do sistema de log, com hora, nível e mensagem"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

class GitHubClient:
    BASE_URL = "https://api.github.com" #define a URL básica da API

    def __init__(self, token: str):
        if not token:
            raise ValueError(
                "Token do GitHub não encontrado. "
                "Defina GITHUB_TOKEN no arquivo .env ou como variável de ambiente."
            )
        self.session = requests.Session() #construtor recebe o token e cria uma session
        self.session.headers.update({
            "Authorization": f"Bearer {token}", #autenticação
            "Accept": "application/vnd.github+json", #formao json
            "X-GitHub-Api-Version": "2022-11-28",
        })

    def _get(self, url: str, params: dict = None) -> dict | list: #método privado que faz get
        while True:
            response = self.session.get(url, params=params)
            if response.status_code == 403: #se forbbiden, inclui no header o timestamp para limite resetar, calcula, sleep e continue
                reset_at = int(response.headers.get("X-RateLimit-Reset", time.time() + 60))
                wait = max(reset_at - int(time.time()), 1)
                log.warning(f"Rate limit atingido. Aguardando {wait}s...")
                time.sleep(wait)
                continue
            response.raise_for_status()
            return response.json()

    def _get_paginated(self, url: str, params: dict = None, max_items: int = None) -> list: #coletar todas as páginas automaticamente
        params = params or {}
        params["per_page"] = 100 #max por requisição
        results = []
        page = 1
        while True:
            params["page"] = page
            data = self._get(url, params)
            if not data:
                break
            results.extend(data) #acumula em uma lista única
            log.info(f"  → Página {page}: {len(data)} itens (total: {len(results)})")
            if max_items and len(results) >= max_items:
                results = results[:max_items]
                break #limite
            if len(data) < 100:
                break #última página
            page += 1
        return results

    def list_pull_requests(self, owner: str, repo: str, state: str = "closed", max_prs: int = 50) -> list: #lista PRs retornando fechados e ordenando
        log.info(f"Listando PRs [{state}] de {owner}/{repo}...")
        url = f"{self.BASE_URL}/repos/{owner}/{repo}/pulls"
        return self._get_paginated(url, {"state": state, "sort": "updated", "direction": "desc"}, max_prs)
    #endpoints para dados de um PR específico
    def get_pr_files(self, owner: str, repo: str, pr_number: int) -> list:
        url = f"{self.BASE_URL}/repos/{owner}/{repo}/pulls/{pr_number}/files"
        return self._get_paginated(url)

    def get_pr_comments(self, owner: str, repo: str, pr_number: int) -> list:
        url = f"{self.BASE_URL}/repos/{owner}/{repo}/issues/{pr_number}/comments" #PRs são issues com código anexado
        return self._get_paginated(url)

    def get_pr_review_comments(self, owner: str, repo: str, pr_number: int) -> list:
        url = f"{self.BASE_URL}/repos/{owner}/{repo}/pulls/{pr_number}/comments"
        return self._get_paginated(url)

    def get_pr_reviews(self, owner: str, repo: str, pr_number: int) -> list:
        url = f"{self.BASE_URL}/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
        return self._get_paginated(url)

    def get_issue(self, owner: str, repo: str, issue_number: int) -> dict | None:
        #busca dados de uma issue linkada ao PR
        try:
            url = f"{self.BASE_URL}/repos/{owner}/{repo}/issues/{issue_number}"
            return self._get(url)
        except Exception:
            return None

#extratores e transformadores - pega o dado bruto e extrai dele 
_LINKED_ISSUE_RE = re.compile(
    r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*#(\d+)",
    re.IGNORECASE,
)
#lê o texto de descrição do PR para encontrar referências a issues
def extract_linked_issue_numbers(body: str) -> list[int]:
    if not body:
        return []
    return [int(n) for n in _LINKED_ISSUE_RE.findall(body)]


def classify_file(filename: str) -> dict:
    #retorna linguagem e categoria do arquivo com base na extensão
    ext = Path(filename).suffix.lower()
    language_map = {
        ".java": "Java", ".kt": "Kotlin", ".scala": "Scala",
        ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
        ".go": "Go", ".rb": "Ruby", ".cs": "C#", ".cpp": "C++",
        ".xml": "XML", ".yaml": "YAML", ".yml": "YAML",
        ".json": "JSON", ".properties": "Properties",
        ".sql": "SQL", ".md": "Markdown", ".html": "HTML",
        ".css": "CSS", ".sh": "Shell",
    }
    category_map = {
        ".java": "source", ".kt": "source", ".py": "source",
        ".js": "source", ".ts": "source", ".go": "source",
        ".xml": "config", ".yaml": "config", ".yml": "config",
        ".json": "config", ".properties": "config",
        ".sql": "database", ".md": "documentation",
        ".html": "frontend", ".css": "frontend",
        ".sh": "script",
    }
    parts = Path(filename).parts
    if any(p in ("test", "tests", "__tests__") for p in parts):
        category = "test"
    elif any(p in ("docs", "doc", "documentation") for p in parts):
        category = "documentation"
    else:
        category = category_map.get(ext, "other")

    return {
        "language": language_map.get(ext, "Other"),
        "category": category,
    }


def extract_file_data(file: dict) -> dict:
    patch = file.get("patch")  

    patch_stats = {"added_lines": 0, "removed_lines": 0, "context_lines": 0}
    if patch:
        for line in patch.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                patch_stats["added_lines"] += 1
            elif line.startswith("-") and not line.startswith("---"):
                patch_stats["removed_lines"] += 1
            else:
                patch_stats["context_lines"] += 1

    classification = classify_file(file.get("filename", ""))

    return {
        "filename": file.get("filename"),
        "previous_filename": file.get("previous_filename"), 
        "status": file.get("status"),           #added/modified/removed/renamed
        "language": classification["language"],
        "category": classification["category"],  #source/test/config/docs
        "additions": file.get("additions", 0),
        "deletions": file.get("deletions", 0),
        "changes": file.get("changes", 0),
        "patch": patch,
        "patch_truncated": patch is None and file.get("changes", 0) > 0, 
        "patch_stats": patch_stats,
    }


def build_review_thread_map(review_comments: list) -> dict[int, list]: #a API devolve como lista plana
    #agrupa comentários de revisão em threads, cria dicionario com chave id do raiz 
    threads: dict[int, list] = {}
    id_to_comment = {c["id"]: c for c in review_comments}

    for comment in review_comments:
        reply_to = comment.get("in_reply_to_id")
        root_id = reply_to if reply_to and reply_to in id_to_comment else comment["id"]
        threads.setdefault(root_id, []).append(comment)

    for root_id in threads:
        threads[root_id].sort(key=lambda c: c.get("created_at", ""))

    return threads


def extract_review_thread(comments: list) -> dict:
    #monta um objeto de thread de revisão a partir de uma lista de comentários
    root = comments[0]
    return {
        "path": root.get("path"),
        "line": root.get("line") or root.get("original_line"),
        "diff_hunk": root.get("diff_hunk"),#trecho do diff onde o comentário foi feito
        "replies": [
            {
                "id": c.get("id"),
                "author": c.get("user", {}).get("login"),
                "body": c.get("body"),
                "created_at": c.get("created_at"),
                "is_reply": c.get("in_reply_to_id") is not None,
            }
            for c in comments
        ],
        "total_replies": len(comments) - 1,
        "resolved": root.get("subject_type") == "line" and root.get("line") is None,
    }


def extract_comment_data(comment: dict) -> dict:
    return {
        "id": comment.get("id"),
        "author": comment.get("user", {}).get("login"),
        "body": comment.get("body"),
        "created_at": comment.get("created_at"),
        "updated_at": comment.get("updated_at"),
    }


def extract_formal_review(review: dict) -> dict:
    return {
        "id": review.get("id"),
        "author": review.get("user", {}).get("login"),
        "state": review.get("state"), #APPROVED/CHANGES_REQUESTED/COMMENTED/DISMISSED
        "body": review.get("body"),
        "submitted_at": review.get("submitted_at"),
    }


def build_files_summary(files: list[dict]) -> dict:
    #gera um resumo dos arquivos modificados, agrupado por categoria e linguagem, visão agregada
    by_category: dict[str, list] = {}
    by_language: dict[str, int] = {}
    total_additions = 0
    total_deletions = 0

    for f in files:
        cat = f["category"]
        lang = f["language"]
        by_category.setdefault(cat, []).append(f["filename"])
        by_language[lang] = by_language.get(lang, 0) + 1
        total_additions += f["additions"]
        total_deletions += f["deletions"]

    return {
        "total_files": len(files),
        "total_additions": total_additions,
        "total_deletions": total_deletions,
        "by_category": by_category,     
        "by_language": by_language,       
    }


def build_pr_document( #monta o JSON final
    owner: str,
    repo: str,
    pr: dict,
    files: list,
    comments: list,
    review_comments: list,
    reviews: list,
    linked_issues: list[dict] | None = None,
) -> dict:
    extracted_files = [extract_file_data(f) for f in files]
    files_summary = build_files_summary(extracted_files)

    #threads de revisão estruturadas
    thread_map = build_review_thread_map(review_comments)
    review_threads = [extract_review_thread(thread) for thread in thread_map.values()]
    review_threads.sort(key=lambda t: t["replies"][0]["created_at"] if t["replies"] else "")

    review_states = [r.get("state") for r in reviews if r.get("state") != "COMMENTED"]
    if "APPROVED" in review_states and "CHANGES_REQUESTED" not in review_states:
        final_review_decision = "APPROVED"
    elif "CHANGES_REQUESTED" in review_states:
        final_review_decision = "CHANGES_REQUESTED"
    elif review_states:
        final_review_decision = review_states[-1]
    else:
        final_review_decision = "NO_REVIEW"

    return {
        "metadata": {
            "collected_at": datetime.utcnow().isoformat() + "Z",
            "source": "github_api",
            "repository": f"{owner}/{repo}",
        },

        "pr": {
            "number": pr.get("number"),
            "title": pr.get("title"),
            "description": pr.get("body"),
            "state": pr.get("state"),
            "author": pr.get("user", {}).get("login"),
            "created_at": pr.get("created_at"),
            "updated_at": pr.get("updated_at"),
            "merged_at": pr.get("merged_at"),
            "closed_at": pr.get("closed_at"),
            "url": pr.get("html_url"),
            "base_branch": pr.get("base", {}).get("ref"),
            "head_branch": pr.get("head", {}).get("ref"),
            "labels": [lbl.get("name") for lbl in pr.get("labels", [])],
            "additions": pr.get("additions"),
            "deletions": pr.get("deletions"),
            "changed_files": pr.get("changed_files"),
            "linked_issue_numbers": extract_linked_issue_numbers(pr.get("body", "")),
        },

        "linked_issues": [
            {
                "number": iss.get("number"),
                "title": iss.get("title"),
                "body": iss.get("body"),
                "labels": [lbl.get("name") for lbl in iss.get("labels", [])],
            }
            for iss in (linked_issues or [])
        ],

        "files_summary": files_summary,

        "files": extracted_files,

        "issue_comments": [extract_comment_data(c) for c in comments],

        "review_threads": review_threads,

        "formal_reviews": [extract_formal_review(r) for r in reviews],

        "final_review_decision": final_review_decision,

        "llm_summary": None,
    }


class PRCollector: #cria diretório de saída
    def __init__(self, token: str, output_dir: str = "prs_data"):
        self.client = GitHubClient(token)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def collect(self, owner: str, repo: str, state: str = "closed", max_prs: int = 50):
        repo_dir = self.output_dir / f"{owner}__{repo}"
        repo_dir.mkdir(parents=True, exist_ok=True)

        prs = self.client.list_pull_requests(owner, repo, state, max_prs)
        log.info(f"Total de PRs encontrados: {len(prs)}")

        saved, skipped = 0, 0

        for pr in prs:
            number = pr["number"]
            output_file = repo_dir / f"pr_{number:05d}.json"

            if output_file.exists():
                log.info(f"PR #{number} já existe, pulando.")
                skipped += 1
                continue

            log.info(f"Coletando PR #{number}: {pr['title'][:60]}...")

            try:
                files           = self.client.get_pr_files(owner, repo, number)
                comments        = self.client.get_pr_comments(owner, repo, number)
                review_comments = self.client.get_pr_review_comments(owner, repo, number)
                reviews         = self.client.get_pr_reviews(owner, repo, number)

                linked_issue_numbers = extract_linked_issue_numbers(pr.get("body", ""))
                linked_issues = []
                for issue_number in linked_issue_numbers:
                    issue_data = self.client.get_issue(owner, repo, issue_number)
                    if issue_data:
                        linked_issues.append(issue_data)
                        log.info(f"  → Issue linkada #{issue_number} coletada")

                document = build_pr_document(
                    owner=owner,
                    repo=repo,
                    pr=pr,
                    files=files,
                    comments=comments,
                    review_comments=review_comments,
                    reviews=reviews,
                    linked_issues=linked_issues,
                )

                with open(output_file, "w", encoding="utf-8") as f:
                    json.dump(document, f, ensure_ascii=False, indent=2)

                log.info(f"Salvo: {output_file.name} "
                         f"| {document['files_summary']['total_files']} arquivos "
                         f"| +{document['files_summary']['total_additions']} "
                         f"-{document['files_summary']['total_deletions']} linhas "
                         f"| decisão: {document['final_review_decision']}")
                saved += 1
                time.sleep(0.5)

            except Exception as e:
                log.error(f"Erro ao coletar PR #{number}: {e}")

        index = {
            "repository": f"{owner}/{repo}",
            "collected_at": datetime.utcnow().isoformat() + "Z",
            "total_prs": len(prs),
            "saved": saved,
            "skipped": skipped,
            "files": sorted(str(p.name) for p in repo_dir.glob("pr_*.json")),
        }
        with open(repo_dir / "_index.json", "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)

        log.info(f"\n{'='*50}")
        log.info(f"Coleta concluída! Salvos: {saved} | Pulados: {skipped}")
        log.info(f"Diretório: {repo_dir.resolve()}")
        return repo_dir


def main():
    parser = argparse.ArgumentParser(
        description="Coleta Pull Requests do GitHub e salva em JSON."
    )
    parser.add_argument("--repo", required=True,
                        help="Repositório no formato owner/repo")
    parser.add_argument("--state", default="closed",
                        choices=["open", "closed", "all"])
    parser.add_argument("--max", type=int, default=50)
    parser.add_argument("--output", default="prs_data")
    parser.add_argument("--token", default=os.getenv("GITHUB_TOKEN"))

    args = parser.parse_args()

    try:
        owner, repo = args.repo.split("/")
    except ValueError:
        parser.error("--repo deve estar no formato owner/repo")

    collector = PRCollector(token=args.token, output_dir=args.output)
    collector.collect(owner=owner, repo=repo, state=args.state, max_prs=args.max)


if __name__ == "__main__":
    main()
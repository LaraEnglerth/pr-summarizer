# 🤖 PR Summarizer — TCC

Pipeline automatizado para coleta de Pull Requests do GitHub e geração de resumos estruturados utilizando Modelos de Linguagem de Grande Escala (LLMs).

Este projeto faz parte de um Trabalho de Conclusão de Curso (TCC) do curso de Engenharia da Computação, cujo objetivo é avaliar se resumos de PRs gerados automaticamente por IA são percebidos por desenvolvedores como claros, úteis e precisos o suficiente para apoiar o processo de revisão de código.

---

## Sobre o projeto

O desenvolvimento de software moderno depende fortemente de Pull Requests como mecanismo de revisão e integração de código. No entanto, PRs frequentemente apresentam descrições incompletas ou excessivamente técnicas, dificultando a compreensão rápida das mudanças propostas.

Este projeto propõe uma solução em três fases:

1. **Coleta** — extração automatizada de dados de PRs via API do GitHub, incluindo título, descrição, diffs de código, comentários de revisão e issues linkadas
2. **Geração** — envio dos dados ao modelo Claude (Anthropic) com um prompt estruturado, gerando um resumo com 10 campos técnicos
3. **Avaliação** — experimento com estudantes e profissionais de desenvolvimento para medir clareza, utilidade, facilidade de entendimento e precisão técnica dos resumos

### Campos do resumo gerado

| Campo | Descrição |
|---|---|
| `objetivo` | O que o PR busca resolver ou implementar |
| `contexto` | Por que a mudança foi necessária |
| `mudancas_principais` | Lista das alterações realizadas |
| `modulos_afetados` | Partes do sistema impactadas |
| `tipo_de_mudanca` | feature / bugfix / refactor / test / config / docs / chore |
| `riscos` | Possíveis problemas ou pontos de atenção |
| `como_testar` | Passos para validar as mudanças |
| `decisao_revisores` | Resultado do processo de revisão |
| `nivel_de_complexidade` | baixo / médio / alto |
| `resumo_executivo` | Síntese em linguagem acessível |

---

## Estrutura do projeto

```
pr-summarizer/
├── pr_collector.py       # Fase 1 — coleta PRs via API do GitHub
├── llm_summarizer.py     # Fase 2 — gera resumos com Claude (Anthropic)
├── requirements.txt      # Dependências Python
├── .env.example          # Modelo do arquivo de variáveis de ambiente
└── prs_data/             # Diretório de saída (gerado automaticamente)
    └── owner__repo/
        ├── _index.json
        ├── pr_00001.json
        └── pr_00002.json
```

---

## Pré-requisitos

- Python 3.10 ou superior
- Conta no [GitHub](https://github.com) com token de acesso pessoal
- Conta na [Anthropic](https://console.anthropic.com) com créditos disponíveis

---

## Como usar

### 1. Clone o repositório

```bash
git clone https://github.com/seu-usuario/pr-summarizer.git
cd pr-summarizer
```

### 2. Instale as dependências

```bash
pip install -r requirements.txt
```

### 3. Configure as variáveis de ambiente

Copie o arquivo de exemplo e preencha com suas chaves:

```bash
cp .env.example .env
```

Abra o arquivo `.env` e preencha:

```
GITHUB_TOKEN=ghp_seuTokenAqui
ANTHROPIC_API_KEY=sk-ant-seuTokenAqui
```

**Como obter o token do GitHub:**
1. Acesse github.com → Settings → Developer settings → Personal access tokens → Tokens (classic)
2. Clique em "Generate new token"
3. Selecione o escopo `repo`
4. Copie o token gerado e cole no `.env`

**Como obter a chave da Anthropic:**
1. Acesse [console.anthropic.com](https://console.anthropic.com)
2. Vá em API Keys → Create Key
3. Copie a chave e cole no `.env`

---

### 4. Colete os PRs

```bash
python pr_collector.py --repo spring-projects/spring-petclinic --max 50
```

**Parâmetros disponíveis:**

| Parâmetro | Descrição | Padrão |
|---|---|---|
| `--repo` | Repositório no formato `owner/repo` | obrigatório |
| `--max` | Número máximo de PRs a coletar | 50 |
| `--state` | Estado dos PRs: `open`, `closed` ou `all` | closed |
| `--output` | Diretório de saída | prs_data |

---

### 5. Gere os resumos

```bash
python llm_summarizer.py --input prs_data --delay 1.0
```

**Parâmetros disponíveis:**

| Parâmetro | Descrição | Padrão |
|---|---|---|
| `--input` | Pasta com os JSONs coletados | prs_data |
| `--delay` | Intervalo entre chamadas à API (segundos) | 1.0 |
| `--limit` | Limite de arquivos a processar | sem limite |

---

## Sobre o formulário de avaliação

Como parte da Fase 3 deste trabalho, foi conduzido um experimento com estudantes e estagiários de Engenharia da Computação para avaliar a qualidade dos resumos gerados.

Os participantes analisaram resumos de PRs reais do repositório [spring-projects/spring-petclinic](https://github.com/spring-projects/spring-petclinic) e responderam questões em escala Likert (1 a 5) sobre quatro critérios:

- **Clareza** — o resumo está bem escrito e organizado?
- **Utilidade** — o resumo ajuda a entender rapidamente o PR?
- **Precisão técnica** — as informações técnicas parecem corretas?
- **Facilidade de entendimento** — é possível compreender o PR sem ver o código?

Os resultados do experimento são apresentados e discutidos no TCC.

---

## Exemplo de saída

Após executar os dois scripts, cada PR terá um arquivo JSON com a seguinte estrutura:

```json
{
  "metadata": { "repository": "spring-projects/spring-petclinic", "collected_at": "..." },
  "pr": { "number": 1042, "title": "Add virtual threads support", "..." },
  "files_summary": { "total_files": 5, "by_language": { "Java": 4, "YAML": 1 } },
  "files": [ { "filename": "...", "patch": "...", "language": "Java" } ],
  "review_threads": [ { "path": "...", "replies": [ ... ] } ],
  "formal_reviews": [ { "author": "...", "state": "APPROVED" } ],
  "final_review_decision": "APPROVED",
  "llm_summary": {
    "objetivo": "Adicionar suporte a virtual threads na autoconfiguração do Spring Boot",
    "contexto": "...",
    "mudancas_principais": [ "..." ],
    "modulos_afetados": [ "autoconfigure", "actuator" ],
    "tipo_de_mudanca": "feature",
    "riscos": [ "..." ],
    "como_testar": [ "..." ],
    "decisao_revisores": "Aprovado após ajustes solicitados",
    "nivel_de_complexidade": "médio",
    "resumo_executivo": "..."
  }
}
```

---

## Dependências

```
requests
python-dotenv
anthropic
```

---

## Licença

Este projeto foi desenvolvido para fins acadêmicos como parte de um TCC.

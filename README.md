> **Acesso atualizado (v0.8.0):** o OpenStudy conta com uma tela de login e cadastro própria, sessão segura via cookie e proteção CSRF. Em uma instalação nova, cadastre o primeiro responsável (o cadastro público é fechado após a criação da conta). O WhatsApp opera em background via Docker e não depende do navegador aberto.

# OpenStudy AI 0.8.0

**Plataforma open source e local-first de aprendizagem adaptativa com IA, RAG híbrido, repetição espaçada FSRS-6, exercícios gamificados e assistente autônomo via WhatsApp.**

O OpenStudy transforma arquivos PDF, DOCX e Markdown em uma base de conhecimento ativa. A inteligência artificial gera flashcards, exercícios adaptativos, diagnósticos de defasagem, microaulas e planos de estudo personalizados; o sistema rastreia curvas de esquecimento e histórico de erros para orientar pedagogicamente o estudante.

O sistema pode ser executado em dois modos com Docker:
1. **Sem WhatsApp (Apenas Web + Modelos de IA):** Inicia apenas a aplicação web FastAPI e o banco SQLite. Leve, rápido e ideal para estudos no computador.
2. **Com WhatsApp (Web + Evolution API + Workers):** Inicia a aplicação web, Evolution API (v2.3.7), PostgreSQL (para o WhatsApp), Redis e quatro workers em background independentes. Permite estudar, resolver exercícios e enviar documentos diretamente pelo WhatsApp.

O **Ollama executa diretamente no sistema operacional do host** (fora do Docker), aproveitando diretamente a CPU/GPU da sua máquina sem duplicar pesos de modelos em volumes de containers. Alternativamente, você pode usar provedores em nuvem (Gemini, OpenAI, Claude, Groq) cadastrando suas chaves diretamente na interface.

---

## Índice Completo

1. [Visão Geral e Funcionalidades](#1-visão-geral-e-funcionalidades)
   - [1.1. Biblioteca Inteligente e Processamento Documental](#11-biblioteca-inteligente-e-processamento-documental)
   - [1.2. Motor de Aprendizagem Ativa e Adaptativa](#12-motor-de-aprendizagem-ativa-e-adaptativa)
   - [1.3. Orquestração Multiagente Especializada](#13-orquestração-multiagente-especializada)
   - [1.4. Provedores de IA e Fallback Resiliente](#14-provedores-de-ia-e-fallback-resiliente)
   - [1.5. Assistente WhatsApp e Workers em Background](#15-assistente-whatsapp-e-workers-em-background)
   - [1.6. Gamificação, Métricas e Diagnósticos](#16-gamificação-métricas-e-diagnósticos)
   - [1.7. Acessibilidade e Idiomas](#17-acessibilidade-e-idiomas)
2. [Arquitetura do Sistema](#2-arquitetura-do-sistema)
   - [2.1. Serviços e Execução](#21-serviços-e-execução)
   - [2.2. Diagrama de Comunicação e Fluxo](#22-diagrama-de-comunicação-e-fluxo)
   - [2.3. Persistência e Volumes](#23-persistência-e-volumes)
3. [Tecnologias e Conceitos Utilizados](#3-tecnologias-e-conceitos-utilizados)
   - [3.1. Tecnologias de Backend e API](#31-tecnologias-de-backend-e-api)
   - [3.2. Tecnologias de Frontend e Interface](#32-tecnologias-de-frontend-e-interface)
   - [3.3. Tecnologias de IA, LLM e Embeddings](#33-tecnologias-de-ia-llm-e-embeddings)
   - [3.4. Tecnologias de Dados e Mensageria](#34-tecnologias-de-dados-e-mensageria)
   - [3.5. Tecnologias de Infraestrutura e Redes](#35-tecnologias-de-infraestrutura-e-redes)
   - [3.6. Conceitos Pedagógicos e Ciências Cognitivas](#36-conceitos-pedagógicos-e-ciências-cognitivas)
   - [3.7. Conceitos de Engenharia de Software e Criptografia](#37-conceitos-de-engenharia-de-software-e-criptografia)
4. [Requisitos de Sistema](#4-requisitos-de-sistema)
5. [Guia Passo a Passo: Instalação com Docker](#5-guia-passo-a-passo-instalação-com-docker)
   - [5.1. Pré-requisitos de Instalação (Docker e Git)](#51-pré-requisitos-de-instalação-docker-e-git)
   - [5.2. MODO 1: Docker SEM WhatsApp (Apenas Web + Modelos de IA)](#52-modo-1-docker-sem-whatsapp-apenas-web--modelos-de-ia)
   - [5.3. MODO 2: Docker COM WhatsApp (Web + Evolution API + Workers)](#53-modo-2-docker-com-whatsapp-web--evolution-api--workers)
6. [Configurar o Ollama no Computador para o Docker](#6-configurar-o-ollama-no-computador-para-o-docker)
   - [6.1. Configuração no Windows](#61-configuração-no-windows)
   - [6.2. Configuração no macOS](#62-configuração-no-macos)
   - [6.3. Configuração no Linux (systemd)](#63-configuração-no-linux-systemd)
   - [6.4. Teste de Conexão Docker ↔ Ollama](#64-teste-de-conexão-docker--ollama)
7. [Guia de Utilização do OpenStudy](#7-guia-de-utilização-do-openstudy)
   - [7.1. Primeiro Acesso e Cadastro](#71-primeiro-acesso-e-cadastro)
   - [7.2. Cadastrar Chaves de Provedores em Nuvem (BYOK)](#72-cadastrar-chaves-de-provedores-em-nuvem-byok)
   - [7.3. Fluxo de Estudo no Desktop](#73-fluxo-de-estudo-no-desktop)
8. [Operação e Comandos do WhatsApp](#8-operação-e-comandos-do-whatsapp)
   - [8.1. Conexão do Número Oficial e Vinculação do Aluno](#81-conexão-do-número-oficial-e-vinculação-do-aluno)
   - [8.2. Tabela de Comandos do Chatbot](#82-tabela-de-comandos-do-chatbot)
   - [8.3. Configuração de Lembretes Automáticos](#83-configuração-de-lembretes-automáticos)
9. [Integração com n8n e Webhooks](#9-integração-com-n8n-e-webhooks)
   - [9.1. n8n na mesma rede Docker (Sem Cloudflare)](#91-n8n-na-mesma-rede-docker-sem-cloudflare)
   - [9.2. n8n Externo ou Nuvem via Proxy Caddy HTTPS](#92-n8n-externo-ou-nuvem-via-proxy-caddy-https)
10. [Escala com PostgreSQL (Opcional)](#10-escala-com-postgresql-opcional)
11. [Instalação Local Alternativa com uv (Sem Docker)](#11-instalação-local-alternativa-com-uv-sem-docker)
12. [Comandos Úteis do Docker](#12-comandos-úteis-do-docker)
13. [Backup, Atualização e Restauração](#13-backup-atualização-e-restauração)
14. [Testes e Validação do Sistema](#14-testes-e-validação-do-sistema)
15. [Solução de Problemas (Troubleshooting)](#15-solução-de-problemas-troubleshooting)
16. [Segurança, Privacidade e Limitações](#16-segurança-privacidade-e-limitações)
17. [Licença](#licenca)
---

## 1. Visão Geral e Funcionalidades

### 1.1. Biblioteca Inteligente e Processamento Documental

- **Upload Multiformato:** Suporte para arquivos `.pdf`, `.docx` e `.md`.
- **Extração e Normalização:** Limpeza textual, remoção de quebras artificiais e estruturação de seções.
- **Chunking Semântico com Overlap:** Segmentação de textos em blocos configuráveis (padrão de 6.000 caracteres com sobreposição de 450) para garantir a continuidade contextual nos prompts de IA.
- **RAG Híbrido (Retrieval-Augmented Generation):**
  - Busca densa (vetorial) com embeddings gerados localmente pelo `nomic-embed-text` no Ollama.
  - Fallback automático para busca lexical/BM25 caso o servidor de embeddings esteja indisponível.
- **Rastreamento de Proveniência:** Cada flashcard e exercício gerado referencia o chunk e o documento de origem.

### 1.2. Motor de Aprendizagem Ativa e Adaptativa

- **FSRS-6 (Free Spaced Repetition Scheduler v6):** Algoritmo moderno de repetição espaçada baseado em estabilidade e dificuldade de memória (superior ao SM-2 do Anki tradicional), mirando retenção de 90%.
- **Exercícios com Correção Detalhada:** Questões de múltipla escolha e respostas abertas com avaliação semântica.
- **Método Feynman:** O usuário explica um conceito com suas palavras e a IA avalia clareza, termos técnicos desnecessários, analogias e lacunas de raciocínio.
- **Microaulas com Checagem Ativa:** Explicações concisas de 3 a 5 minutos seguidas de uma pergunta diagnóstica imediata.
- **Diagnóstico de Defasagens:** Avaliação em múltiplos níveis para detectar e categorizar as maiores dificuldades do estudante por tópico.

### 1.3. Orquestração Multiagente Especializada

O OpenStudy implementa 6 agentes lógicos especializados, utilizando um despachante determinístico de custo zero (sem latência de classificação por LLM):

1. **Agente de Biblioteca (`biblioteca`):** Busca semântica, controle de embeddings e fundamentação rigorosa em fontes primárias.
2. **Agente Tutor (`tutor`):** Explicações conceituais, microaulas e tutoria socrática com dicas progressivas (sem fornecer respostas antecipadas).
3. **Agente Avaliador (`avaliador`):** Correção criteriosa de respostas, avaliação do Método Feynman e diagnóstico de erros conceituais.
4. **Agente de Exercícios (`exercicios`):** Formulação de questões inéditas baseadas estritamente nos materiais anexados, com alternativas balanceadas e justificativas.
5. **Agente de Documentos (`documentos`):** Extração de conceitos e sintetização em flashcards estruturados.
6. **Agente Planejador (`planejador`):** Criação de planos de estudo semanais adaptativos e análise de curvas de progresso.

### 1.4. Provedores de IA e Fallback Resiliente

| Provedor | Modo de Execução | Requer Chave Própria (BYOK) |
|---|---|---|
| **Ollama** | Local no host (CPU/GPU) | Não (100% gratuito e offline) |
| **Google Gemini** | Nuvem via API | Sim (Chave cadastrada no painel) |
| **OpenAI (GPT-4o/mini)** | Nuvem via API | Sim (Chave cadastrada no painel) |
| **Anthropic Claude** | Nuvem via API | Sim (Chave cadastrada no painel) |
| **Groq (Llama ultra-rápido)** | Nuvem via API | Sim (Chave cadastrada no painel) |

- **Fallback Automático para Ollama:** Se um provedor remoto falhar ou atingir timeout (configurável em `REMOTE_LLM_TIMEOUT_SECONDS=60`), a requisição migra imediatamente para o modelo local `qwen2.5:7b` no Ollama, garantindo operação contínua.
- **Criptografia BYOK:** Chaves de API cadastradas na interface são encriptadas em repouso com algoritmo Fernet (AES-256) gerado localmente.

### 1.5. Assistente WhatsApp e Workers em Background

- **Independência Completa:** Não depende do navegador estar aberto. Toda a mensageria e o agendamento rodam em containers Docker dedicados.
- **Arquitetura de Filas Especializadas:**
  - `whatsapp-fast`: Respostas instantâneas a comandos de menu e progresso.
  - `whatsapp-worker`: Sessões conversacionais com o Tutor, microaulas e exercícios com IA.
  - `whatsapp-media`: Download e validação de áudios e documentos anexados.
  - `whatsapp-documents`: Ingestão pesada, chunking e geração assíncrona de flashcards.
- **Sessões Compartilhadas:** Comece uma lista de exercícios no computador e continue respondendo pelo celular (ou vice-versa), com XP e revisões sincronizados em tempo real.
- **Lembretes Ativos:** Disparo configurável de notificações de revisão respeitando fusos horários e períodos de silêncio noturno.

### 1.6. Gamificação, Métricas e Diagnósticos

- Sistema de experiência (XP), níveis e sequências de estudos diárias (streaks).
- Missões diárias dinâmicas baseadas nas prioridades de repetição espaçada.
- Minijogo de associação de pares de conceitos para fixação rápida.
- Painel analítico de distribuição de carga cognitiva, tempo dedicado e tópicos dominados vs. tópicos críticos.

### 1.7. Acessibilidade e Idiomas

- Suporte completo a **Português (pt-BR)**, **Inglês (en-US)** e **Espanhol (es-ES)**.
- Alternância entre tema claro (light) e escuro (dark).
- Controles de acessibilidade: ajuste gradual de escala tipográfica, alto contraste e preferência de movimento reduzido (CSS `prefers-reduced-motion`).

---

## 2. Arquitetura do Sistema

### 2.1. Serviços e Execução

| Componente | Ambiente de Execução | Perfil Docker | Função Principal |
|---|---|---|---|
| **OpenStudy Web (FastAPI)** | Container Docker | `default` (sempre ativo) | Servidor HTTP, API REST, UI web e gerenciamento de arquivos |
| **Ollama** | **Host local (fora do Docker)** | N/A | Inferência de IA local (LLMs) e geração de embeddings densos |
| **Evolution API (v2.3.7)** | Container Docker | `whatsapp` | Gateway de conexão e integração com o WhatsApp via Baileys |
| **PostgreSQL 15** | Container Docker | `whatsapp` | Armazenamento de sessões, contatos e mensagens da Evolution API |
| **Redis 7** | Container Docker | `whatsapp` | Cache de alta velocidade para a Evolution API |
| **WhatsApp Workers (4x)** | Containers Docker | `whatsapp` | Processamento assíncrono de chat, comandos rápidos, mídias e indexação |
| **Caddy 2 (Opcional)** | Container Docker | `public-webhooks` | Proxy reverso com terminação TLS/HTTPS para integrações externas |

### 2.2. Diagrama de Comunicação e Fluxo

```text
       +-----------------------------------------------------------+
       |                  NAVEGADOR DO USUÁRIO                     |
       +-----------------------------------------------------------+
                                     |
                          http://127.0.0.1:8000
                                     v
       +-----------------------------------------------------------+
       |                 OpenStudy App (FastAPI)                   |
       |  - Autenticação scrypt / Sessão Segura / CSRF             |
       |  - Biblioteca, RAG Híbrido e Motor FSRS-6                 |
       |  - Orquestração Multiagente (6 agentes lógicos)           |
       +-----------------------------------------------------------+
             |                           |                     |
   (Rede openstudy-net)          (host-gateway)           (APIs HTTPS)
             |                           |                     |
             v                           v                     v
+------------------------+  +------------------------+  +-------------------+
|  Workers em Background |  |     OLLAMA NO HOST     |  | Provedores Nuvem  |
| - whatsapp-worker      |  | (host.docker.internal) |  | - Gemini / OpenAI |
| - whatsapp-fast        |  |  * qwen2.5:7b          |  | - Claude / Groq   |
| - whatsapp-media       |  |  * nomic-embed-text    |  +-------------------+
| - whatsapp-documents   |  +------------------------+
+------------------------+
             ^
             | Eventos Webhook Internos
+------------------------------------------------------------------+
|                   EVOLUTION API (v2.3.7)                         |
|  - Conexão WhatsApp via Baileys                                  |
|  - Rede interna isolada com PostgreSQL 15 + Redis 7              |
+------------------------------------------------------------------+
                                     ^
                                     |
                            APARELHO WHATSAPP
```

### 2.3. Persistência e Volumes

- **Banco de Dados da Aplicação:** Armazenado em `./data/openstudy.db` (SQLite com modo WAL habilitado, bloqueio otimista e pool de conexões controlada).
- **Diretório de Uploads:** Arquivos físicos enviados pelo usuário ficam em `./data/uploads/`.
- **Chave Criptográfica BYOK:** Salva em `./data/.llm_encryption_key` com permissão restrita.
- **Credenciais do Proprietário:** Hash seguro armazenado em `./data/.openstudy-owner.json`.
- **Volumes Docker da Evolution API:** Volumes nomeados `evolution_postgres_data`, `evolution_redis_data` e `evolution_instances` mantêm a conexão com o WhatsApp persistida mesmo ao reiniciar o Docker.

---

## 3. Tecnologias e Conceitos Utilizados

### 3.1. Tecnologias de Backend e API

- **Python 3.11 / 3.12:** Linguagem base, moderna e tipada.
- **FastAPI (>=0.115.6):** Framework web assíncrono de alta performance para a construção de APIs REST.
- **Uvicorn (>=0.34.0):** Servidor ASGI baseado em uvloop para execução do backend.
- **SQLAlchemy 2.0 (>=2.0.36):** ORM declarativo moderno para modelagem relacional e abstração de consultas.
- **Pydantic & Pydantic Settings (>=2.7.1):** Validação de esquemas de dados em tempo de execução e injeção de configurações via variáveis de ambiente.
- **HTTPX (>=0.28.1):** Cliente HTTP assíncrono robusto para chamadas de APIs externas e streaming de respostas.
- **PyPDF (>=5.1.0) & Python-Docx (>=1.1.2):** Motores especializados para extração textual e estrutural de arquivos PDF e documentos do Word.
- **Jinja2 (>=3.1.5):** Mecanismo de renderização de templates HTML com proteção contextual contra XSS.

### 3.2. Tecnologias de Frontend e Interface

- **HTML5 Semântico:** Marcação estruturada focada em acessibilidade e navegação por teclado.
- **CSS3 Moderno:** Utilização de CSS Custom Properties (variáveis nativas), Flexbox, CSS Grid, media queries responsivas e regras de acessibilidade (`prefers-reduced-motion`, `prefers-contrast`).
- **Design System com Tipografia Dedicada:** Fontes corporativas e legíveis (*Manrope* e *DM Sans*), com paleta refinada (roxo escuro, lilás, verde-lima e cinzas neutros).
- **JavaScript Vanilla Modular (ES6+):** Arquitetura frontend sem frameworks pesados, dividida em módulos desacoplados (`app.js`, `login.js`, `preferences.js`), minimizando o consumo de memória no navegador.

### 3.3. Tecnologias de IA, LLM e Embeddings

- **Ollama Engine:** Servidor de inferência local de modelos abertos executando diretamente sobre a infraestrutura de hardware do host (suporte nativo a aceleração por GPU NVIDIA CUDA, Apple Metal e AMD ROCm).
- **Qwen 2.5 (7B Instruct):** Modelo padrão de linguagem local, destacando-se por raciocínio lógico apurado, geração multilíngue e aderência a instruções de formatação JSON.
- **Nomic Embed Text:** Modelo especializado de embeddings densos de 768 dimensões com alta fidelidade semântica para indexação RAG.
- **APIs de LLM em Nuvem:** Clientes nativos com injeção segura para Google Gemini (Gemini 1.5/2.0), OpenAI (GPT-4o / GPT-4o-mini), Anthropic (Claude 3.5 Sonnet) e Groq (Llama 3.1/3.3 rodando em LPUs de ultra-baixa latência).

### 3.4. Tecnologias de Dados e Mensageria

- **SQLite 3 com WAL (Write-Ahead Logging):** Banco padrão local com suporte a escritas não bloqueantes por leitores, temporizador de concorrência (`busy_timeout=10000`) e sincronização otimizada (`synchronous=NORMAL`).
- **PostgreSQL 15 & 16:** Utilizado obrigatoriamente pela Evolution API e opcionalmente para a aplicação via `compose.postgres.yaml` com driver de alto desempenho `psycopg`.
- **Redis 7:** Armazenamento em memória para sessões, estados efêmeros e cache da Evolution API.
- **Evolution API (v2.3.7):** Plataforma open source de gateway de mensageria baseada na biblioteca Baileys, encapsulando a comunicação com a rede do WhatsApp.

### 3.5. Tecnologias de Infraestrutura e Redes

- **Docker & Docker Compose (v2.20+):** Padronização de containers, orquestração declarativa e isolamento de dependências.
- **Compose Profiles:** Segregação inteligente de serviços em perfis (`default`, `whatsapp`, `postgres`, `public-webhooks`), garantindo que apenas os containers necessários sejam alocados.
- **Docker Networks Segregadas:** Rede interna `openstudy-net` interligando os containers com isolamento da rede pública, e rede restrita `evolution_data` interna para banco de dados e Redis.
- **Caddy 2:** Servidor web reverso com gestão automática de certificados SSL/TLS (Let's Encrypt / ZeroSSL), HTTP/2 e HTTP/3 para exposição segura de webhooks externos.

### 3.6. Conceitos Pedagógicos e Ciências Cognitivas

- **FSRS (Free Spaced Repetition Scheduler):** Modelo matemático de ponta em ciência da aprendizagem que calcula intervalos de revisão individualizados baseados na probabilidade de retenção ($R$), estabilidade ($S$) e dificuldade ($D$), superando deficiências históricas de algoritmos baseados em heurísticas simples como o SM-2.
- **Efeito de Recuperação (Retrieval Practice):** Princípio de fortalecimento de vias neuronais de memória a longo prazo por meio de testes ativos (exercícios e flashcards) em oposição ao reestudo passivo.
- **Técnica Feynman:** Metodologia de consolidação do conhecimento baseada na simplificação de conceitos complexos em linguagem acessível, identificando lacunas conceituais e ambiguidades.
- **Tutoria Socrática:** Abordagem pedagógica onde a IA não entrega a resposta direta a uma dúvida de exercício, mas formula perguntas direcionadas e fornece pistas progressivas para conduzir o estudante à conclusão autônoma.
- **Microaprendizagem (Microlearning):** Fracionamento de matérias extensas em unidades autossuficientes e digeríveis de curta duração (3 a 5 minutos) com checagem de retenção imediata.
- **Gamificação Estruturada:** Aplicação de elementos lúdicos (XP, níveis de maestria, metas diárias e minijogos de associação) sustentada pela Teoria da Autodeterminação para fomento da consistência e disciplina de estudo.

### 3.7. Conceitos de Engenharia de Software e Criptografia

- **Local-First Architecture:** Filosofia onde todos os dados pertencem ao usuário e residem na sua máquina local, funcionando plenamente sem dependência de nuvem.
- **BYOK (Bring Your Own Key):** Modelo de segurança onde o usuário utiliza suas próprias credenciais de provedores externos, evitando markups de terceiros e dependência de serviços centralizados.
- **Criptografia Simétrica Fernet (AES-128 em CBC com HMAC SHA-256):** Encriptação das chaves de API em disco através de segredo gerado e armazenado exclusivamente na máquina do usuário.
- **Hashing Criptográfico scrypt:** Algoritmo resistente a ataques de força bruta com hardware especializado (GPU/ASIC) utilizado para proteção da senha administrativa.
- **Sessões Web com Cookies HttpOnly e SameSite=Lax:** Prevenção contra vazamento de tokens de sessão por ataques de XSS (Cross-Site Scripting).
- **Proteção contra CSRF (Cross-Site Request Forgery):** Tokens criptográficos anti-CSRF exigidos em todas as operações de mutação de estado.
- **Padrão Outbox / Fila Assíncrona Persistente:** Desacoplamento entre o recebimento de eventos de webhook e o processamento de IA, garantindo que interrupções de rede ou reinicializações de container não provoquem perda de mensagens.
- **Resiliência e Fallback Dinâmico:** Monitoramento de falhas de rede ou exaustão de cotas em APIs de terceiros com desvio transparente e automático para a IA local.
- **Princípio do Menor Privilégio em Containers:** Configuração `cap_drop: [ALL]` e `no-new-privileges:true` no Docker Compose para blindagem contra escalação de privilégios.

---

## 4. Requisitos de Sistema

### Requisitos Mínimos (Modo sem WhatsApp)
- **Sistema Operacional:** Windows 10/11 (com WSL 2), macOS (Apple Silicon ou Intel) ou Linux x86_64 / arm64.
- **Docker:** Docker Desktop 4.20+ ou Docker Engine + Docker Compose Plugin 2.20+.
- **Memória RAM:** Mínimo de 8 GB (16 GB recomendados caso decida rodar modelos de 7B no Ollama local).
- **Armazenamento:** 4 GB de espaço livre em disco para imagens Docker e arquivos da aplicação.

### Requisitos Adicionais (Modo com WhatsApp e Ollama)
- **Memória RAM:** 16 GB ou superior para executar simultaneamente a Evolution API, PostgreSQL, Redis, OpenStudy e o Ollama com o modelo `qwen2.5:7b`.
- **Aparelho Celular:** Um número exclusivo de WhatsApp para o bot (número oficial) e um número pessoal para o estudante interagir com a plataforma.
- **Ollama:** Instalado diretamente no sistema operacional do host (<https://ollama.com/download>).

---

## 5. Guia Passo a Passo: Instalação com Docker

Abaixo você encontra os dois modos de instalação. Ambos são independentes e foram estruturados para você **copiar e colar diretamente no terminal**.

### 5.1. Pré-requisitos de Instalação (Docker e Git)

Antes de iniciar, certifique-se de que o **Docker** e o **Git** estão instalados e ativos no seu sistema.

#### No Windows (PowerShell como Administrador):
```powershell
# 1. Garanta que o WSL 2 está habilitado
wsl --install

# 2. Baixe e instale o Docker Desktop em: https://www.docker.com/products/docker-desktop/
# Certifique-se de manter a opção "Use WSL 2 instead of Hyper-V" marcada na instalação.

# 3. Abra o Docker Desktop e confirme no terminal:
docker --version
docker compose version
```

#### No Linux (Ubuntu / Debian):
```bash
sudo apt update
sudo apt install -y git curl docker.io docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
# Saia e entre novamente no terminal para aplicar o grupo docker
```

#### No macOS:
Instale o Docker Desktop em <https://www.docker.com/products/docker-desktop/> e inicie o aplicativo.

---

### 5.2. MODO 1: Docker SEM WhatsApp (Apenas Web + Modelos de IA)

Use este modo se você deseja estudar exclusivamente pelo navegador do computador. Nenhum container de WhatsApp, PostgreSQL ou Redis será criado, tornando a inicialização ultra-rápida e leve.

#### Passo a Passo no Windows (PowerShell):

```powershell
# 1. Navegue até a pasta onde deseja salvar o projeto
cd $HOME\Documents

# 2. Clone o repositório
git clone https://github.com/jzmlucas/openstudy-ai.git
cd openstudy-ai

# 3. Crie o arquivo .env a partir do modelo
Copy-Item .env.example .env

# 4. Desative o WhatsApp no .env (garantia do modo leve)
(Get-Content .env) -replace 'WHATSAPP_ENABLED=true', 'WHATSAPP_ENABLED=false' | Set-Content .env

# 5. Inicie a aplicação no Docker
docker compose up -d --build

# 6. Verifique se o container está saudável
docker compose ps
```

#### Passo a Passo no Linux / macOS (Terminal Bash):

```bash
# 1. Navegue até a pasta de sua preferência
cd ~/Documents

# 2. Clone o repositório
git clone https://github.com/jzmlucas/openstudy-ai.git
cd openstudy-ai

# 3. Crie o arquivo .env a partir do modelo
cp .env.example .env

# 4. Desative o WhatsApp no .env
sed -i 's/WHATSAPP_ENABLED=true/WHATSAPP_ENABLED=false/g' .env

# 5. No Linux, repasse as permissões do usuário para os volumes
export APP_UID="$(id -u)"
export APP_GID="$(id -g)"

# 6. Inicie a aplicação no Docker
docker compose up -d --build

# 7. Verifique o status
docker compose ps
```

#### Acessando a Aplicação:
1. Abra o navegador em: **`http://127.0.0.1:8000`**
2. Crie sua conta de administrador (usuário e senha de no mínimo 10 caracteres).
3. **Pronto!** O sistema está no ar.
   - Para usar IA gratuitamente sem gastar créditos, instale o Ollama no seu PC e baixe os modelos ([siga a Seção 6](#6-configurar-o-ollama-no-computador-para-o-docker)).
   - Ou vá na aba **Configurar IA** e cadastre uma chave gratuita do Google Gemini, OpenAI, Claude ou Groq.

---

### 5.3. MODO 2: Docker COM WhatsApp (Web + Evolution API + Workers)

Use este modo se você deseja a experiência completa: estudar pelo computador E pelo WhatsApp com IA conversacional, envio de materiais e resolução de exercícios sincronizada.

#### Passo a Passo no Windows (PowerShell):

```powershell
# 1. Navegue até a pasta onde deseja salvar o projeto
cd $HOME\Documents

# 2. Clone o repositório
git clone https://github.com/jzmlucas/openstudy-ai.git
cd openstudy-ai

# 3. Crie o arquivo .env
Copy-Item .env.example .env

# 4. Garanta que o WhatsApp está ativado
(Get-Content .env) -replace 'WHATSAPP_ENABLED=false', 'WHATSAPP_ENABLED=true' | Set-Content .env

# 5. Gere segredos criptográficos exclusivos automaticamente para a Evolution API:
$dbPass = -join ((1..32) | ForEach-Object { '{0:x}' -f (Get-Random -Max 16) })
$globalKey = -join ((1..32) | ForEach-Object { '{0:x}' -f (Get-Random -Max 16) })
$instToken = -join ((1..32) | ForEach-Object { '{0:x}' -f (Get-Random -Max 16) })
$webhookSec = -join ((1..32) | ForEach-Object { '{0:x}' -f (Get-Random -Max 16) })

(Get-Content .env) `
  -replace 'EVOLUTION_DB_PASSWORD=.*', "EVOLUTION_DB_PASSWORD=$dbPass" `
  -replace 'EVOLUTION_GLOBAL_API_KEY=.*', "EVOLUTION_GLOBAL_API_KEY=$globalKey" `
  -replace 'EVOLUTION_INSTANCE_TOKEN=.*', "EVOLUTION_INSTANCE_TOKEN=$instToken" `
  -replace 'EVOLUTION_WEBHOOK_SECRET=.*', "EVOLUTION_WEBHOOK_SECRET=$webhookSec" | Set-Content .env

# 6. Inicie TODOS os serviços (Web + Evolution API + Postgres + Redis + 4 Workers):
docker compose --profile whatsapp up -d --build

# 7. Verifique o status de todos os containers
docker compose --profile whatsapp ps
```

#### Passo a Passo no Linux / macOS (Terminal Bash):

```bash
# 1. Navegue até sua pasta
cd ~/Documents

# 2. Clone o repositório
git clone https://github.com/jzmlucas/openstudy-ai.git
cd openstudy-ai

# 3. Crie o arquivo .env
cp .env.example .env

# 4. Garanta que o WhatsApp está ativado
sed -i 's/WHATSAPP_ENABLED=false/WHATSAPP_ENABLED=true/g' .env

# 5. Gere segredos criptográficos exclusivos automaticamente:
DB_PASS=$(openssl rand -hex 24)
GLOBAL_KEY=$(openssl rand -hex 32)
INST_TOKEN=$(openssl rand -hex 32)
WEBHOOK_SEC=$(openssl rand -hex 32)

sed -i "s/EVOLUTION_DB_PASSWORD=.*/EVOLUTION_DB_PASSWORD=$DB_PASS/g" .env
sed -i "s/EVOLUTION_GLOBAL_API_KEY=.*/EVOLUTION_GLOBAL_API_KEY=$GLOBAL_KEY/g" .env
sed -i "s/EVOLUTION_INSTANCE_TOKEN=.*/EVOLUTION_INSTANCE_TOKEN=$INST_TOKEN/g" .env
sed -i "s/EVOLUTION_WEBHOOK_SECRET=.*/EVOLUTION_WEBHOOK_SECRET=$WEBHOOK_SEC/g" .env

# 6. Permissões de usuário no Linux
export APP_UID="$(id -u)"
export APP_GID="$(id -g)"

# 7. Inicie todos os serviços:
docker compose --profile whatsapp up -d --build

# 8. Verifique o status dos serviços:
docker compose --profile whatsapp ps
```

#### Conectando o WhatsApp em 3 minutos:
1. Abra **`http://127.0.0.1:8000`** e faça login na sua conta.
2. No menu superior, clique na aba **WhatsApp**.
3. Clique no botão **Configurar instância e gerar QR**.
4. No celular dedicado ao bot, abra o WhatsApp, vá em **Aparelhos Conectados → Conectar um aparelho** e escaneie o QR Code na tela.
5. Em **Vincular seu WhatsApp pessoal**, digite seu próprio número com código do país e DDD (ex: `+55 11 99999-9999`) e clique em **Enviar código**.
6. Você receberá um código no seu WhatsApp pessoal enviado pelo bot. Responda na conversa com:
   ```text
   VINCULAR 123456
   ```
   *(substituindo 123456 pelo código que você recebeu)*
7. **Pronto!** Envie `menu` ou `ajuda` para o bot para começar a estudar pelo WhatsApp.

---

## 6. Configurar o Ollama no Computador para o Docker

> [!NOTE]
> O Ollama roda diretamente no computador hospedeiro para obter acesso direto à sua GPU/CPU. Como o container Docker roda em uma rede virtual, o Ollama precisa escutar em `0.0.0.0:11434` para receber as requisições vindas do container via `host.docker.internal`.

### 6.1. Configuração no Windows

1. Baixe e instale o Ollama em: <https://ollama.com/download>.
2. Encerre o Ollama clicando com o botão direito no ícone dele perto do relógio do Windows e selecionando **Quit Ollama**.
3. Abra o **PowerShell** e configure a variável de ambiente:
   ```powershell
   [Environment]::SetEnvironmentVariable("OLLAMA_HOST", "0.0.0.0:11434", "User")
   ```
4. Inicie o Ollama novamente pelo menu Iniciar.
5. No PowerShell, baixe os modelos necessários:
   ```powershell
   ollama pull qwen2.5:7b
   ollama pull nomic-embed-text
   ```
6. Teste se o Ollama está respondendo:
   ```powershell
   Invoke-RestMethod http://127.0.0.1:11434/api/tags
   ```

### 6.2. Configuração no macOS

1. Encerre o aplicativo Ollama.
2. No Terminal, execute:
   ```bash
   launchctl setenv OLLAMA_HOST "0.0.0.0:11434"
   ```
3. Abra o Ollama novamente e baixe os modelos:
   ```bash
   ollama pull qwen2.5:7b
   ollama pull nomic-embed-text
   ```

### 6.3. Configuração no Linux (systemd)

1. Edite as configurações do serviço Ollama:
   ```bash
   sudo systemctl edit ollama
   ```
2. Adicione as linhas:
   ```ini
   [Service]
   Environment="OLLAMA_HOST=0.0.0.0:11434"
   ```
3. Salve, feche e reinicie o serviço:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl restart ollama
   ```
4. Baixe os modelos:
   ```bash
   ollama pull qwen2.5:7b
   ollama pull nomic-embed-text
   ```

### 6.4. Teste de Conexão Docker ↔ Ollama

Para confirmar que o container do OpenStudy consegue conversar perfeitamente com o Ollama no seu PC:

```powershell
# Windows PowerShell:
docker compose exec app python -c "import urllib.request; print('Conexão Ollama Status:', urllib.request.urlopen('http://host.docker.internal:11434/api/tags', timeout=5).status)"
```

```bash
# Linux / macOS:
docker compose exec app python -c "import urllib.request; print('Conexão Ollama Status:', urllib.request.urlopen('http://host.docker.internal:11434/api/tags', timeout=5).status)"
```

O resultado esperado é **`Conexão Ollama Status: 200`**.

---

## 7. Guia de Utilização do OpenStudy

### 7.1. Primeiro Acesso e Cadastro

1. Acesse `http://127.0.0.1:8000`.
2. A tela de **Cadastro** será exibida. Crie seu usuário e defina uma senha forte (mínimo de 10 caracteres combinando letras e números).
3. Após criar a conta, o cadastro público é automaticamente bloqueado para garantir a privacidade dos seus dados.

### 7.2. Cadastrar Chaves de Provedores em Nuvem (BYOK)

Se preferir usar modelos comerciais como Gemini, ChatGPT ou Claude em vez do Ollama local:
1. No menu superior, clique em **Configurar IA**.
2. Selecione o provedor desejado:
   - **Google Gemini:** Obtenha sua chave em <https://aistudio.google.com/apikey>
   - **OpenAI:** Obtenha sua chave em <https://platform.openai.com/api-keys>
   - **Anthropic Claude:** Obtenha sua chave em <https://platform.claude.com/settings/keys>
   - **Groq:** Obtenha sua chave em <https://console.groq.com/keys>
3. Cole sua chave, selecione o modelo desejado e clique em **Testar e Salvar**.
4. Sua chave é criptografada localmente com AES-256 e nunca é transmitida a terceiros além do próprio provedor oficial.

### 7.3. Fluxo de Estudo no Desktop

1. **Biblioteca:** Envie uma apostila ou resumo em PDF, DOCX ou Markdown.
2. O sistema faz a divisão inteligente em blocos contextuais e gera os embeddings.
3. **Flashcards:** Revise os cards gerados automaticamente pelo algoritmo FSRS-6, avaliando sua retenção em *De Novo*, *Difícil*, *Bom* ou *Fácil*.
4. **Exercícios:** Gere baterias de questões de múltipla escolha ou discursivas sobre os documentos selecionados.
5. **Método Feynman:** Escolha um tópico e escreva uma explicação simples para a IA analisar suas lacunas conceituais.
6. **Diagnósticos & Métricas:** Acompanhe gráficos de evolução, áreas críticas e acúmulo de pontos de experiência (XP).

---

## 8. Operação e Comandos do WhatsApp

### 8.1. Conexão do Número Oficial e Vinculação do Aluno

O sistema utiliza o conceito de dois números:
- **Número Oficial:** Aparelho exclusivo conectado ao bot pelo QR Code.
- **Número Pessoal do Aluno:** Telefone pessoal vinculado com código de segurança. Somente números vinculados têm permissão para interagir com o assistente, impedindo uso indevido por desconhecidos.

### 8.2. Tabela de Comandos do Chatbot

Envie qualquer um dos comandos abaixo diretamente no WhatsApp do bot usando seu número pessoal cadastrado:

| Comando | Descrição da Ação |
|---|---|
| `menu` ou `ajuda` | Exibe o menu principal com opções e comandos rápidos |
| `biblioteca` | Lista todos os materiais disponíveis e seus IDs correspondentes |
| `usar [ID]` | Restringe o foco das perguntas e exercícios a um documento específico (ex: `usar 2`) |
| `todos` | Habilita a busca semântica em toda a biblioteca de documentos |
| `analisar [dúvida]` | Faz uma pergunta ao Agente Tutor fundamentada no material ativo |
| `exercicios [qtd] [tema]` | Gera uma bateria de questões para responder pelo WhatsApp (ex: `exercicios 3 biologia`) |
| `diagnostico` | Inicia um teste adaptativo de defasagem sobre o conteúdo |
| `aula [assunto]` | Solicita uma microaula concisa de 3 minutos com pergunta de checagem |
| `explique [conceito]` | Inicia uma sessão de avaliação pelo Método Feynman |
| `progresso` | Exibe resumo do nível atual, total de XP, ofensiva de dias e revisões pendentes |
| `plano [dias] [minutos]` | Monta um cronograma semanal de estudos (ex: `plano 7 40`) |
| `ia [provedor]` | Alterna o provedor de IA ativo (`ia ollama`, `ia gemini`, `ia openai`, `ia claude`, `ia groq`) |
| `modelo [nome]` | Altera o modelo de LLM ativo no momento (ex: `modelo gpt-4o-mini`) |
| `lembretes on` / `off` | Ativa ou desativa o envio automático de lembretes diários |
| `cancelar` | Cancela qualquer fluxo ou sessão ativa de perguntas |

> **Envio de Arquivos pelo WhatsApp:** Você pode anexar um arquivo PDF ou DOCX diretamente na conversa com o bot. Ele será baixado pelo worker de mídia, processado pela pipeline de ingestão e adicionado automaticamente à sua biblioteca.

### 8.3. Configuração de Lembretes Automáticos

Na aba **WhatsApp** do painel web, você pode configurar:
- Dias da semana para notificações.
- Até quatro horários por dia para lembretes de revisão.
- Definição de meta diária de estudos (em minutos).
- Fuso horário local e intervalo de silêncio para não receber notificações durante o sono.

---

## 9. Integração com n8n e Webhooks

### 9.1. n8n na mesma rede Docker (Sem Cloudflare)

Se você já utiliza o n8n em container no mesmo computador ou servidor:

1. Conecte o container do n8n à rede do OpenStudy:
   ```powershell
   docker network connect openstudy-net NOME_DO_CONTAINER_N8N
   ```
2. No n8n, aponte seus nós **HTTP Request** diretamente para o endereço interno:
   ```text
   http://app:8000/api/...
   ```
3. Para autenticar requisições automatizadas do n8n, defina uma chave secreta no `.env` do OpenStudy:
   ```dotenv
   OPENSTUDY_SERVICE_API_TOKEN=sua_chave_secreta_com_mais_de_32_caracteres_aleatorios
   ```
   E envie o cabeçalho HTTP no n8n:
   ```text
   X-OpenStudy-Api-Key: sua_chave_secreta_com_mais_de_32_caracteres_aleatorios
   ```

### 9.2. n8n Externo ou Nuvem via Proxy Caddy HTTPS

Se o seu n8n estiver na nuvem (n8n Cloud) ou em outro servidor e precisar se comunicar com o OpenStudy:

1. Tenha um domínio ou subdomínio apontado via DNS para o IP público do seu servidor (portas 80 e 443 abertas).
2. No `.env`, configure:
   ```dotenv
   WEBHOOK_DOMAIN=openstudy.seudominio.com
   N8N_BASIC_AUTH_USER=seu_usuario_n8n
   ```
3. Gere o hash de senha para o Caddy:
   ```powershell
   docker run --rm caddy:2-alpine hash-password --plaintext "SuaSenhaUltraForte123"
   ```
4. Cole o resultado em `N8N_BASIC_AUTH_HASH` (entre aspas simples):
   ```dotenv
   N8N_BASIC_AUTH_HASH='$2a$14$exemploDeHashGeradoPeloCaddy...'
   ```
5. Inicie o perfil do proxy:
   ```powershell
   docker compose --profile public-webhooks up -d
   ```
O Caddy gerará automaticamente certificados SSL/TLS válidos e liberará apenas os endpoints sob `/api/*` mediante autenticação HTTP Basic Auth, mantendo o painel web administrativo protegido na rede local.

---

## 10. Escala com PostgreSQL (Opcional)

Por padrão, o OpenStudy utiliza SQLite com modo WAL, o que atende perfeitamente ao uso individual e sessões pelo WhatsApp. Caso pretenda realizar dezenas de escritas simultâneas ou queira centralizar os dados da aplicação em um PostgreSQL dedicado:

1. Defina uma senha forte em `OPENSTUDY_DB_PASSWORD` no seu `.env`.
2. Pare os serviços que utilizam o SQLite:
   ```powershell
   docker compose --profile whatsapp stop app whatsapp-worker whatsapp-fast whatsapp-media whatsapp-documents
   ```
3. Suba o container PostgreSQL da aplicação:
   ```powershell
   docker compose -f compose.yaml -f compose.postgres.yaml --profile postgres up -d study-postgres
   ```
4. Execute o script de migração segura de dados:
   ```powershell
   docker compose -f compose.yaml -f compose.postgres.yaml --profile postgres run --rm --no-deps app python scripts/migrate_sqlite_to_postgres.py --sqlite /app/data/openstudy.db
   ```
5. Inicie a aplicação com o novo banco de dados:
   ```powershell
   docker compose -f compose.yaml -f compose.postgres.yaml --profile whatsapp --profile postgres up -d --build
   ```

---

## 11. Instalação Local Alternativa com uv (Sem Docker)

Caso prefira rodar a aplicação diretamente no sistema operacional sem usar Docker:

1. Instale o gerenciador de pacotes ultrarrápido `uv`:
   - **Windows:** `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
   - **Linux / macOS:** `curl -LsSf https://astral.sh/uv/install.sh | sh`
2. Clone o repositório e instale as dependências:
   ```bash
   git clone https://github.com/jzmlucas/openstudy-ai.git
   cd openstudy-ai
   cp .env.example .env
   uv python install 3.12
   uv sync --no-dev
   ```
3. Inicie o servidor FastAPI:
   ```bash
   uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
   ```
4. Acesse em `http://127.0.0.1:8000`.

---

## 12. Comandos Úteis do Docker

Execute estes comandos na pasta raiz do projeto onde está o `compose.yaml`:

| Ação Desejada | Comando no Terminal |
|---|---|
| Iniciar modo sem WhatsApp | `docker compose up -d` |
| Iniciar modo com WhatsApp | `docker compose --profile whatsapp up -d` |
| Ver status dos containers | `docker compose --profile whatsapp ps` |
| Ver logs da aplicação Web | `docker compose logs -f app` |
| Ver logs dos workers do WhatsApp | `docker compose logs -f whatsapp-worker whatsapp-fast` |
| Ver logs da Evolution API | `docker compose logs -f evolution-api` |
| Reiniciar apenas o app | `docker compose restart app` |
| Parar todos os serviços | `docker compose --profile whatsapp stop` |
| Desligar containers (preservando dados) | `docker compose --profile whatsapp down` |
| Reconstruir imagens após alteração | `docker compose --profile whatsapp up -d --build` |

> [!WARNING]
> **Nunca execute `docker compose down -v`**, pois o modificador `-v` apagará permanentemente os volumes de dados do PostgreSQL e as instâncias ativas do WhatsApp.

---

## 13. Backup, Atualização e Restauração

### O que precisa ser preservado:
1. Pasta `./data/openstudy.db` (Banco de dados principal).
2. Pasta `./data/uploads/` (Documentos e arquivos enviados).
3. Arquivo `./data/.llm_encryption_key` (Chave criptográfica das chaves BYOK).
4. Arquivo `./data/.openstudy-owner.json` (Credencial segura do proprietário).
5. Arquivo `.env` (Senhas e segredos de webhook).

### Como fazer backup rápido (PowerShell):
```powershell
docker compose --profile whatsapp stop app
$dataHoje = (Get-Date -Format "yyyyMMdd-HHmmss")
New-Item -ItemType Directory -Path "backup-$dataHoje"
Copy-Item data/openstudy.db "backup-$dataHoje/"
Copy-Item data/.llm_encryption_key "backup-$dataHoje/" -ErrorAction SilentlyContinue
Copy-Item data/.openstudy-owner.json "backup-$dataHoje/" -ErrorAction SilentlyContinue
Copy-Item data/uploads "backup-$dataHoje/uploads" -Recurse
Copy-Item .env "backup-$dataHoje/.env"
docker compose --profile whatsapp start app
Write-Host "Backup concluído com sucesso em backup-$dataHoje"
```

### Como atualizar o código para novas versões:
```bash
git pull
docker compose --profile whatsapp up -d --build
```

---

## 14. Testes e Validação do Sistema

Para desenvolvedores que desejam rodar as suítes de validação de código:

```bash
# Instalação das dependências de desenvolvimento com uv
uv sync --group dev

# Execução da bateria completa de testes unitários e de integração
uv run pytest -q

# Análise estática de código e tipagem com Ruff
uv run ruff check .

# Verificação de sintaxe dos scripts JavaScript
node --check app/static/js/app.js
node --check app/static/js/login.js
node --check app/static/js/preferences.js
```

---

## 15. Solução de Problemas (Troubleshooting)

### O container não consegue se comunicar com o Ollama no host
- **Causa:** O Ollama está escutando apenas em `127.0.0.1` no host, rejeitando conexões do Docker.
- **Solução:** Siga a [Seção 6](#6-configurar-o-ollama-no-computador-para-o-docker) para definir `OLLAMA_HOST=0.0.0.0:11434`, reinicie o Ollama e verifique as regras do Firewall local para a porta 11434.

### O QR Code do WhatsApp não aparece na interface
- **Causa:** A Evolution API ainda está subindo ou as chaves não coincidem.
- **Solução:** Execute `docker compose logs -f evolution-api`. Aguarde a mensagem informando que o servidor está ouvindo na porta 8080. Verifique se `EVOLUTION_GLOBAL_API_KEY` e `EVOLUTION_INSTANCE_TOKEN` estão preenchidos no `.env`.

### O código de vinculação do WhatsApp não chega no meu celular
- Verifique se a instância do WhatsApp oficial está com o status **Conectada** na tela.
- Certifique-se de que o número do seu celular foi digitado no formato internacional com o sinal de `+`, código do país e DDD (ex: `+5511999998888`).
- Verifique os logs do worker: `docker compose logs -f whatsapp-worker`.

### Erro 403 Forbidden ao realizar ações na interface Web
- Recarregue a página com `Ctrl + F5` para atualizar o token de sessão e o cabeçalho anti-CSRF. Se você reiniciou o container com outro segredo, faça logout e entre novamente.

---

## 16. Segurança, Privacidade e Limitações

- **Isolamento de Portas:** Por padrão, as portas 8000 (OpenStudy) e 8080 (Evolution) estão associadas a `127.0.0.1`. Elas não ficam expostas para a internet pública a menos que você configure explicitamente um proxy reverso.
- **Armazenamento Seguro de Chaves:** Nenhuma chave de API de IA é gravada em texto puro. O sistema gera uma chave Fernet aleatória na primeira inicialização para blindagem dos segredos.
- **Proteção dos Modelos e Documentos:** Em modo 100% Ollama, nenhum dado, pergunta ou documento sai da sua rede local. Em modo com provedores externos (Gemini/OpenAI/Claude/Groq), apenas os trechos estritamente necessários para responder à consulta são enviados via TLS aos servidores da respectiva empresa.
- **Arquivos Escaneados (OCR):** Documentos PDF compostos unicamente por imagens digitalizadas necessitam de pré-processamento via OCR externo antes do envio, pois a extração nativa lê exclusivamente camadas de texto vetorial.

---

## 17. Licença
- [`CHANGELOG.md`](CHANGELOG.md) — Registro histórico de versões e melhorias.

Distribuído sob a licença **MIT**. Consulte [`LICENSE`](LICENSE) para mais detalhes.

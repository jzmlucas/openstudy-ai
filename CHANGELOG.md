## Acesso renovado — tela de login e cadastro

- Tela de autenticação personalizada e animada, alinhada às fontes, cores e componentes visuais do OpenStudy.
- Cadastro inicial para o proprietário, protegido pela senha antiga quando ela já existe.
- Substituição do login HTTP Basic por sessão assinada, senha com hash scrypt, cookies HttpOnly e CSRF.
- Compatibilidade com login legado sem apagar dados ou reconectar o WhatsApp.
- Logout no painel e redirecionamento amigável quando a sessão expira.
- Testes de autenticação, CSRF, migração e preservação do webhook.

## Correção — fallback de IA local durante sobrecarga de provedores (2026-11-17)

- Fallback automático para Ollama em 429/500/502/503/504/529 e falhas transitórias de rede do provedor remoto.
- Checagem da presença do modelo configurado em `/api/tags` antes de iniciar geração local.
- Preservação de prompts, operação e formato JSON (sem mudar o provedor salvo no OpenStudy).
- Mensagens amigáveis em falhas de documentos no WhatsApp e no desktop.
- Timeout remoto separado (`REMOTE_LLM_TIMEOUT_SECONDS`) e novas configurações de fallback.
- Testes de regressão para fallback, ausência de modelo e erros não transitórios.

## Atualização multiagente — desempenho e especialização (compatível com 0.8.0)

- Orquestrador com 6 papéis: biblioteca, tutor, exercícios, avaliador, documentos e planejador, usando o mesmo provedor e estado de aprendizagem.
- Cliente HTTP do Ollama com conexões reutilizáveis, `keep_alive`, limite de contexto e limites de saída por operação.
- Cache temporário de embeddings para consultas repetidas e agrupamento de trechos para geração de cards, com fallback de lote vazio.
- Fila do WhatsApp com sondagem configurável (padrão 0,4 s).
- Observabilidade por agente (contadores, erros, latência média e p95); endpoint `GET /api/agents/status` e painel no desktop.
- Testes para roteamento, agrupamento, cache e métricas; documentação detalhada em `docs/multiagent-performance.md`.
- O SQLite e os volumes continuam compatíveis; não há necessidade de novas migrações ou serviços de nuvem.

# Changelog

## 0.8.0 — Sessões de exercícios compartilhadas entre desktop e WhatsApp

- Criada a fila persistente de exercícios compartilhados, acessível pelo painel desktop e pelo WhatsApp.
- Exercícios gerados no computador podem ser retomados no WhatsApp com `continuar`; o inverso também funciona.
- Respostas, tentativas, diagnósticos e XP passam pelo mesmo avaliador e banco de dados, sem duplicar tentativas em reenvios do mesmo item.
- O painel desktop restaura sessões ativas após recarregar a página e sincroniza respostas recebidas pelo WhatsApp.
- WhatsApp ganhou comandos `continuar`, `dica` e `explicar questão` para acompanhar a questão compartilhada.
- Histórico do Tutor é atualizado periodicamente no desktop enquanto a aba Tutor está aberta.
- Adicionados testes de retomada entre canais e idempotência de respostas.

## 0.8.0 — Docker-first deployment

- Adicionado Dockerfile multi-stage com dependências Python instaladas via uv.
- Adicionado compose.yaml para iniciar OpenStudy, Evolution API, PostgreSQL e Redis em um único comando.
- Webhook da Evolution configurado para comunicação direta `http://app:8000` pela rede Docker privada, sem dependência de Cloudflare ou publicação pública.
- Adicionado Caddy opcional com HTTPS automático e Basic Auth para integrações externas, como n8n fora da rede Docker.
- Criada rede Docker estável `openstudy-net` para permitir que n8n no mesmo host acesse o OpenStudy sem expor portas.
- WhatsApp e scheduler habilitados somente no modo Docker; a execução local com `uv` informa que o WhatsApp não está disponível e bloqueia suas rotas.
- Ollama permanece fora do Docker; o container OpenStudy usa `host.docker.internal:11434`, com mapeamento `host-gateway` para Linux.
- Persistência do OpenStudy compartilhada por `./data`, permitindo alternar entre Docker e execução local com uv.
- Portas do painel e da Evolution vinculadas somente ao loopback por padrão.
- README atualizado com instalação Docker-first, comunicação com Ollama local, modo uv, WhatsApp, backup e troubleshooting.
- Removidos os arquivos antigos de Compose isolado da Evolution; a infraestrutura agora é definida em `compose.yaml`.

## 0.7.0 — WhatsApp Learning Assistant

- Integração com Evolution API v2.3.7 por cliente HTTP isolado.
- Provisionamento da instância oficial do OpenStudy, configuração do webhook e fluxo de QR Code.
- Vinculação do telefone pessoal por código de uso único, expiração e limite de tentativas.
- Webhook com segredo em rota, validação de instância, deduplicação por ID da mensagem e processamento em background.
- Chatbot com RAG, histórico compartilhado com o Tutor web, biblioteca selecionável e comandos de aprendizagem.
- Geração e correção de exercícios, diagnóstico, microaulas, método Feynman e criação de plano pelo WhatsApp.
- Recebimento de documentos PDF, DOCX e Markdown com processamento pela pipeline existente.
- Configuração de provedor/modelo pelo chat somente entre credenciais já cadastradas; chaves nunca são aceitas pelo WhatsApp.
- Lembretes configuráveis por dia, horário, fuso e período silencioso.
- Outbox persistente com idempotência, tentativas e backoff para entrega de lembretes.
- Interface dedicada para QR, vínculo do telefone, rotina, teste e histórico de mensagens.
- Docker Compose opcional para Evolution API, PostgreSQL e Redis.

## 0.6.0 — Aprendizagem lúdica e adaptativa

- Missões diárias, minijogo de associação e recompensa idempotente.
- Microaulas adaptativas, verificação ativa de compreensão e método Feynman.
- Diagnóstico por assunto e relatório de prioridades.
- Coach de aprendizagem com reforço e duas verificações corretas em exercícios distintos.
- Revisão espaçada FSRS-6 e memória de aprendizagem.
- Melhorias de internacionalização e acessibilidade.


- Adicionadas missões diárias de recuperação ativa, prática e explicação, com recompensa XP idempotente.
- Adicionado minijogo de associação entre conceitos e definições com validação e pontuação no servidor.
- Adicionada verificação ativa de microaula; respostas discursivas são avaliadas e dificuldades viram registros do Coach.
- Diagnóstico agora produz relatório por assunto, identifica prioridades e usa tags dos cards como fallback quando o modelo não informa o assunto.
- Microaulas adaptam a estratégia ao domínio estimado, pré-requisitos e erros recentes; o cache considera a identidade das dificuldades, não apenas sua quantidade.
- Método Feynman agora cria registros de coaching quando encontra lacunas ou concepções equivocadas.
- Dificuldades de exercícios exigem duas verificações corretas em exercícios distintos antes de serem marcadas como superadas.
- O método Feynman não duplica eventos, progresso ou XP quando a mesma explicação é reenviada.
- Corrigida contagem de acertos quando há crédito parcial mas a avaliação semântica indica que o conceito central não foi demonstrado.
- Corrigida inicialização dos contadores do aluno na primeira tentativa.
- Corrigido parsing de `is_correct` textual para evitar que a string `false` seja interpretada como verdadeira.
- Analytics passou a expor distribuição de erros por categoria.
- Adicionados testes para missões, minijogo, relatório diagnóstico, verificação de aula e consistência de pontuação.


## 0.5.0 — Acessibilidade, internacionalização e tema escuro

- Adicionada interface em português do Brasil, espanhol e inglês.
- Idioma selecionado propagado para geração de flashcards, exercícios, tutor RAG, avaliação, insights e planos.
- Preferências de idioma e acessibilidade persistidas localmente no navegador.
- Tema claro/escuro com suporte à preferência do sistema operacional.
- Escala tipográfica ajustável em quatro níveis.
- Modo de alto contraste e redução de movimento.
- Link para pular ao conteúdo, foco visível, regiões ARIA e navegação por teclado no painel.
- Datas e números localizados conforme o idioma selecionado.
- Documentação técnica adicionada em `docs/accessibility-localization.md`.


## 0.4.0 — Integração multi-provedor BYOK

- Adicionados provedores Google Gemini, OpenAI, Anthropic Claude e Groq, mantendo Ollama.
- Configurações independentes por provedor e alternância do provedor ativo sem reiniciar.
- Chaves de API criptografadas em repouso com Fernet; API nunca devolve os segredos.
- Teste de conexão e consulta dinâmica dos modelos disponíveis na conta.
- Embeddings mantidos localmente via Ollama, com fallback lexical do RAG.
- Observabilidade passa a registrar provedor e modelo.
- Interface de configuração de IA e avisos de privacidade para provedores em nuvem.


## 0.3.0 — Redesign visual completo

- Nova identidade visual colorida e expressiva.
- Design system com tokens, superfícies, tipografia, raios e sombras.
- Botões táteis 3D com hover e estado de pressão.
- Cards, indicadores, navegação, biblioteca, exercícios, Tutor IA e Inteligência redesenhados.
- Layout mobile-first com navegação horizontal em telas menores.
- Microanimações discretas e suporte a `prefers-reduced-motion`.
- Mantidos IDs, endpoints, contratos de API e funcionalidades existentes.


## 0.2.0 — Evolução GenAI

- Adicionado pipeline RAG com chunking, embeddings locais e busca por similaridade de cosseno.
- Adicionado Tutor IA com referências aos trechos recuperados e fallback lexical.
- Adicionada memória de aprendizagem baseada em eventos e domínio por assunto.
- Adicionado scheduler adaptativo inspirado no FSRS com ratings Again, Hard, Good e Easy.
- Adicionado AI Study Agent para criação de planos e recomendações.
- Adicionada avaliação LLM-as-a-judge de flashcards.
- Adicionada observabilidade de chamadas ao Ollama.
- Atualizada interface com telas Tutor IA e Inteligência.
- Adicionada migração incremental do SQLite e limpeza de dados derivados.
- Adicionados testes para scheduler, RAG e limpeza de documentos.

## 0.1.0 — MVP inicial

- Upload de PDF, DOCX e Markdown.
- Extração de texto e geração de flashcards com Ollama.
- Geração e correção de exercícios.
- Interface web responsiva.
- Persistência local em SQLite.
## Correção de integração WhatsApp — webhook Evolution API v2.3.7
- Reutiliza instância quando a Evolution responde HTTP 403 informando que o nome já está em uso.
- Envia a configuração do webhook aninhada na propriedade `webhook` ao endpoint `/webhook/set/{instance}`.

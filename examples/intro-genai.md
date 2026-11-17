# Introdução à GenAI — material de demonstração

## O que é Inteligência Artificial Generativa?

Inteligência Artificial Generativa (GenAI) é uma categoria de sistemas de IA capaz de produzir novos conteúdos, como texto, imagens, áudio, vídeo e código, a partir de padrões aprendidos durante o treinamento.

Um modelo generativo recebe uma entrada, frequentemente chamada de prompt, e produz uma saída. A qualidade da resposta depende do modelo, dos dados de treinamento, do contexto fornecido e da forma como a tarefa é especificada.

## Modelos de linguagem (LLMs)

Large Language Models são modelos treinados para processar e gerar linguagem natural. Eles representam texto por tokens e estimam distribuições de probabilidade sobre sequências de tokens.

LLMs são úteis para sumarização, classificação, extração de informação, tradução, geração de conteúdo e interfaces conversacionais. Entretanto, podem produzir alucinações: afirmações plausíveis que não são sustentadas por evidências.

## Embeddings

Embeddings são representações vetoriais de textos, imagens ou outros dados. Em aplicações de texto, trechos semanticamente semelhantes tendem a possuir vetores próximos em um espaço multidimensional.

Uma aplicação pode comparar vetores usando similaridade de cosseno. O resultado permite recuperar trechos relacionados a uma pergunta mesmo quando as palavras exatas não coincidem.

## RAG — Retrieval-Augmented Generation

RAG combina recuperação de informação com geração por LLM. O fluxo típico é:

1. Extrair texto de fontes confiáveis.
2. Dividir o texto em chunks com tamanho adequado.
3. Gerar embeddings para os chunks.
4. Armazenar texto, vetor e metadados.
5. Converter a pergunta do usuário em embedding.
6. Recuperar os chunks mais similares.
7. Enviar pergunta e contexto ao LLM.
8. Gerar resposta com referências às fontes recuperadas.

RAG não altera os pesos do modelo. Ele fornece contexto externo no momento da inferência e pode reduzir respostas sem fundamentação quando a recuperação e as instruções são bem projetadas.

## Engenharia de prompts

Um bom prompt define papel, objetivo, contexto, restrições e formato esperado. Em sistemas de produção, saídas estruturadas em JSON facilitam validação e integração com o restante da aplicação.

Prompts não substituem validação de dados. A aplicação deve verificar tipos, campos obrigatórios, limites e consistência antes de persistir respostas do modelo.

## Avaliação de sistemas GenAI

A avaliação de uma solução GenAI deve considerar dimensões como:

- Precisão factual e fundamentação em fontes.
- Clareza e utilidade da resposta.
- Cobertura dos pontos importantes.
- Consistência do formato estruturado.
- Latência e taxa de falhas.
- Satisfação e resultado para o usuário.

LLM-as-a-judge pode acelerar avaliações, mas não é uma verdade absoluta. O ideal é combinar avaliação automatizada, testes determinísticos, conjunto de referência e revisão humana.

## Agentes de IA

Um agente de IA combina um modelo com contexto, instruções, ferramentas e um ciclo de decisão para executar uma tarefa. Um agente de estudos pode analisar desempenho, identificar lacunas, selecionar materiais, propor revisões e montar um plano.

A autonomia deve ser limitada por regras explícitas. A aplicação continua responsável por validar dados, controlar permissões e persistir ações.

## Revisão espaçada e recuperação ativa

A recuperação ativa consiste em tentar lembrar uma informação sem consultar imediatamente a resposta. A revisão espaçada distribui essas tentativas no tempo.

Modelos de agendamento adaptativo consideram dificuldade, estabilidade da memória, histórico de respostas e retenção desejada para estimar o próximo intervalo de revisão.

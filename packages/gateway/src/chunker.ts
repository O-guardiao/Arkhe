/**
 * Divisão de respostas longas em chunks para canais com limite de caracteres.
 * Respeita estrutura markdown: não corta dentro de blocos de código.
 */

export interface ChunkerOptions {
  /** Tamanho máximo de cada chunk em caracteres (default: 4096 — limite Telegram) */
  maxLength: number;
  /** Prefere quebrar em fim de parágrafo (linha em branco) */
  breakOnParagraph: boolean;
  /** Prefere quebrar em fim de linha se não encontrar parágrafo */
  breakOnNewline: boolean;
}

const DEFAULT_OPTS: ChunkerOptions = {
  maxLength: 4_096,
  breakOnParagraph: true,
  breakOnNewline: true,
};

/**
 * Divide um texto em chunks respeitando limites de carateres e estrutura markdown.
 *
 * Prioridade de quebra (do mais preferido ao menos):
 * 1. Fim de parágrafo (linha em branco) — se breakOnParagraph=true
 * 2. Fim de linha — se breakOnNewline=true
 * 3. Fim do bloco de código (```) — nunca corta dentro de code block
 * 4. Fallback: corta em maxLength
 */
export function chunkText(text: string, opts: Partial<ChunkerOptions> = {}): string[] {
  const options: ChunkerOptions = { ...DEFAULT_OPTS, ...opts };

  if (text.length <= options.maxLength) {
    return [text];
  }

  const chunks: string[] = [];
  let remaining = text;

  while (remaining.length > 0) {
    if (remaining.length <= options.maxLength) {
      chunks.push(remaining);
      break;
    }

    const window = remaining.slice(0, options.maxLength);
    let splitAt = -1;

    // Não corta dentro de bloco de código
    if (isInsideCodeBlock(window)) {
      // Avança até o fim do bloco de código
      const closeIdx = remaining.indexOf("```", options.maxLength);
      if (closeIdx !== -1) {
        const end = closeIdx + 3;
        chunks.push(remaining.slice(0, end));
        remaining = remaining.slice(end).trimStart();
        continue;
      }
      // Se não encontrar fechamento, força corte em maxLength
      splitAt = options.maxLength;
    }

    if (splitAt === -1 && options.breakOnParagraph) {
      // Procura a última linha em branco dentro da janela
      const paraIdx = window.lastIndexOf("\n\n");
      if (paraIdx > 0) splitAt = paraIdx + 2;
    }

    if (splitAt === -1 && options.breakOnNewline) {
      // Procura a última quebra de linha dentro da janela
      const nlIdx = window.lastIndexOf("\n");
      if (nlIdx > 0) splitAt = nlIdx + 1;
    }

    if (splitAt === -1) {
      // Fallback: corta em maxLength — busca o último espaço para não quebrar palavra
      const spaceIdx = window.lastIndexOf(" ");
      splitAt = spaceIdx > Math.floor(options.maxLength * 0.5) ? spaceIdx + 1 : options.maxLength;
    }

    chunks.push(remaining.slice(0, splitAt).trimEnd());
    remaining = remaining.slice(splitAt).trimStart();
  }

  return chunks.filter((c) => c.length > 0);
}

/**
 * Verifica se a string termina dentro de um bloco de código (número ímpar de ```)
 */
function isInsideCodeBlock(text: string): boolean {
  let count = 0;
  let pos = 0;
  while ((pos = text.indexOf("```", pos)) !== -1) {
    count++;
    pos += 3;
  }
  return count % 2 === 1;
}

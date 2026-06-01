interface OllamaEmbedResponse {
  embeddings: number[][];
}

export interface EmbedConfig {
  ollamaUrl: string;
  model: string;
  timeoutMs?: number;
}

export async function embedQuery(
  text: string,
  config: EmbedConfig,
): Promise<Float32Array> {
  const url = `${config.ollamaUrl.replace(/\/$/, '')}/api/embed`;
  const controller = new AbortController();
  const timeoutMs = config.timeoutMs ?? 5000;
  const timeout = timeoutMs > 0 ? setTimeout(() => controller.abort(), timeoutMs) : null;

  let res: Response;
  try {
    res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ model: config.model, input: [text] }),
      signal: controller.signal,
    });
  } catch (err) {
    if (timeout) clearTimeout(timeout);
    throw new Error(
      `embedQuery: failed to connect to Ollama at ${url}: ${(err as Error).message}`,
    );
  }

  if (timeout) clearTimeout(timeout);

  if (!res.ok) {
    const body = await res.text().catch(() => '');
    throw new Error(
      `embedQuery: Ollama returned HTTP ${res.status} from ${url}: ${body}`,
    );
  }

  let data: OllamaEmbedResponse;
  try {
    data = (await res.json()) as OllamaEmbedResponse;
  } catch (error) {
    throw new Error('embedQuery: invalid JSON in Ollama response');
  }

  if (
    !data.embeddings ||
    !Array.isArray(data.embeddings) ||
    data.embeddings.length === 0
  ) {
    throw new Error('embedQuery: Ollama response missing embeddings array');
  }

  const embedding = data.embeddings[0];
  if (!Array.isArray(embedding) || embedding.length === 0) {
    throw new Error('embedQuery: embedding[0] is empty or not an array');
  }

  if (!embedding.every((v) => typeof v === 'number' && isFinite(v))) {
    throw new Error('embedQuery: embedding contains non-numeric values');
  }

  return new Float32Array(embedding);
}

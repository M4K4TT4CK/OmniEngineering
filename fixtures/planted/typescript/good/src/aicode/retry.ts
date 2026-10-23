// Job retries with exponential backoff and jitter (ARB-083).
export class RetryQueue {
  private readonly maxRetries = 3;
  private readonly retryDelayMs = 1000;
  private readonly pending: string[] = [];

  failed(id: string, attempts: number): void {
    if (attempts > this.maxRetries) return;
    const wait = Math.min(30_000, this.retryDelayMs * 2 ** attempts) * (0.5 + Math.random() / 2);
    setTimeout(() => this.pending.push(id), wait);
  }
}

// Job retries as model-written queues schedule them (ARB-083).
export class RetryQueue {
  private readonly maxRetries = 3;
  private readonly retryDelayMs = 1000;
  private readonly pending: string[] = [];

  failed(id: string, attempts: number): void {
    if (attempts > this.maxRetries) return;
    setTimeout(() => this.pending.push(id), this.retryDelayMs); // expect: arbiter/typescript.retry-constant-delay
  }
}

export class ConfigurationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConfigurationError";
  }
}

export class AuthorizationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AuthorizationError";
  }
}

export class FnoHttpError extends Error {
  readonly status: number;
  readonly requestId?: string;
  readonly body?: unknown;

  constructor(options: {
    message: string;
    status: number;
    requestId?: string;
    body?: unknown;
  }) {
    super(options.message);
    this.name = "FnoHttpError";
    this.status = options.status;
    this.requestId = options.requestId;
    this.body = options.body;
  }
}

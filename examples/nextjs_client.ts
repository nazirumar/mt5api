/**
 * Browser-safe TypeScript client for the Nazbeen MT5 Market Data API.
 *
 * Environment:
 *   NEXT_PUBLIC_MT5_API_BASE  Default: http://mt5.nazbeen.test
 *   NEXT_PUBLIC_API_KEY   Required for authenticated calls and WebSocket
 *                         (NEXT_PUBLIC_MT5_API_KEY accepted as a compatibility fallback)
 *
 * Security note: NEXT_PUBLIC_* values are embedded in browser JavaScript.
 * Use these helpers only for local development, or proxy through a server-side
 * Next.js route that keeps the real API key private.
 *
 * This client is read-only. It never sends trading commands.
 */

export type Timeframe =
  | "M1" | "M2" | "M3" | "M4" | "M5" | "M6"
  | "M10" | "M12" | "M15" | "M20" | "M30"
  | "H1" | "H2" | "H3" | "H4" | "H6" | "H8" | "H12"
  | "D1" | "W1" | "MN1";

export interface Meta {
  [key: string]: unknown;
}

export interface ApiSuccess<T> {
  success: true;
  data: T;
  meta?: Meta;
}

export interface ApiFailure {
  success: false;
  error: {
    code: string;
    message: string;
    details?: unknown;
  };
  meta?: Meta;
}

export type ApiEnvelope<T> = ApiSuccess<T> | ApiFailure;

export interface Quote {
  symbol: string;
  bid?: number;
  ask?: number;
  last?: number;
  time?: string | number;
  timestamp?: string | number;
  stale?: boolean;
  [key: string]: unknown;
}

export interface Candle {
  time: string | number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
  tick_volume?: number;
  real_volume?: number;
  spread?: number;
  completed?: boolean;
  [key: string]: unknown;
}

export interface Health {
  status?: string;
  ok?: boolean;
  mt5_connected?: boolean;
  version?: string;
  time?: string | number;
  [key: string]: unknown;
}

export interface WelcomeMessage {
  type: "welcome";
  data?: unknown;
  [key: string]: unknown;
}

export interface SubscribedMessage {
  type: "subscribed" | "unsubscribed";
  data?: unknown;
  [key: string]: unknown;
}

export interface QuoteMessage {
  type: "quote";
  data: Quote;
  [key: string]: unknown;
}

export interface ConnectionMessage {
  type: "connection";
  data?: { status?: string; [key: string]: unknown };
  [key: string]: unknown;
}

export interface HeartbeatMessage {
  type: "heartbeat" | "pong";
  data?: unknown;
  [key: string]: unknown;
}

export interface ErrorMessage {
  type: "error";
  error?: { code?: string; message?: string; details?: unknown };
  message?: string;
  data?: unknown;
  [key: string]: unknown;
}

export type MarketSocketMessage =
  | WelcomeMessage
  | SubscribedMessage
  | QuoteMessage
  | ConnectionMessage
  | HeartbeatMessage
  | ErrorMessage
  | { type: string; data?: unknown; [key: string]: unknown };

export class MT5ApiError extends Error {
  readonly code: string;
  readonly details?: unknown;
  readonly status?: number;

  constructor(
    message: string,
    code = "UNKNOWN_ERROR",
    details?: unknown,
    status?: number,
  ) {
    super(message);
    this.name = "MT5ApiError";
    this.code = code;
    this.details = details;
    this.status = status;
  }
}

function getBaseUrl(): string {
  return (process.env.NEXT_PUBLIC_MT5_API_BASE || "http://mt5.nazbeen.test").replace(
    /\/+$/,
    "",
  );
}

function getApiKey(): string | undefined {
  return (
    process.env.NEXT_PUBLIC_API_KEY ||
    process.env.API_KEY ||
    process.env.NEXT_PUBLIC_MT5_API_KEY ||
    process.env.MT5_API_KEY ||
    undefined
  );
}

interface RequestOptions {
  params?: Record<string, string | number | boolean | undefined>;
  auth?: boolean;
}

async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { params, auth = true } = options;
  const url = new URL(`${getBaseUrl()}${path}`);

  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined) url.searchParams.set(key, String(value));
    }
  }

  const headers = new Headers({ Accept: "application/json" });

  if (auth) {
    const apiKey = getApiKey();
    if (!apiKey) {
      throw new MT5ApiError(
        "NEXT_PUBLIC_API_KEY is not set.",
        "MISSING_API_KEY",
      );
    }
    headers.set("X-API-Key", apiKey);
  }

  let response: Response;
  try {
    response = await fetch(url.toString(), {
      headers,
      cache: "no-store",
    });
  } catch (cause) {
    throw new MT5ApiError(
      `Cannot reach ${getBaseUrl()}. Check that the API and proxy are running.`,
      "CONNECTION_ERROR",
      cause,
    );
  }

  let payload: ApiEnvelope<T>;
  try {
    payload = (await response.json()) as ApiEnvelope<T>;
  } catch (cause) {
    throw new MT5ApiError(
      "API returned invalid JSON.",
      "INVALID_RESPONSE",
      cause,
      response.status,
    );
  }

  if (!payload || typeof payload !== "object") {
    throw new MT5ApiError(
      "API returned an unexpected response shape.",
      "INVALID_RESPONSE",
      payload,
      response.status,
    );
  }

  if (payload.success === false) {
    throw new MT5ApiError(
      payload.error?.message || "API request failed.",
      payload.error?.code || "UNKNOWN_ERROR",
      payload.error?.details,
      response.status,
    );
  }

  if (!response.ok) {
    throw new MT5ApiError(
      `HTTP ${response.status} ${response.statusText}`,
      "HTTP_ERROR",
      undefined,
      response.status,
    );
  }

  return payload.data;
}

/** Public health endpoint; no API key required. */
export async function fetchHealth(): Promise<Health> {
  return apiFetch<Health>("/api/v1/health", { auth: false });
}

/** Batch quotes. Example: ["XAUUSD", "EURUSD"]. */
export async function fetchQuotes(symbols: string[]): Promise<Quote[]> {
  if (!symbols.length) throw new MT5ApiError("At least one symbol is required.", "INVALID_INPUT");
  return apiFetch<Quote[]>("/api/v1/quotes", {
    params: { symbols: symbols.join(",") },
  });
}

/** OHLC candles. Timeframes use MT5 tokens such as M1, M5, H1, MN1. */
export async function fetchCandles(
  symbol: string,
  timeframe: Timeframe = "M1",
  limit = 500,
  start?: string,
  end?: string,
): Promise<Candle[]> {
  return apiFetch<Candle[]>(`/api/v1/candles/${encodeURIComponent(symbol)}`, {
    params: { timeframe, limit, start, end },
  });
}

function toWebSocketBase(baseUrl: string): string {
  const url = new URL(baseUrl);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = "";
  url.search = "";
  url.hash = "";
  return url.toString().replace(/\/$/, "");
}

function buildWebSocketUrl(): string {
  // First-message auth keeps the key out of the URL and proxy logs.
  return `${toWebSocketBase(getBaseUrl())}/ws/market`;
}

export interface MarketSocketOptions {
  symbols: string[];
  /** Defaults to NEXT_PUBLIC_API_KEY if omitted. */
  apiKey?: string;
  /** Optional completed-candle subscriptions. */
  candles?: Array<{ symbol: string; timeframe: string }>;
  onQuote: (quote: Quote, message: QuoteMessage) => void;
  onStatus: (status: string, message?: MarketSocketMessage) => void;
  onError: (error: Error, message?: MarketSocketMessage) => void;
  pingIntervalMs?: number;
  initialBackoffMs?: number;
  maxBackoffMs?: number;
}

export interface MarketSocketHandle {
  close: () => void;
}

/**
 * Create an auto-reconnecting market WebSocket client.
 *
 * Server message types: auth_required, authenticated, welcome, subscribed,
 * subscribed_candles, unsubscribed, quote, candle, connection, heartbeat,
 * pong, error. Payloads arrive inside `data`.
 */
export function createMarketSocket(options: MarketSocketOptions): MarketSocketHandle {
  const apiKey = options.apiKey || getApiKey();
  if (!apiKey) {
    options.onError(new MT5ApiError("API key is required for the WebSocket.", "MISSING_API_KEY"));
    return { close: () => undefined };
  }
  if (!options.symbols.length) {
    options.onError(new MT5ApiError("At least one symbol is required.", "INVALID_INPUT"));
    return { close: () => undefined };
  }

  const url = buildWebSocketUrl();
  const pingIntervalMs = options.pingIntervalMs ?? 30_000;
  const initialBackoffMs = options.initialBackoffMs ?? 1_000;
  const maxBackoffMs = options.maxBackoffMs ?? 60_000;

  let ws: WebSocket | null = null;
  let closedByClient = false;
  let authenticated = false;
  let subscribed = false;
  let candleSubscribed = false;
  let reconnectTimer: number | undefined;
  let pingTimer: number | undefined;
  let backoffMs = initialBackoffMs;

  const clearTimers = (): void => {
    if (reconnectTimer !== undefined) {
      window.clearTimeout(reconnectTimer);
      reconnectTimer = undefined;
    }
    if (pingTimer !== undefined) {
      window.clearInterval(pingTimer);
      pingTimer = undefined;
    }
  };

  const send = (payload: Record<string, unknown>): boolean => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return false;
    ws.send(JSON.stringify(payload));
    return true;
  };

  const subscribe = (): void => {
    if (send({ action: "subscribe", symbols: options.symbols })) {
      subscribed = true;
    }
  };

  const subscribeCandles = (): void => {
    if (options.candles?.length && send({ action: "subscribe_candles", candles: options.candles })) {
      candleSubscribed = true;
    }
  };

  const unsubscribeCandles = (): void => {
    if (candleSubscribed && options.candles?.length) {
      send({ action: "unsubscribe_candles", candles: options.candles });
      candleSubscribed = false;
    }
  };

  const unsubscribe = (): void => {
    if (subscribed && send({ action: "unsubscribe", symbols: options.symbols })) {
      subscribed = false;
    }
  };

  const handleMessage = (message: MarketSocketMessage): void => {
    switch (message.type) {
      case "auth_required":
        if (!authenticated) {
          authenticated = send({ action: "auth", api_key: apiKey });
          options.onStatus("authenticating", message);
        }
        break;
      case "authenticated":
        authenticated = true;
        options.onStatus("authenticated", message);
        break;
      case "welcome":
        options.onStatus("welcome", message);
        if (!subscribed) subscribe();
        if (!candleSubscribed) subscribeCandles();
        break;
      case "subscribed":
        subscribed = true;
        options.onStatus("subscribed", message);
        break;
      case "subscribed_candles":
        candleSubscribed = true;
        options.onStatus("subscribed_candles", message);
        break;
      case "unsubscribed":
        subscribed = false;
        options.onStatus("unsubscribed", message);
        break;
      case "quote":
        options.onQuote(message.data, message);
        break;
      case "candle":
        options.onStatus("candle", message);
        break;
      case "connection":
        options.onStatus(message.data?.status || "connection", message);
        break;
      case "heartbeat":
      case "pong":
        options.onStatus(message.type, message);
        break;
      case "error": {
        const detail = message.error || { message: message.message };
        options.onError(
          new MT5ApiError(
            String(detail?.message || "WebSocket error"),
            String(detail?.code || "WS_ERROR"),
            detail,
          ),
          message,
        );
        break;
      }
      default:
        options.onStatus(message.type, message);
    }
  };

  const scheduleReconnect = (): void => {
    if (closedByClient || reconnectTimer !== undefined) return;
    options.onStatus("reconnecting");
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = undefined;
      connect();
    }, backoffMs);
    backoffMs = Math.min(backoffMs * 2, maxBackoffMs);
  };

  const connect = (): void => {
    if (closedByClient) return;
    options.onStatus("connecting");

    try {
      ws = new WebSocket(url);
    } catch (cause) {
      options.onError(cause instanceof Error ? cause : new Error(String(cause)));
      scheduleReconnect();
      return;
    }

    ws.onopen = () => {
      backoffMs = initialBackoffMs;
      authenticated = false;
      subscribed = false;
      candleSubscribed = false;
      options.onStatus("connected");

      if (pingTimer !== undefined) window.clearInterval(pingTimer);
      pingTimer = window.setInterval(() => {
        send({ action: "ping" });
      }, pingIntervalMs);
    };

    ws.onmessage = (event: MessageEvent<string>) => {
      try {
        const message = JSON.parse(event.data) as MarketSocketMessage;
        handleMessage(message);
      } catch (cause) {
        options.onError(
          new MT5ApiError("Invalid WebSocket JSON.", "INVALID_RESPONSE", cause),
        );
      }
    };

    ws.onerror = (event: Event) => {
      options.onError(new MT5ApiError("WebSocket error event.", "WS_ERROR", event));
    };

    ws.onclose = () => {
      if (pingTimer !== undefined) {
        window.clearInterval(pingTimer);
        pingTimer = undefined;
      }
      const wasSubscribed = subscribed;
      subscribed = false;
      ws = null;
      if (wasSubscribed) options.onStatus("unsubscribed");
      options.onStatus("disconnected");
      if (!closedByClient) scheduleReconnect();
    };
  };

  connect();

  return {
    close: () => {
      closedByClient = true;
      clearTimers();
      unsubscribe();
      unsubscribeCandles();
      if (ws) {
        ws.close(1000, "client shutdown");
        ws = null;
      }
      options.onStatus("closed");
    },
  };
}

/**
 * React hook usage example (in a client component):
 *
 * import { useEffect, useState } from "react";
 * import { createMarketSocket, type Quote } from "./nextjs_client";
 *
 * export function useQuotes() {
 *   const [quotes, setQuotes] = useState<Quote[]>([]);
 *
 *   useEffect(() => {
 *     const socket = createMarketSocket({
 *       symbols: ["XAUUSD", "EURUSD"],
 *       onQuote: (quote) =>
 *         setQuotes((current) => [quote, ...current].slice(0, 50)),
 *       onStatus: (status) => console.log("market socket:", status),
 *       onError: (error) => console.error("market socket:", error),
 *     });
 *     return () => socket.close();
 *   }, []);
 *
 *   return quotes;
 * }
 */

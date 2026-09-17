import type { Response } from 'express';

export const RUNNER_EVENT_PREFIX = 'AITEST_EVENT ';

export interface LiveEvent {
  sequence: number;
  campaignId: string;
  timestamp: string;
  type: string;
  message: string;
  runId?: string;
  taskId?: string;
  frameworkId?: string;
  [key: string]: unknown;
}

export interface PublishEvent {
  type: string;
  message: string;
  timestamp?: string;
  [key: string]: unknown;
}

interface LiveChannel {
  sequence: number;
  events: LiveEvent[];
  subscribers: Set<Response>;
  cleanupTimer?: NodeJS.Timeout;
}

const channels = new Map<string, LiveChannel>();

export function validCampaignId(value: unknown): value is string {
  return typeof value === 'string' && /^[A-Za-z0-9_-]{8,80}$/.test(value);
}

function channelFor(campaignId: string): LiveChannel {
  let channel = channels.get(campaignId);
  if (!channel) {
    channel = { sequence: 0, events: [], subscribers: new Set() };
    channels.set(campaignId, channel);
  }
  if (channel.cleanupTimer) {
    clearTimeout(channel.cleanupTimer);
    channel.cleanupTimer = undefined;
  }
  return channel;
}

function writeEvent(response: Response, event: LiveEvent): void {
  response.write(`id: ${event.sequence}\ndata: ${JSON.stringify(event)}\n\n`);
}

export function publishLive(
  campaignId: string,
  event: PublishEvent,
): LiveEvent {
  const channel = channelFor(campaignId);
  const emitted: LiveEvent = {
    ...event,
    sequence: ++channel.sequence,
    campaignId,
    timestamp: event.timestamp || new Date().toISOString(),
  };
  channel.events.push(emitted);
  if (channel.events.length > 2_000) channel.events.splice(0, channel.events.length - 2_000);
  for (const response of channel.subscribers) writeEvent(response, emitted);
  return emitted;
}

export function subscribeLive(campaignId: string, response: Response, afterSequence = 0): () => void {
  const channel = channelFor(campaignId);
  channel.subscribers.add(response);
  for (const event of channel.events) {
    if (event.sequence > afterSequence) writeEvent(response, event);
  }
  return () => {
    channel.subscribers.delete(response);
  };
}

export function scheduleLiveCleanup(campaignId: string, delayMs = 5 * 60_000): void {
  const channel = channels.get(campaignId);
  if (!channel) return;
  if (channel.cleanupTimer) clearTimeout(channel.cleanupTimer);
  channel.cleanupTimer = setTimeout(() => channels.delete(campaignId), delayMs);
}

export function parseRunnerEvent(line: string): Record<string, unknown> | null {
  if (!line.startsWith(RUNNER_EVENT_PREFIX)) return null;
  try {
    const parsed = JSON.parse(line.slice(RUNNER_EVENT_PREFIX.length));
    return parsed && typeof parsed === 'object' ? parsed : null;
  } catch {
    return null;
  }
}

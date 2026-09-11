// SPDX-License-Identifier: MIT OR Apache-2.0
// STOCK MCP CLIENT — TypeScript SDK 1.27.1, unmodified. Two modes.
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { z } from 'zod';

const mode = process.argv[2]; // 'fallback' | 'specific'
const seen: string[] = [];
const P = (tag: string, n: any) => {
  const line = `${tag} method=${n.method} params=${JSON.stringify(n.params)}`;
  seen.push(line);
  console.log(line);
};

const client = new Client({ name: 'stock-ts-client', version: '0.0.1' }, { capabilities: {} });

if (mode === 'fallback') {
  client.fallbackNotificationHandler = async (n) => P('[FALLBACK]', n);
} else {
  const ChannelNotif = z.object({
    method: z.literal('notifications/claude/channel'),
    params: z.object({ content: z.string(), meta: z.any() }).loose(),
  });
  client.setNotificationHandler(ChannelNotif as any, async (n: any) => P('[SPECIFIC]', n));
  // no fallback: the control MUST be invisible here, which is the discriminator
}

const transport = new StdioClientTransport({
  command: 'bun',
  args: ['run', 'C:/Projects/OverMind/probe/fam-push-server.ts'],
  stderr: 'pipe',
});
await client.connect(transport);
console.log(`[client] connected, mode=${mode}`);
await new Promise((r) => setTimeout(r, 3000));
console.log(`[client] RESULT mode=${mode} notifications_surfaced=${seen.length}`);
for (const s of seen) console.log(`[client]   -> ${s}`);
process.exit(0);

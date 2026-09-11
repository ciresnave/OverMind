// SPDX-License-Identifier: MIT OR Apache-2.0
// PROBE SERVER — drives FAM's REAL ChannelPushHandler over a real MCP stdio server.
// No reimplementation of the push: ChannelPushHandler is imported from FAM's tree,
// so the notification is emitted by FAM's own code path (channel-push.ts:134).
import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { ChannelPushHandler } from 'C:/Projects/fam/src/adapters/mcp/channel-push.ts';

const log = (m: string) => console.error(`[probe-server] ${m}`);

// Stub FamClient: registers the handlers exactly as the real client would.
let undeliveredCb: ((msgs: any[]) => Promise<void>) | null = null;
const stubClient: any = {
  onMessage: () => {},
  offMessage: () => {},
  onInvitation: () => {},
  offInvitation: () => {},
  onUndeliveredMessages: (cb: any) => { undeliveredCb = cb; },
  offUndeliveredMessages: () => {},
  markDelivered: async () => {},
  listVoucherRecords: async () => [],
  getEntityId: () => 'probe@overmind',
};

const mcp = new Server(
  { name: 'fam-probe', version: '0.0.1' },
  { capabilities: { tools: {}, logging: {} } }
);

mcp.oninitialized = async () => {
  log('client initialized; firing notifications in 300ms');
  setTimeout(async () => {
    // ---- POSITIVE CONTROL: a method the MCP spec/SDK DOES know ----
    try {
      await mcp.notification({
        method: 'notifications/message',
        params: { level: 'info', logger: 'probe', data: 'CONTROL-NOTIFICATION-OK' },
      });
      log('SENT control notifications/message');
    } catch (e) { log(`CONTROL SEND FAILED: ${e}`); }

    // ---- SUBJECT: FAM's real push path ----
    try {
      await undeliveredCb!([
        { id: 4242, from_entity: 'pm@portfolio', text: 'SUBJECT-PAYLOAD-OK', channel_id: 'c-probe', sent_at: '2026-09-09T22:00:00Z' },
      ]);
      log('SENT subject notifications/claude/channel (via FAM ChannelPushHandler)');
    } catch (e) { log(`SUBJECT SEND FAILED: ${e}`); }

    setTimeout(() => { log('done'); process.exit(0); }, 1500);
  }, 300);
};

await mcp.connect(new StdioServerTransport());
const push = new ChannelPushHandler(mcp as any, stubClient, 'Probe', null, 'probe@overmind', { seenPath: 'C:/Users/cires/AppData/Local/Temp/probe-seen.json', anchorsPath: 'C:/Users/cires/AppData/Local/Temp/probe-anchors.json' });
push.start();
log('connected, push handler started');

import {WorkerEntrypoint} from 'cloudflare:workers';
import {notify} from './notifications.mjs';
export {default} from './worker.mjs';

// Available only to callers bound to this named entrypoint, not the public URL.
export class NotificationSender extends WorkerEntrypoint {
  async notify(event) {
    await notify(this.env, event);
  }
}

import {WorkerEntrypoint} from 'cloudflare:workers';
import {notify} from './notifications.mjs';
import {sendLogin} from './login.mjs';
export {default} from './worker.mjs';

// Available only to callers bound to this named entrypoint, not the public URL.
export class NotificationSender extends WorkerEntrypoint {
  async sendLogin(event) {
    await sendLogin(this.env, event);
  }
  async notify(event) {
    await notify(this.env, event);
  }
}

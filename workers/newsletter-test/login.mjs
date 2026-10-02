export async function sendLogin(env, event) {
  const url = new URL(event.url);
  const allowed = new URL(env.SITE_ORIGIN || 'https://hackeratlas.com').origin;
  if (url.origin !== allowed || url.pathname !== '/for-you/' || !/^#token=[a-f0-9]{64}$/.test(url.hash)
    || typeof event.email !== 'string' || event.email.length > 254 || /[\r\n]/.test(event.email)) {
    throw new Error('Invalid sign-in message');
  }
  if (env.DELIVERY_DISABLED === 'true') throw new Error('Email delivery disabled');
  await env.EMAIL.send({
    from: {email: env.FROM_EMAIL, name: 'Hacker Atlas'}, to: event.email,
    subject: 'Your Hacker Atlas sign-in link',
    text: `Your daily reading starts here.\n\nSign in to Hacker Atlas:\n${url.href}\n\nThis link expires in 15 minutes and can be used once.\nIf you didn't request it, you can ignore this email.`,
  });
}

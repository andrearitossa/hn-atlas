export async function notify(env, event) {
  let subject, text;
  if (event.type === 'feedback') {
    subject = 'New Hacker Atlas feedback';
    text = `From: ${event.email || 'Anonymous'}\nPage: ${event.page}\n\n${event.message}`;
  } else if (event.type === 'signup') {
    subject = `hey! new user for topics "${event.topic.replace(/[\r\n]/g, ' ')}" :>`;
    text = `${subject}\n\nEmail: ${event.email}`;
  } else {
    throw new Error('Unknown notification type');
  }
  await env.EMAIL.send({
    from: {email: env.FROM_EMAIL, name: 'Hacker Atlas'},
    to: 'andre.ritossa@gmail.com',
    subject,
    text,
  });
}

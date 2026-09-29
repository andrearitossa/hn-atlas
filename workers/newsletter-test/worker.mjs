const reply = (body, status=200) => Response.json(body, {status, headers:{'Cache-Control':'no-store'}});

const escapeHTML = value => value.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#x27;'}[c]));
const articleURL = post => {
  const url=(post.url||'').trim();
  try { if (['http:','https:'].includes(new URL(url).protocol)) return url; } catch {}
  return `https://news.ycombinator.com/item?id=${post.id}`;
};

function trackedHTML(issue, edition, origin) {
  const links=new Map(JSON.parse(issue.posts).map(post =>
    [escapeHTML(articleURL(post)), `${origin}/click/${issue.topic}/${edition}/${post.id}`]));
  return issue.html.replace(/href="([^"]*)"/g, (attribute,url) =>
    links.has(url) ? `href="${links.get(url)}"` : attribute);
}

async function click(request, env, match) {
  if (!['GET','HEAD'].includes(request.method)) return new Response('Method not allowed',{status:405});
  const [topic,edition,story]=match.slice(1).map(Number);
  if (![topic,edition,story].every(Number.isSafeInteger)) return reply({error:'Not found'},404);
  const issue=await env.DB.prepare('SELECT posts FROM newsletter_issues WHERE topic=? AND edition=?')
    .bind(topic,edition).first();
  const post=issue && JSON.parse(issue.posts).find(post => post.id===story);
  if (!post) return reply({error:'Not found'},404);
  if (request.method==='GET') {
    try {
      await env.DB.prepare(`INSERT INTO newsletter_clicks(topic,edition,story,clicks,last_clicked_at)
        VALUES(?,?,?,1,?) ON CONFLICT(topic,edition,story)
        DO UPDATE SET clicks=clicks+1,last_clicked_at=excluded.last_clicked_at`)
        .bind(topic,edition,story,Math.floor(Date.now()/1000)).run();
    } catch (error) {
      console.error('Newsletter click recording failed',error); // Reading must still work.
    }
  }
  return new Response(null,{status:302,headers:{Location:articleURL(post),
    'Cache-Control':'no-store','Referrer-Policy':'no-referrer'}});
}

export async function deliver(env, edition, now=Math.floor(Date.now()/1000)) {
  if (env.DELIVERY_DISABLED === 'true') return {disabled:true,sent:0,failures:0,pending:0};
  const origin=env.PUBLIC_URL?.replace(/\/$/,'');
  if (!origin?.startsWith('https://')) throw new Error('PUBLIC_URL must be HTTPS');
  const {results:issues}=await env.DB.prepare('SELECT topic,subject,html,posts FROM newsletter_issues WHERE edition=?').bind(edition).all();
  let sent=0, failures=0;
  for (const issue of issues) {
    const html=trackedHTML(issue,edition,origin);
    const countSql='UPDATE newsletter_issues SET sent_count=(SELECT count(*) FROM newsletter_deliveries WHERE topic=? AND edition=? AND state=\'sent\') WHERE topic=? AND edition=?';
    const count=()=>env.DB.prepare(countSql).bind(issue.topic,edition,issue.topic,edition);
    await count().run(); // Repair a count left behind by an interrupted earlier run.
    const {results:subscribers}=await env.DB.prepare(
      'SELECT id,email FROM newsletter_subscriptions WHERE topic=? AND unsubscribed_at IS NULL'
    ).bind(issue.topic).all();
    for (const sub of subscribers) {
      const claim=await env.DB.prepare(
        "INSERT OR IGNORE INTO newsletter_deliveries(topic,edition,subscription,state) VALUES(?,?,?,'sending')"
      ).bind(issue.topic,edition,sub.id).run();
      if (claim.meta.changes!==1) continue;
      const active=await env.DB.prepare(
        'SELECT id FROM newsletter_subscriptions WHERE id=? AND unsubscribed_at IS NULL'
      ).bind(sub.id).first();
      if (!active) {
        await env.DB.prepare("DELETE FROM newsletter_deliveries WHERE topic=? AND edition=? AND subscription=? AND state='sending'")
          .bind(issue.topic,edition,sub.id).run();
        continue;
      }
      const unsubscribe=`${origin}/unsubscribe/${sub.id}`;
      try {
        const result=await env.EMAIL.send({
          from:{email:env.FROM_EMAIL,name:'Hacker Atlas'},to:sub.email,
          subject:issue.subject,html:html.replaceAll('{{unsubscribe_url}}',unsubscribe),
          headers:{'List-Unsubscribe':`<${unsubscribe}>`,'List-Unsubscribe-Post':'List-Unsubscribe=One-Click',
            'X-Campaign-ID':`hackeratlas-weekly-${issue.topic}-${edition}`},
        });
        await env.DB.batch([
          env.DB.prepare("UPDATE newsletter_deliveries SET state='sent',message_id=?,sent_at=? WHERE topic=? AND edition=? AND subscription=? AND state='sending'")
            .bind(result.messageId,now,issue.topic,edition,sub.id),
          count(),
        ]);
        sent++;
      } catch (error) {
        // A provider error can follow acceptance. Hold the claim for inspection.
        await env.DB.prepare("UPDATE newsletter_deliveries SET state='failed',error=? WHERE topic=? AND edition=? AND subscription=? AND state='sending'")
          .bind(String(error.code||error.message).slice(0,300),issue.topic,edition,sub.id).run();
        failures++;
      }
    }
  }
  const pending=(await env.DB.prepare(`SELECT count(*) AS n FROM newsletter_issues i
    JOIN newsletter_subscriptions s ON s.topic=i.topic AND s.unsubscribed_at IS NULL
    LEFT JOIN newsletter_deliveries d ON d.topic=i.topic AND d.edition=i.edition AND d.subscription=s.id
    WHERE i.edition=? AND (d.state IS NULL OR d.state!='sent')`).bind(edition).first()).n;
  console.log(JSON.stringify({edition,sent,failures,pending}));
  return {sent,failures,pending};
}

export default {
  async fetch(request,env) {
    const url=new URL(request.url);
    const tracked=/^\/click\/(\d+)\/(\d+)\/(\d+)$/.exec(url.pathname);
    if (tracked) return click(request,env,tracked);
    const token=/^\/unsubscribe\/([A-Za-z0-9_-]{20,80})$/.exec(url.pathname)?.[1];
    if (token) {
      const sub=await env.DB.prepare(`SELECT id FROM newsletter_subscriptions WHERE id=? OR EXISTS
        (SELECT 1 FROM json_each(legacy_tokens) WHERE value=?)`).bind(token,token).first();
      if (!sub) return new Response('Subscription not found',{status:404});
      if (request.method==='GET') return new Response('<!doctype html><title>Unsubscribe</title><form method="post"><button>Unsubscribe from this topic</button></form>',{headers:{'Content-Type':'text/html; charset=utf-8'}});
      if (request.method==='POST') {
        await env.DB.prepare('UPDATE newsletter_subscriptions SET unsubscribed_at=coalesce(unsubscribed_at,?) WHERE id=?')
          .bind(Math.floor(Date.now()/1000),sub.id).run();
        return new Response('Unsubscribed from this topic.');
      }
      return new Response('Method not allowed',{status:405});
    }
    if (url.pathname!=='/run' || request.method!=='POST') return reply({error:'Not found'},404);
    if (!env.ADMIN_TOKEN || request.headers.get('Authorization')!==`Bearer ${env.ADMIN_TOKEN}`) return reply({error:'Unauthorized'},401);
    let edition;
    try { edition=(await request.json()).edition; } catch { return reply({error:'Invalid request'},400); }
    if (!Number.isSafeInteger(edition) || edition<0) return reply({error:'Invalid edition'},400);
    return reply(await deliver(env,edition));
  }
};

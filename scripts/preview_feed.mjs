// Local-only preview: real Workers and persistent D1, seeded login, no email delivery.
import {Miniflare, convertV4MiniflareOptions} from 'miniflare';
import {readFile} from 'node:fs/promises';
import {resolve, extname, relative} from 'node:path';
import {randomUUID} from 'node:crypto';
import {execFileSync} from 'node:child_process';
import {parseArgs} from 'node:util';
import {hash, token, sessionCookie} from '../lib/reader.js';
const {values:args}=parseArgs({options:{
  source:{type:'string',default:'.wrangler/feed-preview/dist'},
  site:{type:'string',default:'.wrangler/feed-preview/pages'},
  email:{type:'string',default:'andre.ritossa@gmail.com'},
  port:{type:'string',default:'8792'},
  state:{type:'string',default:'.wrangler/feed-local-db'},
  'skip-build':{type:'boolean',default:false}
}});
const root=resolve(args.site), email=args.email.trim().toLowerCase(), secret=token();
if (!args['skip-build']) {
  const env={...process.env,WRANGLER_SEND_METRICS:'false',WRANGLER_LOG_PATH:'/tmp/atlas-preview-build.log'};
  execFileSync('./node_modules/.bin/wrangler',['pages','functions','build','--outdir','.wrangler/feed-worker-build'],{env,stdio:'inherit'});
  execFileSync('./node_modules/.bin/wrangler',['deploy','--dry-run','--config','workers/feed/wrangler.jsonc','--outdir',resolve('.wrangler/feed-service-build')],{env,stdio:'inherit'});
}
const input=JSON.parse(execFileSync('.venv/bin/python',['-c',
  "import json,feed_sync,sys; print(json.dumps({'payload':feed_sync.payload(sys.argv[1]),'backfill':feed_sync.BACKFILL}))",args.source],{maxBuffer:8*1024*1024}).toString());
execFileSync('.venv/bin/python',['-c',`
import re,shutil,sys
from pathlib import Path
import static_pages
root=Path(sys.argv[1]); version=re.search(r'/releases/([a-f0-9]+)/',(root/'for-you/index.html').read_text())[1]
release=root/'releases'/version
(release/'public').mkdir(exist_ok=True)
static_pages.write_feed(release,Path('index.html').read_text(),version)
shutil.copyfile(release/'public/for-you/index.html',root/'for-you/index.html')
for name in ('feed.json','feed-ranking.js'):(release/name).unlink(missing_ok=True)
`,root]);
const types={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.json':'application/json','.svg':'image/svg+xml','.png':'image/png','.ico':'image/x-icon','.woff2':'font/woff2'};
async function assets(request) {
  const url=new URL(request.url);
  if(url.pathname==='/local-login')return new Response(null,{status:302,headers:{Location:'/for-you/','Set-Cookie':sessionCookie(secret),'Cache-Control':'no-store'}});
  let path=resolve(root,'.'+decodeURIComponent(url.pathname));
  if(!path.startsWith(root+'/')&&path!==root)return new Response('Not found',{status:404});
  if(url.pathname.endsWith('/'))path+='/index.html';
  try{return new Response(await readFile(path),{headers:{'Content-Type':types[extname(path)]||'application/octet-stream','Cache-Control':'no-cache'}});}catch{return new Response('Not found',{status:404});}
}
const d1Databases={NEWSLETTER_DB:'atlas-feed-local'};
let embeddingKey=process.env.OPENAI_API_KEY;
if (!embeddingKey) {
  try { embeddingKey=(await readFile('.env','utf8')).match(/^OPENAI_API_KEY=(.*)$/m)?.[1].trim().replace(/^['"]|['"]$/g,''); } catch {}
}
const bindings=embeddingKey?{OPENAI_API_KEY:embeddingKey}:{};
const mf=new Miniflare(convertV4MiniflareOptions({host:'127.0.0.1',port:Number(args.port),resourcePersistencePath:resolve(args.state),workers:[
  {name:'atlas',modules:true,scriptPath:'.wrangler/feed-worker-build/index.js',compatibilityDate:'2026-09-01',d1Databases,bindings,
    serviceBindings:{ASSETS:assets,FEED:'hackeratlas-feed'}},
  {name:'hackeratlas-feed',modules:true,scriptPath:relative(process.cwd(),resolve('.wrangler/feed-service-build/worker.js')),compatibilityDate:'2026-09-01',d1Databases}
]}));
try {
  const db=await mf.getD1Database('NEWSLETTER_DB','atlas');
  const schema=(await readFile('migrations/0006_feed_profiles.sql','utf8')).split('CREATE TRIGGER');
  for(const sql of schema[0].split(';').filter(s=>s.trim()))await db.prepare(sql).run();
  await db.prepare('CREATE TRIGGER'+schema[1]).run();
  for(const sql of (await readFile('migrations/0007_feed_state.sql','utf8')).split(';').filter(s=>s.trim()))await db.prepare(sql).run();
  await db.prepare('INSERT INTO feed_catalog(id,payload) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload').bind(input.payload).run();
  await db.prepare(input.backfill).run();
  const now=Math.floor(Date.now()/1000);
  await db.prepare('INSERT INTO feed_users(id,email,created_at,verified_at) VALUES(?,?,?,?) ON CONFLICT(email) DO NOTHING').bind(randomUUID(),email,now,now).run();
  const user=await db.prepare('SELECT id FROM feed_users WHERE email=?').bind(email).first();
  await db.prepare('INSERT INTO feed_sessions(token_hash,user_id,expires_at) VALUES(?,?,?)').bind(await hash(secret),user.id,now+30*86400).run();
  await mf.ready;
  console.log(`Local feed: http://localhost:${args.port}/local-login\nProfile: ${email}\nPersistent D1: ${args.state}\nNo email sent.`);
  for(const signal of ['SIGINT','SIGTERM'])process.on(signal,async()=>{await mf.dispose();process.exit(0);});
} catch(error) {await mf.dispose();throw error;}

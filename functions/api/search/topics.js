import {database, reader, rate, reply, jsonBody, topicCatalog, HTTPError, errorReply} from '../../../lib/reader.js';
import {embedding, topicEmbeddings} from '../../../lib/search-embedding.js';
import {rankTopics} from '../../../lib/topic-search.js';
export async function onRequest({request,env}) {
 if(request.method!=='POST')return reply(405,{detail:'Method not allowed.'},{Allow:'POST'});
 try {
  const db=database(env), user=await reader(db,request), body=await jsonBody(request,2048);
  const q=typeof body.q==='string'?body.q.trim():'';
  if(q.length<2||q.length>120)throw new HTTPError(400,'Enter between 2 and 120 characters.');
  await rate(db,'topic-search:'+user.id,40,60);
  const topics=await topicCatalog(env,request);
  if(!topics.length)return reply(200,{topics:[],notice:''});
  let vector,vectors,notice='';
  try {
   if(topics.length>2048)throw Error('Topic catalog too large');
   [vector,vectors]=await Promise.all([
    embedding(q,env),topicEmbeddings(topics.map(t=>`${t.name}\n${t.description||''}`),env)
   ]);
  } catch { notice='Related topics are temporarily unavailable. Showing name matches.'; }
  return reply(200,{topics:rankTopics(topics,q,vector,vectors).map(({id,name,slug,description})=>({id,name,slug,description})),notice});
 } catch(error){return errorReply(error);}
}

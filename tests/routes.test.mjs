import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {test} from 'node:test';

const html = readFileSync(new URL('../index.html', import.meta.url), 'utf8');
const routeCode = html.slice(html.indexOf('function route() {'), html.indexOf('// Keep ordinary navigation'));
const navigationCode = html.slice(html.indexOf('// Keep ordinary navigation'), html.indexOf('addEventListener("hashchange", route);'));
function browser(path) {
  let url = new URL(path, 'https://hackeratlas.com');
  const calls = [], listeners = {}, scrolled = [];
  const element = {classList:{toggle(){}, remove(){}}, remove(){}, append(){}, scrollIntoView(){}, setAttribute(){}, removeAttribute(){}};
  const context = vm.createContext({
    URL, URLSearchParams,
    location: url,
    history: {
      replaceState(_, __, path) { context.location = url = new URL(path, url); },
      pushState(_, __, path) { context.location = url = new URL(path, url); },
    },
    routeVersion:0, tip:{}, siteFooter:element,
    ABOUT_CONTENT:html.match(/const ABOUT_CONTENT = `([\s\S]*?)`;/)[1],
    activityResizeObserver:null, mapResizeObserver:null,
    $: selector => selector === '#map-section' ? null :
      selector === '#trends-section' || selector === '#browse-section' ?
        {...element,scrollIntoView(){scrolled.push(selector);}} : element,
    document:{body:element, addEventListener:(event, fn) => { listeners[event] = fn; }},
    addEventListener:(event, fn) => { listeners[event] = fn; },
    reduceMotion:()=>true, updateMetadata(){}, esc:String,
    topic:async id => calls.push(['topic', id]),
    connections:async () => calls.push(['home']),
  });
  vm.runInContext(routeCode + navigationCode, context);
  return {context, calls, listeners, element, scrolled};
}
test('direct and legacy topic URLs select the same topic', () => {
  for (const path of ['/topic/1124/', '/#/topic/1124', '/#/timeline?topic=1124']) {
    const {context, calls} = browser(path);
    context.route();
    assert.deepEqual(calls, [['topic', '1124']]);
    assert.equal(context.location.pathname, '/topic/1124/');
    assert.equal(context.location.hash, '');
  }
});
test('slug routes pass the name to the topic resolver', () => {
  const {context, calls} = browser('/topic/llm-advances/');
  context.route();
  assert.deepEqual(calls, [['topic', 'llm-advances']]);
});
test('ordinary links navigate in the client, modified clicks stay native, and popstate routes', () => {
  const {context, calls, listeners} = browser('/');
  const link = {href:'https://hackeratlas.com/topic/llm-advances/', hasAttribute:()=>false};
  let prevented = 0;
  const event = {target:{closest:()=>link}, button:0, preventDefault(){ prevented++; }};
  listeners.click({...event, ctrlKey:true});
  assert.equal(prevented, 0);
  listeners.click(event);
  assert.equal(prevented, 1);
  assert.equal(context.location.pathname, '/topic/llm-advances/');
  context.location = new URL('https://hackeratlas.com/');
  listeners.popstate();
  assert.deepEqual(calls, [['topic', 'llm-advances'], ['home']]);
});
test('Trends navigates to the embedded section', async () => {
  const {context, calls, listeners, scrolled} = browser('/');
  const link = {href:'https://hackeratlas.com/#/trends', hasAttribute:()=>false};
  let prevented = 0;
  listeners.click({target:{closest:()=>link}, button:0, preventDefault(){ prevented++; }});
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(prevented, 1);
  assert.equal(context.location.hash, '#/trends');
  assert.deepEqual(calls, [['home']]);
  assert.deepEqual(scrolled, ['#trends-section']);
});

test('About opens from the footer and keeps Feedback available', () => {
  const {context, calls, listeners, element} = browser('/');
  const link = {href:'https://hackeratlas.com/about/', hasAttribute:()=>false};
  let prevented = 0;
  listeners.click({target:{closest:()=>link}, button:0, preventDefault(){ prevented++; }});
  assert.equal(prevented, 1);
  assert.equal(context.location.pathname, '/about/');
  assert.match(element.innerHTML, /Andrea Ritossa/);
  assert.match(element.innerHTML, /https:\/\/github.com\/andrearitossa\/hn-atlas/);
  assert.equal(context.siteFooter.hidden, false);
  assert.deepEqual(calls, []);
  const footer = html.match(/<footer class="site-footer" hidden>[\s\S]*?<\/footer>/)[0];
  assert.match(footer, />About<\/a>/);
  assert.match(footer, />Feedback<\/button>/);
  assert.doesNotMatch(footer, /Trends/);
  context.location = new URL('https://hackeratlas.com/');
  context.route();
  assert.deepEqual(calls, [['home']]);
});

test('topic loading resolves slugs to numeric data IDs and canonicalizes old links', async () => {
  const start = html.indexOf('async function topic(id, version) {');
  const end = html.indexOf('  const related =', start);
  const loadingCode = html.slice(start, end) + '\n}';
  for (const key of ['llm-advances', '1124', '12']) {
    const requested = [], metadata = [];
    const topic = {id:1124, slug:'llm-advances', name:'LLM Advances', description:'Research'};
    const context = vm.createContext({
      T:{topics:[topic]}, routeVersion:1,
      location:{pathname:`/topic/${key}/`},
      document:{querySelector:()=>null},
      topicDetails:async id => { requested.push(id); return topic; },
      history:{replaceState:(_, __, path) => { context.location.pathname = path; }},
      updateMetadata:(...args)=>metadata.push(args),
    });
    vm.runInContext(loadingCode, context);
    await context.topic(key, 1);
    assert.deepEqual(requested, [key === '12' ? 12 : 1124]);
    assert.equal(context.location.pathname, '/topic/llm-advances/');
    assert.equal(metadata[0][2], '/topic/llm-advances/');
    await assert.rejects(context.topic('missing-topic', 1), /Topic not found/);
  }
});

test('navigation preserves visible content while the next topic is pending', () => {
  const {context, element} = browser('/topic/llm-advances/');
  element.innerHTML = '<article>Current page</article>';
  context.topic = () => new Promise(() => {});
  context.route();
  assert.equal(element.innerHTML, '<article>Current page</article>');
});

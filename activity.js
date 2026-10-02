(() => {
const host=document.querySelector('#global-activity');if(!host)return;
const $=selector=>host.querySelector(selector);
const t=JSON.parse(document.querySelector('#analytics-data').textContent);
function selectBar(event) {
  const bar = event.target.closest('[data-month]');
  if (bar) $('#chart-readout').textContent = bar.getAttribute('aria-label');
}
$('#activity-chart').onpointerover = selectBar;
$('#activity-chart').onfocusin = selectBar;
$('#activity-chart').onclick = selectBar;
host.querySelectorAll('[data-range]').forEach(button => button.onclick = () => {
  const count = Number(button.dataset.range), rows = count ? t.monthly.slice(-count) : t.monthly;
  const max = Math.max(1,...rows.map(r=>r.posts)), width = 900/Math.max(1,rows.length);
  $('#activity-chart').innerHTML = rows.map((r,i) => {
    const height=145*r.posts/max, label=`${r.month}: ${r.posts.toLocaleString()} posts`;
    return `<rect x="${50+i*width}" y="${170-height}" width="${Math.max(.5,width*.8)}" height="${height}" rx="2" tabindex="0" role="button" data-month="${r.month}" aria-label="${label}"><title>${label}</title></rect>`;
  }).join('');
  $('#chart-axis').innerHTML = rows.length ? `<span>${rows[0].month}</span><span>${rows.at(-1).month}</span>` : '';
  host.querySelectorAll('[data-range]').forEach(b=>{b.classList.toggle('on',b===button);b.setAttribute('aria-pressed',b===button);});
});


})();

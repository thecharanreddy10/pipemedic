// Run in a signed-in Azure DevOps tab (javascript console or the Browser pane) after storing the spec:
//   localStorage.setItem('pmspec', JSON.stringify(<contents of data/spec.json>))
// Pushes one branch per defect off the clean baseline commit, then queues the matching pipeline on each branch.
// No credentials are handled: the page's own session cookie authorises the REST calls.
const ORG = 'benchmark-org', PROJECT = 'azure-test-project';
const base = `https://dev.azure.com/${ORG}/${PROJECT}/_apis`;
const repoId = '<repository id>';           // GET {base}/git/repositories
const baseline = '<clean baseline commit>';  // the commit every defect branch starts from
const PIDS = { 'click-tests': 1, 'click-lint': 2, 'click-typecheck': 3, 'click-package': 4 };

const spec = JSON.parse(localStorage.getItem('pmspec'));
const cache = {};
async function content(path) {
  if (cache[path] !== undefined) return cache[path];
  const r = await fetch(`${base}/git/repositories/${repoId}/items?path=${encodeURIComponent(path)}` +
    `&versionDescriptor.versionType=commit&versionDescriptor.version=${baseline}&includeContent=true&api-version=7.1&$format=text`);
  cache[path] = r.status === 200 ? await r.text() : null;
  return cache[path];
}

const pushed = JSON.parse(localStorage.getItem('pmpushed') || '{}');
for (const d of spec) {
  if (pushed[d.id]) continue;
  const files = {}, created = new Set();
  for (const e of d.edits) {
    const p = e.path.startsWith('/') ? e.path : '/' + e.path;
    if (e.create !== undefined) { files[p] = e.create; created.add(p); continue; }
    let text = files[p] !== undefined ? files[p] : await content(p);
    if (e.append !== undefined) text += e.append;
    else {
      if (text.split(e.old).length - 1 !== 1) throw new Error(`anchor must match exactly once in ${p}`);
      text = text.replace(e.old, () => e.new);
    }
    files[p] = text;
  }
  const changes = Object.entries(files).map(([p, c]) => ({
    changeType: created.has(p) ? 'add' : 'edit', item: { path: p }, newContent: { content: c, contentType: 'rawtext' } }));
  const res = await fetch(`${base}/git/repositories/${repoId}/pushes?api-version=7.1`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refUpdates: [{ name: 'refs/heads/' + d.branch, oldObjectId: '0'.repeat(40) }],
                           commits: [{ comment: d.message, parents: [baseline], changes }] }) });
  pushed[d.id] = (await res.json()).commits[0].commitId;
}
localStorage.setItem('pmpushed', JSON.stringify(pushed));

const runs = JSON.parse(localStorage.getItem('pmruns') || '{}');
for (const d of spec) {
  if (runs[d.id]) continue;
  const r = await fetch(`${base}/pipelines/${PIDS[d.pipeline]}/runs?api-version=7.1`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ resources: { repositories: { self: { refName: 'refs/heads/' + d.branch } } } }) });
  runs[d.id] = { runId: (await r.json()).id, pipeline: d.pipeline };
}
localStorage.setItem('pmruns', JSON.stringify(runs));

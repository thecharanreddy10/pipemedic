// Collects each finished run the way the PipeMedic watcher does: the first failed task (else failed job), its error issues
// and its log text. Then hands the batch to the local bridge (server.py) by navigating to it with the data in the URL
// fragment; Azure DevOps pages block direct requests to localhost, but top-level navigation is allowed.
// Afterwards navigate back to dev.azure.com and run:  mark collected (see README).
const ORG = 'benchmark-org', PROJECT = 'azure-test-project';
const base = `https://dev.azure.com/${ORG}/${PROJECT}/_apis`;

async function collect(defectId, run) {
  const bid = run.runId;
  const b = await fetch(`${base}/build/builds/${bid}?api-version=7.1`).then(r => r.json());
  const tl = await fetch(`${base}/build/builds/${bid}/timeline?api-version=7.1`).then(r => r.json());
  const recs = tl.records || [];
  const bad = recs.filter(r => ['failed', 'canceled'].includes(r.result) && ['Task', 'Job'].includes(r.type));
  const tasks = bad.filter(r => r.type === 'Task'), jobs = bad.filter(r => r.type === 'Job');
  const step = tasks[0] || jobs[0] || null;
  const issues = [];
  for (const r of bad) {
    issues.push(...(r.issues || []).filter(i => i.type === 'error').map(i => i.message));
    issues.push(...(r.issues || []).filter(i => i.type === 'warning' && /maximum time/.test(i.message)).map(i => i.message));
  }
  let log = '';
  if (step && step.log) {
    log = await fetch(`${base}/build/builds/${bid}/logs/${step.log.id}?api-version=7.1`, { headers: { Accept: 'text/plain' } }).then(r => r.text());
  }
  const job = jobs[0] || recs.find(r => r.type === 'Job') || {};
  return { defect_id: defectId, pipeline: b.definition.name, pipeline_id: b.definition.id, build_id: bid,
    source_branch: b.sourceBranch, source_version: b.sourceVersion, result: b.result, status: b.status,
    start: b.startTime, finish: b.finishTime, failed_step: step ? step.name : 'unknown step', issues, log_text: log,
    agent_name: job.workerName || null, collected_at: new Date().toISOString() };
}

const runs = JSON.parse(localStorage.getItem('pmruns'));
const done = JSON.parse(localStorage.getItem('pmcollected') || '[]');
const items = [], ids = [];
for (const id of Object.keys(runs)) {
  if (done.includes(id)) continue;
  const b = await fetch(`${base}/build/builds/${runs[id].runId}?api-version=7.1`).then(r => r.json());
  if (b.status !== 'completed') continue;
  items.push({ name: 'run_' + runs[id].runId, data: await collect(id, runs[id]) });
  ids.push(id);
}
const cs = new Blob([JSON.stringify(items)]).stream().pipeThrough(new CompressionStream('gzip'));
const buf = new Uint8Array(await new Response(cs).arrayBuffer());
let bin = ''; for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode.apply(null, buf.subarray(i, i + 0x8000));
localStorage.setItem('pmbatch', JSON.stringify(ids));
location.href = 'http://127.0.0.1:8765/ingest.html#' + btoa(bin).replace(/\+/g, '-').replace(/\//g, '_');

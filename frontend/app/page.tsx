'use client';
import {useCallback, useEffect, useMemo, useState} from 'react';
import {ReactFlow, Background, Controls, MarkerType, type Edge, type Node, type NodeMouseHandler} from '@xyflow/react';

type Tx = {id:string;source:string;target:string;paise:number;at_ms:number;device:string;kind:string};
type Candidate = {account:string;evidence_ids:string[];baseline:number;hold_now:number;hold_30m:number;simulated_difference_now:number;simulated_difference_30m:number;blocked_now:{id:string;reason:string}[]};
type Case = {id:string;title:string;flagged:string;truth:string};
type Step = {step:number;tool:string;args?:Record<string,unknown>;rows?:number;ms?:number;cypher?:string;note?:string;error?:string};
type Report = {case:Case;transfers:Tx[];paths:{accounts:string[];tx_ids:string[];cashout:boolean}[];shared_device:{device:string;account:string;tx_id:string;case_id:string}[];account_group:{method:string;members:string[]};candidates:Candidate[];evidence_ids:string[];recommendation:string;phrasing:string;path_check:string;decision_ms:number;trace:Step[]};
type Review = {id:number;action:string;account:string|null;rationale:string;evidence_ids:string[];created_utc:string};
const API = process.env.NEXT_PUBLIC_API_BASE || '/api';
const inr = (p:number) => `₹${(p/100).toLocaleString('en-IN', {minimumFractionDigits:2})}`;
const stamp = (ms:number) => new Date(ms).toLocaleTimeString('en-IN',{hour:'2-digit',minute:'2-digit',timeZone:'UTC'})+' UTC';

export default function Home() {
  const [cases,setCases]=useState<Case[]>([]), [caseId,setCaseId]=useState('split');
  const [report,setReport]=useState<Report|null>(null), [reviews,setReviews]=useState<Review[]>([]);
  const [selected,setSelected]=useState<string|null>(null), [time,setTime]=useState(100), [error,setError]=useState('');
  const [busy,setBusy]=useState(false), [rationale,setRationale]=useState(''), [account,setAccount]=useState<string|null>(null);
  const load=useCallback(async (id:string)=>{
    setError(''); setReport(null); setSelected(null); setAccount(null);
    try {
      const [a,b]=await Promise.all([fetch(`${API}/cases/${id}`),fetch(`${API}/reviews/${id}`)]);
      if(!a.ok) throw new Error(`Investigation unavailable (${a.status}). Start FalkorDB and seed the graph.`);
      setReport(await a.json()); setReviews(b.ok?await b.json():[]);
    } catch(e) {setError((e as Error).message)}
  },[]);
  useEffect(()=>{fetch(`${API}/cases`).then(r=>r.json()).then(setCases).catch(()=>setError('API unavailable. Start the FastAPI server.'));},[]);
  useEffect(()=>{load(caseId)},[caseId,load]);
  const visible=useMemo(()=>{if(!report)return []; const all=report.transfers; const max=Math.max(...all.map(t=>t.at_ms)); const min=Math.min(...all.map(t=>t.at_ms)); const cut=min+(max-min)*time/100; return all.filter(t=>t.at_ms<=cut)},[report,time]);
  const graphData=useMemo(()=>{
    const all=report?.transfers||[], depth:Record<string,number>={};
    for(const t of all){depth[t.source]??=0; depth[t.target]=Math.max(depth[t.target]??0,depth[t.source]+1);}
    const ids=[...new Set(visible.flatMap(t=>[t.source,t.target]))], row:Record<number,number>={};
    const cited=new Set(report?.evidence_ids||[]), cand=new Set(report?.candidates.map(c=>c.account)||[]);
    const nodes:Node[]=ids.map(id=>{const d=depth[id]??0, r=(row[d]=(row[d]??-1)+1); const hot=id===account;
      return {id,position:{x:d*240,y:r*120},data:{label:cand.has(id)?`${id} · candidate`:id},style:{background:id.startsWith('CASH')?'#ffdfd6':cand.has(id)?'#f4ecc8':'#e6f0eb',border:hot?'3px solid #102c23':'1px solid #416858',borderRadius:12,padding:10,fontWeight:700,color:'#18352c',width:170}}});
    const edges:Edge[]=visible.map(t=>{const on=cited.has(t.id)||t.id===selected;
      return {id:t.id,source:t.source,target:t.target,label:`${t.id} · ${inr(t.paise)}`,animated:t.kind==='cashout',markerEnd:{type:MarkerType.ArrowClosed},style:{stroke:on?'#c2412b':'#8aa496',strokeWidth:on?2.5:1.2},labelStyle:{fontSize:11,fontWeight:on?700:400}}});
    return {nodes,edges};
  },[visible,report,account,selected]);
  const selectedTx=report?.transfers.find(t=>t.id===selected);
  const selectedPaths=report?.paths.filter(p=>!selected||p.tx_ids.includes(selected))||[];
  async function seed(){setBusy(true);try{const r=await fetch(`${API}/seed`,{method:'POST'});if(!r.ok)throw new Error('Could not seed FalkorDB');await load(caseId)}catch(e){setError((e as Error).message)}finally{setBusy(false)}}
  async function save(action:string,account:string|null,cite:string[]=[]){if(!report)return;setBusy(true);try{
    const body={case_id:caseId,account,action,rationale:rationale.trim()||report.recommendation,evidence_ids:cite};
    const r=await fetch(`${API}/reviews`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    if(!r.ok)throw new Error((await r.json()).detail||'Review save failed');setRationale('');await load(caseId);
  }catch(e){setError((e as Error).message)}finally{setBusy(false)}}
  const onNodeClick:NodeMouseHandler=(_,node)=>{setAccount(node.id);setSelected(null)};
  const accountTx=report&&account?report.transfers.filter(t=>t.source===account||t.target===account):[];
  return <main className="shell">
    <header className="top"><div><span className="eyebrow">GRAPH INTELLIGENCE / LAB 01</span><h1>RING<span>BREAK</span></h1><p>Synthetic UPI network investigator</p></div><div className="badges"><b>SYNTHETIC DEMO</b><button onClick={seed} disabled={busy}>Seed FalkorDB</button></div></header>
    <div className="layout"><aside className="sidebar"><div className="section-title">INVESTIGATION QUEUE <span>04</span></div>{cases.map(c=><button key={c.id} className={`case ${caseId===c.id?'active':''}`} onClick={()=>{setCaseId(c.id);setTime(100)}}><small>{c.id.toUpperCase()} / FLAGGED {c.flagged}</small><strong>{c.title}</strong><span>Open case →</span></button>)}<p className="aside-note">Every identity and transfer is fictional. No real payment or bank action occurs.</p></aside>
    <section className="main-panel">{error&&<div className="error">{error}</div>}{report?<><div className="case-head"><div><span className="eyebrow">CASE {caseId.toUpperCase()} · FLAGGED TRANSACTION {report.case.flagged}</span><h2>{report.case.title}</h2></div><span className="pill">HUMAN REVIEW REQUIRED</span></div>
      <div className="graph-card"><div className="graph-head"><div><strong>Transaction network</strong><small>Click a node or evidence row to inspect</small></div><span>{visible.length} / {report.transfers.length} transfers</span></div><div className="flow"><ReactFlow nodes={graphData.nodes} edges={graphData.edges} onNodeClick={onNodeClick} fitView nodesDraggable={false} nodesConnectable={false}><Background color="#dce6de"/><Controls showInteractive={false}/></ReactFlow></div><div className="timeline"><span>TIME WINDOW</span><input aria-label="Time window" type="range" min="0" max="100" value={time} onChange={e=>setTime(Number(e.target.value))}/><strong>{visible.length?stamp(visible[visible.length-1].at_ms):'—'}</strong></div></div>
      <div className="lower"><section className="card"><div className="section-title">GRAPH EVIDENCE <span>{report.transfers.length}</span></div><div className="evidence-list">{visible.map(t=><button className={`evidence ${selected===t.id?'selected':''}`} key={t.id} onClick={()=>{setSelected(t.id);setAccount(null)}}><b>{t.id}</b><span>{t.source} → {t.target}<small>{stamp(t.at_ms)} · {t.device}</small></span><strong>{inr(t.paise)}</strong></button>)}</div></section><section className="card"><div className="section-title">EVIDENCE DRAWER</div>{account&&!selectedTx?<><h3>Account {account}</h3><p>{accountTx.length} graph transfer(s) touch this account{report.account_group.members.includes(account)?` · in flagged account group (${report.account_group.method})`:''}.</p>{accountTx.map(t=><p className="path" key={t.id}><b style={{color:'#264231',marginLeft:0}}>{t.id}</b> {t.source} → {t.target} · {inr(t.paise)} · {stamp(t.at_ms)}</p>)}</>:selectedTx?<><h3>{selectedTx.id}: {selectedTx.source} → {selectedTx.target}</h3><p>{inr(selectedTx.paise)} at {stamp(selectedTx.at_ms)}. Device: {selectedTx.device}.</p><p>{selectedPaths.length} time-ordered path(s) include this transfer.</p></>:<p>Select a node or transaction to inspect its exact evidence ID.</p>}<div className="divider"/><h3>Time-ordered paths</h3>{selectedPaths.length?selectedPaths.map((p,i)=><p className="path" key={i}>{p.tx_ids.join(' → ')} {p.cashout&&<b>Cash-out</b>}</p>):<p>No downstream path found.</p>}<div className="divider"/><h3>Shared device</h3><p>{report.shared_device.filter(x=>x.tx_id!==report.case.flagged).map(x=>`${x.account} via ${x.device} (${x.tx_id})`).join(', ')||'None'}</p><small>Device sharing alone does not establish fraud.</small></section></div>
      <div className="card compare"><div className="section-title">SIMULATED INTERVENTION · CANDIDATE COMPARISON</div>{report.candidates.length?<div className="candidate-grid">{report.candidates.map(c=><div className="candidate" key={c.account}><h3>{c.account}</h3><div><span>Baseline cash-out</span><b>{inr(c.baseline)}</b></div><div><span>Hold now</span><b>{inr(c.hold_now)}</b></div><div><span>Hold +30 min</span><b>{inr(c.hold_30m)}</b></div><div className="saving"><span>Simulated difference now</span><b>{inr(c.simulated_difference_now)}</b></div><small>Cites {c.evidence_ids.join(', ')} · Blocked now: {c.blocked_now.map(x=>x.id).join(', ')||'none'}</small><button disabled={busy} onClick={()=>save('recommend_hold',c.account,c.evidence_ids)}>Recommend review of hold</button></div>)}</div>:<p>No candidate supported by a time-ordered cash-out path.</p>}<p className="disclaimer">A hold here blocks simulated outbound transfers only. Differences are counterfactual synthetic cash-out, not recovered funds.</p></div>
      <div className="card trace"><div className="section-title">AGENT TRACE · FALKORDB QUERIES <span>{report.path_check.replaceAll('_',' ')}</span></div>{report.trace.map(s=><details key={s.step}><summary><b>{s.step}. {s.tool}</b> {s.error?`error ${s.error}`:`${s.rows??0} row(s)`}{s.ms!==undefined&&` · ${s.ms} ms`}<small>{JSON.stringify(s.args||{})}</small></summary>{s.cypher&&<pre>{s.cypher}</pre>}{s.note&&<p>{s.note}</p>}</details>)}<small>Tools are allowlisted. Evidence IDs come only from graph rows; replay is a deterministic local simulation.</small></div>
      <div className="card review"><div><div className="section-title">HUMAN REVIEW RECOMMENDATION <span>{report.phrasing}</span></div><p>{report.recommendation}</p><textarea aria-label="Review rationale" placeholder="Add a human review rationale (optional)" value={rationale} onChange={e=>setRationale(e.target.value)}/><div className="actions"><button disabled={busy} onClick={()=>save('no_hold',null)}>Record no hold</button><button disabled={busy} onClick={()=>save('needs_review',null)}>Needs further review</button></div></div><div><div className="section-title">AUDIT HISTORY</div>{reviews.length?reviews.map(r=><p className="history" key={r.id}><b>#{r.id} · {r.action.replaceAll('_',' ')}</b><small>{r.created_utc} · {r.account||'No account'} · {r.evidence_ids.join(', ')||'No cited path'}</small>{r.rationale}</p>):<p>No review actions yet.</p>}</div></div>
    </>:<div className="loading">{error?'Connect to the API and seed the graph to begin.':'Loading investigation…'}</div>}</section></div><footer>RINGBREAK · Synthetic demo · Graph-derived evidence · Simulated outcomes</footer></main>;
}

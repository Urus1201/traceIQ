import { useEffect, useMemo, useState } from 'react';

type SanityOut = Record<string, any>;
type BinaryOut = { sample_interval_us: number | null; samples_per_trace: number | null; format_code: number | null };

function HeaderIQPanel() {
  const [path, setPath] = useState<string>("/Users/souravmukherjee/Apps/traceIQ/data/Line_001.sgy");
  const [sanity, setSanity] = useState<SanityOut | null>(null);
  const [binary, setBinary] = useState<BinaryOut | null>(null);
  const [issues, setIssues] = useState<any[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [applyResult, setApplyResult] = useState<any | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function fetchAll() {
      if (!path) return;
      try {
        const [sRes, bRes] = await Promise.all([
          fetch('/header/sanity', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path }) }),
          fetch('/header/read_binary', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path }) }),
        ]);
        const sJson = await sRes.json();
        const bJson = await bRes.json();
        if (!cancelled) {
          setSanity(sJson);
          setBinary(bJson);
        }
      } catch (e) {
        console.error(e);
      }
    }
    fetchAll();
    return () => {
      cancelled = true;
    };
  }, [path]);

  useEffect(() => {
    async function check() {
      if (!sanity || !binary) return;
      // lightweight local rules mirroring server; server remains source of truth on apply
      const issues: any[] = [];
      const txtSi = sanity.sample_interval_ms?.value;
      const binSi = binary.sample_interval_us != null ? Math.round(binary.sample_interval_us / 1000) : null;
      if (txtSi != null && binSi != null && txtSi !== binSi) {
        issues.push({ field: 'sample_interval_ms', observed_text: txtSi, observed_binary: binSi, severity: 'critical' });
      }
      const txtSpt = sanity.samples_per_trace?.value;
      const binSpt = binary.samples_per_trace;
      if (txtSpt != null && binSpt != null && txtSpt !== binSpt) {
        issues.push({ field: 'samples_per_trace', observed_text: txtSpt, observed_binary: binSpt, severity: 'critical' });
      }
      setIssues(issues);
    }
    check();
  }, [sanity, binary]);

  const bannerText = useMemo(() => {
    if (!issues || issues.length === 0) return null;
    const i1 = issues.find(i => i.field === 'sample_interval_ms');
    const i2 = issues.find(i => i.field === 'samples_per_trace');
    if (i1 && i2) {
      return `Binary header contradicts textual header: interval ${i1.observed_binary} ms vs ${i1.observed_text} ms; samples ${i2.observed_binary} vs ${i2.observed_text}. Apply patch?`;
    }
    return 'Binary header contradicts textual header. Apply patch?';
  }, [issues]);

  const onApply = async () => {
    if (!path) return;
    setBusy(true);
    try {
      const resp = await fetch('/header/apply_patch', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path }) });
      const json = await resp.json();
      setApplyResult(json);
    } catch (e) {
      console.error(e);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div style={{ padding: 16 }}>
      <h2>Header IQ</h2>
      <label>
        SEG-Y Path:{' '}
        <input style={{ width: 500 }} value={path} onChange={e => setPath(e.target.value)} />
      </label>
      {bannerText && (
        <div style={{ background: '#fee2e2', color: '#991b1b', border: '1px solid #fca5a5', padding: 12, marginTop: 12 }}>
          <div>{bannerText}</div>
          <button onClick={onApply} disabled={busy} style={{ marginTop: 8 }}>Apply patch</button>
        </div>
      )}
      {applyResult && (
        <pre style={{ marginTop: 12, background: '#f8fafc', padding: 12 }}>{JSON.stringify(applyResult, null, 2)}</pre>
      )}
    </div>
  );
}

export default function App() {
  return <HeaderIQPanel />;
}
